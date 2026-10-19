"""Windows toast notifications with Yes/No protocol-activation buttons, plus an optional ntfy channel."""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from . import db
from .config import Config
from .models import Job
from .paths import PROJECT_ROOT
from .util import new_token, now_iso

log = logging.getLogger("pipeline.notify")
TOAST_SCRIPT = PROJECT_ROOT / "pipeline" / "scripts" / "toast.ps1"


def decide_url(cfg: Config, job_id: int, token: str, yes: bool) -> str:
    base = str(cfg.dashboard.public_base_url).rstrip("/")
    q = urllib.parse.urlencode({"id": job_id, "token": token, "yes": 1 if yes else 0})
    return f"{base}/decide?{q}"


def job_url(cfg: Config, job_id: int) -> str:
    return f"{str(cfg.dashboard.public_base_url).rstrip('/')}/job/{job_id}"


def _line2(job: Job) -> str:
    parts = [job.location_str or "location n/a", job.category or "Other"]
    if job.pay:
        parts.append(job.pay)
    if job.sponsorship:
        parts.append(job.sponsorship)
    return " | ".join(parts)


def build_toast_xml(job: Job, cfg: Config) -> str:
    token = job.decide_token or ""
    toast = ET.Element("toast", {"scenario": "reminder", "duration": "long", "activationType": "protocol",
                                 "launch": job_url(cfg, job.id)})
    visual = ET.SubElement(toast, "visual")
    binding = ET.SubElement(visual, "binding", {"template": "ToastGeneric"})
    t1 = ET.SubElement(binding, "text", {"hint-maxLines": "2"})
    t1.text = f"{job.company}: {job.title}"
    t2 = ET.SubElement(binding, "text")
    t2.text = _line2(job)
    t3 = ET.SubElement(binding, "text", {"placement": "attribution"})
    flags = [f for f in job.flags if f not in ("ping_deferred", "seed")]
    attribution = ", ".join(job.sources)
    if flags:
        attribution += " | flags: " + ", ".join(flags)
    t3.text = attribution
    actions = ET.SubElement(toast, "actions")
    ET.SubElement(actions, "action", {"content": "Yes, apply", "activationType": "protocol",
                                      "arguments": decide_url(cfg, job.id, token, True)})
    ET.SubElement(actions, "action", {"content": "No", "activationType": "protocol",
                                      "arguments": decide_url(cfg, job.id, token, False)})
    ET.SubElement(toast, "audio", {"src": "ms-winsoundevent:Notification.Default"})
    return ET.tostring(toast, encoding="unicode")


def write_toast_xml(job: Job, cfg: Config) -> Path:
    cfg.toasts_dir.mkdir(parents=True, exist_ok=True)
    p = cfg.toasts_dir / f"{job.id}.xml"
    p.write_text(build_toast_xml(job, cfg), encoding="utf-8")
    return p


def toast_argv(cfg: Config, xml_path: Path, tag: str, group: str = "jobpipe") -> list[str]:
    return [str(cfg.notify.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(TOAST_SCRIPT), "-XmlPath", str(xml_path), "-AppId", str(cfg.notify.app_id),
            "-Tag", tag, "-Group", group]


def show_toast(cfg: Config, xml_path: Path, tag: str, group: str = "jobpipe") -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(toast_argv(cfg, xml_path, tag, group), capture_output=True, text=True, timeout=30,
                          creationflags=flags)


def in_quiet_hours(cfg: Config, now: datetime | None = None) -> bool:
    qh = cfg.notify.get("quiet_hours")
    if not qh:
        return False
    try:
        start = qh["start"] if not hasattr(qh, "start") else qh.start
        end = qh["end"] if not hasattr(qh, "end") else qh.end
    except (KeyError, AttributeError, TypeError):
        return False
    now = now or datetime.now()
    hm = now.strftime("%H:%M")
    if start <= end:
        return start <= hm < end
    return hm >= start or hm < end


def _ntfy(cfg: Config, job: Job, token: str) -> None:
    topic = cfg.notify.get("ntfy_topic")
    if not topic:
        return
    try:
        import urllib.request
        yes, no = decide_url(cfg, job.id, token, True), decide_url(cfg, job.id, token, False)
        body = f"{job.company}: {job.title}\n{_line2(job)}".encode("utf-8")
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=body, headers={
            "Title": "New internship listing", "Actions": f"view, Yes, {yes}; view, No, {no}"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001
        log.warning("ntfy push failed: %s", e)


def notify_job(conn: sqlite3.Connection, cfg: Config, job: Job) -> bool:
    """Ensure a decide token, write the toast XML, show it. Returns True when the toast was shown."""
    token = job.decide_token or new_token()
    if not job.decide_token:
        db.update_job(conn, job.id, decide_token=token)
        job.decide_token = token
    if not bool(cfg.notify.enabled):
        db.update_job(conn, job.id, pinged_at=now_iso())
        db.add_event(conn, job.id, "pinged", detail={"toast": "disabled"})
        return False
    xml_path = write_toast_xml(job, cfg)
    ok = False
    try:
        proc = show_toast(cfg, xml_path, tag=f"job{job.id}")
        ok = proc.returncode == 0
        if not ok:
            log.warning("toast failed for job %s: rc=%s stderr=%s", job.id, proc.returncode, (proc.stderr or "")[:400])
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("toast error for job %s: %s", job.id, e)
    db.update_job(conn, job.id, pinged_at=now_iso())
    if not ok:
        db.add_flag(conn, job.id, "toast_failed")
    db.add_event(conn, job.id, "pinged", detail={"toast": ok})
    _ntfy(cfg, job, token)
    return ok
