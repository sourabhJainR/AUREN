"""Detect optional AI coding extensions without installing or mutating them."""

from __future__ import annotations

import json
import re

from artifact_freshness import validate_artifact_freshness
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


def _skill_metadata(path: Path) -> dict[str, Any]:
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
        "artifact_fresh": False,
        "artifact_freshness_check": {"status": "not-applicable", "fresh": False, "checked": []},
        "failure_mode": "skip skill; preserve core workflow",
        "permissions": "not inferred from skill text",
        "side_effects": "not inferred from skill text",
    }


def discover_skills() -> list[dict[str, Any]]:
    """Discover existing skills without deciding to invoke or install them."""
    return [_skill_metadata(path) for path in _discover_skill_dirs()]



def _artifact_freshness(artifacts: list[str]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for value in artifacts:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file():
            try:
                display_path = str(path.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                checks.append({"path": str(path), "status": "invalid", "fresh": False, "reason": "artifact_outside_repository"})
                continue
            result = validate_artifact_freshness(path, ROOT)
            checks.append({"path": display_path, **result})
    if not checks:
        return {"status": "unavailable", "fresh": False, "checked": checks}
    fresh = all(item.get("fresh") is True for item in checks)
    status = "fresh" if fresh else (
        "stale" if any(item.get("status") == "stale" for item in checks)
        else "unverified"
    )
    return {"status": status, "fresh": fresh, "checked": checks}



def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return []
    return value


def validate_registry_manifest(data: dict[str, Any]) -> dict[str, list[str]]:
    """Validate extension metadata before availability can be treated as eligible."""
    registry = data.get("registry", {}) if isinstance(data, dict) else {}
    global_errors: list[str] = []
    if not isinstance(registry, dict):
        global_errors.append("registry_section_invalid")
        registry = {}
    if type(registry.get("version")) is not int or registry.get("version") != 2:
        global_errors.append("registry_schema_version_invalid")
    for key in ("install_optional", "mutate_external_tools", "metadata_is_trust_grant"):
        if registry.get(key) is not False:
            global_errors.append(f"registry_{key}_must_be_false")
    extensions = data.get("extensions", {}) if isinstance(data, dict) else {}
    if not isinstance(extensions, dict):
        return {"__registry__": sorted(set(global_errors + ["extensions_section_invalid"]))}
    results: dict[str, list[str]] = {}
    required_strings = (
        "kind", "version", "cost_class", "trust_class", "freshness",
        "failure_mode", "policy", "source", "license",
    )
    required_lists = ("capabilities", "evidence_types", "permissions", "side_effects")
    for name, spec in extensions.items():
        errors = list(global_errors)
        if not isinstance(spec, dict):
            results[str(name)] = sorted(set(errors + ["extension_spec_invalid"]))
            continue
        for key in required_strings:
            if not isinstance(spec.get(key), str) or not spec[key].strip():
                errors.append(f"{key}_required")
        trust_class = spec.get("trust_class")
        if not isinstance(trust_class, str) or trust_class not in {"local-unverified", "external-unverified", "unverified", "trusted"}:
            errors.append("trust_class_invalid")
        for key in required_lists:
            value = spec.get(key)
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                errors.append(f"{key}_must_be_string_list")
        for key in ("commands", "markers", "artifacts"):
            if key in spec and (
                not isinstance(spec[key], list)
                or any(not isinstance(item, str) or not item.strip() for item in spec[key])
            ):
                errors.append(f"{key}_must_be_string_list")
        if type(spec.get("requires_approval")) is not bool:
            errors.append("requires_approval_must_be_boolean")
        limit = spec.get("output_limit_chars")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1_000_000:
            errors.append("output_limit_chars_out_of_range")
        if not _string_list(spec.get("capabilities", [])):
            errors.append("capabilities_must_not_be_empty")
        if not _string_list(spec.get("evidence_types", [])):
            errors.append("evidence_types_must_not_be_empty")
        results[str(name)] = sorted(set(errors))
    return results

def detect_extensions() -> dict[str, dict[str, Any]]:
    """Return deterministic availability and trust metadata; never mutates tools.

    Metadata is descriptive, not a trust grant. Availability is only a local
    command/marker observation; it does not prove provider health or safety.
    """
    import tomllib

    data = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))
    extensions = data.get("extensions", {})
    if not isinstance(extensions, dict):
        extensions = {}
    validation = validate_registry_manifest(data)
    result: dict[str, dict[str, Any]] = {}
    for name, raw_spec in extensions.items():
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        manifest_errors = validation.get(name, ["extension_manifest_missing"])
        commands = _string_list(spec.get("commands", []))
        markers = _string_list(spec.get("markers", []))
        command_hits = [c for c in commands if _command_available(c)]
        marker_hits = [m for m in markers if _marker_exists(m)]
        available = bool(command_hits or marker_hits) and not manifest_errors
        freshness_check = _artifact_freshness(_string_list(spec.get("artifacts", [])))
        result[name] = {
            "available": available,
            "health": "invalid-manifest" if manifest_errors else ("discovered" if available else "unavailable"),
            "manifest_valid": not manifest_errors,
            "manifest_errors": manifest_errors,
            "command_hits": command_hits,
            "marker_hits": marker_hits,
            "kind": spec.get("kind", "unknown"),
            "version": spec.get("version", "unknown"),
            "capabilities": sorted(set(_string_list(spec.get("capabilities", [])))),
            "cost_class": spec.get("cost_class", "unknown"),
            "trust_class": spec.get("trust_class", "unverified"),
            "evidence_types": sorted(set(_string_list(spec.get("evidence_types", [])))),
            "freshness": spec.get("freshness", "not-checked"),
            "artifact_fresh": freshness_check["fresh"],
            "artifact_freshness_check": freshness_check,
            "failure_mode": spec.get("failure_mode", "skip; preserve core workflow"),
            "permissions": sorted(set(_string_list(spec.get("permissions", [])))),
            "side_effects": sorted(set(_string_list(spec.get("side_effects", [])))),
            "requires_approval": spec.get("requires_approval") if type(spec.get("requires_approval")) is bool else True,
            "output_limit_chars": spec.get("output_limit_chars") if isinstance(spec.get("output_limit_chars"), int) and not isinstance(spec.get("output_limit_chars"), bool) else 12000,
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
        "artifact_fresh": False,
        "artifact_freshness_check": {"status": "not-applicable", "fresh": False, "checked": []},
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
