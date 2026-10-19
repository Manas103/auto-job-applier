"""Logging setup: rotating file under data/logs plus console; per-stage log paths."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .config import Config

_configured = False


def setup_logging(cfg: Config, *, console: bool = True) -> logging.Logger:
    global _configured
    root = logging.getLogger("pipeline")
    if _configured:
        return root
    root.setLevel(getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(cfg.logs_dir / "pipeline.log", maxBytes=5_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)
    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"pipeline.{name}")


def stage_log_path(cfg: Config, job_id: int, stage: str, attempt: int) -> Path:
    d = cfg.logs_dir / "jobs" / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{stage}_{attempt}.log"
