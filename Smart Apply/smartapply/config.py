"""Configuration: DEFAULTS merged with config.json. Same shape as the pipeline's config.py."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT, expand, local_app_dir

DEFAULTS: dict[str, Any] = {
    "pipeline_root": str(PROJECT_ROOT.parent / "Total Job Application process flow"),
    "pipeline_dashboard": "http://127.0.0.1:8765",
    "archon_env": "mail.env",   # optional .env with GMAIL_ADDRESS / GMAIL_APP_PASSWORD (read-only IMAP); never commit it
    "data_dir": "data",
    "profile_path": "profile.yaml",
    "dashboard": {"host": "127.0.0.1", "port": 8770},
    "chrome": {
        "exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "profile_dir": str(local_app_dir() / "ChromeProfile"),
        "max_tabs": 6,
        "connect_timeout_s": 15,
        "extra_args": [],
    },
    "claude": {
        "cmd": ["claude"],
        "answers_model": "claude-opus-5-5",
        "agent_model": "claude-sonnet-5",
        "max_budget_usd": None,
        "answers_max_turns": 40,
        "agent_max_turns": 120,
        "agent_timeout_min": 45,
        "timeout_min": 30,
        "env": {"ARCHON_AGENT": "1"},
    },
    "notify": {
        "enabled": True,
        "app_id": r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe",
        "powershell": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "quiet_hours": {"start": "23:00", "end": "08:00"},
    },
    "batch": {"size": 5, "single_page_last": True, "include_agent": True},
    "guard": {"ttl_ms": 60000, "heartbeat_s": 20},
    "mail": {"days": 2, "limit": 50, "wait_timeout_s": 300, "poll_s": 15},
    "log_level": "INFO",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Section:
    """Attribute access over a nested dict. Lists stay lists; dicts become Sections."""

    def __init__(self, data: dict[str, Any]):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name not in data:
            raise AttributeError(name)
        v = data[name]
        return Section(v) if isinstance(v, dict) else v

    def __setattr__(self, name: str, value: Any) -> None:
        object.__getattribute__(self, "_data")[name] = value

    def __getitem__(self, name: str) -> Any:
        # Raw dict access, never getattr: Config defines properties with the same names as top-level keys.
        data = object.__getattribute__(self, "_data")
        v = data[name]
        return Section(v) if isinstance(v, dict) else v

    def __contains__(self, name: str) -> bool:
        return name in object.__getattribute__(self, "_data")

    def get(self, name: str, default: Any = None) -> Any:
        data = object.__getattribute__(self, "_data")
        if name not in data:
            return default
        v = data[name]
        return Section(v) if isinstance(v, dict) else v

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(object.__getattribute__(self, "_data"))


class Config(Section):
    def __init__(self, data: dict[str, Any], project_root: Path):
        super().__init__(data)
        object.__setattr__(self, "project_root", project_root)

    @property
    def data_dir(self) -> Path:
        p = expand(self["data_dir"])
        return p if p.is_absolute() else self.project_root / p

    @property
    def db_path(self) -> Path:
        return self.data_dir / "smartapply.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def applications_dir(self) -> Path:
        return self.data_dir / "applications"

    @property
    def toasts_dir(self) -> Path:
        return self.data_dir / "toasts"

    @property
    def pipeline_root_path(self) -> Path:
        return expand(self["pipeline_root"])

    @property
    def pipeline_db_path(self) -> Path:
        return self.pipeline_root_path / "data" / "pipeline.sqlite3"

    @property
    def archon_env_path(self) -> Path:
        return expand(self["archon_env"])

    @property
    def profile_path(self) -> Path:
        p = expand(self["profile_path"])
        return p if p.is_absolute() else self.project_root / p

    @property
    def chrome_profile_dir(self) -> Path:
        return expand(self.chrome.profile_dir)


def load_config(path: Path | None = None, *, project_root: Path | None = None,
                overrides: dict | None = None) -> Config:
    root = project_root or PROJECT_ROOT
    path = path or (root / "config.json")
    data = DEFAULTS
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = _deep_merge(data, json.load(f))
    if overrides:
        data = _deep_merge(data, overrides)
    return Config(data, root)
