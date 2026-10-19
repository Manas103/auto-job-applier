"""Worker: runs approved jobs through the stages with locks, budgets, fix rounds, and crash recovery."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import db, usage
from . import watchlist as wl
from .config import Config
from .jd import fetch_jd, store_jd
from .log import stage_log_path
from .models import Job, JobState, Stage
from .roles import ensure_role_folder
from .screen import screen_jd
from .stages import STAGE_ORDER, StageSpec, get_specs
from .stages.claude_runner import CONTINUE_PROMPT, ClaudeResult, run_claude, session_cost
from .statemachine import (LITE_STAGE, STAGE_STATES, IllegalTransition, done_state_for_stage, ready_state_for_stage,
                           running_state_for_stage, stage_after, stage_for_running_state, transition)
from .util import now_iso, today

log = logging.getLogger("pipeline.worker")

# A job that has started the full pipeline finishes it, whatever its tier says now.
_FULL_ONLY_STATES = {JobState.IDEAMAKER_RUNNING, JobState.IDEAMAKER_DONE, JobState.BUILDING, JobState.BUILT,
                     JobState.RECONCILING, JobState.RECONCILED, JobState.REVIEWING}


def _stamp_meta(role_folder: Path, cfg: Config) -> None:
    """Record lane and project policy in a full run's meta.json so outcomes can later be compared by lane/policy."""
    p = role_folder / "meta.json"
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    want = {"lane": "full", "policy": str(cfg.get("project_policy") or "design_first")}
    if isinstance(meta, dict) and any(meta.get(k) != v for k, v in want.items()):
        meta.update(want)
        p.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def effective_lane(conn: sqlite3.Connection, cfg: Config, job: Job) -> str:
    """full | light | archetype for this job right now (see rank.job_lane and config `tiers`)."""
    if job.state in _FULL_ONLY_STATES:
        return "full"
    from .rank import job_lane
    try:
        return job_lane(conn, cfg, job.id)
    except Exception as e:  # noqa: BLE001
        log.warning("lane lookup failed for job %s (%s); running the full pipeline", job.id, e)
        return "full"


# ----------------------------------------------------------------------------- Windows Job Object

class JobObject:
    """Job object holding the build subprocesses. Kill-on-close so children die with this process, plus (Windows)
    a hard total-memory cap and a hard CPU-rate cap so a build can never starve Windows/Chrome. No-op off Windows.
    WSL builds run in a separate VM and are capped independently by .wslconfig (memory/processors)."""

    def __init__(self, *, mem_limit_gb: float | None = None, cpu_rate_pct: int | None = None) -> None:
        self.handle = None
        self.mem_limit_gb = mem_limit_gb
        self.cpu_rate_pct = cpu_rate_pct
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                               "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if mem_limit_gb and mem_limit_gb > 0:
                flags |= 0x00000200  # JOB_OBJECT_LIMIT_JOB_MEMORY: cap total committed memory of all job processes
                info.JobMemoryLimit = int(mem_limit_gb * (1024 ** 3))
            info.BasicLimitInformation.LimitFlags = flags
            ok = k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info))  # JobObjectExtendedLimitInformation
            if not ok:
                k32.CloseHandle(h)
                return
            # Hard CPU-rate cap: all processes in the job together get at most cpu_rate_pct of total CPU.
            if cpu_rate_pct and 0 < cpu_rate_pct < 100:
                class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
                    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]
                cpu = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
                cpu.ControlFlags = 0x1 | 0x4  # ENABLE | HARD_CAP
                cpu.CpuRate = int(cpu_rate_pct * 100)  # units of 1/100 of a percent (60% -> 6000)
                k32.SetInformationJobObject(h, 15, ctypes.byref(cpu), ctypes.sizeof(cpu))  # JobObjectCpuRateControlInformation
            self.handle = h
            self._k32 = k32
        except Exception as e:  # noqa: BLE001
            log.debug("job object unavailable: %s", e)
            self.handle = None

    def assign(self, pid: int) -> bool:
        if not self.handle:
            return False
        try:
            PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
            hp = self._k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
            if not hp:
                return False
            try:
                return bool(self._k32.AssignProcessToJobObject(self.handle, hp))
            finally:
                self._k32.CloseHandle(hp)
        except Exception as e:  # noqa: BLE001
            log.debug("assign to job object failed: %s", e)
            return False


