from __future__ import annotations

import threading
import time
import unittest

from portable.bounded_crew_dispatch import BoundedCrewDispatcher


class BoundedCrewDispatcherTests(unittest.TestCase):
    def test_parallel_execution_is_bounded_and_results_keep_input_order(self):
        lock = threading.Lock()
        active = 0
        maximum_active = 0

        def dispatch(request):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                # Deliberately complete later input items first.
                time.sleep(0.02 * (5 - int(request["task"])))
                return {"crew_id": request["crew_id"], "task": request["task"]}
            finally:
                with lock:
                    active -= 1

        requests = [
            {"crew_id": f"crew-{i}", "task": str(i), "evidence": {}}
            for i in range(1, 5)
        ]
        results = BoundedCrewDispatcher(dispatch).run(requests, max_workers=2)

        self.assertEqual([request["task"] for request in requests], [item["task"] for item in results])
        self.assertLessEqual(maximum_active, 2)
        self.assertGreaterEqual(maximum_active, 2)

    def test_empty_batch_returns_empty_without_dispatch(self):
        dispatcher = BoundedCrewDispatcher(lambda _request: self.fail("must not dispatch"))
        self.assertEqual([], dispatcher.run([]))

    def test_batch_size_and_worker_budget_are_bounded(self):
        dispatcher = BoundedCrewDispatcher(lambda request: request["task"])
        requests = [{"crew_id": "crew", "task": str(i)} for i in range(9)]
        with self.assertRaisesRegex(ValueError, "8 request limit"):
            dispatcher.run(requests)
        with self.assertRaisesRegex(ValueError, "between 1 and 4"):
            dispatcher.run([{"crew_id": "crew", "task": "one"}], max_workers=5)
        with self.assertRaisesRegex(ValueError, "between 1 and 4"):
            dispatcher.run([{"crew_id": "crew", "task": "one"}], max_workers=True)

    def test_invalid_request_fails_before_any_dispatch(self):
        called = []
        dispatcher = BoundedCrewDispatcher(lambda request: called.append(request))
        with self.assertRaisesRegex(ValueError, "requires crew_id"):
            dispatcher.run([{"task": "missing crew"}])
        self.assertEqual([], called)

    def test_dispatch_failure_is_not_converted_to_success(self):
        def dispatch(request):
            if request["task"] == "bad":
                raise RuntimeError("provider failure")
            return {"accepted": True}

        dispatcher = BoundedCrewDispatcher(dispatch)
        with self.assertRaisesRegex(RuntimeError, "provider failure"):
            dispatcher.run([
                {"crew_id": "crew-a", "task": "good"},
                {"crew_id": "crew-b", "task": "bad"},
            ], max_workers=2)


if __name__ == "__main__":
    unittest.main()
