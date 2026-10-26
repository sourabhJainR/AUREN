"""Detect optional AI coding extensions without installing or mutating them."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / ".ai-harness" / "extension_registry.toml"


def _command_available(name: str) -> bool:
    return shutil.which(name) is not None


def _marker_exists(marker: str) -> bool:
    path = Path(marker).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.exists()


def _discover_skill_dirs() -> list[Path]:
    roots = [
        ROOT / ".agents" / "skills",
        ROOT / ".claude" / "skills",
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".gemini" / "skills",
    ]
    result: list[Path] = []
    for root in roots:
        if root.exists():
            result.extend(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
    return sorted(set(result))


def _skill_metadata(path: Path) -> dict[str, str]:
    text = (path / "SKILL.md").read_text(encoding="utf-8", errors="replace")[:12000]
    name = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    description = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    return {
        "name": name.group(1).strip() if name else path.name,
        "description": description.group(1).strip() if description else "",
        "path": str(path),
        "version": "unknown",
        "health": "discovered",
        "cost_class": "unknown",
        "trust_class": "unverified",
        "evidence_types": "skill-instructions",
        "freshness": "not-checked",
        "failure_mode": "skip skill; preserve core workflow",
        "permissions": "not inferred from skill text",
        "side_effects": "not inferred from skill text",
    }


def discover_skills() -> list[dict[str, str]]:
    """Discover existing skills without deciding to invoke or install them."""
    return [_skill_metadata(path) for path in _discover_skill_dirs()]


def detect_extensions() -> dict[str, dict[str, Any]]:
    """Return deterministic availability and trust metadata; never mutates tools.

    Metadata is descriptive, not a trust grant. Availability is only a local
    command/marker observation; it does not prove provider health or safety.
    """
    import tomllib

    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for name, spec in data.get("extensions", {}).items():
        commands = spec.get("commands", [])
        markers = spec.get("markers", [])
        command_hits = [c for c in commands if _command_available(c)]
        marker_hits = [m for m in markers if _marker_exists(m)]
        available = bool(command_hits or marker_hits)
        result[name] = {
            "available": available,
            "health": "discovered" if available else "unavailable",
            "command_hits": command_hits,
            "marker_hits": marker_hits,
            "kind": spec.get("kind", "unknown"),
            "version": spec.get("version", "unknown"),
            "capabilities": sorted(set(spec.get("capabilities", []))),
            "cost_class": spec.get("cost_class", "unknown"),
            "trust_class": spec.get("trust_class", "unverified"),
            "evidence_types": sorted(set(spec.get("evidence_types", []))),
            "freshness": spec.get("freshness", "not-checked"),
            "failure_mode": spec.get("failure_mode", "skip; preserve core workflow"),
            "permissions": sorted(set(spec.get("permissions", []))),
            "side_effects": sorted(set(spec.get("side_effects", []))),
            "requires_approval": bool(spec.get("requires_approval", True)),
            "output_limit_chars": int(spec.get("output_limit_chars", 12000)),
            "policy": spec.get("policy", ""),
            "source": spec.get("source", ""),
            "license": spec.get("license", "unknown"),
        }
    result["discovered_skills"] = {
        "available": True,
        "health": "discovered",
        "kind": "agent-skills",
        "version": "unknown",
        "capabilities": ["skill-discovery"],
        "cost_class": "unknown",
        "trust_class": "unverified",
        "evidence_types": ["skill-instructions"],
        "freshness": "not-checked",
        "failure_mode": "skip skill; preserve core workflow",
        "permissions": [],
        "side_effects": [],
        "requires_approval": True,
        "output_limit_chars": 12000,
        "policy": "discover only; invocation remains task-driven",
        "source": "",
        "license": "unknown",
        "skills": discover_skills(),
    }
    return result


def main() -> int:
    print(json.dumps(detect_extensions(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
