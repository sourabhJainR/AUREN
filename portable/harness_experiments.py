"""Evidence-gated, bounded harness experiments (AUREN-native; no external runtime dependency).

This module coordinates candidate generation and records probe/evaluator receipts. It
is not itself a sandbox: probes must be delegated to a host that enforces isolation,
and the controller fails closed when its receipt does not attest to that enforcement.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Callable, Mapping, Sequence

from .runtime_recovery import ExecutionBudgetExceeded, RuntimeExecutionGuard


def canonical_digest(value: Any) -> str:
    """Return a stable digest for JSON-compatible experiment artifacts."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("experiment artifacts must be finite JSON-compatible values") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _finite_score(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class HarnessExperimentManifest:
    schema_version: str
    run_id: str
    task_digest: str
    base_harness_digest: str
    executor_id: str
    evaluator_id: str
    seed: int
    max_candidates: int
    max_depth: int
    isolation_profile: str
    policy_digest: str
    environment_digest: str

    def __post_init__(self) -> None:
        for name in ("run_id", "task_digest", "base_harness_digest", "executor_id",
                     "evaluator_id", "isolation_profile", "policy_digest", "environment_digest"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.schema_version != "auren-harness-experiment-v1":
            raise ValueError("unsupported experiment manifest schema")
        if self.executor_id == self.evaluator_id:
            raise ValueError("executor and evaluator must be distinct identities")
        if type(self.seed) is not int:
            raise ValueError("seed must be an integer")
        if type(self.max_candidates) is not int or not 1 <= self.max_candidates <= 16:
            raise ValueError("max_candidates must be between 1 and 16")
        if type(self.max_depth) is not int or not 0 <= self.max_depth <= 4:
            raise ValueError("max_depth must be between 0 and 4")

    @property
    def digest(self) -> str:
        return canonical_digest({
            "schema_version": self.schema_version, "run_id": self.run_id,
            "task_digest": self.task_digest, "base_harness_digest": self.base_harness_digest,
            "executor_id": self.executor_id, "evaluator_id": self.evaluator_id,
            "seed": self.seed, "max_candidates": self.max_candidates, "max_depth": self.max_depth,
            "isolation_profile": self.isolation_profile, "policy_digest": self.policy_digest,
            "environment_digest": self.environment_digest,
        })


@dataclass(frozen=True, slots=True)
class HarnessExperimentResult:
    status: str
    manifest_digest: str
    base_harness_digest: str
    candidate_digests: tuple[str, ...] = ()
    adopted_digest: str | None = None
    evidence: tuple[Mapping[str, Any], ...] = ()
    reasons: tuple[str, ...] = ()
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None

    @property
    def success(self) -> bool:
        return self.status == "adopted" and self.adopted_digest is not None


class HarnessExperimentController:
    """Bounded Propose–Probe–Compare–Compose with evidence-gated adoption.

    Callback contracts:
      propose(base_copy, task, depth, seed) -> sequence of candidate harness mappings
      probe(candidate_copy, task, manifest_mapping, stage) -> receipt mapping
      judge(candidate_copy, probe_receipt, manifest_mapping) -> evaluator receipt mapping
      adopt(candidate_copy, expected_base_digest) -> {"atomic": True, "active_digest": ...}
      rollback(expected_base_digest) -> {"restored": True, "active_digest": ...}

    The probe receipt must attest to isolation, egress restriction and no inherited
    secrets. These are assertions from a trusted host adapter, not facts this Python
    coordinator can establish on its own. The judge must be separate from the
    candidate executor and must provide scored evidence. The controller never
    mutates the caller's active harness mapping.
    """

    def __init__(
        self,
        *,
        guard: RuntimeExecutionGuard,
        persist: Callable[[Mapping[str, Any]], None],
        verify_isolation_receipt: Callable[[Mapping[str, Any], HarnessExperimentManifest, str], bool] | None = None,
        max_candidates: int = 4,
        max_depth: int = 1,
        minimum_quality_delta: float = 0.01,
        maximum_cost_ratio: float = 1.25,
        maximum_latency_ratio: float = 1.5,
    ) -> None:
        if type(max_candidates) is not int or not 1 <= max_candidates <= 16:
            raise ValueError("max_candidates must be between 1 and 16")
        if type(max_depth) is not int or not 0 <= max_depth <= 4:
            raise ValueError("max_depth must be between 0 and 4")
        self.guard = guard
        self.persist = persist
        # No verifier means no trusted isolation boundary; fail closed at baseline.
        self.verify_isolation_receipt = verify_isolation_receipt
        self.max_candidates = max_candidates
        self.max_depth = max_depth
        self.minimum_quality_delta = _finite_score(minimum_quality_delta, "minimum_quality_delta")
        self.maximum_cost_ratio = _finite_score(maximum_cost_ratio, "maximum_cost_ratio")
        self.maximum_latency_ratio = _finite_score(maximum_latency_ratio, "maximum_latency_ratio")
        if self.minimum_quality_delta < 0 or self.maximum_cost_ratio < 1 or self.maximum_latency_ratio < 1:
            raise ValueError("invalid adoption thresholds")

    @staticmethod
    def _copy_json(value: Mapping[str, Any]) -> dict[str, Any]:
        # Round-trip produces an independent tree and rejects executable/live objects.
        try:
            copied = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("harness candidates must be JSON-compatible declarative data") from exc
        if not isinstance(copied, dict):
            raise ValueError("harness must be a mapping")
        return copied

    @staticmethod
    def _receipt(receipt: Any, *, kind: str) -> dict[str, Any]:
        if not isinstance(receipt, Mapping):
            raise ValueError(f"{kind} must return a structured receipt")
        try:
            return json.loads(json.dumps(dict(receipt), sort_keys=True, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{kind} receipt must be finite JSON-compatible data") from exc

    def run(
        self,
        *,
        manifest: HarnessExperimentManifest,
        active_harness: Mapping[str, Any],
        task: str,
        propose: Callable[[Mapping[str, Any], str, int, int], Sequence[Mapping[str, Any]]],
        probe: Callable[[Mapping[str, Any], str, Mapping[str, Any], str], Mapping[str, Any]],
        judge: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        adopt: Callable[[Mapping[str, Any], str], Mapping[str, Any]],
        rollback: Callable[[str], Mapping[str, Any]],
    ) -> HarnessExperimentResult:
        if not isinstance(manifest, HarnessExperimentManifest):
            raise TypeError("manifest must be a HarnessExperimentManifest")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be non-empty text")
        base = self._copy_json(active_harness)
        base_digest = canonical_digest(base)
        if base_digest != manifest.base_harness_digest:
            raise ValueError("manifest base harness digest does not match active harness")
        if manifest.max_candidates > self.max_candidates or manifest.max_depth > self.max_depth:
            raise ValueError("manifest exceeds controller candidate/depth policy")
        task_digest = canonical_digest({"task": task})
        if task_digest != manifest.task_digest:
            raise ValueError("manifest task digest does not match task")
        manifest_payload = {
            "schema_version": manifest.schema_version, "run_id": manifest.run_id,
            "manifest_digest": manifest.digest, "task_digest": task_digest,
            "base_harness_digest": base_digest, "executor_id": manifest.executor_id,
            "evaluator_id": manifest.evaluator_id, "seed": manifest.seed,
            "isolation_profile": manifest.isolation_profile, "policy_digest": manifest.policy_digest,
            "environment_digest": manifest.environment_digest,
        }
        evidence: list[Mapping[str, Any]] = []
        candidate_digests: list[str] = []
        adopted_digest: str | None = None
        rollback_attempted = False
        rollback_succeeded: bool | None = None
        adoption_attempted = False

        def charge_once(operation_key: str) -> None:
            if not self.guard.charge_attempt(operation_key):
                raise ExecutionBudgetExceeded(
                    "experiment operation was already charged; replay is refused until its durable receipt is restored"
                )

        def checkpoint(stage: str, *, status: str, details: Mapping[str, Any] | None = None) -> None:
            snapshot = {
                "schema_version": "auren-harness-experiment-checkpoint-v1",
                "run_id": manifest.run_id, "manifest_digest": manifest.digest,
                "base_harness_digest": base_digest, "stage": stage, "status": status,
                "candidate_digests": list(candidate_digests),
                "evidence": list(evidence), "details": dict(details or {}),
            }
            snapshot["digest"] = canonical_digest(snapshot)
            self.persist(snapshot)

        def result(status: str, *reasons: str) -> HarnessExperimentResult:
            return HarnessExperimentResult(
                status, manifest.digest, base_digest, tuple(candidate_digests), adopted_digest,
                tuple(evidence), tuple(reasons), rollback_attempted, rollback_succeeded,
            )

        def rollback_after_failed_adoption() -> None:
            nonlocal rollback_attempted, rollback_succeeded, adopted_digest
            if not adoption_attempted:
                return
            rollback_attempted = True
            try:
                receipt = self._receipt(rollback(base_digest), kind="rollback")
                rollback_succeeded = receipt.get("restored") is True and receipt.get("active_digest") == base_digest
                evidence.append({"kind": "rollback", "receipt": receipt})
                if rollback_succeeded:
                    adopted_digest = None
            except Exception as rollback_exc:
                rollback_succeeded = False
                evidence.append({"kind": "rollback_error", "error": f"{type(rollback_exc).__name__}: {rollback_exc}"})

        try:
            self.guard.check()
            self.guard.charge_step()
            checkpoint("prepare", status="in_progress")

            # Probe and judge the baseline in the same configured environment first.
            self.guard.check()
            charge_once(f"harness:{manifest.run_id}:baseline:probe")
            checkpoint("baseline_probe", status="in_progress")
            baseline = self._copy_json(base)
            baseline_probe = self._receipt(probe(baseline, task, manifest_payload, "baseline"), kind="probe")
            evidence.append({"kind": "baseline_probe", "receipt": baseline_probe})
            if not self._verified_isolation_receipt(baseline_probe, manifest, base_digest):
                checkpoint("blocked", status="blocked", details={"reason": "baseline isolation evidence missing"})
                return result("blocked", "baseline isolation evidence missing")
            self.guard.check()
            charge_once(f"harness:{manifest.run_id}:baseline:judge")
            checkpoint("baseline_judge", status="in_progress")
            baseline_judge = self._receipt(judge(baseline, baseline_probe, manifest_payload), kind="judge")
            evidence.append({"kind": "baseline_judge", "receipt": baseline_judge})
            if not self._judge_receipt(baseline_judge, manifest, base_digest):
                checkpoint("blocked", status="blocked", details={"reason": "baseline evaluator evidence missing"})
                return result("blocked", "baseline evaluator evidence missing")

            best: tuple[float, dict[str, Any], str, Mapping[str, Any], Mapping[str, Any]] | None = None
            proposal_count = 0
            for depth in range(manifest.max_depth + 1):
                self.guard.check()
                checkpoint("propose", status="in_progress", details={"depth": depth})
                proposal_base = best[1] if best is not None else base
                parent_digest = best[2] if best is not None else base_digest
                proposed = propose(self._copy_json(proposal_base), task, depth, manifest.seed + depth)
                if not isinstance(proposed, Sequence) or isinstance(proposed, (str, bytes)):
                    raise ValueError("propose must return a sequence of declarative candidates")
                proposal_count += len(proposed)
                if proposal_count > manifest.max_candidates:
                    raise ValueError("recursive proposer exceeded the total candidate cap")
                if not proposed:
                    evidence.append({"kind": "proposal_round", "depth": depth, "status": "no_candidates"})
                    continue
                for index, candidate_value in enumerate(proposed):
                    self.guard.check()
                    self.guard.charge_step()
                    candidate = self._copy_json(candidate_value)
                    digest = canonical_digest(candidate)
                    if digest == base_digest or digest in candidate_digests:
                        evidence.append({"kind": "candidate_rejected", "depth": depth, "index": index, "digest": digest,
                                         "reason": "unchanged or duplicate candidate"})
                        continue
                    candidate_digests.append(digest)
                    charge_once(f"harness:{manifest.run_id}:candidate:{digest}:probe")
                    # Persist the shared guard charge before the executor is allowed to run.
                    checkpoint("candidate_probe", status="in_progress", details={
                        "depth": depth, "parent_digest": parent_digest, "candidate_index": index, "candidate_digest": digest
                    })
                    probe_receipt = self._receipt(
                        probe(self._copy_json(candidate), task, manifest_payload, "candidate"), kind="probe"
                    )
                    evidence.append({"kind": "candidate_probe", "depth": depth, "parent_digest": parent_digest, "digest": digest, "receipt": probe_receipt})
                    if not self._verified_isolation_receipt(probe_receipt, manifest, digest):
                        evidence.append({"kind": "candidate_rejected", "digest": digest, "reason": "isolation receipt invalid"})
                        checkpoint("candidate_rejected", status="rejected", details={
                            "candidate_digest": digest, "reason": "isolation receipt invalid"
                        })
                        continue
                    self.guard.check()
                    charge_once(f"harness:{manifest.run_id}:candidate:{digest}:judge")
                    # The durable checkpoint must include this charge before judging.
                    checkpoint("candidate_judge", status="in_progress", details={
                        "depth": depth, "parent_digest": parent_digest, "candidate_digest": digest
                    })
                    judge_receipt = self._receipt(
                        judge(self._copy_json(candidate), probe_receipt, manifest_payload), kind="judge"
                    )
                    evidence.append({"kind": "candidate_judge", "depth": depth, "parent_digest": parent_digest, "digest": digest, "receipt": judge_receipt})
                    if not self._judge_receipt(judge_receipt, manifest, digest):
                        evidence.append({"kind": "candidate_rejected", "digest": digest, "reason": "evaluator receipt invalid"})
                        continue
                    if not self._improves(baseline_judge, judge_receipt, manifest, digest):
                        evidence.append({"kind": "candidate_rejected", "digest": digest, "reason": "no safe net improvement"})
                        continue
                    score = float(judge_receipt["quality_score"])
                    if best is None or score > best[0]:
                        best = (score, candidate, digest, probe_receipt, judge_receipt)

            if best is None:
                checkpoint("complete", status="rejected", details={"reason": "no candidate passed evidence and improvement gates"})
                return result("rejected", "no candidate passed evidence and improvement gates")

            self.guard.check()
            checkpoint("compose", status="ready_to_adopt", details={"candidate_digest": best[2]})
            adoption_attempted = True
            adoption = self._receipt(adopt(self._copy_json(best[1]), base_digest), kind="adopt")
            if adoption.get("atomic") is not True or adoption.get("active_digest") != best[2]:
                raise RuntimeError("adoption receipt did not confirm atomic activation of the selected candidate")
            adopted_digest = best[2]
            evidence.append({"kind": "adoption", "receipt": adoption})
            checkpoint("complete", status="adopted", details={"adopted_digest": adopted_digest})
            return result("adopted")
        except (ExecutionBudgetExceeded, TimeoutError) as exc:
            rollback_after_failed_adoption()
            try:
                checkpoint("stopped", status="unscored", details={"reason": f"{type(exc).__name__}: {exc}"})
            except Exception:
                pass
            return result("unscored", f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            # If the adoption adapter may have changed active state before failing,
            # restore the exact pre-experiment digest or report rollback failure.
            rollback_after_failed_adoption()
            try:
                checkpoint("failed", status="failed", details={"reason": f"{type(exc).__name__}: {exc}"})
            except Exception:
                # A failed durable checkpoint means no success may be reported.
                pass
            return result("failed", f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _isolation_receipt(
        receipt: Mapping[str, Any], manifest: HarnessExperimentManifest, harness_digest: str
    ) -> bool:
        return (
            receipt.get("schema_version") == "auren-isolation-receipt-v1"
            and receipt.get("manifest_digest") == manifest.digest
            and receipt.get("isolation_profile") == manifest.isolation_profile
            and receipt.get("task_digest") == manifest.task_digest
            and receipt.get("harness_digest") == harness_digest
            and receipt.get("isolation_enforced") is True
            and receipt.get("egress_restricted") is True
            and receipt.get("secrets_absent") is True
            and isinstance(receipt.get("workspace_digest"), str)
            and len(receipt["workspace_digest"]) == 64
            and all(character in "0123456789abcdef" for character in receipt["workspace_digest"].lower())
        )

    def _verified_isolation_receipt(
        self, receipt: Mapping[str, Any], manifest: HarnessExperimentManifest, harness_digest: str
    ) -> bool:
        # Boolean fields in a probe receipt are claims, not proof. Require an injected
        # verifier configured with an out-of-band signer/key trust root. Never infer
        # independent isolation from the executor's own JSON.
        if not self._isolation_receipt(receipt, manifest, harness_digest):
            return False
        verifier = self.verify_isolation_receipt
        if not callable(verifier):
            return False
        try:
            return verifier(receipt, manifest, harness_digest) is True
        except Exception:
            return False

    @staticmethod
    def _judge_receipt(
        receipt: Mapping[str, Any], manifest: HarnessExperimentManifest, harness_digest: str
    ) -> bool:
        if (
            receipt.get("schema_version") != "auren-evaluation-receipt-v1"
            or receipt.get("manifest_digest") != manifest.digest
            or receipt.get("evaluator_id") != manifest.evaluator_id
            or receipt.get("task_digest") != manifest.task_digest
            or receipt.get("harness_digest") != harness_digest
            or receipt.get("independent_of_executor") is not True
            or receipt.get("scored") is not True
            or receipt.get("tests_passed") is not True
            or receipt.get("security_passed") is not True
            or receipt.get("reliability_passed") is not True
            or not isinstance(receipt.get("evidence_refs"), list)
            or not receipt["evidence_refs"]
            or any(not isinstance(ref, str) or not ref.strip() for ref in receipt["evidence_refs"])
        ):
            return False
        try:
            for key in ("quality_score", "safety_score", "reliability_score", "cost", "latency_ms"):
                if _finite_score(receipt.get(key), key) < 0:
                    return False
        except ValueError:
            return False
        return True

    def _improves(
        self,
        baseline: Mapping[str, Any],
        candidate: Mapping[str, Any],
        manifest: HarnessExperimentManifest,
        candidate_digest: str,
    ) -> bool:
        if not self._judge_receipt(baseline, manifest, manifest.base_harness_digest) or not self._judge_receipt(candidate, manifest, candidate_digest):
            # Missing or incompatible evidence is never a positive promotion signal.
            return False
        return (
            float(candidate["quality_score"]) >= float(baseline["quality_score"]) + self.minimum_quality_delta
            and float(candidate["safety_score"]) >= float(baseline["safety_score"])
            and float(candidate["reliability_score"]) >= float(baseline["reliability_score"])
            and float(candidate["cost"]) <= max(0.001, float(baseline["cost"])) * self.maximum_cost_ratio
            and float(candidate["latency_ms"]) <= max(1.0, float(baseline["latency_ms"])) * self.maximum_latency_ratio
        )
