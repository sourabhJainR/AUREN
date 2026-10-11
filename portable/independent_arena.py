"""Minimal executable kernel for independently supplied Arena evaluations.

The policy receives only the prompt; case IDs, domain labels, split assignments, and oracle labels stay in evaluator-owned state. This protocol boundary is not an OS sandbox. Independent campaigns must run
the evaluated runtime and evaluator under separately controlled principals/processes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .sealed_arena_replay import SealedArenaReplayLedger

SCHEMA_VERSION = "arena-run-v1"
_ALLOWED_SPLITS = {"development", "evaluation", "sealed_holdout"}


class ArenaError(ValueError):
    """Raised when benchmark input or evaluation evidence is invalid."""


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArenaError("value is not canonical JSON data") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArenaError(f"{field} must be a non-empty string")
    return value




NonceLedger = SealedArenaReplayLedger


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and copy a benchmark manifest; fail closed on ambiguous input."""
    if not isinstance(manifest, Mapping):
        raise ArenaError("manifest must be an object")
    for field in ("benchmark_id", "evaluator_id", "runtime_id", "cases"):
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
        if not isinstance(split, str) or split not in _ALLOWED_SPLITS:
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
    normalized_manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": benchmark_id,
        "evaluator_id": evaluator_id,
        "runtime_id": runtime_id,
        "cases": normalized,
    }
    canonical_json(normalized_manifest)
    return normalized_manifest


