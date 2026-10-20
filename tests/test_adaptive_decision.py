import json
import unittest

from portable.adaptive_decision import AdaptiveInferencePolicy, HttpDecisionProvider


class AdaptiveDecisionTests(unittest.TestCase):
    def _provider_for_level(self, level, confidence=0.99):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def read(self):
                levels = ("minimal", "standard", "deep", "human")
                probabilities = {item: (0.97 if item == level else 0.01) for item in levels}
                return json.dumps({
                    "decisions": {
                        "depth": {
                            "level": level,
                            "probabilities": probabilities,
                            "confidence": confidence,
                        }
                    }
                }).encode()

        return HttpDecisionProvider(
            "https://decision.example/v1",
            opener=lambda *_args, **_kwargs: Response(),
        )

    def test_policy_escalates_with_uncertainty_and_failure_history(self) -> None:
        policy = AdaptiveInferencePolicy()
        minimal = policy.decide(uncertainty=0.05, risk=0.1, evidence_quality=0.95)
        deep = policy.decide(uncertainty=0.8, risk=0.7, evidence_quality=0.2)
        human = policy.decide(uncertainty=0.7, risk=0.95, evidence_quality=0.8)
        self.assertEqual(minimal.depth, "minimal")
        self.assertEqual(deep.depth, "deep")
        self.assertEqual(human.depth, "human")

    def test_http_provider_is_optional_and_typed(self) -> None:
        calls = []

        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def read(self):
                return json.dumps({
                    "decisions": {
                        "depth": {
                            "level": "standard",
                            "probabilities": {
                                "minimal": 0.05,
                                "standard": 0.9,
                                "deep": 0.04,
                                "human": 0.01,
                            },
                            "confidence": 0.92,
                        }
                    }
                }).encode()

        def opener(req, timeout):
            calls.append((req.full_url, timeout, req.get_header("Authorization")))
            return Response()

        provider = HttpDecisionProvider("https://decision.example/v1", api_key="secret", opener=opener)
        decision = AdaptiveInferencePolicy(provider=provider).decide(
            uncertainty=0.2, risk=0.2, evidence_quality=0.8
        )
        self.assertEqual(decision.depth, "standard")
        self.assertEqual(decision.provider, "external")
        self.assertEqual(calls[0][2], "Bearer secret")

    def test_provider_falls_back_on_bad_response(self) -> None:
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return None
            def read(self):
                return b"not-json"

        provider = HttpDecisionProvider("https://decision.example/v1", opener=lambda *_args, **_kwargs: Response())
        decision = AdaptiveInferencePolicy(provider=provider).decide(
            uncertainty=0.1, risk=0.1, evidence_quality=0.9
        )
        self.assertEqual(decision.provider, "deterministic")
        self.assertEqual(decision.depth, "minimal")

    def test_high_confidence_provider_cannot_override_hard_risk_gate(self) -> None:
        policy = AdaptiveInferencePolicy(provider=self._provider_for_level("minimal"))
        decision = policy.decide(uncertainty=0.1, risk=0.99, evidence_quality=1.0)
        self.assertEqual(decision.depth, "human")
        self.assertEqual(decision.provider, "deterministic")
        self.assertFalse(decision.abstained)

    def test_provider_cannot_undercut_deterministic_depth_floor(self) -> None:
        policy = AdaptiveInferencePolicy(provider=self._provider_for_level("standard"))
        decision = policy.decide(uncertainty=0.85, risk=0.4, evidence_quality=0.8)
        self.assertEqual(decision.depth, "deep")
        self.assertEqual(decision.provider, "external")
        self.assertTrue(decision.abstained)

    def test_provider_may_recommend_more_conservative_depth(self) -> None:
        policy = AdaptiveInferencePolicy(provider=self._provider_for_level("human"))
        decision = policy.decide(uncertainty=0.1, risk=0.1, evidence_quality=0.95)
        self.assertEqual(decision.depth, "human")
        self.assertEqual(decision.provider, "external")


if __name__ == "__main__":
    unittest.main()
