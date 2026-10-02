import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from portable.cross_task_capability_abstraction import CrossTaskCapabilityAbstraction
from portable.transfer_benchmark import TransferBenchmarkHarness


class TransferBenchmarkTests(unittest.TestCase):
    def test_empty_history_fails_closed(self):
        with TemporaryDirectory() as td:
            engine=CrossTaskCapabilityAbstraction(Path(td))
            # No pattern can be synthesized from empty evidence.
            self.assertEqual(engine.discover(), ())

    def test_benchmark_is_bounded_and_dependency_free(self):
        with TemporaryDirectory() as td:
            engine=CrossTaskCapabilityAbstraction(Path(td))
            harness=TransferBenchmarkHarness(Path(td))
            self.assertEqual(harness.minimum_holdout_tasks,2)
            self.assertEqual(harness.minimum_uplift,.03)


if __name__=="__main__":
    unittest.main()
