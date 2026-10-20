from __future__ import annotations

import threading
import time
import unittest

from portable.process_isolated_crew_dispatch import ProcessIsolatedCrewDispatcher


def quick_handler(request):
    return {"crew_id": request["crew_id"], "task": request["task"], "accepted": True}


def hanging_handler(request):
    time.sleep(30)
    return {"accepted": True}


def oversized_handler(request):
    return {"data": "x" * 10000}


class ProcessIsolatedCrewDispatcherTests(unittest.TestCase):
    def test_importable_handler_returns_structured_result(self):
        dispatcher = ProcessIsolatedCrewDispatcher("tests.test_process_isolated_crew_dispatch:quick_handler")
        result = dispatcher.run([{"crew_id": "crew-a", "task": "proposal"}], timeout_seconds=5)
        self.assertEqual(["completed"], [item.status for item in result])
        self.assertEqual({"crew_id": "crew-a", "task": "proposal", "accepted": True}, result[0].result)

    def test_hung_handler_is_hard_stopped_within_deadline(self):
        dispatcher = ProcessIsolatedCrewDispatcher(
            "tests.test_process_isolated_crew_dispatch:hanging_handler",
            terminate_grace_seconds=0.1,
        )
        started = time.monotonic()
        result = dispatcher.run([{"crew_id": "crew-a", "task": "hang"}], timeout_seconds=0.25)
        self.assertEqual("timed_out", result[0].status)
        self.assertLess(time.monotonic() - started, 4.0)

    def test_cancellation_stops_worker_and_does_not_claim_success(self):
        dispatcher = ProcessIsolatedCrewDispatcher(
            "tests.test_process_isolated_crew_dispatch:hanging_handler",
            terminate_grace_seconds=0.1,
        )
        cancel = threading.Event()
        threading.Thread(target=lambda: (time.sleep(0.25), cancel.set()), daemon=True).start()
        result = dispatcher.run([{"crew_id": "crew-a", "task": "hang"}], timeout_seconds=5, cancellation_event=cancel)
        self.assertEqual("cancelled", result[0].status)
        self.assertIsNone(result[0].result)

    def test_result_size_is_bounded(self):
        dispatcher = ProcessIsolatedCrewDispatcher(
            "tests.test_process_isolated_crew_dispatch:oversized_handler",
            output_limit_bytes=512,
        )
        result = dispatcher.run([{"crew_id": "crew-a", "task": "large"}], timeout_seconds=5)
        self.assertEqual("failed", result[0].status)
        self.assertIn("output limit", result[0].error)

    def test_invalid_callable_reference_and_payload_fail_before_spawn(self):
        with self.assertRaises(ValueError):
            ProcessIsolatedCrewDispatcher("tests.test_process_isolated_crew_dispatch:<locals>.bad")
        dispatcher = ProcessIsolatedCrewDispatcher("tests.test_process_isolated_crew_dispatch:quick_handler")
        with self.assertRaises(ValueError):
            dispatcher.run([{"crew_id": "crew-a", "task": "bad", "not_json": object()}], timeout_seconds=1)

    def test_request_size_is_bounded_before_worker_spawn(self):
        dispatcher = ProcessIsolatedCrewDispatcher(
            "tests.test_process_isolated_crew_dispatch:quick_handler",
            max_request_bytes=256,
        )
        with self.assertRaisesRegex(ValueError, "request byte limit"):
            dispatcher.run([{"crew_id": "crew-a", "task": "x" * 1024}], timeout_seconds=2)

    def test_batch_deadline_prevents_later_dispatch(self):
        dispatcher = ProcessIsolatedCrewDispatcher(
            "tests.test_process_isolated_crew_dispatch:hanging_handler",
            terminate_grace_seconds=0.1,
        )
        results = dispatcher.run(
            [{"crew_id": "first", "task": "hang"}, {"crew_id": "second", "task": "never"}],
            timeout_seconds=0.25,
        )
        self.assertEqual(["timed_out", "not_run"], [item.status for item in results])


if __name__ == "__main__":
    unittest.main()