def seal_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a digest-locked envelope; the caller retains it outside policy access."""
    validated = validate_manifest(manifest)
    return {"manifest": validated, "manifest_digest": digest(validated)}


def _score(expected: Any, actual: Any) -> bool:
    # Exact structured equality is the conservative default oracle.
    return type(expected) is type(actual) and expected == actual


def _output_digest(value: Any) -> str:
    try:
        return digest(value)
    except ArenaError:
        # Do not serialize repr(value), which can leak secrets or memory addresses.
        return digest({"unserializable_output_type": type(value).__name__})


def run_campaign(
    sealed: Mapping[str, Any],
    policy: Callable[[Mapping[str, str]], Any],
    *,
    policy_digest: str,
    run_nonce: str,
    nonce_ledger: NonceLedger,
) -> dict[str, Any]:
    """Run sealed cases; policy sees only the task prompt, never case IDs, domain labels, split assignments, neighboring cases, or oracle labels.

    A unique nonce distinguishes fresh runs. Compare result_digest for deterministic
    repeatability; receipt_digest also binds the issuance timestamp.
    """
    _require_text(run_nonce, "run_nonce")
    _require_text(policy_digest, "policy_digest")
    if not isinstance(sealed, Mapping) or not isinstance(sealed.get("manifest"), Mapping):
        raise ArenaError("sealed envelope missing manifest")
    manifest = validate_manifest(sealed["manifest"])
    expected_manifest_digest = digest(manifest)
    if sealed.get("manifest_digest") != expected_manifest_digest:
        raise ArenaError("manifest digest mismatch")
    if not isinstance(nonce_ledger, NonceLedger):
        raise ArenaError("a persistent NonceLedger is required for replay protection")
    nonce_ledger.claim(
        run_nonce=run_nonce,
        campaign_digest=manifest["benchmark_id"],
        corpus_digest=expected_manifest_digest,
    )
    results = []
    for case in manifest["cases"]:
        # No oracle, split, manifest, neighboring case, or expected answer crosses
        # the policy interface.
        policy_input = {"prompt": case["prompt"]}
        try:
            actual = policy(policy_input)
            outcome = "pass" if _score(case["oracle"], actual) else "fail"
        except Exception:
            actual = None
            outcome = "error"
        results.append({
            "case_id": case["case_id"],
            "domain": case["domain"],
            "split": case["split"],
            "outcome": outcome,
            "output_digest": _output_digest(actual),
        })
    counts = {
        key: sum(row["outcome"] == key for row in results)
        for key in ("pass", "fail", "error")
    }
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
    # Result identity excludes the unique run nonce and issuance time. This lets
    # independent fresh runs compare deterministic outcomes while each receipt
    # remains unique and replay-protected.
    result_body = {
        key: body[key]
        for key in (
            "schema_version", "benchmark_id", "manifest_digest", "evaluator_id",
            "runtime_id", "policy_digest", "results", "counts", "domain_scores",
        )
    }
    receipt = {
        **body,
        "result_digest": digest(result_body),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def verify_receipt(receipt: Mapping[str, Any], sealed: Mapping[str, Any]) -> bool:
    """Verify receipt integrity *and* semantic consistency with the sealed manifest.

    Digest checks alone are not sufficient: a caller can recompute an unkeyed
    digest after changing fields. Re-derive case identity, outcomes, counts, and
    domain aggregates from the evaluator-owned manifest before accepting evidence.
    """
    if not isinstance(receipt, Mapping) or not isinstance(sealed, Mapping):
        return False
    try:
        expected_fields = {
            "schema_version", "benchmark_id", "manifest_digest", "evaluator_id",
            "runtime_id", "policy_digest", "run_nonce", "results", "counts",
            "domain_scores", "result_digest", "issued_at", "receipt_digest",
        }
        # Closed-world receipt schema prevents downstream consumers from silently
        # accepting unrecognized fields with unclear trust or promotion semantics.
        if set(receipt) != expected_fields:
            return False
        manifest = validate_manifest(sealed["manifest"])
        expected_manifest_digest = digest(manifest)
        if sealed.get("manifest_digest") != expected_manifest_digest:
            return False
        if receipt.get("manifest_digest") != expected_manifest_digest:
            return False
        if receipt.get("benchmark_id") != manifest["benchmark_id"]:
            return False
        if receipt.get("evaluator_id") != manifest["evaluator_id"] or receipt.get("runtime_id") != manifest["runtime_id"]:
            return False
        if receipt.get("schema_version") != SCHEMA_VERSION:
            return False
        if not _require_text(receipt.get("policy_digest"), "policy_digest"):
            return False
        if not _require_text(receipt.get("run_nonce"), "run_nonce"):
            return False
        issued_at = receipt.get("issued_at")
        if not isinstance(issued_at, str) or not issued_at:
            return False
        parsed_issued_at = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        if parsed_issued_at.tzinfo is None or parsed_issued_at.utcoffset() is None:
            return False

        results = receipt.get("results")
        if not isinstance(results, list) or len(results) != len(manifest["cases"]):
            return False
        allowed_outcomes = {"pass", "fail", "error"}
        counts = {"pass": 0, "fail": 0, "error": 0}
        domain_scores: dict[str, dict[str, int]] = {}
        for expected_case, row in zip(manifest["cases"], results):
            if not isinstance(row, Mapping):
                return False
            if set(row) != {"case_id", "domain", "split", "outcome", "output_digest"}:
                return False
            if any(row.get(key) != expected_case[key] for key in ("case_id", "domain", "split")):
                return False
            outcome = row.get("outcome")
            if outcome not in allowed_outcomes:
                return False
            output_digest = row.get("output_digest")
            if not isinstance(output_digest, str) or len(output_digest) != 64 or any(c not in "0123456789abcdef" for c in output_digest):
                return False
            counts[outcome] += 1
            stats = domain_scores.setdefault(expected_case["domain"], {"total": 0, "passed": 0})
            stats["total"] += 1
            stats["passed"] += int(outcome == "pass")

        if receipt.get("counts") != counts or receipt.get("domain_scores") != domain_scores:
            return False
        receipt_body = dict(receipt)
        claimed_receipt_digest = receipt_body.pop("receipt_digest", None)
        if not isinstance(claimed_receipt_digest, str) or claimed_receipt_digest != digest(receipt_body):
            return False
        result_body = {
            key: receipt[key]
            for key in (
                "schema_version", "benchmark_id", "manifest_digest", "evaluator_id",
                "runtime_id", "policy_digest", "results", "counts", "domain_scores",
            )
        }
        return receipt.get("result_digest") == digest(result_body)
    except (ArenaError, KeyError, TypeError, ValueError, OverflowError):
        return False
