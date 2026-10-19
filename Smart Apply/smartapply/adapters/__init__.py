"""One adapter per ATS. `for_ats` returns the deterministic adapter when one exists, the Claude fallback
(`agent.AgentAdapter`) for every other site, and None only for LinkedIn (never automated)."""

from __future__ import annotations

from .base import Adapter

NEVER = {"linkedin"}


def _registry() -> dict[str, type[Adapter]]:
    from . import ashby, greenhouse, lever, workday
    return {"greenhouse": greenhouse.GreenhouseAdapter, "lever": lever.LeverAdapter, "ashby": ashby.AshbyAdapter,
            "workday": workday.WorkdayAdapter}


def for_ats(ats: str | None) -> type[Adapter] | None:
    if (ats or "") in NEVER:
        return None
    cls = _registry().get(ats or "")
    if cls is not None:
        return cls
    from . import agent
    return agent.AgentAdapter


def is_agent(ats: str | None) -> bool:
    return (ats or "") not in NEVER and (ats or "") not in _registry()


def supported() -> list[str]:
    """ATS kinds with a deterministic adapter (the agent takes the rest)."""
    return sorted(_registry())
