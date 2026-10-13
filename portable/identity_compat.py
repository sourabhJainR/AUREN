"""Canonical AUREN configuration and state-root resolution with safe legacy fallback.

The resolver never moves or deletes state. Migration is explicit and verified by
portable.state_migration so older installations remain recoverable.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

LEGACY_HOME_NAME = ".aer"
CANONICAL_HOME_NAME = ".auren"


def get_auren_env(
    suffix: str,
    default: Any = None,
    *,
    environ: Mapping[str, str] | None = None,
    legacy_names: tuple[str, ...] = (),
) -> str | None:
    """Read AUREN_<suffix> first, then documented legacy aliases.

    An explicitly set canonical value wins even if empty; conflicting values are
    never silently merged.
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


def auren_home(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return canonical home, honoring explicit settings and preserving old data.

    Precedence: explicit AUREN_HOME, explicit legacy AER_HOME, existing
    ~/.auren, existing ~/.aer, then the new-install default ~/.auren.
    The legacy directory is selected only when canonical state is absent; no
    copy, rename, deletion, or migration is performed here.
    """
    source = os.environ if environ is None else environ
    if "AUREN_HOME" in source:
        return Path(source["AUREN_HOME"]).expanduser()
    if "AER_HOME" in source:
        return Path(source["AER_HOME"]).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    canonical = base / CANONICAL_HOME_NAME
    legacy = base / LEGACY_HOME_NAME
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def project_state_dir(
    project_root: str | Path,
    *,
    home_name: str = CANONICAL_HOME_NAME,
    legacy_name: str = LEGACY_HOME_NAME,
) -> Path:
    """Resolve project-local state without moving or deleting an existing store."""
    root = Path(project_root).resolve()
    canonical = root / home_name
    legacy = root / legacy_name
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


__all__ = ["auren_home", "get_auren_env", "project_state_dir"]
