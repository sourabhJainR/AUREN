"""Minimal executable kernel for independently supplied Arena evaluations.

The policy receives task-facing fields only; oracle labels stay in the evaluator-owned
manifest. This protocol boundary is not an OS sandbox. Independent campaigns must run
the evaluated runtime and evaluator under separately controlled principals/processes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

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



class NonceLedger:
    """SQLite-backed atomic replay ledger; use one shared path across evaluator restarts."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ArenaError("NonceLedger requires a persistent database path")
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database_path, timeout=10) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS arena_nonce_claims (
                    nonce TEXT PRIMARY KEY,
                    benchmark_id TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                )"""
            )

    def claim(self, nonce: str, benchmark_id: str, manifest_digest: str, policy_digest: str) -> None:
        """Atomically claim a nonce before execution; duplicates fail closed."""
        _require_text(nonce, "run_nonce")
        try:
            with sqlite3.connect(self.database_path, timeout=10) as connection:
                connection.execute(
                    "INSERT INTO arena_nonce_claims VALUES (?, ?, ?, ?, ?)",
                    (nonce, benchmark_id, manifest_digest, policy_digest, datetime.now(timezone.utc).isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise ArenaError("run_nonce has already been used") from exc


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
    """Run sealed cases; policy sees task inputs only, never oracle labels.

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
    nonce_ledger.claim(run_nonce, manifest["benchmark_id"], expected_manifest_digest, policy_digest)
    results = []
    for case in manifest["cases"]:
        # No oracle, split, manifest, neighboring case, or expected answer crosses
        # the policy interface.
        policy_input = {
            "case_id": case["case_id"],
            "domain": case["domain"],
            "prompt": case["prompt"],
        }
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
    receipt = {
        **body,
        "result_digest": digest(body),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt["receipt_digest"] = digest(receipt)
    return receipt


def verify_receipt(receipt: Mapping[str, Any], sealed: Mapping[str, Any]) -> bool:
    """Verify manifest binding and receipt integrity; reject malformed evidence."""
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
        if (
            receipt.get("evaluator_id") != manifest["evaluator_id"]
            or receipt.get("runtime_id") != manifest["runtime_id"]
        ):
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
