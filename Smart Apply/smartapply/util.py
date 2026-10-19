"""Small shared helpers: time, atomic writes, JSON, HTTP, tokens, process flags."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

EM_DASH = "—"


def now_iso() -> str:
    """UTC, seconds, ISO 8601."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def now_local_iso() -> str:
    """Local time with offset, so day counts in the tracker match the wall clock.
    (Archon compares substr(ts,1,10) to a local date; a UTC stamp after 20:00 EDT lands on the next day.)"""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def new_token(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def atomic_write(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding=encoding, newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    atomic_write(Path(path), json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


class HttpError(Exception):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{message} ({url})")
        self.url = url
        self.status = status


def http_get(url: str, *, headers: dict | None = None, timeout: int = 30, user_agent: str = DEFAULT_UA) -> bytes:
    hdrs = {"User-Agent": user_agent, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(url, e.code, f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise HttpError(url, None, f"URL error: {e.reason}") from e
    except TimeoutError as e:
        raise HttpError(url, None, "timeout") from e


def http_post(url: str, data: bytes, *, headers: dict | None = None, timeout: int = 30,
              user_agent: str = DEFAULT_UA) -> bytes:
    hdrs = {"User-Agent": user_agent, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(url, e.code, f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise HttpError(url, None, f"URL error: {e.reason}") from e


def http_get_json(url: str, **kw: Any) -> Any:
    return json.loads(http_get(url, **kw).decode("utf-8", "replace"))


def no_window_flags() -> int:
    """Creation flags for every child process: never flash a console on Alex's screen."""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def detached_flags() -> int:
    """For a child that must outlive us (Chrome): detached, own process group, no console."""
    if sys.platform != "win32":
        return 0
    return (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def scan_em_dashes(text: str) -> list[tuple[int, str]]:
    """(line number, reason) for every em dash in prose. Code fences, table separators and rules are exempt.
    Same rule the pipeline enforces on resumes: Alex flags em dashes as AI-sounding."""
    import re
    out: list[tuple[int, str]] = []
    in_code = False
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if s.startswith("---") and s.strip("-") == "":
            continue
        if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line) and "-" in line:
            continue
        if EM_DASH in line:
            out.append((i, "em dash (U+2014)"))
        if "---" in re.sub(r"`[^`]*`", "", line):
            out.append((i, "'---' in prose"))
    return out
