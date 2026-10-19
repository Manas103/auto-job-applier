"""Small shared helpers: time, atomic writes, HTTP, retry, hashing, file tails."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> date:
    """Monkeypatch target for deterministic tests."""
    return date.today()


def new_token(nbytes: int = 16) -> str:
    return secrets.token_hex(nbytes)


def stable_hash(s: str, n: int = 12) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]


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
    atomic_write(Path(path), json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


class HttpError(Exception):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{message} ({url})")
        self.url = url
        self.status = status


def http_get(url: str, *, headers: dict | None = None, timeout: int = 60, user_agent: str = DEFAULT_UA) -> bytes:
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


def http_post_json(url: str, body: dict, *, headers: dict | None = None, timeout: int = 60,
                   user_agent: str = DEFAULT_UA) -> bytes:
    hdrs = {"User-Agent": user_agent, "Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(url, e.code, f"HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise HttpError(url, None, f"URL error: {e.reason}") from e
    except TimeoutError as e:
        raise HttpError(url, None, "timeout") from e


def retry(fn: Callable[[], Any], *, attempts: int = 3, delay_s: float = 1.0,
          exceptions: tuple[type[BaseException], ...] = (Exception,)) -> Any:
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except exceptions as e:  # noqa: PERF203
            last = e
            if i < attempts - 1:
                time.sleep(delay_s * (2 ** i))
    assert last is not None
    raise last


def tail_lines(path: Path, n: int = 40) -> list[str]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= n:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-n:]
    except OSError:
        return []


def parse_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def chunks(items: Iterable[Any], n: int) -> Iterable[list[Any]]:
    buf: list[Any] = []
    for it in items:
        buf.append(it)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf
