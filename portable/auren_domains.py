"""Canonical AUREN six-domain ownership registry.

This is metadata, not a second runtime or authority layer.  It gives every
portable capability one stable architectural owner and lets validators catch
cross-domain drift before it becomes an implementation fork.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

@dataclass(frozen=True)
class AurenDomain:
    key: str
    name: str
    responsibility: str
    authority: str

DOMAINS: Final[tuple[AurenDomain, ...]] = (
    AurenDomain("core", "AUREN Core", "world model, self model, reasoning and goals", "cannot authorize consequential execution"),
    AurenDomain("engine", "AUREN Engine", "planning, StateGraph execution, agent teams and resource routing", "routes execution but Guard controls consequential authority"),
    AurenDomain("memory", "AUREN Memory", "episodic, semantic, engineering and evidence-graph persistence", "observations require verification before becoming facts"),
    AurenDomain("learning", "AUREN Learning", "experimentation, causal attribution, capability invention and curriculum evolution", "cannot grant permissions, bypass verification, or authorize release"),
    AurenDomain("arena", "AUREN Arena", "independent evaluation, sealed holdouts, transfer and attestation", "cannot promote capabilities or act as its own oracle"),
    AurenDomain("guard", "AUREN Guard", "security, authority, verification, canary and rollback", "authorizes consequential lifecycle mutation"),
)

DOMAIN_BY_KEY: Final[dict[str, AurenDomain]] = {domain.key: domain for domain in DOMAINS}


def domain_for(key: str) -> AurenDomain:
    """Return the canonical domain or fail closed for unknown ownership."""
    try:
        return DOMAIN_BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"unknown AUREN domain: {key!r}") from exc


def canonical_domain_keys() -> tuple[str, ...]:
    return tuple(domain.key for domain in DOMAINS)
