#!/usr/bin/env python3
"""Evidence-driven candidate generation and scoring for self-improving AUREN."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from typing import Iterable, Sequence
try:
    from .experience_store import Experience
except ImportError:
    from experience_store import Experience

VERSION = "2.0"

@dataclass(frozen=True, slots=True)
class Observation:
    task_id: str; task_class: str; strategy: str; success: bool; accepted: bool; verification_passed: bool
    retries: int = 0; regressions: int = 0; cost: float = 0.0; latency_ms: float = 0.0
    safety_passed: bool = True; evidence_score: float = 1.0; environment_fingerprint: str = ""
    policy_id: str = ""; transfer_key: str = ""; failure_class: str = ""; timestamp: int = 0

@dataclass(frozen=True, slots=True)
class CandidateScore:
    policy_id: str; task_class: str; strategy: str; score: float; lower_bound: float; quality: float
    sample_count: int; regression_rate: float; retry_rate: float; efficiency: float
    improvement_over_incumbent: float; eligible: bool; reasons: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class PolicyCandidate:
    policy_id: str; task_class: str; strategy: str; reason: str; evidence: tuple[str, ...]; confidence: float
    risk: str = "medium"; score: float = 0.0; lower_bound: float = 0.0; sample_count: int = 0
    incumbent_policy_id: str = ""; improvement: float = 0.0; evidence_hash: str = ""

def _id(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "policy-" + sha256(raw.encode()).hexdigest()[:16]

def _wilson_lower(successes: float, n: int, z: float = 1.96) -> float:
    if n <= 0: return 0.0
    p = max(0.0, min(1.0, successes / n)); denominator = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n); margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denominator)

def _quality(rows: Sequence[Observation]) -> float:
    if not rows: return 0.0
    return sum(0.35 * float(r.success) + 0.25 * float(r.accepted) + 0.25 * float(r.verification_passed) + 0.10 * float(r.regressions == 0) + 0.05 * float(r.safety_passed) for r in rows) / len(rows)

def _efficiency(rows: Sequence[Observation]) -> float:
    if not rows: return 0.0
    retry_penalty = min(1.0, sum(max(0, r.retries) for r in rows) / max(1, len(rows) * 4)); costs = [max(0.0, r.cost) for r in rows if r.cost > 0]; cost_penalty = 0.0
    if costs:
        median = sorted(costs)[len(costs) // 2]; cost_penalty = min(1.0, sum(costs) / len(costs) / max(1.0, median * 4.0))
    return max(0.0, 1.0 - 0.65 * retry_penalty - 0.35 * cost_penalty)

def _coerce(row: Observation | Experience) -> Observation:
    if isinstance(row, Observation): return row
    return Observation(row.task_id, row.task_class, row.strategy, row.success, row.accepted, row.verification_passed, row.retries, row.regressions, row.cost, row.latency_ms, row.safety_passed, row.evidence_score, row.environment_fingerprint, row.policy_id, row.transfer_key, row.failure_class, row.timestamp)

def score_candidates(observations: Iterable[Observation | Experience], *, task_class: str | None = None, incumbent: tuple[str, str, float] | None = None, min_samples: int = 5, min_lower_bound: float = 0.70, min_improvement: float = 0.03) -> list[CandidateScore]:
    rows = [_coerce(x) for x in observations]
    if task_class: rows = [x for x in rows if x.task_class == task_class]
    groups: dict[tuple[str, str], list[Observation]] = {}
    for row in rows: groups.setdefault((row.task_class, row.strategy), []).append(row)
    incumbent_score = incumbent[2] if incumbent else 0.0; result: list[CandidateScore] = []
    for (family, strategy), group in groups.items():
        quality = _quality(group); successes = sum(r.success and r.accepted and r.verification_passed and r.regressions == 0 and r.safety_passed for r in group); lower = _wilson_lower(successes, len(group)); regression_rate = sum(r.regressions > 0 for r in group) / len(group); retry_rate = sum(r.retries > 0 for r in group) / len(group); efficiency = _efficiency(group)
        score = 0.55 * quality + 0.20 * lower + 0.15 * efficiency + 0.07 * (1.0 - regression_rate) + 0.03 * (1.0 - retry_rate); improvement = score - incumbent_score; reasons: list[str] = []
        if len(group) < min_samples: reasons.append(f"needs {min_samples - len(group)} more samples")
        if lower < min_lower_bound: reasons.append("confidence lower bound below gate")
        if improvement < min_improvement: reasons.append("improvement below gate")
        if regression_rate > 0: reasons.append("contains regression evidence")
        result.append(CandidateScore(_id({"task_class": family, "strategy": strategy, "evidence": sorted(r.task_id for r in group)}), family, strategy, round(score, 6), round(lower, 6), round(quality, 6), len(group), round(regression_rate, 6), round(retry_rate, 6), round(efficiency, 6), round(improvement, 6), not reasons, tuple(reasons)))
    return sorted(result, key=lambda x: (x.eligible, x.score, x.lower_bound, x.sample_count, x.policy_id), reverse=True)

def learn(observations: Iterable[Observation | Experience], min_samples: int = 3, *, incumbent: tuple[str, str, float] | None = None, min_lower_bound: float = 0.0, min_improvement: float = 0.03) -> list[PolicyCandidate]:
    rows = [_coerce(x) for x in observations]; scored = score_candidates(rows, incumbent=incumbent, min_samples=min_samples, min_lower_bound=min_lower_bound, min_improvement=min_improvement); candidates: list[PolicyCandidate] = []
    for item in scored:
        if not item.eligible: continue
        evidence_rows = [r for r in rows if r.task_class == item.task_class and r.strategy == item.strategy]; evidence = tuple(r.task_id for r in sorted(evidence_rows, key=lambda r: (r.timestamp, r.task_id), reverse=True)[:50]); evidence_hash = sha256("|".join(evidence).encode()).hexdigest()[:20]
        confidence = min(0.99, max(item.lower_bound, 0.75 * item.quality + 0.25 * item.lower_bound)); risk = "low" if item.regression_rate == 0 and item.lower_bound >= 0.82 else "medium"
        candidates.append(PolicyCandidate(item.policy_id, item.task_class, item.strategy, f"{item.sample_count} observations; quality={item.quality:.0%}, Wilson lower bound={item.lower_bound:.0%}, score={item.score:.3f}.", evidence, round(confidence, 4), risk, item.score, item.lower_bound, item.sample_count, incumbent[0] if incumbent else "", item.improvement_over_incumbent, evidence_hash))
    return sorted(candidates, key=lambda x: (x.score, x.confidence, x.policy_id), reverse=True)

def candidate_record(candidate: PolicyCandidate) -> dict[str, object]:
    return {"type": "policy_candidate", "version": VERSION, **asdict(candidate)}
