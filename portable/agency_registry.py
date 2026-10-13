"""AUREN specialist registry and deterministic, explainable routing helpers."""
from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "agency" / "registry.json"
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]{1,}", re.I)

# High-signal terms are intentionally small and transparent. The upstream roster
# remains the source of specialist knowledge; this layer only ranks candidates.
DOMAIN_HINTS = {
    "engineering": {"code", "api", "backend", "frontend", "software", "architecture", "bug", "refactor"},
    "testing": {"test", "qa", "quality", "regression", "validation", "verify"},
    "security": {"security", "auth", "oauth", "secret", "vulnerability", "threat"},
    "research": {"research", "investigate", "compare", "evidence", "source", "analysis"},
    "design": {"design", "ux", "ui", "accessibility", "visual", "prototype"},
    "product": {"product", "roadmap", "requirements", "feature", "discovery"},
    "finance": {"finance", "financial", "valuation", "budget", "forecast", "accounting"},
    "marketing": {"marketing", "campaign", "content", "brand", "seo", "growth"},
    "project-management": {"project", "schedule", "milestone", "delivery", "risk", "dependency"},
}


def load_registry(path=DEFAULT_REGISTRY):
    p = Path(path)
    if not p.exists():
        return {"schema_version": "1.0", "agents": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _tokens(text: str) -> set[str]:
    return {x.lower() for x in TOKEN_RE.findall(text or "")}


def _score(task_tokens: set[str], agent: dict) -> tuple[int, list[str]]:
    name = str(agent.get("name", ""))
    division = str(agent.get("division", ""))
    description = str(agent.get("description", ""))
    agent_tokens = _tokens(" ".join((name, division, description)))
    overlap = task_tokens & agent_tokens
    score = min(len(overlap) * 4, 28)
    reasons = [f"keyword:{x}" for x in sorted(overlap)[:5]]

    division_tokens = _tokens(division)
    for domain, hints in DOMAIN_HINTS.items():
        if task_tokens & hints and domain in division.lower():
            score += 14
            reasons.append(f"domain:{domain}")
            break
        if task_tokens & hints and division_tokens & hints:
            score += 7
            reasons.append(f"domain-match:{domain}")
            break

    if any(x in task_tokens for x in {"security", "vulnerability", "auth"}) and "security" in division.lower():
        score += 10
        reasons.append("risk:sensitive-domain")
    if any(x in task_tokens for x in {"test", "verify", "validation", "regression"}) and "test" in division.lower():
        score += 8
        reasons.append("role:verification")

    return score, reasons


def rank(task: str, registry=None, limit: int = 8):
    """Return ranked candidates with reasons; routing is advisory, never authority."""
    if limit < 1:
        return []
    registry = registry or load_registry()
    query = _tokens(task)
    scored = []
    for agent in registry.get("agents", []):
        score, reasons = _score(query, agent)
        scored.append({
            "score": score,
            "name": agent.get("name"),
            "division": agent.get("division"),
            "file": agent.get("file"),
            "description": agent.get("description", ""),
            "reasons": reasons,
        })
    return sorted(scored, key=lambda x: (-x["score"], x["division"] or "", x["name"] or ""))[:limit]