class StageFailed(Exception):
    def __init__(self, stage: Stage, reason: str, failures: list | None = None):
        super().__init__(f"{stage.value}: {reason}")
        self.stage = stage
        self.reason = reason
        self.failures = failures or []


# ----------------------------------------------------------------------------- runner

class JobRunner:
    def __init__(self, cfg: Config, conn: sqlite3.Connection, pool: "WorkerPool | None", job_id: int):
        self.cfg = cfg
        self.conn = conn
        self.pool = pool
        self.job_id = job_id
        self.specs = get_specs()

    # -- helpers
    def job(self) -> Job:
        j = db.job_by_id(self.conn, self.job_id)
        if j is None:
            raise RuntimeError(f"job {self.job_id} vanished")
        return j

    def _status(self, text: str) -> None:
        if self.pool is not None:
            self.pool.status[self.job_id] = text

    # -- approval to jd_ready
    def prepare(self) -> Job:
        """approved -> jd_ready | needs_jd (fetch JD with browser allowed, screen it, create the role folder)."""
        job = self.job()
        if job.state != JobState.APPROVED:
            return job
        if bool(self.cfg.watchlist.auto_add_on_approve):
            try:
                wl.auto_add_from_url(self.conn, self.cfg, job)
            except Exception as e:  # noqa: BLE001
                log.info("watchlist auto-add failed for %s: %s", job.id, e)
        if not job.jd_text:
            self._status("fetching job description")
            res = fetch_jd(job, self.cfg, allow_browser=True)
            store_jd(self.conn, self.cfg, job.id, res)
            job = self.job()
        if not job.jd_text:
            return transition(self.conn, job.id, JobState.NEEDS_JD, "jd_missing")
        sres = screen_jd(job, job.jd_text, self.cfg, today())
        flags = list(job.flags)
        for f in sres.flags:
            if f not in flags:
                flags.append(f)
        if sres.verdict == "screen_out":
            return transition(self.conn, job.id, JobState.SCREENED_OUT, "screened_post_approval", sres.to_dict(),
                              extra_fields={"screen_reason": sres.reason, "flags": flags})
        folder = ensure_role_folder(job, self.cfg)
        return transition(self.conn, job.id, JobState.JD_READY, "jd_ready", {"folder": str(folder)},
                          extra_fields={"role_folder": str(folder), "flags": flags})

    # -- stage execution
    def run_stage(self, stage: Stage) -> bool:
        spec = self.specs[stage]
        st = self.cfg.stage(stage.value)
        job = self.job()
        ready, running, done = ready_state_for_stage(stage), running_state_for_stage(stage), done_state_for_stage(stage)
        if job.state != ready:
            raise IllegalTransition(f"job {job.id} is {job.state.value}, not {ready.value}")
        # Adaptive governor: hold before starting work while the machine is under memory pressure (you are busy).
        # The job stays in its ready state while waiting; it is not claimed until pressure clears.
        if self.pool is not None and getattr(self.pool, "governor", None) is not None:
            self.pool.governor.wait_until_ok(self.pool.stop_event, on_wait=self._status)
        # Usage brake (pipeline/usage.py): full-lane stages wait while the week is at the hard cap or this project has
        # used its share. The job stays in its ready state, unclaimed, and continues after the weekly reset.
        if not usage.wait_until_ok(self.cfg, self.conn, "full", self.pool.stop_event if self.pool is not None else None,
                                   on_wait=self._status):
            return False
        attempt = db.last_attempt(self.conn, job.id, stage.value) + 1
        # Only genuinely failed attempts count; runs aborted by a crash/restart are resumed, not charged.
        failed_before = db.failed_attempts(self.conn, job.id, stage.value)
        if failed_before >= int(st.max_attempts):
            transition(self.conn, job.id, running, "stage_started", {"attempt": attempt}, expect_from=ready)
            transition(self.conn, job.id, JobState.FAILED, "stage_failed", {"reason": "max attempts exhausted"},
                       failed_stage=stage.value)
            return False
        spent = db.job_cost(self.conn, job.id)
        budget = float(self.cfg.worker.job_budget_usd)
        if spent + float(st.max_budget_usd) > budget:
            transition(self.conn, job.id, running, "stage_started", {"attempt": attempt}, expect_from=ready)
            transition(self.conn, job.id, JobState.FAILED, "stage_failed",
                       {"reason": f"job budget exceeded: spent {spent:.2f} + stage {st.max_budget_usd} > {budget}"},
                       failed_stage=stage.value)
            return False
        role_folder = Path(job.role_folder) if job.role_folder else ensure_role_folder(job, self.cfg)
        if not job.role_folder:
            db.update_job(self.conn, job.id, role_folder=str(role_folder))
        # Claim the job.
        transition(self.conn, job.id, running, "stage_started", {"attempt": attempt}, expect_from=ready)
        log_path = stage_log_path(self.cfg, job.id, stage.value, attempt)
        lock = self.pool.builder_lock if (self.pool is not None and spec.exclusive) else None
        if lock is not None:
            self._status(f"{stage.value}: waiting for builder lock")
            lock.acquire()
        run_id = db.start_stage_run(self.conn, job.id, stage.value, attempt, str(log_path), worker_pid=os.getpid())
        try:
            ok, cost, turns, sid, fix_rounds, result_json, verify_json, error = self._run_stage_loop(spec, st, job, role_folder, run_id, attempt)
        finally:
            if lock is not None:
                lock.release()
        db.finish_stage_run(self.conn, run_id, "succeeded" if ok else "failed", session_id=sid, cost_usd=cost,
                            num_turns=turns, result_json=result_json, verify_json=verify_json, fix_rounds=fix_rounds, error=error)
        if ok:
            _stamp_meta(role_folder, self.cfg)
            if spec.on_success:
                try:
                    spec.on_success(role_folder, self.job(), self.cfg)
                except Exception as e:  # noqa: BLE001
                    log.warning("on_success hook failed for %s: %s", stage.value, e)
            transition(self.conn, job.id, done, "stage_finished", {"attempt": attempt, "cost_usd": cost, "fix_rounds": fix_rounds})
            return True
        transition(self.conn, job.id, JobState.FAILED, "stage_failed", {"attempt": attempt, "error": error, "cost_usd": cost},
                   failed_stage=stage.value)
        return False

    def _run_stage_loop(self, spec: StageSpec, st: Any, job: Job, role_folder: Path, run_id: int, attempt: int):
        stage = spec.stage
        max_fix = int(st.max_fix_rounds)
        timeout_s = int(float(st.timeout_min) * 60)
        cost = 0.0
        turns = 0
        results: list[dict] = []
        sid: str | None = None
        env_extra = {"PIPELINE_JOB_ID": str(job.id), "PIPELINE_STAGE": stage.value}
        model = st.get("model") or None  # per-stage override; None falls back to cfg.claude.model
        tools = list(st.get("tools") or []) or None  # per-stage tool allowlist; None offers the CLI defaults
        job_object = self.pool.job_object if self.pool is not None else None

        def _hb() -> None:
            try:
                db.heartbeat_stage_run(self.conn, run_id)
            except sqlite3.Error:
                pass

        def _pid(pid: int) -> None:
            try:
                db.heartbeat_stage_run(self.conn, run_id, pid=pid)
            except sqlite3.Error:
                pass

        def _session(sid_now: str) -> None:
            try:
                db.update_stage_run(self.conn, run_id, session_id=sid_now)
            except sqlite3.Error:
                pass

        if stage == Stage.IDEAMAKER:
            # Company-level research runs once per company on a cheap model with a small context (pipeline/research.py).
            from .research import ensure_dossier
            self._status("ideamaker: company research")
            ensure_dossier(self.cfg, job, self.conn)
        # Resume hint from a crashed previous attempt.
        hint_key = f"resume_hint:{job.id}:{stage.value}"
        hint = db.get_setting(self.conn, hint_key)
        if hint:
            db.del_setting(self.conn, hint_key)
            prompt, resume = CONTINUE_PROMPT + "\n\n" + spec.build_prompt(job, self.cfg, role_folder, {}), hint
        else:
            prompt, resume = spec.build_prompt(job, self.cfg, role_folder, {}), None
        self._status(f"{stage.value}: running (attempt {attempt})")
        res = run_claude(self.cfg, prompt=prompt, cwd=role_folder, log_path=stage_log_path(self.cfg, job.id, stage.value, attempt),
                         max_budget_usd=float(st.max_budget_usd), timeout_s=timeout_s, max_turns=int(st.get("max_turns", 0) or 0),
                         resume_session=resume, add_dirs=[self.cfg.project_root], env_extra=env_extra, on_pid=_pid,
                         heartbeat=_hb, job_object=job_object, on_session=_session, model=model, tools=tools)
        if resume and res.exit_code != 0 and res.duration_s < 60 and not res.timed_out:
            # The resume failed fast (session gone). Start fresh.
            res = run_claude(self.cfg, prompt=spec.build_prompt(job, self.cfg, role_folder, {}), cwd=role_folder,
                             log_path=stage_log_path(self.cfg, job.id, stage.value, attempt), max_budget_usd=float(st.max_budget_usd),
                             timeout_s=timeout_s, max_turns=int(st.get("max_turns", 0) or 0), add_dirs=[self.cfg.project_root],
                             env_extra=env_extra, on_pid=_pid, heartbeat=_hb, job_object=job_object, on_session=_session, model=model, tools=tools)
        fix_rounds = 0
        verify_json = None
        error = None
        ok = False
        while True:
            turns += res.num_turns
            sid = res.session_id or sid
            results.append({"subtype": res.subtype, "cost_usd": res.cost_usd, "num_turns": res.num_turns, "exit_code": res.exit_code,
                            "timed_out": res.timed_out, "session_id": res.session_id, "result": res.result_text[-500:]})
            cost = session_cost(results)
            if not res.ok:
                if res.subtype in ("error_max_turns", "error_max_budget_usd") and res.session_id and fix_rounds < max_fix:
                    # The session was cut off by a cap, not by an error: let it finish once, on the fix budget.
                    # Bounded by max_fix_rounds, so a runaway run still stops.
                    fix_rounds += 1
                    self._status(f"{stage.value}: continuing after {'budget' if res.subtype == 'error_max_budget_usd' else 'turn'} cap (round {fix_rounds})")
                    res = run_claude(self.cfg, prompt=CONTINUE_PROMPT, cwd=role_folder,
                                     log_path=stage_log_path(self.cfg, job.id, stage.value, attempt),
                                     max_budget_usd=float(st.fix_budget_usd), timeout_s=timeout_s,
                                     max_turns=int(st.get("fix_turns", 0) or 0), resume_session=res.session_id,
                                     add_dirs=[self.cfg.project_root], env_extra=env_extra, on_pid=_pid, heartbeat=_hb,
                                     job_object=job_object, on_session=_session, model=model, tools=tools)
                    continue
                error = f"claude {res.subtype} (exit {res.exit_code}{', timed out' if res.timed_out else ''})"
                break
            self._status(f"{stage.value}: verifying")
            failures = spec.check(role_folder, job, self.cfg)
            verify_json = json.dumps([f.to_dict() for f in failures])
            if not failures:
                ok = True
                break
            if fix_rounds >= max_fix:
                error = f"checks failed after {fix_rounds} fix rounds: " + "; ".join(f.format() for f in failures[:6])
                break
            fix_rounds += 1
            self._status(f"{stage.value}: fix round {fix_rounds}")
            res = run_claude(self.cfg, prompt=spec.fix_prompt(failures, role_folder, job, self.cfg), cwd=role_folder,
                             log_path=stage_log_path(self.cfg, job.id, stage.value, attempt),
                             max_budget_usd=float(st.fix_budget_usd), timeout_s=timeout_s,
                             max_turns=int(st.get("fix_turns", 0) or 0), resume_session=sid,
                             add_dirs=[self.cfg.project_root], env_extra=env_extra, on_pid=_pid, heartbeat=_hb,
                             job_object=job_object, on_session=_session, model=model, tools=tools)
        return ok, cost, turns, sid, fix_rounds, json.dumps(results), verify_json, error

    # -- light / archetype lanes
    def run_lite(self, lane: str) -> JobState:
        """jd_ready -> tailoring -> done | failed. The archetype lane copies an approved archetype resume ($0); the
        light lane also runs one short tailoring session. With no approved archetype the job waits, flagged."""
        from . import lite
        from .roles import role_slug
        from .verify import verify_lite
        job = self.job()
        name = lite.pick_archetype(self.cfg, job)
        if name is None:
            if "awaiting_lane" not in job.flags:
                db.add_flag(self.conn, job.id, "awaiting_lane")
                db.add_event(self.conn, job.id, "awaiting_lane", detail={"lane": lane, "reason": "no approved archetype yet"})
            return job.state
        if lane == "light" and not usage.wait_until_ok(self.cfg, self.conn, "light",
                                                       self.pool.stop_event if self.pool is not None else None,
                                                       on_wait=self._status):
            return job.state
        role_folder = Path(job.role_folder) if job.role_folder else ensure_role_folder(job, self.cfg)
        attempt = db.last_attempt(self.conn, job.id, LITE_STAGE) + 1
        flags = [f for f in job.flags if f != "awaiting_lane"]
        transition(self.conn, job.id, JobState.TAILORING, "lite_started", {"lane": lane, "archetype": name, "attempt": attempt},
                   expect_from=JobState.JD_READY, extra_fields={"role_folder": str(role_folder), "flags": flags})
        job = self.job()
        log_path = stage_log_path(self.cfg, job.id, LITE_STAGE, attempt)
        run_id = db.start_stage_run(self.conn, job.id, LITE_STAGE, attempt, str(log_path), worker_pid=os.getpid())
        ok, cost, turns, sid, fix_rounds, result_json, verify_json, error = False, 0.0, 0, None, 0, None, None, None
        try:
            lite.install_archetype(self.cfg, job, role_folder, name, lane)
            if lane == "archetype":
                failures = verify_lite(role_folder, self.cfg, role_slug(job), compile=False).failures
                verify_json = json.dumps([f.to_dict() for f in failures])
                ok = not failures
                if failures:
                    error = "archetype checks failed: " + "; ".join(f.format() for f in failures[:6])
            else:
                ok, cost, turns, sid, fix_rounds, result_json, verify_json, error = self._run_stage_loop(
                    lite.light_spec(name), self.cfg.lite, job, role_folder, run_id, attempt)
        except Exception as e:  # noqa: BLE001
            log.exception("lite lane crashed for job %s", job.id)
            error = f"{type(e).__name__}: {e}"[:500]
        db.finish_stage_run(self.conn, run_id, "succeeded" if ok else "failed", session_id=sid, cost_usd=cost, num_turns=turns,
                            result_json=result_json, verify_json=verify_json, fix_rounds=fix_rounds, error=error)
        if ok:
            return transition(self.conn, job.id, JobState.DONE, "lite_finished",
                              {"lane": lane, "archetype": name, "cost_usd": cost, "fix_rounds": fix_rounds}).state
        return transition(self.conn, job.id, JobState.FAILED, "stage_failed", {"lane": lane, "error": error, "cost_usd": cost},
                          failed_stage=LITE_STAGE).state

    def run_to_completion(self, *, stop_after: Stage | None = None) -> JobState:
        job = self.job()
        if job.state == JobState.APPROVED:
            job = self.prepare()
        if job.state == JobState.JD_READY:
            lane = effective_lane(self.conn, self.cfg, job)
            if lane != "full":
                return self.run_lite(lane)
        while True:
            job = self.job()
            nxt = stage_after(job.state)
            if nxt is None:
                return job.state
            ok = self.run_stage(nxt)
            if not ok:
                return self.job().state
            if stop_after is not None and nxt == stop_after:
                return self.job().state
            if not bool(self.cfg.worker.auto_continue):
                return self.job().state


