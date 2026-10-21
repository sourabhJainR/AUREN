import unittest

from portable.recovery_strategy import choose_recovery_path


class RecoveryStrategyPolicyTests(unittest.TestCase):
    def test_switches_strategy_after_no_progress(self):
        decision = choose_recovery_path(
            candidate_strategies=("direct", "patch_then_test"),
            attempted_strategies=("direct",),
            failure_class="test_failure",
            attempts_remaining=2,
            progress_made=False,
        )
        self.assertEqual((decision.action, decision.strategy), ("alternate", "patch_then_test"))

    def test_cancellation_and_deadline_stop_before_alternatives(self):
        for kwargs, expected in (
            ({"cancelled": True}, "stop"),
            ({"deadline_expired": True}, "stop"),
            ({"checkpoint_verified": False}, "block"),
        ):
            with self.subTest(kwargs=kwargs):
                decision = choose_recovery_path(
                    candidate_strategies=("alternate",),
                    attempted_strategies=(),
                    failure_class="provider_error",
                    attempts_remaining=2,
                    progress_made=False,
                    **kwargs,
                )
                self.assertEqual(decision.action, expected)

    def test_ambiguous_or_confirmed_effect_never_replays(self):
        for state in ("ambiguous", "confirmed"):
            with self.subTest(state=state):
                decision = choose_recovery_path(
                    candidate_strategies=(),
                    attempted_strategies=(),
                    failure_class="connection_drop",
                    attempts_remaining=2,
                    progress_made=True,
                    side_effect_state=state,
                )
                self.assertEqual(decision.action, "block")

    def test_exhausted_budget_and_no_alternative_block(self):
        common = dict(
            candidate_strategies=("direct",),
            attempted_strategies=("direct",),
            failure_class="test_failure",
            progress_made=False,
        )
        self.assertEqual(
            choose_recovery_path(**common, attempts_remaining=0).action, "block"
        )
        self.assertEqual(
            choose_recovery_path(**common, attempts_remaining=1).action, "block"
        )

    def test_progress_can_retry_only_when_no_untried_strategy_remains(self):
        decision = choose_recovery_path(
            candidate_strategies=("direct",),
            attempted_strategies=("direct",),
            failure_class="transient_provider_error",
            attempts_remaining=1,
            progress_made=True,
        )
        self.assertEqual(decision.action, "retry")

    def test_invalid_budget_and_duplicate_strategy_rejected(self):
        with self.assertRaises(ValueError):
            choose_recovery_path(
                candidate_strategies=(),
                attempted_strategies=(),
                failure_class="failure",
                attempts_remaining=-1,
                progress_made=False,
            )
        with self.assertRaises(ValueError):
            choose_recovery_path(
                candidate_strategies=("same", "same"),
                attempted_strategies=(),
                failure_class="failure",
                attempts_remaining=1,
                progress_made=False,
            )
        with self.assertRaises(ValueError):
            choose_recovery_path(
                candidate_strategies=(),
                attempted_strategies=([ "not-hashable" ],),
                failure_class="failure",
                attempts_remaining=1,
                progress_made=False,
            )


if __name__ == "__main__":
    unittest.main()
