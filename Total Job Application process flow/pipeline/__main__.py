"""CLI: python -m pipeline <command>."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from . import db
from .config import Config, load_config
from .log import get_logger, setup_logging
from .models import JobState, Listing, Stage


def _cfg(args) -> Config:
    path = args.config or os.environ.get("PIPELINE_CONFIG")
    if path:
        p = Path(path)
        return load_config(p, project_root=p.parent)
    return load_config()


def cmd_serve(args) -> int:
    cfg = _cfg(args)
    setup_logging(cfg, console=not args.quiet)
    log = get_logger("serve")
    from .collector import CollectorThread
    from .web.server import AppState, serve_in_thread
    from .worker import WorkerPool

    lock_path = cfg.data_dir / "serve.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "a+")  # noqa: SIM115
    try:
        if sys.platform == "win32":
            import msvcrt
            lock_fh.seek(0)
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another serve instance holds data/serve.lock; exiting", file=sys.stderr)
        return 3
    lock_fh.seek(0)
    lock_fh.truncate()
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()

    conn = db.thread_conn(cfg.db_path)
    worker = None
    if not args.no_worker:
        worker = WorkerPool(cfg, cfg.db_path, n_threads=args.workers or None)
        recovered = worker.recover(conn)
        if recovered:
            log.info("recovered %d interrupted job(s): %s", len(recovered), recovered)
        worker.start()
    collector = None
    if not args.no_collector:
        collector = CollectorThread(cfg, cfg.db_path)
        collector.start()
    state = AppState(cfg, collector=collector, worker=worker)
    host = str(cfg.dashboard.host)
    port = int(args.port if args.port is not None else cfg.dashboard.port)
    srv, thread = serve_in_thread(state, host, port)
    actual = srv.server_address[1]
    msg = f"LISTENING http://{host}:{actual}"
    print(msg, flush=True)
    log.info(msg)
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)  # type: ignore[attr-defined]
    signal.signal(signal.SIGTERM, _stop)
    try:
        while not stop["flag"]:
            time.sleep(0.5)
    finally:
        log.info("shutting down")
        if collector:
            collector.stop()
        if worker:
            worker.stop()
        srv.shutdown()
        try:
            lock_fh.close()
        except OSError:
            pass
    return 0


def cmd_collect(args) -> int:
    cfg = _cfg(args)
    setup_logging(cfg, console=True)
    from .collector import run_collect
    conn = db.thread_conn(cfg.db_path)
    rep = run_collect(conn, cfg, notify=not args.no_notify, only_sources=args.source or None, tier2=not args.no_tier2)
    print(rep.summary())
    if rep.seed:
        print(f"First run: seeded {rep.new} listings quietly. Future runs ping only what is new.")
    return 0 if not (rep.errors and not rep.fetched_per_source) else 1


def cmd_run(args) -> int:
    cfg = _cfg(args)
    setup_logging(cfg, console=True)
    from .worker import run_one
    conn = db.thread_conn(cfg.db_path)
    job = None
    if args.job.isdigit():
        job = db.job_by_id(conn, int(args.job))
    if job is None:
        job = db.job_by_key(conn, args.job)
    if job is None:
        print(f"no job {args.job!r}", file=sys.stderr)
        return 2
    if job.state in (JobState.NEW, JobState.PINGED, JobState.IGNORED, JobState.SCREENED_OUT):
        from .statemachine import transition
        if job.state == JobState.NEW:
            transition(conn, job.id, JobState.PINGED, "pinged_state")
        transition(conn, job.id, JobState.APPROVED, "approved_cli")
    stop_after = Stage(args.stop_after) if args.stop_after else None
    state = run_one(cfg, job.id, stop_after=stop_after)
    j = db.job_by_id(conn, job.id)
    print(f"job {job.id} ({job.company}: {job.title}) -> {state.value}; cost ${db.job_cost(conn, job.id):.2f}; folder {j.role_folder}")
    return 0 if state != JobState.FAILED else 1


def cmd_verify(args) -> int:
    cfg = _cfg(args)
    from .verify import format_failures, verify_role_folder
    res = verify_role_folder(Path(args.folder), cfg, compile=None if not args.no_compile else False,
                             check_meta_json=not args.no_meta)
    if res.ok:
        print(f"OK {args.folder} pages={res.pages}")
        return 0
    print(format_failures(res.failures))
    return 1


def cmd_check(args) -> int:
    """The stage self-check: the same deterministic checks the worker runs after the stage, in one compact report.
    Stages run this instead of compiling, counting pages and grepping by hand, and finish only on PASS."""
    cfg = _cfg(args)
    from .roles import role_slug
    from .stages import get_specs
    from .verify import verify_role_folder
    folder = Path(args.folder).resolve()
    conn = db.thread_conn(cfg.db_path)
    row = conn.execute("SELECT id FROM jobs WHERE role_folder=? ORDER BY id DESC LIMIT 1", (str(folder),)).fetchone()
    job = db.job_by_id(conn, int(row[0])) if row else None
    if args.stage == "lite":
        from .verify import infer_slug, verify_lite
        slug = role_slug(job) if job else None
        if slug is None:
            try:
                slug = json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("slug")
            except (OSError, ValueError):
                slug = None
        failures = verify_lite(folder, cfg, slug if slug is not None else (infer_slug(folder) or "")).failures
    elif args.stage == "resume" or job is None:
        failures = verify_role_folder(folder, cfg, slug=role_slug(job) if job else None).failures
    else:
        failures = get_specs()[Stage(args.stage)].check(folder, job, cfg)
    if not failures:
        print("PASS")
        return 0
    print(f"FAIL ({len(failures)})")
    for f in failures:
        print("- " + f.format())
    return 1


def cmd_jd(args) -> int:
    cfg = _cfg(args)
    from .jd import fetch_jd, store_jd
    conn = db.thread_conn(cfg.db_path)
    job = db.job_by_id(conn, int(args.job_id))
    if job is None:
        print("no such job", file=sys.stderr)
        return 2
    res = fetch_jd(job, cfg, allow_browser=not args.no_browser)
    store_jd(conn, cfg, job.id, res)
    print(f"source={res.source} ok={res.ok} chars={len(res.text or '')} attempts={res.attempts}")
    if res.ok and args.print:
        print(res.text)
    return 0 if res.ok else 1


def cmd_notify_test(args) -> int:
    cfg = _cfg(args)
    from .notify import notify_job
    conn = db.thread_conn(cfg.db_path)
    url = "https://example.com/jobs/test-toast"
    job = db.job_by_key(conn, "example.com/jobs/test-toast")
    if job is None:
        jid = db.insert_job(conn, Listing(source="manual", company="Example Co", title="Software Engineer Intern (toast test)", url=url,
                                          locations=["Chicago, IL"], category="Software", pay="$50/hr", terms=["Summer 2027"]),
                            "example.com/jobs/test-toast", "example|software engineer intern toast test|chicago il")
        job = db.job_by_id(conn, jid)
    from .statemachine import transition
    if job.state == JobState.NEW:
        transition(conn, job.id, JobState.PINGED, "pinged_state")
    ok = notify_job(conn, cfg, job)
    print(f"toast shown: {ok}; job id {job.id}; decide with the buttons or at {cfg.dashboard.public_base_url}/job/{job.id}")
    return 0 if ok else 1


def cmd_seed(args) -> int:
    """Copy the original role folders and project repos into this workspace (idempotent; originals untouched)."""
    cfg = _cfg(args)
    import shutil
    src_roles = Path(args.roles_src)
    src_projects = Path(args.projects_src)
    copied = 0
    if src_roles.exists():
        for d in sorted(src_roles.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            dst = cfg.roles_dir / d.name
            if dst.exists():
                continue
            shutil.copytree(d, dst, ignore=shutil.ignore_patterns("*.aux", "*.log", "*.out", "*.synctex.gz", "*.fls", "*.fdb_latexmk"))
            copied += 1
    if src_projects.exists():
        for d in sorted(src_projects.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            dst = cfg.projects_dir / d.name
            if dst.exists():
                continue
            shutil.copytree(d, dst, ignore=shutil.ignore_patterns("build", "out", "venv", ".venv", "__pycache__", ".pytest_cache", ".ipynb_checkpoints"))
            copied += 1
    print(f"seeded {copied} folder(s); roles={sum(1 for p in cfg.roles_dir.iterdir() if p.is_dir())} projects={sum(1 for p in cfg.projects_dir.iterdir() if p.is_dir())}")
    return 0


def cmd_screen_all(args) -> int:
    cfg = _cfg(args)
    from .screen import screen_metadata
    from .statemachine import transition
    from .util import today
    conn = db.thread_conn(cfg.db_path)
    n_out = 0
    for job in db.jobs_in_states(conn, [JobState.NEW]):
        res = screen_metadata(job, cfg, today())
        if res.verdict == "screen_out":
            transition(conn, job.id, JobState.SCREENED_OUT, "screened", res.to_dict(), extra_fields={"screen_reason": res.reason})
            n_out += 1
    print(f"screened out {n_out} job(s)")
    return 0


def cmd_backfill_experience(args) -> int:
    """Write experience_descriptions_<slug>.md for finished role folders that predate the deliverable."""
    cfg = _cfg(args)
    from .stages.experience import backfill, check, experience_file
    from .roles import role_slug
    from .worker import stage_log_path
    conn = db.connect(cfg.db_path)
    db.init_schema(conn)
    if args.all_done:
        rows = conn.execute("SELECT id FROM jobs WHERE state='done' AND role_folder IS NOT NULL ORDER BY id").fetchall()
        ids = [int(r["id"]) for r in rows]
    else:
        ids = [int(x) for x in args.job_ids]
    rc = 0
    for jid in ids:
        job = db.job_by_id(conn, jid)
        if job is None or not job.role_folder:
            print(f"{jid}: no such job or no role folder")
            rc = 1
            continue
        folder = Path(job.role_folder)
        slug = role_slug(job)
        if (folder / experience_file(slug)).exists() and not args.force:
            print(f"{jid} {job.company}: already has {experience_file(slug)} (use --force to rewrite)")
            continue
        attempt = db.last_attempt(conn, jid, "experience") + 1
        log_path = stage_log_path(cfg, jid, "experience", attempt)
        run_id = db.start_stage_run(conn, jid, "experience", attempt, str(log_path), worker_pid=os.getpid())
        res, failures = backfill(cfg, job, folder, log_path, budget_usd=float(args.budget), timeout_s=int(args.timeout_min * 60))
        ok = res.ok and not failures
        db.finish_stage_run(conn, run_id, "succeeded" if ok else "failed", session_id=res.session_id, cost_usd=res.cost_usd,
                            num_turns=res.num_turns, error=None if ok else (res.subtype if not res.ok else "; ".join(f.format() for f in failures[:6])))
        print(f"{jid} {job.company}: {'OK' if ok else 'FAILED'} ${res.cost_usd:.2f} {res.num_turns} turns"
              + ("" if ok else "\n  " + "\n  ".join(f.format() for f in failures[:6])))
        rc = rc or (0 if ok else 1)
    return rc


def cmd_tiers(args) -> int:
    """One-time backfill for company tiers: write jobs.priority, auto-ignore pinged jobs in tiers that no longer ping,
    and flag approved light/archetype jobs `awaiting_lane` while no archetype is approved. --dry-run writes nothing."""
    cfg = _cfg(args)
    from collections import Counter
    from .lite import library_ready
    from .rank import company_tier, ensure_scores, get_score, tier_pings, unscored
    from .statemachine import transition
    from .worker import effective_lane
    conn = db.connect(cfg.db_path)
    db.init_schema(conn)
    dry = bool(args.dry_run)
    live_states = [JobState.PINGED, JobState.APPROVED, JobState.NEEDS_JD, JobState.JD_READY, JobState.IDEAMAKER_DONE,
                   JobState.BUILT, JobState.RECONCILED, JobState.FAILED, JobState.DONE]
    jobs = [j for j in db.jobs_in_states(conn, live_states, order="id") if j.active or j.state == JobState.DONE]
    todo = unscored(conn, {j.company for j in jobs})
    if todo:
        print(f"{len(todo)} unscored companies" + (" (would score them, a few cents)" if dry else ", scoring now"))
        if not dry:
            ensure_scores(conn, cfg, todo)
    by_tier: Counter = Counter()
    n_prio = n_ignored = n_hold = 0
    ignored_tiers: Counter = Counter()
    held_lanes: Counter = Counter()
    ready = library_ready(cfg)
    for j in jobs:
        tier = company_tier(conn, cfg, j.company)
        by_tier[(j.state.value, tier)] += 1
        score = get_score(conn, j.company)
        if j.priority != score:
            n_prio += 1
            if not dry:
                db.update_job(conn, j.id, priority=score)
        if j.state == JobState.PINGED and not tier_pings(cfg, tier):
            n_ignored += 1
            ignored_tiers[tier] += 1
            if not dry:
                transition(conn, j.id, JobState.IGNORED, "ignored_tier", {"tier": tier, "score": score, "backfill": True},
                           extra_fields={"decided_at": None})
            continue
        if j.state in (JobState.APPROVED, JobState.JD_READY) and not ready:
            lane = effective_lane(conn, cfg, j)
            if lane != "full" and "awaiting_lane" not in j.flags:
                n_hold += 1
                held_lanes[lane] += 1
                if not dry:
                    db.add_flag(conn, j.id, "awaiting_lane")
                    db.add_event(conn, j.id, "awaiting_lane", detail={"lane": lane, "reason": "tiers backfill"})
    print("jobs by state and tier (active only; done includes inactive):")
    for st in sorted({s for s, _ in by_tier}):
        row = "  ".join(f"{t}={by_tier[(st, t)]}" for t in "SABCD" if by_tier[(st, t)])
        print(f"  {st:16} {row}")
    verb = "would" if dry else "did"
    print(f"priority: {verb} write {n_prio} job(s)")
    print(f"pinged -> ignored (ignored_tier): {verb} move {n_ignored} {dict(ignored_tiers)}")
    print(f"awaiting_lane hold: {verb} flag {n_hold} {dict(held_lanes)}" + (" (archetypes approved: no hold)" if ready else ""))
    return 0


def cmd_archetypes(args) -> int:
    cfg = _cfg(args)
    from . import lite
    from .index import ARCHETYPES
    names = [args.name] if args.name else list(ARCHETYPES)
    if args.action == "list":
        for name in ARCHETYPES:
            meta = lite.archetype_meta(cfg, name)
            if meta is None:
                print(f"{name:8} (missing)")
                continue
            print(f"{name:8} approved={bool(meta.get('approved'))} version={meta.get('version')} source={meta.get('source_folder')}")
        return 0
    if args.action == "candidates":
        conn = db.connect(cfg.db_path)
        db.init_schema(conn)
        for name in names:
            print(f"== {name}")
            for c in lite.seed_candidates(cfg, conn, name, k=args.k):
                print(f"  {c['score']:5.1f}  job {c['job_id']}  {c['folder']}  ({c['company']}: {c['title']}; review {c['review']})")
        return 0
    if args.action == "seed":
        if not (args.name and args.source):
            print("seed needs NAME and --from FOLDER", file=sys.stderr)
            return 2
        msg = lite.seed_archetype(cfg, args.name, Path(args.source), clean=not args.no_clean)
        print(msg)
        return 0 if msg.startswith("OK") else 1
    if args.action == "check":
        rc = 0
        for name in names:
            failures = lite.check_archetype(cfg, name)
            print(f"{name}: " + ("PASS" if not failures else "FAIL\n  " + "\n  ".join(f.format() for f in failures)))
            rc = rc or (1 if failures else 0)
        return rc
    if args.action in ("approve", "unapprove"):
        if not args.name:
            print(f"{args.action} needs NAME", file=sys.stderr)
            return 2
        if args.action == "approve":
            failures = lite.check_archetype(cfg, args.name)
            if failures:
                print("not approved, checks fail:\n  " + "\n  ".join(f.format() for f in failures))
                return 1
        lite.set_approved(cfg, args.name, args.action == "approve")
        print(f"{args.name}: {args.action}d")
        return 0
    return 2


def cmd_db_path(args) -> int:
    print(_cfg(args).db_path)
    return 0


def cmd_install_task(args) -> int:
    import subprocess
    script = Path(__file__).resolve().parents[1] / "scripts" / ("uninstall_service.ps1" if args.uninstall else "install_service.ps1")
    return subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline", description="Total Job Application Process Flow")
    ap.add_argument("--config", help="path to config.json (default: project root; env PIPELINE_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run dashboard + collector + worker")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--no-collector", action="store_true")
    s.add_argument("--no-worker", action="store_true")
    s.add_argument("--workers", type=int, default=None)
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_serve)

    c = sub.add_parser("collect", help="one collection pass")
    c.add_argument("--source", action="append")
    c.add_argument("--no-notify", action="store_true")
    c.add_argument("--no-tier2", action="store_true")
    c.set_defaults(fn=cmd_collect)

    r = sub.add_parser("run", help="run one job through the pipeline (approves it if needed)")
    r.add_argument("job", help="job id or key")
    r.add_argument("--stop-after", choices=[st.value for st in Stage])
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("verify", help="verify a role folder")
    v.add_argument("folder")
    v.add_argument("--no-compile", action="store_true")
    v.add_argument("--no-meta", action="store_true")
    v.set_defaults(fn=cmd_verify)

    ck = sub.add_parser("check", help="stage self-check: run the deterministic checks for a stage on a role folder")
    ck.add_argument("stage", choices=["resume", "ideamaker", "builder", "reconcile", "lite"])
    ck.add_argument("folder")
    ck.set_defaults(fn=cmd_check)

    j = sub.add_parser("jd", help="fetch the job description for a job")
    j.add_argument("job_id")
    j.add_argument("--no-browser", action="store_true")
    j.add_argument("--print", action="store_true")
    j.set_defaults(fn=cmd_jd)

    n = sub.add_parser("notify-test", help="show a real toast for a test job")
    n.set_defaults(fn=cmd_notify_test)

    sd = sub.add_parser("seed", help="copy the original role folders and repos into this workspace")
    sd.add_argument("--roles-src", default=r"C:\Users\you\Downloads\Resume Tailor Ideamaker")
    sd.add_argument("--projects-src", default=r"C:\Users\you\Downloads\3 Projects")
    sd.set_defaults(fn=cmd_seed)

    sa = sub.add_parser("screen-all", help="re-screen every job in state new")
    sa.set_defaults(fn=cmd_screen_all)

    be = sub.add_parser("backfill-experience", help="write experience_descriptions_<slug>.md for finished role folders")
    be.add_argument("job_ids", nargs="*")
    be.add_argument("--all-done", action="store_true", help="every job in state done without the file")
    be.add_argument("--force", action="store_true", help="rewrite even if the file exists")
    be.add_argument("--budget", type=float, default=3.0)
    be.add_argument("--timeout-min", type=float, default=15)
    be.set_defaults(fn=cmd_backfill_experience)

    ti = sub.add_parser("tiers", help="company-tier maintenance")
    ti.add_argument("action", choices=["backfill"])
    ti.add_argument("--dry-run", action="store_true")
    ti.set_defaults(fn=cmd_tiers)

    ar = sub.add_parser("archetypes", help="archetype resumes for the light/archetype lanes")
    ar.add_argument("action", choices=["list", "candidates", "seed", "check", "approve", "unapprove"])
    ar.add_argument("name", nargs="?")
    ar.add_argument("--from", dest="source", help="seed: the finished role folder to start from")
    ar.add_argument("--no-clean", action="store_true", help="seed: copy only, skip the cleanup session")
    ar.add_argument("-k", type=int, default=5, help="candidates: how many to list")
    ar.set_defaults(fn=cmd_archetypes)

    dp = sub.add_parser("db-path")
    dp.set_defaults(fn=cmd_db_path)

    it = sub.add_parser("install-task", help="register the at-logon scheduled task")
    it.add_argument("--uninstall", action="store_true")
    it.set_defaults(fn=cmd_install_task)

    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
