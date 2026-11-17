import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / ".ai-harness/runtime/token_optimizer.py"
spec = importlib.util.spec_from_file_location("token_optimizer", MODULE)
optimizer = importlib.util.module_from_spec(spec)
assert spec.loader is not None
# dataclasses and other runtime introspection expect dynamically loaded modules
# to be registered in sys.modules before execution.
sys.modules[spec.name] = optimizer
spec.loader.exec_module(optimizer)


class TokenOptimizerTests(unittest.TestCase):
    def test_relevance_budget_excludes_unrelated_large_context(self):
        sections = [
            "authentication login token refresh service",
            "unrelated historical document " + ("x " * 500),
            "token refresh integration tests and security policy",
        ]
        selected = optimizer.select_relevant_context("fix token refresh security", sections, 180)
        self.assertTrue(selected)
        self.assertLessEqual(sum(len(x.text) + 1 for x in selected), 180)
        self.assertTrue(any("token refresh" in x.text for x in selected))

    def test_mvm_routes_simple_tasks_low(self):
        decision = optimizer.choose_model_tier(task="rename typo in documentation")
        self.assertEqual(decision.tier, "low")

    def test_risk_escalates(self):
        decision = optimizer.choose_model_tier(task="small change", risk="critical")
        self.assertEqual(decision.tier, "high")


    def test_high_uncertainty_escalates_model_tier(self):
        decision = optimizer.choose_model_tier(task="normal feature", uncertainty="high")
        self.assertEqual(decision.tier, "high")

    def test_script_candidate_for_repeatable_work(self):
        self.assertTrue(optimizer.should_use_script("validate configuration", repeat_count=0))
        self.assertTrue(optimizer.should_use_script("inspect repository", repeat_count=2))
        self.assertFalse(optimizer.should_use_script("design architecture", repeat_count=0))


    def test_required_policy_context_is_preserved_and_budget_is_fail_closed(self):
        required = "Guard approval rollback acceptance policy"
        selected = optimizer.select_relevant_context(
            "summarize documentation",
            [required, "unrelated history " + ("x " * 100)],
            80,
            required_sections=[required],
        )
        self.assertEqual(selected[0].text, required)
        self.assertEqual(selected[0].reason, "required-preserved")
        with self.assertRaises(ValueError):
            optimizer.select_relevant_context("task", [required], 10, required_sections=[required])

    def test_verified_capability_and_privacy_constraints_precede_cost(self):
        options = [
            optimizer.ModelOption("cheap", "small", "low", .1, .1, 10000,
                                  ("coding",), ("public",), True, 50),
            optimizer.ModelOption("safe", "secure", "high", 2.0, 4.0, 10000,
                                  ("coding", "security"), ("private",), True, 100),
            optimizer.ModelOption("unknown", "unknown", "high", .01, .01, 10000,
                                  ("coding", "security"), ("private",), False, 1),
        ]
        route = optimizer.choose_capable_route(
            options, required_capabilities=("coding", "security"),
            privacy_classification="private", minimum_tier="high",
            estimated_input_tokens=1000, estimated_output_tokens=500,
        )
        self.assertEqual(route.provider, "safe")
        self.assertEqual(route.model, "secure")
        self.assertGreater(route.estimated_cost_units, 0)



    def test_paired_optimizer_rejects_mismatched_model_or_policy(self):
        baseline = dict(
            task_digest="task", model_tier="high", model_id="model-v1",
            provider_version="provider-v1", context_budget_tokens=32000,
            acceptance_digest="acceptance-v1", rubric_digest="rubric-v1",
            policy_digest="policy-v1", input_tokens=1000, output_tokens=200,
            latency_ms=900, quality_score=95, policy_violations=0,
        )
        with self.assertRaises(ValueError):
            optimizer.compare_paired_runs(baseline, dict(baseline, model_id="different"))
        with self.assertRaises(ValueError):
            optimizer.compare_paired_runs(baseline, dict(baseline, policy_digest="different"))

    def test_model_route_keeps_unknown_prices_unknown(self):
        option = optimizer.ModelOption(
            "provider", "model", "standard", None, None, 10000,
            ("coding",), ("private",), True, 200,
        )
        route = optimizer.choose_capable_route(
            [option], required_capabilities=("coding",),
            privacy_classification="private", minimum_tier="standard",
            estimated_input_tokens=100, estimated_output_tokens=50,
        )
        self.assertIsNone(route.estimated_cost_units)
        self.assertIn("price-unknown", route.reason)


    def test_task_prompt_is_included_in_capacity_and_input_token_estimate(self):
        query = "Implement a secure authentication and authorization workflow with audit logging"
        report = optimizer.optimize(query, ["small relevant code section"], 100)
        self.assertGreaterEqual(
            report.estimated_input_tokens,
            optimizer.estimate_tokens(query),
        )
        option = optimizer.ModelOption(
            "provider", "too-small", "high", 1.0, 2.0, 5,
            ("coding",), ("private",), True, 50,
        )
        with self.assertRaises(ValueError):
            optimizer.optimize(
                query, ["small relevant code section"], 100,
                estimated_output_tokens=10, model_options=[option],
                required_capabilities=("coding",),
            )

    def test_output_price_is_not_reported_as_total_when_output_tokens_unknown(self):
        report = optimizer.optimize(
            "fix authentication", ["authentication code"], 100,
            input_cost_per_million=1.0, output_cost_per_million=2.0,
        )
        self.assertIsNone(report.estimated_cost_units)
        self.assertIsNone(report.estimated_output_tokens)

    def test_optimizer_cost_is_unknown_without_pricing_not_reported_as_zero(self):
        report = optimizer.optimize("fix authentication", ["authentication code"], 100)
        self.assertIsNone(report.estimated_cost_units)

    def test_optimizer_cost_and_cache_identity_are_reported(self):
        identity = dict(
            context_digest="context", model_id="model-v1", tool_version="tool-v1",
            policy_digest="policy-v1", repository_digest="repo-v1",
            rubric_digest="rubric-v1", privacy_scope="local-user",
        )
        report = optimizer.optimize(
            "fix authentication", ["authentication code"], 100,
            estimated_output_tokens=20, input_cost_per_million=1.0,
            output_cost_per_million=2.0, cache_identity=identity,
        )
        self.assertGreater(report.estimated_cost_units, 0)
        self.assertEqual(len(report.cache_key), 64)
        changed = dict(identity, rubric_digest="rubric-v2")
        self.assertNotEqual(
            report.cache_key,
            optimizer.optimization_cache_key(**changed),
        )

    def test_paired_optimizer_evaluation_rejects_quality_or_policy_regression(self):
        baseline = dict(task_digest="task", model_tier="high", model_id="model-v1",
                        provider_version="provider-v1", context_budget_tokens=32000,
                        acceptance_digest="acceptance-v1", rubric_digest="rubric",
                        policy_digest="policy-v1", input_tokens=1000, output_tokens=200, latency_ms=900,
                        quality_score=95, policy_violations=0)
        candidate = dict(baseline, input_tokens=700, latency_ms=800, quality_score=94)
        result = optimizer.compare_paired_runs(baseline, candidate)
        self.assertEqual(result["input_token_savings"], 300)
        self.assertFalse(result["promotion_eligible"])
        with self.assertRaises(ValueError):
            optimizer.compare_paired_runs(baseline, dict(candidate, context_budget_tokens=16000))

    def test_report_records_token_reduction(self):
        report = optimizer.optimize(
            "fix authentication token refresh",
            ["authentication token refresh implementation", "unrelated " + ("x" * 1000)],
            120,
        )
        self.assertLessEqual(report.selected_chars, 120)
        self.assertGreater(report.estimated_input_tokens, 0)
        self.assertLessEqual(report.compression_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
