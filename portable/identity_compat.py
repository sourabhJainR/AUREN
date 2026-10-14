"""Canonical AUREN configuration and state-root resolution with safe legacy fallback.

The resolver never moves or deletes state. Migration is explicit and verified by
portable.state_migration so older installations remain recoverable.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from auren_env_compat import get_auren_env

LEGACY_HOME_NAME = ".aer"
CANONICAL_HOME_NAME = ".auren"
AUREN_CHECKPOINT_STATE_SCHEMA_VERSION = 2
LEGACY_CHECKPOINT_STATE_KEYS = {
    "aer_execution_strategy": "auren_execution_strategy",
    "aer_execution_mode": "auren_execution_mode",
    "aer_adaptive_policy": "auren_adaptive_policy",
    "aer_context_pack": "auren_context_pack",
    "aer_cognitive_plan": "auren_cognitive_plan",
}


def canonicalize_auren_checkpoint_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate only registered legacy keys, rejecting ambiguous dual-key state.

    The returned mapping is a new value; callers keep the original checkpoint
    intact until the canonicalized checkpoint is successfully persisted.
    """
    if not isinstance(state, Mapping):
        raise TypeError("checkpoint state must be a mapping")
    result = dict(state)
    version = result.get("auren_state_schema_version", 1)
    if type(version) is not int or version < 1 or version > AUREN_CHECKPOINT_STATE_SCHEMA_VERSION:
        raise ValueError("unsupported AUREN checkpoint state schema version")
    for legacy_key, canonical_key in LEGACY_CHECKPOINT_STATE_KEYS.items():
        if legacy_key not in result:
            continue
        legacy_value = result[legacy_key]
        if canonical_key in result and result[canonical_key] != legacy_value:
            raise ValueError(f"conflicting legacy and canonical checkpoint keys: {legacy_key} / {canonical_key}")
        result.setdefault(canonical_key, legacy_value)
        result.pop(legacy_key, None)
    result["auren_state_schema_version"] = AUREN_CHECKPOINT_STATE_SCHEMA_VERSION
    return result


def canonical_auren_home(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the canonical destination root; never interpret an empty value as CWD."""
    source = os.environ if environ is None else environ
    if "AUREN_HOME" in source:
        value = source["AUREN_HOME"]
        if not value.strip():
            raise ValueError("AUREN_HOME must not be empty")
        return Path(value).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / CANONICAL_HOME_NAME


def legacy_aer_home(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the legacy source root; never interpret an empty value as CWD."""
    source = os.environ if environ is None else environ
    if "AER_HOME" in source:
        value = source["AER_HOME"]
        if not value.strip():
            raise ValueError("legacy AER_HOME must not be empty")
        return Path(value).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / LEGACY_HOME_NAME


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
        value = source["AUREN_HOME"]
        if not value.strip():
            raise ValueError("AUREN_HOME must not be empty")
        return Path(value).expanduser()
    if "AER_HOME" in source:
        value = source["AER_HOME"]
        if not value.strip():
            raise ValueError("legacy AER_HOME must not be empty")
        return Path(value).expanduser()
    base = Path(home).expanduser() if home is not None else Path.home()
    canonical = base / CANONICAL_HOME_NAME
    legacy = base / LEGACY_HOME_NAME
    if canonical.exists():
        return canonical
    if legacy.exists():
        return legacy
    return canonical


def canonical_runtime_home(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Prepare canonical runtime storage and copy known legacy state non-destructively.

    All new runtime writes use the canonical root. The explicit state-path
    migration validates known durable files and retains the legacy source for
    rollback; ambiguous conflicts fail closed.
    """
    from portable.state_paths import AurenStatePaths

    canonical = canonical_auren_home(home=home, environ=environ)
    legacy = legacy_aer_home(home=home, environ=environ)
    return AurenStatePaths.prepare(canonical_root=canonical, legacy_root=legacy).root


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


__all__ = ["AUREN_CHECKPOINT_STATE_SCHEMA_VERSION", "LEGACY_CHECKPOINT_STATE_KEYS", "auren_home", "canonical_auren_home", "canonical_runtime_home", "canonicalize_auren_checkpoint_state", "get_auren_env", "legacy_aer_home", "project_state_dir"]
