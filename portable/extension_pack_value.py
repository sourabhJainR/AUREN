"""Evidence-backed extension-pack value gate.

A pack is never enabled by default from discovery or self-reported capability.
This module compares paired baseline/candidate runs and returns a decision only;
it does not change installation, trust, policy, or activation state.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    denominator = 1 + z2 / total
    center = p + z2 / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * total)) / total)
    return (center - margin) / denominator


def _validate_runs(
    runs: Sequence[Mapping[str, Any]], *, label: str, config_digest: str, corpus_digest: str
) -> dict[str, Mapping[str, Any]]:
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        raise ValueError(f"{label} runs must be a sequence")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in runs:
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} run must be a mapping")
        task_id = row.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in indexed:
            raise ValueError(f"{label} task IDs must be non-empty and unique")
        if row.get("config_digest") != config_digest or row.get("corpus_digest") != corpus_digest:
            raise ValueError(f"{label} run configuration or corpus digest mismatch")
        if type(row.get("success")) is not bool:
            raise ValueError(f"{label} success must be a boolean supplied by the evaluator")
        for key in ("input_tokens", "latency_ms", "safety_violations"):
            value = row.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{label} {key} must be a non-negative integer")
        ref = row.get("evidence_ref")
        if not isinstance(ref, str) or not ref.strip() or len(ref) > 512:
            raise ValueError(f"{label} run requires a bounded evidence reference")
        indexed[task_id] = row
    return indexed


def evaluate_extension_pack_value(
    *,
    baseline_runs: Sequence[Mapping[str, Any]],
    candidate_runs: Sequence[Mapping[str, Any]],
    baseline_digest: str,
    candidate_digest: str,
    corpus_digest: str,
    min_paired_runs: int = 20,
    min_success_uplift: float = 0.05,
    max_token_cost_ratio: float = 1.10,
    max_latency_ratio: float = 1.25,
    min_discordant_pairs: int = 5,
) -> dict[str, Any]:
    """Return a fail-closed default-enablement recommendation from paired evidence.

    A passing decision means measured task value met this policy for this exact
    corpus/configuration. It is not a trust grant and must not bypass Guard.
    """
    for name, digest in (
        ("baseline_digest", baseline_digest),
        ("candidate_digest", candidate_digest),
        ("corpus_digest", corpus_digest),
    ):
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    if baseline_digest == candidate_digest:
        raise ValueError("baseline and candidate configuration digests must differ")
    if type(min_paired_runs) is not int or min_paired_runs < 2:
        raise ValueError("min_paired_runs must be an integer >= 2")
    if type(min_discordant_pairs) is not int or min_discordant_pairs < 1:
        raise ValueError("min_discordant_pairs must be a positive integer")
    if not 0 <= min_success_uplift <= 1 or max_token_cost_ratio <= 0 or max_latency_ratio <= 0:
        raise ValueError("invalid value-gate thresholds")

    baseline = _validate_runs(
        baseline_runs, label="baseline", config_digest=baseline_digest, corpus_digest=corpus_digest
    )
    candidate = _validate_runs(
        candidate_runs, label="candidate", config_digest=candidate_digest, corpus_digest=corpus_digest
    )
    if set(baseline) != set(candidate):
        raise ValueError("baseline and candidate must cover exactly the same task IDs")
    task_ids = sorted(baseline)
    count = len(task_ids)
    baseline_successes = sum(bool(baseline[t]["success"]) for t in task_ids)
    candidate_successes = sum(bool(candidate[t]["success"]) for t in task_ids)
    baseline_rate = baseline_successes / count if count else 0.0
    candidate_rate = candidate_successes / count if count else 0.0
    wins = sum(not baseline[t]["success"] and candidate[t]["success"] for t in task_ids)
    losses = sum(bool(baseline[t]["success"]) and not candidate[t]["success"] for t in task_ids)
    discordant = wins + losses
    win_lower = _wilson_lower_bound(wins, discordant) if discordant else 0.0
    baseline_tokens = sum(baseline[t]["input_tokens"] for t in task_ids)
    candidate_tokens = sum(candidate[t]["input_tokens"] for t in task_ids)
    baseline_latency = sum(baseline[t]["latency_ms"] for t in task_ids)
    candidate_latency = sum(candidate[t]["latency_ms"] for t in task_ids)
    token_ratio = candidate_tokens / max(1, baseline_tokens)
    latency_ratio = candidate_latency / max(1, baseline_latency)
    safety_violations = sum(candidate[t]["safety_violations"] for t in task_ids)

    checks = {
        "minimum_sample": count >= min_paired_runs,
        "minimum_discordant_pairs": discordant >= min_discordant_pairs,
        "paired_win_confidence": win_lower > 0.5,
        "minimum_success_uplift": candidate_rate - baseline_rate >= min_success_uplift,
        "no_success_regression": candidate_rate >= baseline_rate,
        "no_candidate_safety_violations": safety_violations == 0,
        "token_cost_within_budget": token_ratio <= max_token_cost_ratio,
        "latency_within_budget": latency_ratio <= max_latency_ratio,
        "evidence_complete": count > 0 and all(
            bool(baseline[t]["evidence_ref"].strip()) and bool(candidate[t]["evidence_ref"].strip())
            for t in task_ids
        ),
    }
    return {
        "default_enable_recommended": all(checks.values()),
        "activation_performed": False,
        "trust_granted": False,
        "checks": checks,
        "sample_size": count,
        "discordant_pairs": discordant,
        "candidate_wins": wins,
        "candidate_losses": losses,
        "paired_win_wilson_95_lower_bound": round(win_lower, 6),
        "baseline_success_rate": round(baseline_rate, 6),
        "candidate_success_rate": round(candidate_rate, 6),
        "success_uplift": round(candidate_rate - baseline_rate, 6),
        "baseline_input_tokens": baseline_tokens,
        "candidate_input_tokens": candidate_tokens,
        "input_token_cost_ratio": round(token_ratio, 6),
        "baseline_latency_ms_total": baseline_latency,
        "candidate_latency_ms_total": candidate_latency,
        "latency_ratio": round(latency_ratio, 6),
        "candidate_safety_violations": safety_violations,
        "baseline_digest": baseline_digest,
        "candidate_digest": candidate_digest,
        "corpus_digest": corpus_digest,
        "task_ids": task_ids,
        "reason": (
            "all measured value gates passed; explicit Guard activation is still required"
            if all(checks.values())
            else "default enablement denied: " + ", ".join(name for name, passed in checks.items() if not passed)
        ),
    }


__all__ = ["evaluate_extension_pack_value"]
