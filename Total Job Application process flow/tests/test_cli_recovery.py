"""CLI commands and the subprocess crash-and-resume test against a real `serve` process."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

from pipeline import db as dbmod
from pipeline.models import JobState, Listing

ROOT = Path(__file__).resolve().parents[1]
GOOD = Path(__file__).parent / "fixtures" / "tex" / "good"


def _run(args, cfg, env=None, timeout=120):
    e = dict(os.environ)
    e["PIPELINE_CONFIG"] = str(cfg.config_path)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, "-m", "pipeline", *args], cwd=str(ROOT), capture_output=True, text=True, env=e, timeout=timeout)


def test_verify_cli_exit_codes(cfg, fixtures_dir):
    r = _run(["verify", str(fixtures_dir / "tex" / "good"), "--no-compile"], cfg)
    assert r.returncode == 0 and r.stdout.startswith("OK")
    r = _run(["verify", str(fixtures_dir / "tex" / "bad"), "--no-compile"], cfg)
    assert r.returncode == 1 and "[missing_file]" in r.stdout and "[em_dash_unicode]" in r.stdout


def test_db_path_and_seed(cfg, tmp_path):
    r = _run(["db-path"], cfg)
    assert r.returncode == 0 and r.stdout.strip().endswith("pipeline.sqlite3")
    src_roles = tmp_path / "src_roles"
    (src_roles / "Role_A").mkdir(parents=True)
    (src_roles / "Role_A" / "x.tex").write_text("x", encoding="utf-8")
    (src_roles / "Role_A" / "x.aux").write_text("junk", encoding="utf-8")
    src_proj = tmp_path / "src_proj"
    (src_proj / "repo-a" / "build").mkdir(parents=True)
    (src_proj / "repo-a" / "README.md").write_text("# a", encoding="utf-8")
    (src_proj / "repo-a" / "build" / "out.o").write_text("bin", encoding="utf-8")
    r = _run(["seed", "--roles-src", str(src_roles), "--projects-src", str(src_proj)], cfg)
    assert r.returncode == 0 and "seeded 2" in r.stdout
    assert (cfg.roles_dir / "Role_A" / "x.tex").exists() and not (cfg.roles_dir / "Role_A" / "x.aux").exists()
    assert (cfg.projects_dir / "repo-a" / "README.md").exists() and not (cfg.projects_dir / "repo-a" / "build").exists()
    r = _run(["seed", "--roles-src", str(src_roles), "--projects-src", str(src_proj)], cfg)
    assert "seeded 0" in r.stdout


def test_collect_cli_offline(cfg, conn):
    # All sources are example.invalid; the collector must report failure without crashing.
    r = _run(["collect", "--no-notify", "--no-tier2"], cfg, timeout=180)
    assert r.returncode == 1 and "errors=" in r.stdout


def test_run_cli_unknown_job(cfg, conn):
    r = _run(["run", "999"], cfg)
    assert r.returncode == 2


def _post(port, path, data):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("POST", path, body=urllib.parse.urlencode(data, doseq=True), headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    c.close()
    return r.status


def _get_json(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path)
    r = c.getresponse()
    data = json.loads(r.read().decode("utf-8"))
    c.close()
    return data


def _start_serve(cfg, env):
    e = dict(os.environ)
    e["PIPELINE_CONFIG"] = str(cfg.config_path)
    e.update(env)
    proc = subprocess.Popen([sys.executable, "-m", "pipeline", "serve", "--port", "0", "--no-collector", "--workers", "1"],
                            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=e)
    port = None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("LISTENING"):
            port = int(line.strip().rsplit(":", 1)[1])
            break
    assert port, "serve did not report a port"
    return proc, port


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@pytest.mark.slow
def test_crash_mid_stage_then_resume(cfg, conn):
    """Kill the serve process while the fake claude is mid-run; on restart the job is recovered and resumed."""
    from pipeline.roles import ensure_role_folder
    jid = dbmod.insert_job(conn, Listing(source="s", company="Acme Inc.", title="Software Engineer Intern",
                                         url="https://job-boards.greenhouse.io/acme/jobs/4001", locations=["Chicago, IL"],
                                         category="Software", terms=["Summer 2027"]), "gh:4001", "f")
    dbmod.update_job(conn, jid, jd_text="Responsibilities: x. Qualifications: y. " * 10)
    folder = ensure_role_folder(dbmod.job_by_id(conn, jid), cfg)
    dbmod.update_job(conn, jid, role_folder=str(folder), state=JobState.JD_READY)
    token = dbmod.get_setting(conn, "csrf_token")
    script = cfg.data_dir / "script.json"
    script.write_text(json.dumps({"calls": [{"sleep_s": 600, "result": {"subtype": "success"}}]}), encoding="utf-8")
    logp = cfg.data_dir / "calls.jsonl"
    env = {"FAKE_CLAUDE_SCRIPT": str(script), "FAKE_CLAUDE_LOG": str(logp)}
    conn.close()

    proc, port = _start_serve(cfg, env)
    try:
        assert _post(port, "/backlog/run", {"csrf": token, "ids": [str(jid)], "stop_after": "ideamaker"}) == 303
        c2 = dbmod.connect(cfg.db_path)
        child_pid = None
        deadline = time.time() + 30
        while time.time() < deadline:
            runs = dbmod.running_stage_runs(c2)
            if runs and runs[0].pid:
                child_pid = runs[0].pid
                break
            time.sleep(0.3)
        assert child_pid, "stage never started"
        status = _get_json(port, "/api/status")
        has_job_object = bool(status.get("job_object"))
        c2.close()
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=30)
    time.sleep(1.5)
    if has_job_object:
        assert not _pid_alive(child_pid), "fake claude child survived the service crash"
    elif _pid_alive(child_pid):
        subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"], capture_output=True)

    # Restart with a succeeding script: recovery must roll the job back, resume the session, and finish.
    script.write_text(json.dumps({"calls": [{"match": {"resume": "*"}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 0.3}},
                                            {"match": {"resume": None}, "artifacts": str(GOOD), "result": {"subtype": "success", "cost": 0.3}}]}), encoding="utf-8")
    st = Path(str(script) + ".state")
    if st.exists():
        st.unlink()
    proc, port = _start_serve(cfg, env)
    try:
        c3 = dbmod.connect(cfg.db_path)
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            job = dbmod.job_by_id(c3, jid)
            if job.state in (JobState.IDEAMAKER_DONE, JobState.FAILED, JobState.DONE, JobState.BUILDING):
                final = job.state
                break
            time.sleep(0.5)
        runs = dbmod.stage_runs_for_job(c3, jid)
        events = [e["event"] for e in dbmod.events_for_job(c3, jid)]
        c3.close()
    finally:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()
        proc.wait(timeout=30)
    assert final in (JobState.IDEAMAKER_DONE, JobState.BUILDING, JobState.DONE), (final, [(r.status, r.error) for r in runs])
    assert "recovered" in events
    assert [r.status for r in runs][:2] == ["aborted", "succeeded"]
    calls = [json.loads(l) for l in logp.read_text(encoding="utf-8").splitlines()]
    assert calls[0]["resume"] is None and calls[1]["resume"] == "fake-1", [c["resume"] for c in calls]
