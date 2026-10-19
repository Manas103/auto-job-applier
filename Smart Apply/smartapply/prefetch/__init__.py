"""Question prefetchers: read an application's questions over plain HTTP before any browser opens."""

from __future__ import annotations

from typing import Callable

from ..models import Application, Question

_REGISTRY: dict[str, Callable[[Application], list[Question]]] = {}


def register(ats: str):
    def deco(fn):
        _REGISTRY[ats] = fn
        return fn
    return deco


def _load() -> None:
    from . import ashby, greenhouse, lever, workday  # noqa: F401  (registers)


def prefetch_questions(app: Application) -> list[Question] | None:
    """None when no prefetcher exists for the ATS (the adapter then reads the page itself)."""
    _load()
    fn = _REGISTRY.get(app.ats or "")
    return fn(app) if fn else None


def supported() -> list[str]:
    _load()
    return sorted(_REGISTRY)
