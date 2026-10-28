import unittest

from portable.bounded_work import (
    WorkBudgetExceeded,
    iter_batches,
    iter_text_chunks,
    plan_bounded_work,
    reservoir_sample,
)


class BoundedWorkTests(unittest.TestCase):
    def test_plan_uses_direct_when_within_limits(self):
        plan = plan_bounded_work(
            item_count=3, payload_chars=20,
            max_items_per_call=3, max_chars_per_call=20,
        )
        self.assertEqual(plan.strategy, "direct")
        self.assertTrue(plan.complete_coverage)

    def test_plan_prefers_coverage_preserving_chunked_batches(self):
        plan = plan_bounded_work(
            item_count=100, payload_chars=10000,
            max_items_per_call=10, max_chars_per_call=1000,
            independent_batches=True, chunkable=True,
        )
        self.assertEqual(plan.strategy, "chunked_batches")
        self.assertTrue(plan.complete_coverage)

    def test_plan_never_samples_required_security_coverage(self):
        with self.assertRaises(WorkBudgetExceeded):
            plan_bounded_work(
                item_count=100, payload_chars=10000,
                max_items_per_call=10, max_chars_per_call=1000,
                allow_sampling=True, complete_coverage_required=True,
            )

    def test_plan_only_samples_explicit_exploratory_work(self):
        plan = plan_bounded_work(
            item_count=100, payload_chars=10000,
            max_items_per_call=10, max_chars_per_call=1000,
            allow_sampling=True, complete_coverage_required=False,
        )
        self.assertEqual(plan.strategy, "sample")
        self.assertFalse(plan.complete_coverage)

    def test_text_chunks_preserve_all_source_without_overlap(self):
        source = ("alpha beta gamma\n" * 20) + "終わり"
        chunks = list(iter_text_chunks(source, 19))
        self.assertTrue(all(0 < len(chunk) <= 19 for chunk in chunks))
        self.assertEqual("".join(chunks), source)

    def test_text_chunks_bound_overlap_and_make_progress(self):
        source = "abcdefghijklmnopqrstuvwxyz" * 3
        chunks = list(iter_text_chunks(source, 12, overlap=3))
        self.assertTrue(all(0 < len(chunk) <= 12 for chunk in chunks))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunks[i][-3:] == chunks[i + 1][:3] for i in range(len(chunks) - 1)))

    def test_batches_are_lazy_and_keep_all_items(self):
        batches = list(iter_batches(range(11), 4))
        self.assertEqual(batches, [list(range(4)), list(range(4, 8)), list(range(8, 11))])

    def test_batch_cap_fails_instead_of_returning_partial_success(self):
        batches = iter_batches(range(9), 4, max_batches=2)
        self.assertEqual(next(batches), [0, 1, 2, 3])
        self.assertEqual(next(batches), [4, 5, 6, 7])
        with self.assertRaises(WorkBudgetExceeded):
            next(batches)

    def test_sampling_requires_explicit_opt_in_and_is_reproducible(self):
        with self.assertRaises(WorkBudgetExceeded):
            reservoir_sample(range(100), 5, seed=7)
        first = reservoir_sample(
            range(100), 5, seed=7, allow_sampling=True, complete_coverage_required=False,
        )
        second = reservoir_sample(
            range(100), 5, seed=7, allow_sampling=True, complete_coverage_required=False,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.population_count, 100)
        self.assertEqual(first.sample_size, 5)
        self.assertEqual(len(set(first.items)), 5)


if __name__ == "__main__":
    unittest.main()
