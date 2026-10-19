"""Listing sources: tier-1 community lists and tier-2 ATS boards."""

from __future__ import annotations

from typing import Callable

from ..models import Listing
from . import simplify, speedyapply

SOURCE_TYPES: dict[str, Callable[..., list[Listing]]] = {
    "simplify_json": simplify.parse_simplify_listings,
    "speedyapply_md": speedyapply.parse_speedyapply_readme,
}

__all__ = ["SOURCE_TYPES"]
