"""Project-root-relative paths, slugging, and WSL path translation."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]


def slugify(s: str, max_len: int = 40) -> str:
    """Turn free text into a filesystem-safe token: [A-Za-z0-9] runs joined by '_'."""
    tokens = re.findall(r"[A-Za-z0-9]+", s or "")
    out = "_".join(tokens)
    if not out:
        out = "x"
    if len(out) > max_len:
        out = out[:max_len].rstrip("_")
    return out


def short_slug(s: str, max_len: int = 12) -> str:
    """Lowercase token used inside artifact filenames (e.g. 'optiver', 'gea')."""
    tokens = re.findall(r"[A-Za-z0-9]+", (s or "").lower())
    if not tokens:
        return "role"
    out = tokens[0]
    if len(out) < 3 and len(tokens) > 1:
        out = "".join(tokens[:2])
    return out[:max_len]


def to_wsl_path(p: Path | str) -> str:
    """C:\\Users\\x\\a b -> /mnt/c/Users/x/a b. Already-POSIX paths pass through."""
    s = str(p)
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", s)
    if not m:
        return s.replace("\\", "/")
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def wsl_quote(p: Path | str) -> str:
    return shlex.quote(to_wsl_path(p))


def file_url(p: Path | str) -> str:
    """file:/// URL for a Windows path, for dashboard links."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return "file:///" + s
