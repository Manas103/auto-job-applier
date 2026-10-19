"""CLI: python -m smartapply <command>.

Under pythonw (the scheduled task) there is no stdout, so both streams are pointed at a log first, exactly as
Archon does; otherwise a bare print would kill the daemon silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "logs"


def _redirect_to_log() -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        f = _LOG_DIR / "pythonw.log"
        if f.exists() and f.stat().st_size > 2_000_000:
            f.replace(f.with_suffix(".log.1"))
        stream = f.open("a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except OSError:
        import io
        sys.stdout = sys.stderr = io.StringIO()


if sys.stdout is None or sys.stderr is None:
    _redirect_to_log()

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

from . import db, intake, tracker  # noqa: E402
from .config import Config, load_config  # noqa: E402
from .log import get_logger, setup_logging  # noqa: E402
from .models import Application, AppState  # noqa: E402
from .paths import PROJECT_ROOT  # noqa: E402
from .profile import Profile, load_profile, save_profile, seed_from_assets  # noqa: E402
from .util import no_window_flags  # noqa: E402

log = get_logger("cli")


def _cfg(args) -> Config:
    path = Path(args.config) if getattr(args, "config", None) else None
    cfg = load_config(path)
    setup_logging(cfg.logs_dir, str(cfg.log_level), quiet=bool(getattr(args, "quiet", False)))
    return cfg


def _profile(cfg: Config, *, required: bool = True) -> Profile | None:
    if cfg.profile_path.exists():
        return load_profile(cfg.profile_path)
    if required:
        print(f"profile not found at {cfg.profile_path}; run `python -m smartapply setup` first")
        sys.exit(2)
    return None


# ---------------------------------------------------------------- commands

def cmd_setup(args) -> int:
    cfg = _cfg(args)
    assets = cfg.pipeline_root_path / "playbooks" / "ASSETS.md"
    if cfg.profile_path.exists() and not args.reset:
        profile = load_profile(cfg.profile_path)
        print(f"loaded existing profile {cfg.profile_path}")
    else:
        profile = seed_from_assets(assets if assets.exists() else None)
        print(f"seeded profile from {assets if assets.exists() else 'built-in defaults'}")
    missing = profile.missing()
    if args.non_interactive or not missing:
        save_profile(profile, cfg.profile_path)
        print(f"saved {cfg.profile_path}; missing: {missing or 'nothing'}")
        return 0
    print("A few facts ASSETS.md does not carry. Press Enter to keep the current value.")

    def ask(label: str, cur: str) -> str:
        v = input(f"  {label} [{cur}]: ").strip()
        return v or cur

    a = profile.address
    if "address" in missing:
        a.street = ask("Street address", a.street)
        a.street2 = ask("Apt / line 2", a.street2)
        a.city = ask("City", a.city)
        a.state = ask("State (2 letters)", a.state).upper()
        a.state_name = ask("State (full name)", a.state_name)
        a.zip = ask("ZIP", a.zip)
    profile.preferred_name = ask("Preferred first name (blank = same)", profile.preferred_name)
    profile.portfolio = ask("Portfolio / website URL (blank = none)", profile.portfolio)
    if any(m.startswith("eeo") for m in missing):
        print("Voluntary self-identification. Any answer including 'Decline to self-identify' is fine; the filler never guesses these.")
        e = profile.eeo
        e.gender = ask("Gender (Male / Female / Non-binary / Decline to self-identify)", e.gender)
        e.hispanic_latino = ask("Hispanic or Latino? (Yes / No / Decline to self-identify)", e.hispanic_latino)
        e.ethnicity = ask("Race/ethnicity (e.g. Asian / Decline to self-identify)", e.ethnicity)
        e.veteran = ask("Veteran status (I am not a protected veteran / Decline to self-identify)", e.veteran)
        e.disability = ask("Disability (No, I do not have a disability / Yes / I do not want to answer)", e.disability)
        e.signature_name = ask("Typed name for the CC-305 disability form signature", e.signature_name or profile.full_name)
    d = profile.defaults
    d.availability = ask("Availability sentence", d.availability)
    d.how_heard = ask("'How did you hear about us' default category", d.how_heard)
    d.how_heard_detail = ask("'How did you hear' detail", d.how_heard_detail)
    save_profile(profile, cfg.profile_path)
    print(f"saved {cfg.profile_path}")
    return 0


def doctor_checks(cfg: Config) -> list[tuple[str, bool, str]]:
    from .browser.chrome import Chrome
    from .claude_runner import claude_available, claude_version
    checks: list[tuple[str, bool, str]] = []
    checks.append(("pipeline DB", cfg.pipeline_db_path.exists(), str(cfg.pipeline_db_path)))
    try:
        with intake.open_pipeline_db(cfg) as p:
            n = len(intake.candidate_jobs(p))
            tok = intake.pipeline_csrf_token(p)
        checks.append(("pipeline candidates", True, f"{n} done jobs not yet applied; csrf {'ok' if tok else 'MISSING'}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("pipeline candidates", False, str(e)))
    env = cfg.archon_env_path
    ok_env = env.exists()
    gmail = False
    if ok_env:
        txt = env.read_text(encoding="utf-8", errors="replace")
        gmail = "GMAIL_ADDRESS=" in txt and "GMAIL_APP_PASSWORD=" in txt
    checks.append(("Archon .env (Gmail IMAP)", ok_env and gmail, str(env)))
    if ok_env and gmail and getattr(cfg, "doctor_mail_login", True):
        try:
            from . import mail as _mail
            n = len(_mail.fetch_recent(cfg, days=1, limit=1, want=lambda f, s_: False))
            checks.append(("Gmail IMAP login (read-only)", True, f"ok, {n} recent message(s) listed"))
        except Exception as e:  # noqa: BLE001
            checks.append(("Gmail IMAP login (read-only)", False, str(e)[:120]))
    checks.append(("profile.yaml", cfg.profile_path.exists(), str(cfg.profile_path)))
    if cfg.profile_path.exists():
        try:
            miss = load_profile(cfg.profile_path).missing()
            checks.append(("profile complete", not miss, ", ".join(miss) or "all fields present"))
        except Exception as e:  # noqa: BLE001
            checks.append(("profile complete", False, str(e)))
    chrome = Chrome(cfg)
    checks.append(("chrome.exe", chrome.exe.exists(), str(chrome.exe)))
    port = chrome.devtools_port()
    checks.append(("SmartApply Chrome running", port is not None, f"port {port}" if port else f"profile {chrome.profile_dir}"))
    checks.append(("claude CLI", claude_available(cfg), claude_version(cfg) if claude_available(cfg) else str(cfg.claude.cmd)))
    try:
        import playwright  # noqa: F401
        checks.append(("playwright", True, "importable"))
    except Exception as e:  # noqa: BLE001
        checks.append(("playwright", False, str(e)))
    try:
        import keyring
        checks.append(("keyring backend", True, str(keyring.get_keyring())))
    except Exception as e:  # noqa: BLE001
        checks.append(("keyring backend", False, str(e)))
    checks.append(("data dir", True, str(cfg.data_dir)))
    return checks


def cmd_doctor(args) -> int:
    cfg = _cfg(args)
    bad = 0
    for name, ok, detail in doctor_checks(cfg):
        print(f"[{'ok ' if ok else 'BAD'}] {name}: {detail}")
        bad += 0 if ok else 1
    if getattr(args, "agent", False):
        from .adapters.agent import self_test
        print("running the browser agent self-test (one real claude -p run, a few cents)...")
        out = self_test(cfg, keep_tab=bool(getattr(args, "keep_tab", False)))
        print(json.dumps(out, indent=2, default=str))
        print(f"[{'ok ' if out['ok'] else 'BAD'}] browser agent self-test: tools={out.get('tools')} "
              f"submit_refused={out.get('submit_refused')} filled={out.get('filled_ok')} cost=${out.get('cost_usd', 0):.2f}")
        bad += 0 if out["ok"] else 1
    return 1 if bad else 0


def cmd_queue(args) -> int:
    cfg = _cfg(args)
    profile = _profile(cfg)
    conn = db.connect(cfg.db_path)
    if args.add:
        with intake.open_pipeline_db(cfg) as pconn:
            for jid in args.add:
                job = intake.pipeline_job(pconn, int(jid))
                if job is None:
                    print(f"pipeline job {jid} not found")
                    continue
                try:
                    app = intake.enqueue_job(conn, cfg, job, profile_resume_name=profile.resume_filename)
                    print(f"queued app {app.id}: {app.company} ({app.ats}) -> {app.workdir}")
                except ValueError as e:
                    print(f"skip: {e}")
        return 0
    if args.candidates:
        queued = {r["job_id"] for r in db.all_applications(conn)}
        with intake.open_pipeline_db(cfg) as pconn:
            rows = [r for r in intake.candidate_jobs(pconn) if int(r["id"]) not in queued]
        for r in rows[: args.limit]:
            print(f"{r['id']:>6}  {r['ats'] or '?':<12} {r['company'][:28]:<28} {r['title'][:60]}")
        print(f"{len(rows)} candidates")
        return 0
    for r in db.all_applications(conn):
        a = Application.from_row(r)
        print(f"{a.id:>4} job {a.job_id:>6} {a.state.value:<17} {a.ats or '?':<12} {a.company[:28]:<28} {a.title[:50]} {a.reason or ''}")
    return 0


def cmd_fill(args) -> int:
    cfg = _cfg(args)
    profile = _profile(cfg)
    conn = db.connect(cfg.db_path)
    row = db.application_by_job(conn, int(args.job_id))
    if row is None:
        with intake.open_pipeline_db(cfg) as pconn:
            job = intake.pipeline_job(pconn, int(args.job_id))
        if job is None:
            print(f"pipeline job {args.job_id} not found")
            return 2
        app = intake.enqueue_job(conn, cfg, job, profile_resume_name=profile.resume_filename)
    else:
        app = Application.from_row(row)
    if app.state == AppState.SELF_APPLY:
        print(f"job {app.job_id} ({app.company}) is marked do-it-yourself; `smartapply self --undo {app.job_id}` to fill it again")
        return 2
    if args.dry_run:
        from .filler import dry_run
        rep = dry_run(cfg, conn, app, profile, keep_tab=not args.close_tab)
        print(json.dumps(rep.to_dict(), indent=2))
        print("DRY RUN " + ("PASSED" if rep.ok else "FAILED"))
        return 0 if rep.ok else 1
    from .browser.chrome import Chrome
    from .filler import fill_application
    report = fill_application(cfg, conn, app, profile, Chrome(cfg), use_claude=not args.no_claude,
                              redo_answers=args.redo_answers)
    print(json.dumps(report.to_dict(), indent=2))
    print(f"RESULT: {report.status}" + (f" ({report.reason})" if report.reason else ""))
    return 0 if report.status == "ready_for_review" else 1


def cmd_batch(args) -> int:
    """Fill the next N pipeline jobs that have an adapter, one after another, then toast once."""
    cfg = _cfg(args)
    profile = _profile(cfg)
    conn = db.connect(cfg.db_path)
    from .batch import pick_candidates, run_batch, summary_lines, to_dicts
    n = int(args.n or cfg.batch.size)
    ids = [int(x) for x in (args.ids or [])]
    if args.list:
        rows = pick_candidates(cfg, conn, n=n, ats_filter=args.ats or None, job_ids=ids or None,
                               include_agent=False if args.no_agent else None)
        for r in rows:
            print(f"{r['id']:>6}  {(r['ats'] or intake.detect_ats(r['url'])[0] or '?'):<11} {r['company'][:28]:<28} {r['title'][:60]}")
        print(f"{len(rows)} job(s) would be filled")
        return 0
    from .browser.chrome import Chrome
    items = run_batch(cfg, conn, profile, Chrome(cfg), n=n, ats_filter=args.ats or None, job_ids=ids or None,
                      use_claude=not args.no_claude, notify=not args.no_toast,
                      include_agent=False if args.no_agent else None)
    for line in summary_lines(items):
        print(line)
    if args.json:
        print(json.dumps(to_dicts(items), indent=2))
    ready = sum(1 for i in items if i.status == "ready_for_review")
    nh = sum(1 for i in items if i.status == "needs_human")
    print(f"BATCH: {len(items)} filled, {ready} ready for review, {nh} need your input; run `python -m smartapply review`")
    return 0 if items else 1


def cmd_prefetch(args) -> int:
    cfg = _cfg(args)
    profile = _profile(cfg)
    conn = db.connect(cfg.db_path)
    from .filler import stage_prefetch
    row = db.application_by_job(conn, int(args.job_id))
    if row is None:
        with intake.open_pipeline_db(cfg) as pconn:
            job = intake.pipeline_job(pconn, int(args.job_id))
        if job is None:
            print("pipeline job not found")
            return 2
        app = intake.enqueue_job(conn, cfg, job, profile_resume_name=profile.resume_filename)
    else:
        app = Application.from_row(row)
    qs = stage_prefetch(cfg, conn, app)
    if qs is None:
        print("no prefetcher for", app.ats)
        return 1
    for q in qs:
        opts = f" options={q.options[:6]}" if q.options else ""
        print(f"[{'req' if q.required else 'opt'}] {q.type:<11} {q.id:<24} {q.label[:70]!r}{opts}")
    print(f"{len(qs)} questions -> {app.questions_path}")
    return 0


def cmd_draft(args) -> int:
    cfg = _cfg(args)
    profile = _profile(cfg)
    conn = db.connect(cfg.db_path)
    from .filler import load_questions, stage_draft
    row = db.application_by_job(conn, int(args.job_id))
    if row is None:
        print("queue and prefetch the job first")
        return 2
    app = Application.from_row(row)
    qs = load_questions(app)
    if not qs:
        print("no questions.json; run prefetch first")
        return 2
    if app.state not in (AppState.PREFETCHED,):
        tracker.transition(conn, app.id, AppState.PREFETCHED, "redraft")
    packet = stage_draft(cfg, conn, app, profile, qs, use_claude=not args.no_claude)
    for qid, a in packet.answers.items():
        v = a.value if a.value is None or len(str(a.value)) < 90 else str(a.value)[:87] + "..."
        print(f"{a.source:<11} {qid:<24} {v!r}" + (f"  NEEDS HUMAN: {a.needs_human}" if a.needs_human else ""))
    print(f"unanswered required: {packet.unanswered_required}; claude cost ${packet.claude_cost_usd:.3f}")
    return 0


def cmd_review(args) -> int:
    """Bring the SmartApply window on-screen and list what is waiting. Alex asked, so focus is allowed here."""
    cfg = _cfg(args)
    from playwright.sync_api import sync_playwright

    from .browser.chrome import Chrome
    conn = db.connect(cfg.db_path)
    chrome = Chrome(cfg)
    if not chrome.is_running():
        print("SmartApply Chrome is not running; nothing to review")
        return 1
    with sync_playwright() as pw:
        b = chrome.attach(pw)
        tids = chrome.unpark_all(b)
        if tids:
            page = chrome.page_for_target(b, tids[0])
            if page is not None:
                page.bring_to_front()
        b.close()
    for r in db.applications_in_states(conn, ("ready_for_review", "needs_human")):
        a = Application.from_row(r)
        print(f"{a.state.value:<17} {a.company[:28]:<28} {a.title[:45]:<45} {a.reason or ''}")
    if args.park:
        with sync_playwright() as pw:
            b = chrome.attach(pw)
            chrome.park_all(b)
            b.close()
    return 0


def cmd_applied(args) -> int:
    """Mark an application applied: in the pipeline (its dashboard route) and here."""
    cfg = _cfg(args)
    conn = db.connect(cfg.db_path)
    from .applied import mark_applied
    row = db.application_by_job(conn, int(args.job_id))
    if row is None:
        print("no application for that pipeline job here")
        return 2
    ok = mark_applied(conn, cfg, int(row["id"]), source="cli")
    print("applied recorded; pipeline dashboard " + ("updated" if ok else "NOT reachable (will retry when serve runs)"))
    return 0


def cmd_self(args) -> int:
    """Jobs Alex applies to by hand: mark them (a packet folder is written for each), list them, or undo."""
    cfg = _cfg(args)
    conn = db.connect(cfg.db_path)
    from . import selfapply
    if args.undo:
        for jid in args.job_ids:
            row = db.application_by_job(conn, int(jid))
            if row is None or row["state"] != AppState.SELF_APPLY.value:
                print(f"job {jid}: not marked do-it-yourself")
                continue
            tracker.transition(conn, int(row["id"]), AppState.QUEUED, "self_apply_undone")
            print(f"job {jid}: back in the fill queue")
        return 0
    if not args.job_ids:
        apps = selfapply.self_apply_list(conn)
        for a in apps:
            print(f"{a.job_id:>6}  {a.company[:28]:<28} {a.title[:50]:<50} {a.reason or ''}")
            print(f"        {selfapply.packet_dir(cfg, a)}")
        print(f"{len(apps)} job(s) for you to apply to yourself; packets in {selfapply.packets_dir(cfg)}")
        return 0
    profile = _profile(cfg)
    for jid in args.job_ids:
        try:
            app, folder = selfapply.mark_self_apply(cfg, conn, profile, int(jid), args.reason)
        except (ValueError, FileNotFoundError) as e:
            print(f"job {jid}: {e}")
            continue
        print(f"job {jid} ({app.company}): marked do-it-yourself -> {folder}")
    return 0


def cmd_memory(args) -> int:
    """List the confirmed answers reused across applications, or forget one."""
    cfg = _cfg(args)
    conn = db.connect(cfg.db_path)
    if args.forget:
        ok = db.forget_answer(conn, int(args.forget))
        print("forgotten" if ok else "no such memory id")
        return 0 if ok else 2
    rows = db.recall_answers(conn)
    if not rows:
        print("no remembered answers yet (they are stored when you mark an application applied)")
        return 0
    for r in rows:
        ans = str(r["answer"]).replace("\n", " ")
        print(f"{r['id']:>4}  {r['created_at'][:10]}  {r['question'][:70]}")
        print(f"      -> {ans[:160]}")
    return 0


def cmd_account(args) -> int:
    """Tenant account passwords (Windows Credential Manager). `set` stores one you already use, e.g. an account
    you created yourself before Smart Apply existed; `list` shows tenants; `delete` forgets one."""
    import getpass
    from . import vault
    cfg = _cfg(args)
    conn = db.connect(cfg.db_path)
    ats = args.ats
    if args.action == "list":
        for r in conn.execute("SELECT ats, tenant, email, created_at, verified_at, last_login_at, note FROM accounts ORDER BY ats, tenant"):
            has = "vault" if vault.get_password(r["ats"], r["tenant"]) else "NO PASSWORD"
            print(f"{r['ats']:<10} {r['tenant']:<20} {r['email']:<32} {has:<12} login {r['last_login_at'] or '-'}")
        return 0
    if args.action == "default":
        pw = args.password or getpass.getpass("password for every new account Smart Apply creates: ")
        if not pw:
            print("nothing stored")
            return 2
        vault.set_default_password(pw)
        print(f"default new-account password stored in {vault.backend_name()}")
        return 0
    if not args.tenant:
        print("tenant required (e.g. stryker for stryker.wd1.myworkdayjobs.com)")
        return 2
    if args.action == "set":
        pw = args.password or getpass.getpass(f"password for {ats}:{args.tenant} ({_profile(cfg).email}): ")
        if not pw:
            print("nothing stored")
            return 2
        vault.set_password(ats, args.tenant, pw)
        db.upsert_account(conn, ats, args.tenant, _profile(cfg).email, note="password set by hand")
        conn.commit()
        print(f"stored in {vault.backend_name()}")
        return 0
    if args.action == "delete":
        vault.delete_password(ats, args.tenant)
        conn.execute("DELETE FROM accounts WHERE ats=? AND tenant=?", (ats, args.tenant))
        conn.commit()
        print("forgotten")
        return 0
    return 2


def cmd_chrome(args) -> int:
    cfg = _cfg(args)
    from .browser.chrome import Chrome
    chrome = Chrome(cfg)
    if args.action == "start":
        port = chrome.launch()
        print(f"chrome up on port {port}; profile {chrome.profile_dir}")
    elif args.action == "status":
        port = chrome.devtools_port()
        print(f"running on port {port}" if port else "not running")
        for t in chrome.list_tabs():
            print(f"  {t['id']}  {t.get('url', '')[:100]}")
    elif args.action == "tabs":
        for t in chrome.list_tabs():
            print(f"{t['id']}  {t.get('title', '')[:40]!r}  {t.get('url', '')[:100]}")
    return 0


def cmd_serve(args) -> int:
    cfg = _cfg(args)
    profile = _profile(cfg)
    from .daemon import Runner
    from .web.server import AppState_, serve_in_thread
    lock_path = cfg.data_dir / "serve.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        import msvcrt
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        log.info("another smartapply serve holds the lock; exiting")
        return 3
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    db.connect(cfg.db_path)
    runner = Runner(cfg, profile)
    try:
        from . import filler
        runner.fill_fn = getattr(filler, "fill_application", None)
    except Exception:  # noqa: BLE001
        runner.fill_fn = None
    runner.start()
    state = AppState_(cfg, profile=profile, runner=runner)
    state.doctor = lambda: doctor_checks(cfg)
    srv = serve_in_thread(state, str(cfg.dashboard.host), int(cfg.dashboard.port))
    stop = False

    def _sig(*_a):
        nonlocal stop
        stop = True
    for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", signal.SIGINT)):
        try:
            signal.signal(s, _sig)
        except (ValueError, OSError):
            pass
    log.info("smartapply serve pid %s", os.getpid())
    while not stop:
        time.sleep(0.5)
    runner.stop.set()
    srv.shutdown()
    return 0


def cmd_install_task(args) -> int:
    script = PROJECT_ROOT / "scripts" / ("uninstall_task.ps1" if args.uninstall else "install_task.ps1")
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], text=True,
                       creationflags=no_window_flags() if args.quiet else 0)
    return p.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smartapply")
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--quiet", action="store_true", help="no stderr logging (scheduled task)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="seed profile.yaml from ASSETS.md and ask for the missing facts")
    s.add_argument("--reset", action="store_true")
    s.add_argument("--non-interactive", action="store_true")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("doctor", help="check the pipeline DB, Archon env, Chrome, claude, profile")
    s.add_argument("--agent", action="store_true", help="also run the browser agent self-test (a real claude -p run)")
    s.add_argument("--keep-tab", action="store_true", help="with --agent: leave the self-test tab open")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("queue", help="list applications, or --candidates, or --add <job ids>")
    s.add_argument("--candidates", action="store_true")
    s.add_argument("--add", nargs="*", help="pipeline job ids to queue")
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("fill", help="fill one pipeline job (queues it first if needed)")
    s.add_argument("job_id")
    s.add_argument("--dry-run", action="store_true", help="prove tab creation, upload, and all three guard layers")
    s.add_argument("--close-tab", action="store_true")
    s.add_argument("--no-claude", action="store_true", help="profile answers only; open-ended questions left to you")
    s.add_argument("--redo-answers", action="store_true")
    s.set_defaults(fn=cmd_fill)

    s = sub.add_parser("batch", help="fill the next N pipeline jobs that have an adapter, then toast once")
    s.add_argument("--n", type=int, help="how many (default: config batch.size)")
    s.add_argument("--ats", nargs="*", help="only these ATSs (workday greenhouse lever ashby, or agent for sites without an adapter)")
    s.add_argument("--ids", nargs="*", help="specific pipeline job ids instead of the next candidates")
    s.add_argument("--no-agent", action="store_true", help="skip sites that only the Claude browser agent can fill")
    s.add_argument("--list", action="store_true", help="show what would be filled and exit")
    s.add_argument("--no-claude", action="store_true")
    s.add_argument("--no-toast", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_batch)

    s = sub.add_parser("prefetch", help="read a job's questions over HTTP and print them")
    s.add_argument("job_id")
    s.set_defaults(fn=cmd_prefetch)

    s = sub.add_parser("draft", help="build answers.json for a prefetched job")
    s.add_argument("job_id")
    s.add_argument("--no-claude", action="store_true")
    s.set_defaults(fn=cmd_draft)

    s = sub.add_parser("applied", help="mark a job applied after you submitted it")
    s.add_argument("job_id")
    s.set_defaults(fn=cmd_applied)

    s = sub.add_parser("self", help="jobs you apply to by hand: mark them (writes a packet folder), list them, or --undo")
    s.add_argument("job_ids", nargs="*")
    s.add_argument("--reason", default="too hard to automate", help="why (shown in the packet and the list)")
    s.add_argument("--undo", action="store_true", help="put the jobs back in the fill queue")
    s.set_defaults(fn=cmd_self)

    s = sub.add_parser("review", help="bring the SmartApply window on-screen and list what is waiting")
    s.add_argument("--park", action="store_true", help="park the window off-screen again instead")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("memory", help="list the confirmed answers reused across applications, or --forget ID")
    s.add_argument("--forget", metavar="ID")
    s.set_defaults(fn=cmd_memory)

    s = sub.add_parser("account", help="tenant account passwords in the Windows vault: list | set TENANT | delete TENANT | "
                                       "default (the password every new account gets)")
    s.add_argument("action", choices=["list", "set", "delete", "default"])
    s.add_argument("tenant", nargs="?")
    s.add_argument("--ats", default="workday")
    s.add_argument("--password", help="(prompted when omitted)")
    s.set_defaults(fn=cmd_account)

    s = sub.add_parser("chrome", help="start | status | tabs")
    s.add_argument("action", choices=["start", "status", "tabs"])
    s.set_defaults(fn=cmd_chrome)

    s = sub.add_parser("serve", help="daemon: dashboard + fill worker")
    s.add_argument("--quiet", action="store_true", help="no stderr logging (the scheduled task passes this)")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("install-task", help="register (or --uninstall) the at-logon scheduled task")
    s.add_argument("--uninstall", action="store_true")
    s.set_defaults(fn=cmd_install_task)

    args = ap.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
