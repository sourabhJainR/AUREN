"""Minimal executable kernel for independently supplied Arena evaluations.

Trust model: the policy receives only task-facing fields. Oracle answers remain in
the evaluator-owned manifest and are never passed in the policy input. This module
is a protocol boundary, not an OS sandbox; independent campaigns must run the
policy and evaluator under separately controlled principals/processes.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

SCHEMA_VERSION = "arena-run-v1"
_ALLOWED_SPLITS = {"development", "evaluation", "sealed_holdout"}


class ArenaError(ValueError):
    """Raised when benchmark input or evaluation evidence is invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArenaError(f"{field} must be a non-empty string")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy a benchmark manifest; fail closed on ambiguous input."""
    if not isinstance(manifest, Mapping):
        raise ArenaError("manifest must be an object")
    required = ("benchmark_id", "evaluator_id", "runtime_id", "cases")
    for field in required:
        if field not in manifest:
            raise ArenaError(f"missing manifest field: {field}")
    benchmark_id = _require_text(manifest["benchmark_id"], "benchmark_id")
    evaluator_id = _require_text(manifest["evaluator_id"], "evaluator_id")
    runtime_id = _require_text(manifest["runtime_id"], "runtime_id")
    if evaluator_id == runtime_id:
        raise ArenaError("evaluator and evaluated runtime must be distinct principals")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise ArenaError("cases must be a non-empty list")
    seen: set[str] = set()
    normalized = []
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ArenaError(f"case {index} must be an object")
        case_id = _require_text(case.get("case_id"), f"cases[{index}].case_id")
        if case_id in seen:
            raise ArenaError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        domain = _require_text(case.get("domain"), f"cases[{index}].domain")
        split = case.get("split")
        if split not in _ALLOWED_SPLITS:
            raise ArenaError(f"invalid split for {case_id}")
        prompt = _require_text(case.get("prompt"), f"cases[{index}].prompt")
        if "oracle" not in case:
            raise ArenaError(f"missing oracle for {case_id}")
        normalized.append({
            "case_id": case_id,
            "domain": domain,
            "split": split,
            "prompt": prompt,
            "oracle": case["oracle"],
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": benchmark_id,
        "evaluator_id": evaluator_id,
        "runtime_id": runtime_id,
        "cases": normalized,
    }


def seal_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a digest-locked manifest envelope; callers retain it outside policy access."""
    validated = validate_manifest(manifest)
    return {"manifest": validated, "manifest_digest": digest(validated)}


def _score(expected: Any, actual: Any) -> bool:
    # Exact structured equality is the intentionally conservative default oracle.
    return type(expected) is type(actual) and expected == actual


def run_campaign(
    sealed: Mapping[str, Any],
    policy: Callable[[Mapping[str, str]], Any],
    *,
    policy_digest: str,
    run_nonce: str,
) -> dict[str, Any]:
    """Run all sealed cases; policy sees task inputs only, never oracle labels.

    A unique nonce prevents accidental replay from being mistaken for a fresh run.
    For deterministic repeat checks compare result_digest, not receipt_digest.
    """
    _require_text(run_nonce, "run_nonce")
    _require_text(policy_digest, "policy_digest")
    if not isinstance(sealed, Mapping) or not isinstance(sealed.get("manifest"), Mapping):
        raise ArenaError("sealed envelope missing manifest")
    manifest = validate_manifest(sealed["manifest"])
    expected_manifest_digest = digest(manifest)
    if sealed.get("manifest_digest") != expected_manifest_digest:
        raise ArenaError("manifest digest mismatch")
    results = []
    for case in manifest["cases"]:
        # Deliberately construct a narrow projection: no oracle, split, manifest,
        # neighboring case, or expected answer crosses into the evaluated policy.
        policy_input = {"case_id": case["case_id"], "domain": case["domain"], "prompt": case["prompt"]}
        try:
            actual = policy(policy_input)
            passed = _score(case["oracle"], actual)
            outcome = "pass" if passed else "fail"
        except Exception as exc:  # evidence records failure, but not arbitrary exception text
            actual = None
            outcome = "error"
        results.append({
            "case_id": case["case_id"],
            "domain": case["domain"],
            "split": case["split"],
            "outcome": outcome,
            "output_digest": digest(actual),
        })
    counts = {key: sum(row["outcome"] == key for row in results) for key in ("pass", "fail", "error")}
    domain_scores: dict[str, dict[str, int]] = {}
    for row in results:
        stats = domain_scores.setdefault(row["domain"], {"total": 0, "passed": 0})
        stats["total"] += 1
        stats["passed"] += int(row["outcome"] == "pass")
    body = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": manifest["benchmark_id"],
        "manifest_digest": expected_manifest_digest,
        "evaluator_id": manifest["evaluator_id"],
        "runtime_id": manifest["runtime_id"],
        "policy_digest": policy_digest,
        "run_nonce": run_nonce,
        "results": results,
        "counts": counts,
        "domain_scores": domain_scores,
    }
    result_digest = digest(body)
    receipt = {
        **body,
        "result_digest": result_digest,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def verify_receipt(receipt: Mapping[str, Any], sealed: Mapping[str, Any]) -> bool:
    """Verify manifest binding, result digest and receipt integrity; reject malformed evidence."""
    if not isinstance(receipt, Mapping) or not isinstance(sealed, Mapping):
        return False
    try:
        manifest = validate_manifest(sealed["manifest"])
        if sealed.get("manifest_digest") != digest(manifest):
            return False
        if receipt.get("manifest_digest") != sealed["manifest_digest"]:
            return False
        if receipt.get("benchmark_id") != manifest["benchmark_id"]:
            return False
        if receipt.get("evaluator_id") != manifest["evaluator_id"] or receipt.get("runtime_id") != manifest["runtime_id"]:
            return False
        if receipt.get("schema_version") != SCHEMA_VERSION:
            return False
        if not isinstance(receipt.get("results"), list) or len(receipt["results"]) != len(manifest["cases"]):
            return False
        if not isinstance(receipt.get("issued_at"), str) or not receipt["issued_at"]:
            return False
        receipt_body = dict(receipt)
        claimed_receipt_digest = receipt_body.pop("receipt_digest", None)
        if claimed_receipt_digest != digest(receipt_body):
            return False
        body = {key: receipt[key] for key in (
            "schema_version", "benchmark_id", "manifest_digest", "evaluator_id",
            "runtime_id", "policy_digest", "run_nonce", "results", "counts", "domain_scores"
        )}
        return receipt.get("result_digest") == digest(body)
    except (ArenaError, KeyError, TypeError, ValueError):
        return False
