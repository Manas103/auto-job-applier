"""Logging: a rotating file under data/logs plus stderr when a console exists."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_configured = False


def setup_logging(logs_dir: Path, level: str = "INFO", *, quiet: bool = False) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = logging.handlers.RotatingFileHandler(logs_dir / "smartapply.log", maxBytes=4_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if not quiet and sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"smartapply.{name}")


def app_log_path(logs_dir: Path, app_id: int, purpose: str) -> Path:
    p = logs_dir / "apps" / str(app_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{purpose}.log"
