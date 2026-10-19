"""pdflatex compilation, page counting, and box-warning scanning."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

latex_lock = threading.Lock()  # MiKTeX's on-the-fly installer is not safe to run concurrently.

_BOX_RE = re.compile(r"^(Overfull|Underfull) \\[hv]box(?:[^\n]*?at lines (\d+)--(\d+))?[^\n]*$", re.M)


@dataclass
class CompileResult:
    ok: bool
    pdf: Path | None
    log: Path | None
    pages: int | None
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""
    error_line: int | None = None
    log_tail: str = ""


def tool_available(path_or_name: str) -> bool:
    p = Path(path_or_name)
    if p.is_absolute():
        return p.exists()
    return shutil.which(path_or_name) is not None


def scan_log_warnings(log_text: str) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    for m in _BOX_RE.finditer(log_text):
        line = int(m.group(2)) if m.group(2) else None
        out.append((m.group(0).strip(), line))
    return out


def _find_error_line(log_text: str, tex_name: str) -> int | None:
    m = re.search(rf"{re.escape(tex_name)}:(\d+):", log_text)
    if m:
        return int(m.group(1))
    m = re.search(r"^l\.(\d+)", log_text, re.M)
    return int(m.group(1)) if m else None


def compile_tex(tex: Path, cfg: Config, *, runs: int = 2, timeout_s: int | None = None) -> CompileResult:
    tex = Path(tex).resolve()
    folder = tex.parent
    name = tex.stem
    timeout_s = timeout_s or int(cfg.verify.compile_timeout_s)
    cmd = [str(cfg.verify.pdflatex), "-interaction=nonstopmode", "-halt-on-error", "-file-line-error",
           "--enable-installer", f"-output-directory={folder}", tex.name]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    log_path = folder / f"{name}.log"
    pdf_path = folder / f"{name}.pdf"
    stderr = ""
    with latex_lock:
        for _ in range(runs):
            try:
                proc = subprocess.run(cmd, cwd=str(folder), capture_output=True, text=True, timeout=timeout_s,
                                      creationflags=flags, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                return CompileResult(False, None, log_path if log_path.exists() else None, None, stderr="pdflatex timed out")
            except OSError as e:
                return CompileResult(False, None, None, None, stderr=f"pdflatex could not start: {e}")
            stderr = proc.stderr or ""
            if proc.returncode != 0:
                log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else proc.stdout
                tail = "\n".join(log_text.splitlines()[-25:])
                return CompileResult(False, None, log_path if log_path.exists() else None, None, stderr=stderr,
                                     error_line=_find_error_line(log_text, tex.name), log_tail=tail)
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    warnings = [w for w, _ in scan_log_warnings(log_text)]
    pages = page_count(pdf_path, cfg) if pdf_path.exists() else None
    return CompileResult(pdf_path.exists(), pdf_path if pdf_path.exists() else None, log_path, pages, warnings, stderr)


def page_count(pdf: Path, cfg: Config) -> int | None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run([str(cfg.verify.pdfinfo), str(pdf)], capture_output=True, text=True, timeout=30,
                              creationflags=flags, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return _page_count_fallback(pdf)
    m = re.search(r"^Pages:\s+(\d+)", proc.stdout or "", re.M)
    if m:
        return int(m.group(1))
    return _page_count_fallback(pdf)


def _page_count_fallback(pdf: Path) -> int | None:
    try:
        data = pdf.read_bytes()
    except OSError:
        return None
    m = re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)
    if m:
        return max(int(x) for x in m)
    n = len(re.findall(rb"/Type\s*/Page\b", data))
    return n or None


def overflow_lines(pdf: Path, cfg: Config) -> int | None:
    """Lines of text past page 1, so a stage fitting a resume to one page knows how much to cut. None if unknown."""
    exe = Path(str(cfg.verify.pdfinfo))
    exe = exe.with_name(exe.name.replace("pdfinfo", "pdftotext"))
    if not tool_available(str(exe)):
        return None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.run([str(exe), "-f", "2", "-layout", str(pdf), "-"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    return sum(1 for ln in proc.stdout.splitlines() if ln.strip()) if proc.returncode == 0 else None
