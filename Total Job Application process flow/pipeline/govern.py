"""Adaptive resource governor.

Samples free physical RAM and, when the machine is under memory pressure (you are actively using Chrome, etc.),
pauses the START of new pipeline builds so the pipeline never competes for the last of your RAM. Resumes with
hysteresis once pressure clears. It deliberately does NOT suspend a build already running: that would risk tripping
the build's wall-clock timeout, and a running build is already bounded by the Job Object's hard memory/CPU caps.

Windows-only sampling via GlobalMemoryStatusEx (no third-party deps). Off Windows it is a no-op that always allows.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable

log = logging.getLogger("pipeline.govern")


def _free_ram_gb() -> float | None:
    """Available physical RAM in GB, or None if it can't be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None
        return m.ullAvailPhys / (1024 ** 3)
    except Exception as e:  # noqa: BLE001
        log.debug("free RAM read failed: %s", e)
        return None


class ResourceGovernor:
    """Background sampler with a gate. `wait_until_ok` blocks callers while the system is under pressure."""

    def __init__(self, cfg) -> None:
        w = cfg.worker
        self.enabled = bool(w.get("governor_enabled", True)) and sys.platform == "win32"
        self.pause_gb = float(w.get("pause_free_ram_gb", 3.5))
        self.resume_gb = float(w.get("resume_free_ram_gb", 5.0))
        self.poll_s = max(1.0, float(w.get("governor_poll_s", 4)))
        self._ok = threading.Event()
        self._ok.set()  # set = safe to run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.free_gb: float | None = None
        self.paused = False

    # -- sampling loop
    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="governor", daemon=True)
        self._thread.start()
        log.info("resource governor on: pause below %.1f GB free, resume at %.1f GB", self.pause_gb, self.resume_gb)

    def stop(self) -> None:
        self._stop.set()
        self._ok.set()  # release anyone waiting so shutdown is not blocked
        t = self._thread
        if t is not None:
            t.join(timeout=self.poll_s + 2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            free = _free_ram_gb()
            self.free_gb = free
            if free is not None:
                if not self.paused and free < self.pause_gb:
                    self.paused = True
                    self._ok.clear()
                    log.info("governor: PAUSING new builds (free RAM %.1f GB < %.1f GB)", free, self.pause_gb)
                elif self.paused and free >= self.resume_gb:
                    self.paused = False
                    self._ok.set()
                    log.info("governor: resuming (free RAM %.1f GB >= %.1f GB)", free, self.resume_gb)
            self._stop.wait(self.poll_s)

    # -- gate consulted before starting a build stage
    def wait_until_ok(self, stop_event: threading.Event | None = None,
                      on_wait: Callable[[str], None] | None = None) -> None:
        """Block while the system is under memory pressure. Returns immediately if disabled or already OK.
        Breaks out if the pool is shutting down (stop_event set)."""
        if not self.enabled:
            return
        notified = False
        while not self._ok.is_set():
            if self._stop.is_set() or (stop_event is not None and stop_event.is_set()):
                return
            if not notified and on_wait is not None:
                free = self.free_gb
                on_wait(f"paused: low system RAM ({free:.1f} GB free)" if free is not None else "paused: low system RAM")
                notified = True
            self._ok.wait(timeout=0.5)

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "paused": self.paused,
                "free_gb": round(self.free_gb, 1) if self.free_gb is not None else None,
                "pause_gb": self.pause_gb, "resume_gb": self.resume_gb}
