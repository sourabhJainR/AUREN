"""Lightweight canonical-first environment compatibility shared by runtime layers.

Kept outside the eager portable package so the harness bootstrap can resolve
settings without importing the full portable runtime or creating import cycles.
"""
from __future__ import annotations

import os
from typing import Any, Mapping


def get_auren_env(
    suffix: str,
    default: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    legacy_names: tuple[str, ...] = (),
) -> str | None:
    """Read AUREN_<suffix> first, then legacy aliases only when absent.

    An explicitly present canonical value wins even when empty. Values are
    never merged or silently overridden by a legacy alias.
    """
    source = os.environ if environ is None else environ
    key = suffix if suffix.startswith("AUREN_") else f"AUREN_{suffix}"
    if key in source:
        return source[key]
    aliases = legacy_names or (key.replace("AUREN_", "AER_", 1),)
    for alias in aliases:
        if alias in source:
            return source[alias]
    return default


__all__ = ["get_auren_env"]
