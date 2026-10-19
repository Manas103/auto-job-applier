"""Fixed locations. Everything else comes from config.json."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = PACKAGE_DIR / "migrations"
SCRIPTS_DIR = PACKAGE_DIR / "scripts"
PLAYBOOKS_DIR = PROJECT_ROOT / "playbooks"


def local_app_dir() -> Path:
    """%LOCALAPPDATA%\\SmartApply: the Chrome profile and the agent cwd live here, outside the repo."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SmartApply"


def expand(p: str | Path) -> Path:
    """Expand %VAR% and ~ in a configured path."""
    return Path(os.path.expandvars(os.path.expanduser(str(p))))
