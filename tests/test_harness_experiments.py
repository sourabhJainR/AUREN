"""Deterministic contract tests for evidence-gated harness experiments."""
from __future__ import annotations

import unittest

from portable.harness_experiments import (
    HarnessExperimentController,
    HarnessExperimentManifest,
    canonical_digest,
)
from portable.runtime_recovery import RuntimeExecutionGuard


class HarnessExperimentControllerTests(unittest.TestCase):
    def setUp(self):
        self.base = {"system_prompt": "stable", "tool_guidance": {"mode": "safe"}}
        self.task = "summarize public facts"
        self.base_digest = canonical_digest(self.base)
        self.manifest = HarnessExperimentManifest(
            schema_version="auren-harness-experiment-v1",
            run_id="test-run-1",
            task_digest=canonical_digest({"task": self.task}),
            base_harness_digest=self.base_digest,
            executor_id="isolated-fake-executor-v1",
            evaluator_id="separate-fake-judge-v1",
            seed=7,
            max_candidates=3,
            max_depth=1,
            isolation_profile="restricted-test-profile-v1",
            policy_digest="a" * 64,
            environment_digest="b" * 64,
        )
        self.checkpoints = []
        self.guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20)

    def controller(self, *, guard=None, persist=None):
        return HarnessExperimentController(
            guard=guard or self.guard,
            persist=persist or self.checkpoints.append,
            max_candidates=3,
            max_depth=1,
        )

    def probe(self, candidate, task, manifest, stage):
        return {
            "schema_version": "auren-isolation-receipt-v1",
            "manifest_digest": manifest["manifest_digest"],
            "task_digest": manifest["task_digest"],
            "harness_digest": canonical_digest(candidate),
            "isolation_profile": manifest["isolation_profile"],
            "isolation_enforced": True,
            "egress_restricted": True,
            "secrets_absent": True,
            "workspace_digest": canonical_digest({"candidate": candidate, "stage": stage}),
            "stage": stage,
        }

    def judge(self, candidate, probe_receipt, manifest):
        mode = candidate.get("tool_guidance", {}).get("mode")
        quality = 0.95 if mode == "ultra-concise" else 0.9 if mode == "concise" else 0.8
        return {
            "schema_version": "auren-evaluation-receipt-v1",
            "manifest_digest": manifest["manifest_digest"],
            "task_digest": manifest["task_digest"],
            "harness_digest": canonical_digest(candidate),
            "evaluator_id": manifest["evaluator_id"],
            "independent_of_executor": True,
            "scored": True,
            "tests_passed": True,
            "security_passed": True,
            "reliability_passed": True,
            "quality_score": quality,
            "safety_score": 1.0,
            "reliability_score": 1.0,
            "cost": 1.0,
            "latency_ms": 100.0,
            "evidence_refs": [canonical_digest({"candidate": candidate, "probe": probe_receipt})],
        }

    def run_experiment(self, **overrides):
        args = {
            "manifest": self.manifest,
            "active_harness": self.base,
            "task": self.task,
            "propose": lambda base, task, depth, seed: [
                {"system_prompt": "stable", "tool_guidance": {"mode": "concise"}}
            ],
            "probe": self.probe,
            "judge": self.judge,
            "adopt": lambda candidate, expected: {
                "atomic": True, "active_digest": canonical_digest(candidate)
            },
            "rollback": lambda expected: {"restored": True, "active_digest": expected},
        }
        args.update(overrides)
        return self.controller().run(**args)

    def test_manifest_rejects_shared_executor_and_evaluator_identity(self):
        with self.assertRaisesRegex(ValueError, "must be distinct"):
            HarnessExperimentManifest(
                schema_version="auren-harness-experiment-v1",
                run_id="invalid-run",
                task_digest=canonical_digest({"task": self.task}),
                base_harness_digest=self.base_digest,
                executor_id="same-identity",
                evaluator_id="same-identity",
                seed=1,
                max_candidates=1,
                max_depth=0,
                isolation_profile="restricted-test-profile-v1",
                policy_digest="a" * 64,
                environment_digest="b" * 64,
            )

    def test_safe_improvement_is_adopted_without_mutating_caller_harness(self):
        original = self.controller()
        result = original.run(
            manifest=self.manifest,
            active_harness=self.base,
            task=self.task,
            propose=lambda base, task, depth, seed: [
                {"system_prompt": "stable", "tool_guidance": {"mode": "concise"}}
            ],
            probe=self.probe,
            judge=self.judge,
            adopt=lambda candidate, expected: {
                "atomic": True, "active_digest": canonical_digest(candidate)
            },
            rollback=lambda expected: {"restored": True, "active_digest": expected},
        )
        self.assertEqual("adopted", result.status)
        self.assertTrue(result.success)
        self.assertEqual(self.base_digest, result.base_harness_digest)
        self.assertEqual({"system_prompt": "stable", "tool_guidance": {"mode": "safe"}}, self.base)
        self.assertTrue(any(item["stage"] == "complete" and item["status"] == "adopted" for item in self.checkpoints))

    def test_sequential_refinement_uses_best_candidate_and_is_bounded(self):
        seen = []
        def propose(base, task, depth, seed):
            seen.append((depth, base["tool_guidance"]["mode"]))
            if depth == 0:
                return [{"system_prompt": "stable", "tool_guidance": {"mode": "concise"}}]
            return [{"system_prompt": "stable", "tool_guidance": {"mode": "ultra-concise"}}]

        result = self.run_experiment(propose=propose)
        self.assertEqual("adopted", result.status)
        self.assertEqual([(0, "safe"), (1, "concise")], seen)
        self.assertEqual(2, len(result.candidate_digests))
        self.assertEqual(canonical_digest({
            "system_prompt": "stable", "tool_guidance": {"mode": "ultra-concise"}
        }), result.adopted_digest)

    def test_missing_isolation_evidence_blocks_before_candidate_probe(self):
        calls = []
        result = self.run_experiment(probe=lambda *args: calls.append(args[3]) or {})
        self.assertEqual("blocked", result.status)
        self.assertFalse(result.success)
        self.assertEqual(["baseline"], calls)

    def test_missing_evaluator_evidence_never_adopts(self):
        adopted = []
        result = self.run_experiment(judge=lambda *args: {})
        self.assertEqual("blocked", result.status)
        self.assertFalse(result.success)
        self.assertEqual([], adopted)

    def test_every_probe_and_judge_charge_is_checkpointed_before_dispatch(self):
        guard = RuntimeExecutionGuard(max_steps=20, max_attempts=20)
        observed = []
        controller = self.controller(
            guard=guard,
            persist=lambda snapshot: observed.append((snapshot["stage"], guard.attempts_used)),
        )
        result = controller.run(
            manifest=self.manifest,
            active_harness=self.base,
            task=self.task,
            propose=lambda base, task, depth, seed: [
                {"system_prompt": "stable", "tool_guidance": {"mode": "concise"}}
            ],
            probe=self.probe,
            judge=self.judge,
            adopt=lambda candidate, expected: {
                "atomic": True, "active_digest": canonical_digest(candidate)
            },
            rollback=lambda expected: {"restored": True, "active_digest": expected},
        )
        self.assertEqual("adopted", result.status)
        for stage, attempts in (("baseline_probe", 1), ("baseline_judge", 2),
                                ("candidate_probe", 3), ("candidate_judge", 4)):
            self.assertIn((stage, attempts), observed)

    def test_candidate_receipt_for_another_harness_is_rejected(self):
        adopted = []
        def mismatched_probe(candidate, task, manifest, stage):
            receipt = self.probe(candidate, task, manifest, stage)
            if stage == "candidate":
                receipt["harness_digest"] = "f" * 64
            return receipt
        result = self.run_experiment(
            probe=mismatched_probe,
            adopt=lambda candidate, expected: adopted.append(candidate) or {
                "atomic": True, "active_digest": canonical_digest(candidate)
            },
        )
        self.assertEqual("rejected", result.status)
        self.assertFalse(result.success)
        self.assertEqual([], adopted)

    def test_no_improvement_is_rejected(self):
        result = self.run_experiment(
            propose=lambda base, task, depth, seed: [
                {"system_prompt": "stable", "tool_guidance": {"mode": "safe-but-different"}}
            ]
        )
        self.assertEqual("rejected", result.status)
        self.assertFalse(result.success)

    def test_candidate_cap_cannot_be_bypassed(self):
        result = self.run_experiment(propose=lambda base, task, depth, seed: [
            {"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}
        ])
        self.assertEqual("failed", result.status)
        self.assertFalse(result.success)

    def test_already_charged_operation_is_not_replayed(self):
        self.guard.charge_attempt("harness:test-run-1:baseline:probe")
        calls = []
        result = self.run_experiment(probe=lambda *args: calls.append(args) or self.probe(*args))
        self.assertEqual("unscored", result.status)
        self.assertEqual([], calls)
        self.assertFalse(result.success)

    def test_exhausted_shared_budget_returns_unscored_not_success(self):
        guard = RuntimeExecutionGuard(max_steps=5, max_attempts=1)
        controller = self.controller(guard=guard)
        result = controller.run(
            manifest=self.manifest,
            active_harness=self.base,
            task=self.task,
            propose=lambda base, task, depth, seed: [{"tool_guidance": {"mode": "concise"}}],
            probe=self.probe,
            judge=self.judge,
            adopt=lambda candidate, expected: self.fail("adoption must not run after budget exhaustion"),
            rollback=lambda expected: {"restored": True, "active_digest": expected},
        )
        self.assertEqual("unscored", result.status)
        self.assertFalse(result.success)

    def test_bad_adoption_receipt_triggers_exact_base_rollback(self):
        rollback_calls = []
        result = self.run_experiment(
            adopt=lambda candidate, expected: {"atomic": False, "active_digest": "wrong"},
            rollback=lambda expected: rollback_calls.append(expected) or {
                "restored": True, "active_digest": expected
            },
        )
        self.assertEqual("failed", result.status)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_succeeded)
        self.assertEqual([self.base_digest], rollback_calls)
        self.assertFalse(result.success)

    def test_timeout_after_adoption_attempt_rolls_back_and_stays_unscored(self):
        rollback_calls = []
        def timed_out_adoption(candidate, expected):
            raise TimeoutError("adoption response lost after possible activation")
        result = self.run_experiment(
            adopt=timed_out_adoption,
            rollback=lambda expected: rollback_calls.append(expected) or {
                "restored": True, "active_digest": expected
            },
        )
        self.assertEqual("unscored", result.status)
        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_succeeded)
        self.assertEqual([self.base_digest], rollback_calls)

    def test_persistence_failure_cannot_report_success(self):
        calls = []
        def persist(snapshot):
            calls.append(snapshot)
            if snapshot["stage"] == "complete" and snapshot["status"] == "adopted":
                raise OSError("durable store unavailable")
        result = self.controller(persist=persist).run(
            manifest=self.manifest,
            active_harness=self.base,
            task=self.task,
            propose=lambda base, task, depth, seed: [
                {"system_prompt": "stable", "tool_guidance": {"mode": "concise"}}
            ],
            probe=self.probe,
            judge=self.judge,
            adopt=lambda candidate, expected: {
                "atomic": True, "active_digest": canonical_digest(candidate)
            },
            rollback=lambda expected: {"restored": True, "active_digest": expected},
        )
        self.assertEqual("failed", result.status)
        self.assertFalse(result.success)
        self.assertTrue(result.rollback_attempted)
        self.assertTrue(result.rollback_succeeded)


if __name__ == "__main__":
    unittest.main()
