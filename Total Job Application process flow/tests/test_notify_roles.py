"""Toast XML, PowerShell argv, quiet hours, notify_job side effects; role folder naming."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

from pipeline import db as dbmod, notify, roles
from pipeline.models import Listing
from pipeline.urlnorm import canonical_key


def _job(conn, **kw):
    base = dict(source="simplify", company="Akuna Capital", title="Software Engineer Intern, C++",
                url="https://akunacapital.com/careers/job/8018847?gh_jid=8018847", locations=["Chicago, IL"],
                category="Software", pay="$75/hr", terms=["Summer 2027"])
    base.update(kw)
    jid = dbmod.insert_job(conn, Listing(**base), canonical_key(base["url"]), "f")
    dbmod.update_job(conn, jid, flags=["jd_mentions_citizenship_or_clearance"], decide_token="abc123")
    return dbmod.job_by_id(conn, jid)


def test_toast_xml(cfg, conn):
    job = _job(conn)
    xml = notify.build_toast_xml(job, cfg)
    root = ET.fromstring(xml)
    assert root.tag == "toast" and root.get("scenario") == "reminder"
    actions = root.find("actions").findall("action")
    assert len(actions) == 2
    yes, no = actions
    assert yes.get("activationType") == "protocol"
    assert yes.get("arguments") == f"http://127.0.0.1:8765/decide?id={job.id}&token=abc123&yes=1"
    assert no.get("arguments").endswith("yes=0")
    assert "&amp;" in xml and "&token" not in xml.replace("&amp;token", "")
    texts = [t.text for t in root.find("visual").find("binding").findall("text")]
    assert texts[0] == "Akuna Capital: Software Engineer Intern, C++"
    assert "Chicago, IL | Software | $75/hr" == texts[1]
    assert "flags: jd_mentions_citizenship_or_clearance" in texts[2]


def test_toast_argv_and_show(cfg, conn, monkeypatch, tmp_path):
    job = _job(conn)
    p = notify.write_toast_xml(job, cfg)
    assert p.exists() and p.read_bytes()[:1] == b"<"
    argv = notify.toast_argv(cfg, p, "job1")
    assert argv[0] == str(cfg.notify.powershell) and "-File" in argv and argv[argv.index("-XmlPath") + 1] == str(p)
    assert argv[argv.index("-AppId") + 1].endswith("powershell.exe")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg.notify.enabled = True
    assert notify.notify_job(conn, cfg, job) is True
    assert seen["cmd"][0] == str(cfg.notify.powershell)
    j = dbmod.job_by_id(conn, job.id)
    assert j.pinged_at and "toast_failed" not in j.flags
    assert dbmod.events_for_job(conn, job.id)[-1]["event"] == "pinged"


def test_notify_failure_flags_job(cfg, conn, monkeypatch):
    job = _job(conn)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "bad"))
    cfg.notify.enabled = True
    assert notify.notify_job(conn, cfg, job) is False
    assert "toast_failed" in dbmod.job_by_id(conn, job.id).flags


def test_notify_disabled_sets_token_and_pinged(cfg, conn):
    job = _job(conn)
    dbmod.update_job(conn, job.id, decide_token=None)
    job = dbmod.job_by_id(conn, job.id)
    cfg.notify.enabled = False
    assert notify.notify_job(conn, cfg, job) is False
    j = dbmod.job_by_id(conn, job.id)
    assert j.decide_token and len(j.decide_token) == 32 and j.pinged_at


def test_quiet_hours(cfg):
    cfg.notify.quiet_hours = {"start": "23:00", "end": "08:00"}
    assert notify.in_quiet_hours(cfg, datetime(2026, 8, 22, 23, 30))
    assert notify.in_quiet_hours(cfg, datetime(2026, 8, 22, 3, 0))
    assert not notify.in_quiet_hours(cfg, datetime(2026, 8, 22, 12, 0))
    cfg.notify.quiet_hours = {"start": "12:00", "end": "13:00"}
    assert notify.in_quiet_hours(cfg, datetime(2026, 8, 22, 12, 30))
    cfg.notify.quiet_hours = None
    assert not notify.in_quiet_hours(cfg)


@pytest.mark.real_toast
@pytest.mark.skipif(sys.platform != "win32", reason="windows only")
def test_real_toast_xml_loads(cfg, conn):
    job = _job(conn)
    p = notify.write_toast_xml(job, cfg)
    ps = Path(cfg.notify.powershell)
    if not ps.exists():
        pytest.skip("powershell missing")
    script = ("[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null; "
              "$d = New-Object Windows.Data.Xml.Dom.XmlDocument; $d.LoadXml([IO.File]::ReadAllText('" + str(p).replace("'", "''") + "')); "
              "Write-Output $d.DocumentElement.TagName")
    out = subprocess.run([str(ps), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "toast"


# ----------------------------------------------------------------------------- roles

def test_role_folder_naming(cfg, conn):
    job = _job(conn)
    assert roles.role_slug(job) == "akuna"
    assert roles.role_folder_name(job, cfg) == "Akuna_Capital_Software_Engineer_Intern_C_Summer2027"
    job2 = _job(conn, company="DRW", title="Software Developer Intern - Summer 2027 (Chicago)", url="https://drw.com/1", terms=[])
    assert roles.role_folder_name(job2, cfg) == "DRW_Software_Developer_Intern_Chicago_Summer2027"
    job3 = _job(conn, company="GE Appliances", title="GE Appliances Software Engineering Co-op", url="https://ge.com/1", terms=["Fall 2027"])
    assert roles.role_folder_name(job3, cfg) == "GE_Appliances_Software_Engineering_Coop_Fall2027"


def test_ensure_role_folder_collision_and_contents(cfg, conn):
    a = _job(conn)
    b = _job(conn, url="https://akunacapital.com/careers/job/999?gh_jid=999")
    dbmod.update_job(conn, a.id, jd_text="JD A")
    a = dbmod.job_by_id(conn, a.id)
    fa = roles.ensure_role_folder(a, cfg)
    fb = roles.ensure_role_folder(b, cfg)
    assert fa.name == "Akuna_Capital_Software_Engineer_Intern_C_Summer2027" and fb.name == fa.name + "_2"
    assert (fa / "jd.txt").read_text(encoding="utf-8") == "JD A"
    meta = json.loads((fa / "job.json").read_text(encoding="utf-8"))
    assert meta["id"] == a.id and meta["slug"] == "akuna" and "decide_token" not in meta
    dbmod.update_job(conn, a.id, role_folder=str(fa))
    assert roles.ensure_role_folder(dbmod.job_by_id(conn, a.id), cfg) == fa
