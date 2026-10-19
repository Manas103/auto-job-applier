"""Windows toasts with protocol-activation buttons (the pipeline's mechanism). Toasts are the only way Smart Apply
ever gets Alex's attention; it never raises a window on its own."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .config import Config
from .log import get_logger
from .paths import SCRIPTS_DIR
from .util import no_window_flags

log = get_logger("notify")
TOAST_SCRIPT = SCRIPTS_DIR / "toast.ps1"


def dashboard_url(cfg: Config, path: str = "/") -> str:
    return f"http://{cfg.dashboard.host}:{cfg.dashboard.port}{path}"


def build_toast_xml(title: str, body: str, *, launch: str, actions: list[tuple[str, str]] = (),
                    attribution: str = "Smart Apply", sound: str = "ms-winsoundevent:Notification.Default") -> str:
    toast = ET.Element("toast", {"scenario": "reminder", "duration": "long", "activationType": "protocol", "launch": launch})
    visual = ET.SubElement(toast, "visual")
    binding = ET.SubElement(visual, "binding", {"template": "ToastGeneric"})
    t1 = ET.SubElement(binding, "text", {"hint-maxLines": "2"})
    t1.text = title
    t2 = ET.SubElement(binding, "text")
    t2.text = body
    t3 = ET.SubElement(binding, "text", {"placement": "attribution"})
    t3.text = attribution
    if actions:
        acts = ET.SubElement(toast, "actions")
        for label, url in actions[:5]:
            ET.SubElement(acts, "action", {"content": label, "activationType": "protocol", "arguments": url})
    ET.SubElement(toast, "audio", {"src": sound})
    return ET.tostring(toast, encoding="unicode")


def in_quiet_hours(cfg: Config, now: datetime | None = None) -> bool:
    qh = cfg.notify.get("quiet_hours")
    if not qh:
        return False
    start, end = str(qh.get("start", "")), str(qh.get("end", ""))
    if not start or not end:
        return False
    hm = (now or datetime.now()).strftime("%H:%M")
    if start <= end:
        return start <= hm < end
    return hm >= start or hm < end


def toast(cfg: Config, title: str, body: str, *, actions: list[tuple[str, str]] = (), tag: str = "smartapply",
          launch: str | None = None, respect_quiet_hours: bool = True) -> bool:
    if not bool(cfg.notify.get("enabled", True)):
        return False
    if respect_quiet_hours and in_quiet_hours(cfg):
        log.info("quiet hours: toast suppressed (%s)", title)
        return False
    cfg.toasts_dir.mkdir(parents=True, exist_ok=True)
    xml_path = cfg.toasts_dir / f"{tag}.xml"
    xml_path.write_text(build_toast_xml(title, body, launch=launch or dashboard_url(cfg), actions=actions), encoding="utf-8")
    argv = [str(cfg.notify.powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(TOAST_SCRIPT), "-XmlPath", str(xml_path), "-AppId", str(cfg.notify.app_id),
            "-Tag", tag, "-Group", "smartapply"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, creationflags=no_window_flags())
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("toast error: %s", e)
        return False
    if proc.returncode != 0:
        log.warning("toast failed rc=%s: %s", proc.returncode, (proc.stderr or "")[:400])
        return False
    return True


def batch_ready_toast(cfg: Config, ready: int, needs_human: int) -> bool:
    body = f"{ready} application(s) filled and waiting for review"
    if needs_human:
        body += f"; {needs_human} need your input"
    return toast(cfg, "Smart Apply: batch ready", body, tag="batch",
                 actions=[("Open review window", dashboard_url(cfg, "/open-review")),
                          ("Show list", dashboard_url(cfg, "/review"))])