def run_one(cfg: Config, job_id: int, *, stop_after: Stage | None = None, pool: "WorkerPool | None" = None) -> JobState:
    conn = db.thread_conn(cfg.db_path)
    job = db.job_by_id(conn, job_id)
    if job is None:
        raise ValueError(f"no job {job_id}")
    if job.state == JobState.FAILED:
        from .statemachine import retry_target
        tgt = retry_target(job)
        if tgt is not None:
            transition(conn, job_id, tgt, "retry")
    return JobRunner(cfg, conn, pool, job_id).run_to_completion(stop_after=stop_after)


# ----------------------------------------------------------------------------- pool

class WorkerPool:
    def __init__(self, cfg: Config, db_path: Path | str, n_threads: int | None = None):
        self.cfg = cfg
        self.db_path = Path(db_path)
        self.n = int(n_threads or cfg.worker.threads)
        self.q: "queue.PriorityQueue[tuple[int, float, int, Stage | None]]" = queue.PriorityQueue()
        # Light/archetype jobs get their own thread so they finish in minutes instead of waiting behind a build.
        self.lite_q: "queue.PriorityQueue[tuple[int, float, int, Stage | None]]" = queue.PriorityQueue()
        self.builder_lock = threading.Lock()
        self.job_object = JobObject(mem_limit_gb=float(cfg.worker.get("job_mem_limit_gb", 8) or 0),
                                    cpu_rate_pct=int(cfg.worker.get("cpu_rate_cap_pct", 60) or 0))
        from .govern import ResourceGovernor
        self.governor = ResourceGovernor(cfg)
        self.enqueued: set[int] = set()
        self.enq_lock = threading.Lock()
        self.status: dict[int, str] = {}
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.active: dict[int, int] = {}  # job_id -> thread ident

    def enqueue(self, job_id: int, *, priority: int | None = None, stop_after: Stage | None = None) -> bool:
        """Queue a job on the lane its company tier picks. Scores an unscored company first (one cheap call) and
        writes jobs.priority, which Smart Apply also sorts by. A light/archetype job with no approved archetype
        yet is not queued; it is flagged `awaiting_lane` and picked up on the next start."""
        from . import lite
        from .rank import ensure_scores, job_priority
        conn = db.thread_conn(self.db_path)
        job = db.job_by_id(conn, job_id)
        lane = "full"
        if job is not None:
            if bool(self.cfg.get("rank", {}).get("enabled", True)) and job.company:
                ensure_scores(conn, self.cfg, [job.company])
            if priority is None:
                try:
                    priority = job_priority(conn, job_id)
                except Exception:  # noqa: BLE001
                    priority = 0
            if job.priority != priority:
                db.update_job(conn, job_id, priority=priority)
            lane = effective_lane(conn, self.cfg, job)
            if lane != "full" and job.state in (JobState.APPROVED, JobState.JD_READY) and not lite.library_ready(self.cfg):
                if "awaiting_lane" not in job.flags:
                    db.add_flag(conn, job_id, "awaiting_lane")
                    db.add_event(conn, job_id, "awaiting_lane", detail={"lane": lane, "reason": "no approved archetype yet"})
                return False
            if "awaiting_lane" in job.flags:
                db.update_job(conn, job_id, flags=[f for f in job.flags if f != "awaiting_lane"])
        priority = int(priority or 0)
        with self.enq_lock:
            if job_id in self.enqueued or job_id in self.active:
                return False
            self.enqueued.add(job_id)
        (self.q if lane == "full" else self.lite_q).put((-priority, time.time(), job_id, stop_after))
        return True

    def queue_snapshot(self) -> list[int]:
        with self.enq_lock:
            return sorted(self.enqueued)

    def recover(self, conn: sqlite3.Connection) -> list[int]:
        """Mark orphaned running stage_runs aborted, roll jobs back to their ready state, store resume hints."""
        recovered: list[int] = []
        for run in db.running_stage_runs(conn):
            db.update_stage_run(conn, run.id, status="aborted", error="process restart", finished_at=now_iso())
            job = db.job_by_id(conn, run.job_id)
            if job is None:
                continue
            if job.state == JobState.TAILORING and run.stage == LITE_STAGE:
                try:
                    transition(conn, job.id, JobState.JD_READY, "recovered", {"run_id": run.id, "attempt": run.attempt})
                except IllegalTransition:
                    pass
            stage = stage_for_running_state(job.state)
            if stage is not None and stage.value == run.stage:
                try:
                    transition(conn, job.id, ready_state_for_stage(stage), "recovered", {"run_id": run.id, "attempt": run.attempt})
                except IllegalTransition:
                    pass
                if run.session_id:
                    db.set_setting(conn, f"resume_hint:{job.id}:{stage.value}", run.session_id)
            if job.id not in recovered:
                recovered.append(job.id)
        # A job can hold a running state with no stage run at all: it claimed the state, then died waiting for the
        # builder lock. Roll it back too, or it sits in `building` forever (Initech, 2026-08-25).
        for stage, (ready, running, _done) in STAGE_STATES.items():
            for job in db.jobs_in_states(conn, [running]):
                if job.id in recovered:
                    continue
                try:
                    transition(conn, job.id, ready, "recovered", {"reason": "claimed the stage but no run had started (lock wait)"})
                except IllegalTransition:
                    continue
                recovered.append(job.id)
        for job in db.jobs_in_states(conn, [JobState.TAILORING]):
            if job.id in recovered:
                continue
            try:
                transition(conn, job.id, JobState.JD_READY, "recovered", {"reason": "tailoring with no run"})
            except IllegalTransition:
                continue
            recovered.append(job.id)
        if bool(self.cfg.worker.auto_requeue_on_restart):
            for jid in recovered:
                job = db.job_by_id(conn, jid)
                if job and stage_after(job.state) is not None:
                    self.enqueue(jid)
            if bool(self.cfg.worker.get("requeue_ready_on_start", True)):
                ready = [JobState.APPROVED] + [ready_state_for_stage(s) for s in STAGE_STATES]
                for job in db.jobs_in_states(conn, ready):
                    if job.active and "shelved" not in job.flags:
                        self.enqueue(job.id)
        # Lite jobs held until an archetype was approved.
        from .lite import library_ready
        if library_ready(self.cfg):
            for job in db.jobs_in_states(conn, [JobState.APPROVED, JobState.JD_READY]):
                if "awaiting_lane" in job.flags and job.active and "shelved" not in job.flags:
                    self.enqueue(job.id)
        return recovered

    def _worker(self, q: "queue.PriorityQueue | None" = None) -> None:
        q = q if q is not None else self.q
        conn = db.thread_conn(self.db_path)
        while not self.stop_event.is_set():
            try:
                _, _, job_id, stop_after = q.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.enq_lock:
                self.enqueued.discard(job_id)
                self.active[job_id] = threading.get_ident()
            try:
                self.status[job_id] = "starting"
                run_one(self.cfg, job_id, stop_after=stop_after, pool=self)
            except Exception as e:  # noqa: BLE001
                log.exception("job %s crashed in worker: %s", job_id, e)
                try:
                    job = db.job_by_id(conn, job_id)
                    if job is not None:
                        stage = stage_for_running_state(job.state)
                        if stage is not None:
                            transition(conn, job_id, JobState.FAILED, "stage_failed", {"error": str(e)[:300]}, failed_stage=stage.value)
                except Exception:  # noqa: BLE001
                    pass
            finally:
                with self.enq_lock:
                    self.active.pop(job_id, None)
                self.status.pop(job_id, None)
                q.task_done()

    def start(self) -> None:
        self.governor.start()
        for i in range(self.n):
            t = threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        t = threading.Thread(target=self._worker, args=(self.lite_q,), name="worker-lite", daemon=True)
        t.start()
        self.threads.append(t)

    def stop(self, timeout: float | None = None) -> None:
        self.stop_event.set()
        self.governor.stop()  # also releases any build waiting on the memory gate
        grace = float(timeout if timeout is not None else self.cfg.worker.stop_grace_s)
        for t in self.threads:
            t.join(timeout=grace)

    def snapshot(self) -> dict[str, Any]:
        return {"threads": self.n, "queued": self.queue_snapshot(), "active": dict(self.active),
                "status": dict(self.status), "governor": self.governor.snapshot()}
