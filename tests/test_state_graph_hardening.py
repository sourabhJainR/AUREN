from __future__ import annotations

import time
import unittest

from portable.agency_state_graph import Checkpoint, RetryPolicy, StateGraph, state_digest


class StateGraphHardeningTests(unittest.TestCase):
    def test_digest_is_order_independent_for_mappings(self) -> None:
        self.assertEqual(state_digest({"b": 2, "a": 1}), state_digest({"a": 1, "b": 2}))

    def test_checkpoint_digest_is_verified(self) -> None:
        checkpoint = Checkpoint("run", 1, {"value": 3}, ("done",))
        self.assertEqual(checkpoint.state_digest, state_digest({"value": 3}))
        with self.assertRaises(ValueError):
            Checkpoint("run", 1, {"value": 3}, ("done",), state_digest="bad")

    def test_non_json_state_is_rejected(self) -> None:
        graph = StateGraph().add_node("work", lambda state: {"ok": True}).add_edge(StateGraph.START, "work").add_edge("work", StateGraph.END).compile()
        with self.assertRaises(TypeError):
            graph.invoke({"bad": object()})

    def test_external_node_cannot_be_retried(self) -> None:
        with self.assertRaises(ValueError):
            StateGraph().add_node("write", lambda state: {}, effect="external", retry_policy=RetryPolicy(2))

    def test_retry_policy_can_be_narrowed(self) -> None:
        calls = {"count": 0}

        def work(_state):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ValueError("transient")
            return {"ok": True}

        graph = StateGraph().add_node("work", work, retry_policy=RetryPolicy(2, (ValueError,))).add_edge(StateGraph.START, "work").add_edge("work", StateGraph.END).compile()
        result = graph.invoke({})
        self.assertTrue(result.state["ok"])
        self.assertEqual(calls["count"], 2)

    def test_unlisted_exception_is_not_retried(self) -> None:
        calls = {"count": 0}

        def work(_state):
            calls["count"] += 1
            raise TypeError("not retryable")

        graph = StateGraph().add_node("work", work, retry_policy=RetryPolicy(3, (ValueError,))).add_edge(StateGraph.START, "work").add_edge("work", StateGraph.END).compile()
        with self.assertRaises(TypeError):
            graph.invoke({})
        self.assertEqual(calls["count"], 1)

    def test_timeout_returns_without_waiting_for_worker(self) -> None:
        graph = StateGraph().add_node("slow", lambda state: (time.sleep(0.2), {"ok": True})[1]).add_edge(StateGraph.START, "slow").add_edge("slow", StateGraph.END).compile()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            graph.invoke({}, node_timeout_seconds=0.001)
        self.assertLess(time.monotonic() - started, 0.1)


if __name__ == "__main__":
    unittest.main()
