"""Load and validate config.json into an attribute-accessible Config."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .paths import PROJECT_ROOT

STAGE_NAMES = ("ideamaker", "builder", "reconcile", "critic")

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "roles_dir": "roles",
    "projects_dir": "projects",
    "dashboard": {"host": "127.0.0.1", "port": 8765, "public_base_url": "http://127.0.0.1:8765"},
    # Smart Apply (the sibling project that fills applications up to Submit). "" hides its buttons.
    "smartapply": {"url": "http://127.0.0.1:8770"},
    "collector": {
        "interval_minutes": 15,
        "user_agent": "internship-collector/2.0 (personal job tracker)",
        "http_timeout_s": 60,
        "seed_quiet": True,
        "sources": [],
    },
    "watchlist": {
        "default_poll_minutes": 60,
        "title_regex": "intern|co-?op",
        "workday_search_text": "intern",
        "workday_max_pages": 5,
        "auto_add_on_approve": True,
    },
    "screen": {
        "categories": ["Software", "AI/ML/Data", "Quant"],
        "target_terms": ["Summer 2027", "Fall 2027", "Spring 2027", "Winter 2027"],
        "require_degree": "Bachelor's",
        "us_locations_only": True,
        "citizenship_required_out": True,
        "exclude_title_regex": r"\b(new grad|full[- ]time|phd)\b",
        "fetch_jd_before_ping": True,
        "applicant": {"work_auth": "us_permanent_resident", "graduation": "2028-05"},
    },
    "jd": {"http_timeout_s": 15, "min_chars": 400, "browser_enabled": True, "browser_timeout_s": 45},
    "notify": {
        "enabled": True,
        "app_id": "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe",
        "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "max_pings_per_run": 8,
        "quiet_hours": None,
        "ntfy_topic": None,
    },
    "claude": {"cmd": ["claude"], "model": "claude-opus-5", "fallback_model": None, "extra_args": [],
               "env": {"ARCHON_AGENT": "1"}, "guard_hook": True},
    "stages": {
        "ideamaker": {"max_budget_usd": 15, "timeout_min": 45, "max_fix_rounds": 3, "fix_budget_usd": 5,
                      "max_attempts": 2, "max_turns": 150, "fix_turns": 40, "model": None},
        "builder": {"max_budget_usd": 40, "timeout_min": 180, "max_fix_rounds": 2, "fix_budget_usd": 10,
                    "max_attempts": 2, "max_turns": 400, "fix_turns": 80, "min_projects": 1, "model": None},
        "reconcile": {"max_budget_usd": 8, "timeout_min": 30, "max_fix_rounds": 2, "fix_budget_usd": 4,
                      "max_attempts": 2, "max_turns": 80, "fix_turns": 30, "model": None},
        "critic": {"max_budget_usd": 6, "timeout_min": 20, "max_fix_rounds": 2, "fix_budget_usd": 6,
                   "max_attempts": 2, "max_turns": 50, "fix_turns": 30, "model": None},
    },
    "worker": {"threads": 2, "auto_continue": True, "auto_requeue_on_restart": True, "stop_grace_s": 20,
               "job_budget_usd": 80, "below_normal_priority": True,
               # Resource containment so builds never starve Windows/Chrome. WSL is capped separately in .wslconfig.
               # Layer 1 (hard Job Object ceilings) is DISABLED (0 = off): a job-WIDE memory cap starves legitimate
               # memory-heavy builds -- an 8 GB cap crashed benchmark builds with "memory allocation failed" /
               # 0xC0000409 on 2026-09-10. Protection instead comes from the adaptive governor below, BelowNormal
               # priority (CPU yields to Chrome), the WSL 12 GB cap, and one build at a time. Leave these 0.
               "job_mem_limit_gb": 0, "cpu_rate_cap_pct": 0,
               # Layer 2: adaptive governor. Pauses the START of new builds when free RAM is low; resumes with hysteresis.
               "governor_enabled": True, "governor_poll_s": 4,
               "pause_free_ram_gb": 3.5, "resume_free_ram_gb": 5.0},
    "verify": {"pdflatex": "pdflatex", "pdfinfo": "pdfinfo", "allowed_future_dates": ["May 2028"],
               "compile_timeout_s": 120, "min_source_urls": 3, "compile": True},
    "wsl": {"distro": "Ubuntu-22.04", "cuda_bin": "/usr/local/cuda/bin"},
    "msvc": {"vcvars": ""},
    "log_level": "INFO",
    "rank": {"enabled": True, "model": "claude-sonnet-5"},
    # How full-lane runs fill project slots; recorded in meta.json. "reuse_first" only in the A/B sandbox.
    "project_policy": "design_first",
    # Spend by company tier (Alex, 2026-10-04): the full Opus pipeline only for S/A; B gets one cheap tailoring pass
    # on an archetype resume; C/D get the matching archetype resume as is. D-tier jobs are auto-ignored, not pinged.
    "tiers": {
        "enabled": True,
        "score_cutoffs": {"S": 90, "A": 75, "B": 55, "C": 35},
        "lanes": {"S": "full", "A": "full", "B": "light", "C": "archetype", "D": "archetype"},
        "ping": {"S": True, "A": True, "B": True, "C": True, "D": False},
    },
    # The light lane's one tailoring pass (pipeline/lite.py).
    "lite": {"model": "claude-opus-5", "max_budget_usd": 3, "max_turns": 30, "max_fix_rounds": 1,
             "fix_budget_usd": 1, "timeout_min": 20, "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]},
}

TIERS = ("S", "A", "B", "C", "D")
LANES = ("full", "light", "archetype")


class ConfigError(Exception):
    pass


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
        return getattr(self, name)

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

    def items(self):
        return object.__getattribute__(self, "_data").items()

    def keys(self):
        return object.__getattribute__(self, "_data").keys()

    def values(self):
        return object.__getattribute__(self, "_data").values()

    def __iter__(self):
        return iter(object.__getattribute__(self, "_data"))

    def __len__(self) -> int:
        return len(object.__getattribute__(self, "_data"))

    def __repr__(self) -> str:
        return f"Section({object.__getattribute__(self, '_data')!r})"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config(Section):
    """The validated configuration. `project_root`, `data_dir`, `roles_dir`, `projects_dir` are absolute Paths."""

    project_root: Path
    data_dir: Path
    roles_dir: Path
    projects_dir: Path
    config_path: Path | None

    def stage(self, name: str) -> Section:
        if name not in STAGE_NAMES:
            raise ConfigError(f"unknown stage {name!r}")
        return self.stages[name]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pipeline.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def jd_dir(self) -> Path:
        return self.data_dir / "jd"

    @property
    def toasts_dir(self) -> Path:
        return self.data_dir / "toasts"

    @property
    def playbooks_dir(self) -> Path:
        return self.project_root / "playbooks"


def default_config_path() -> Path:
    return PROJECT_ROOT / "config.json"


def _validate(data: dict, where: str = "") -> None:
    def req(cond: bool, msg: str) -> None:
        if not cond:
            raise ConfigError(f"{where}{msg}")

    req(isinstance(data["claude"]["cmd"], list) and len(data["claude"]["cmd"]) > 0, "claude.cmd must be a non-empty list")
    req(isinstance(data["collector"]["interval_minutes"], (int, float)) and data["collector"]["interval_minutes"] > 0,
        "collector.interval_minutes must be positive")
    req(isinstance(data["dashboard"]["port"], int) and 0 <= data["dashboard"]["port"] < 65536, "dashboard.port invalid")
    for name in data["stages"]:
        req(name in STAGE_NAMES, f"stages.{name}: unknown stage")
    for name in STAGE_NAMES:
        st = data["stages"][name]
        for key in ("max_budget_usd", "timeout_min", "max_fix_rounds", "max_attempts"):
            req(isinstance(st.get(key), (int, float)) and st[key] >= 0, f"stages.{name}.{key} must be a non-negative number")
        req(st["max_attempts"] >= 1, f"stages.{name}.max_attempts must be >= 1")
        m = st.get("model")
        req(m is None or (isinstance(m, str) and m.strip()), f"stages.{name}.model must be null or a non-empty string")
    for src in data["collector"]["sources"]:
        req(isinstance(src, dict) and "name" in src and "type" in src and "url" in src,
            "collector.sources entries need name, type, url")
    req(isinstance(data["worker"]["threads"], int) and data["worker"]["threads"] >= 1, "worker.threads must be >= 1")
    tiers = data["tiers"]
    cuts = tiers["score_cutoffs"]
    req(all(isinstance(cuts.get(t), (int, float)) for t in ("S", "A", "B", "C")),
        "tiers.score_cutoffs needs numeric S, A, B, C")
    req(cuts["S"] >= cuts["A"] >= cuts["B"] >= cuts["C"], "tiers.score_cutoffs must be S >= A >= B >= C")
    for t in TIERS:
        req(tiers["lanes"].get(t) in LANES, f"tiers.lanes.{t} must be one of {', '.join(LANES)}")
        req(isinstance(tiers["ping"].get(t), bool), f"tiers.ping.{t} must be true or false")
    for key in ("max_budget_usd", "max_turns", "max_fix_rounds", "timeout_min"):
        req(isinstance(data["lite"].get(key), (int, float)) and data["lite"][key] >= 0,
            f"lite.{key} must be a non-negative number")


def load_config(path: Path | str | None = None, *, project_root: Path | None = None,
                overrides: dict | None = None) -> Config:
    """Load config.json merged over DEFAULTS. `project_root` defaults to the package's parent."""
    root = Path(project_root) if project_root else PROJECT_ROOT
    cfg_path: Path | None
    if path is None:
        cfg_path = root / "config.json"
        file_data = {}
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as f:
                file_data = json.load(f)
    else:
        cfg_path = Path(path)
        with cfg_path.open(encoding="utf-8") as f:
            file_data = json.load(f)
    data = _deep_merge(DEFAULTS, file_data)
    if overrides:
        data = _deep_merge(data, overrides)
    _validate(data)
    cfg = Config(data)
    object.__setattr__(cfg, "project_root", root)
    for key in ("data_dir", "roles_dir", "projects_dir"):
        p = Path(data[key])
        object.__setattr__(cfg, key, p if p.is_absolute() else (root / p))
    object.__setattr__(cfg, "config_path", cfg_path)
    return cfg
