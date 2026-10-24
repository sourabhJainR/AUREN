from __future__ import annotations

import time
import unittest
from threading import Event, Thread

from portable.agency_state_graph import (
    GraphInterrupt,
    InMemoryCheckpointStore,
    RetryPolicy,
    StateGraph,
    current_cancellation_event,
)


class StateGraphTests(unittest.TestCase):
    def test_reducer_merges_parallel_updates_from_same_snapshot(self):
        graph = StateGraph(reducers={"findings": lambda left, right: left + right})
        graph.add_node("a", lambda state: {"findings": ["a"], "seen": state["value"]})
        graph.add_node("b", lambda state: {"findings": ["b"], "seen": state["value"]})
        graph.add_edge(StateGraph.START, "a").add_edge(StateGraph.START, "b")
        graph.add_edge("a", StateGraph.END).add_edge("b", StateGraph.END)
        result = graph.compile().invoke({"value": 7, "findings": []})
        self.assertEqual(result.state["findings"], ["a", "b"])
        self.assertEqual(result.state["seen"], 7)

    def test_conditional_routing(self):
        graph = StateGraph()
        graph.add_node("classify", lambda state: {"route": "fix" if state["broken"] else "done"})
        graph.add_node("fix", lambda state: {"fixed": True})
        graph.add_edge(StateGraph.START, "classify")
        graph.add_conditional_edges("classify", lambda state: state["route"])
        graph.add_edge("fix", StateGraph.END)
        result = graph.compile().invoke({"broken": True})
        self.assertTrue(result.state["fixed"])
        self.assertEqual(result.trace, ("classify", "fix"))

    def test_retry_is_bounded(self):
        attempts = []
        def flaky(_state):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("transient")
            return {"ok": True}
        graph = StateGraph()
        graph.add_node("flaky", flaky, retry_policy=RetryPolicy(3))
        graph.add_edge(StateGraph.START, "flaky").add_edge("flaky", StateGraph.END)
        result = graph.compile().invoke({})
        self.assertTrue(result.state["ok"])
        self.assertEqual(result.events[0].attempts, 3)

    def test_checkpoint_and_resume_after_interrupt(self):
        store = InMemoryCheckpointStore()
        graph = StateGraph()
        graph.add_node("plan", lambda state: {"planned": True})
        graph.add_node("act", lambda state: {"acted": True})
        graph.add_edge(StateGraph.START, "plan").add_edge("plan", "act").add_edge("act", StateGraph.END)
        graph.interrupt_after("plan")
        with self.assertRaises(GraphInterrupt) as caught:
            graph.compile().invoke({}, run_id="r1", checkpoint=store)
        self.assertEqual(caught.exception.next_nodes, ("act",))
        resumed = graph.compile().invoke({}, run_id="r1", checkpoint=store, resume=True)
        self.assertTrue(resumed.state["planned"])
        self.assertTrue(resumed.state["acted"])

    def test_parallel_timeout_signals_cancellation_in_isolate_mode(self):
        started = Event()
        observed_cancellation = Event()

        def cooperative_node(_state):
            cancellation = current_cancellation_event()
            self.assertIsNotNone(cancellation)
            started.set()
            assert cancellation is not None
            cancellation.wait(timeout=2)
            if cancellation.is_set():
                observed_cancellation.set()
            return {"completed": True}

        graph = StateGraph()
        graph.add_node("slow", cooperative_node)
        graph.add_node("fast", lambda _state: {"fast": True})
        graph.add_edge(StateGraph.START, "slow").add_edge(StateGraph.START, "fast")
        graph.add_edge("slow", StateGraph.END).add_edge("fast", StateGraph.END)

        result = graph.compile().invoke(
            {},
            parallel_nodes=lambda _name: True,
            max_parallel_nodes=2,
            node_timeout_seconds=0.05,
            parallel_failure_mode="isolate",
        )

        self.assertTrue(started.is_set())
        self.assertTrue(result.interrupted)
        self.assertTrue(observed_cancellation.wait(timeout=1))

    def test_parallel_nodes_share_one_superstep_deadline(self):
        graph = StateGraph()

        def first_node(_state):
            time.sleep(0.22)
            return {"first": True}

        def second_node(_state):
            cancellation = current_cancellation_event()
            assert cancellation is not None
            cancellation.wait(timeout=1)
            return {"second": True}

        graph.add_node("first", first_node)
        graph.add_node("second", second_node)
        graph.add_edge(StateGraph.START, "first").add_edge(StateGraph.START, "second")
        graph.add_edge("first", StateGraph.END).add_edge("second", StateGraph.END)

        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "parallel node exceeded timeout"):
            graph.compile().invoke(
                {}, parallel_nodes=lambda _name: True, max_parallel_nodes=2,
                node_timeout_seconds=0.30, parallel_failure_mode="fail_fast",
            )
        elapsed = time.monotonic() - started
        self.assertLess(
            elapsed, 0.45,
            "parallel nodes must share one deadline instead of each consuming a full timeout",
        )

    def test_node_timeout_signals_cooperative_cancellation(self):
        started = Event()
        observed_cancellation = Event()

        def cooperative_node(_state):
            cancellation = current_cancellation_event()
            self.assertIsNotNone(cancellation)
            started.set()
            assert cancellation is not None
            cancellation.wait(timeout=2)
            if cancellation.is_set():
                observed_cancellation.set()
            return {"completed": True}

        graph = StateGraph()
        graph.add_node("cooperative", cooperative_node)
        graph.add_edge(StateGraph.START, "cooperative").add_edge("cooperative", StateGraph.END)

        with self.assertRaisesRegex(TimeoutError, "node cooperative exceeded timeout"):
            graph.compile().invoke({}, node_timeout_seconds=0.05)

        self.assertTrue(started.is_set())
        self.assertTrue(
            observed_cancellation.wait(timeout=1),
            "timed-out graph nodes must receive a cooperative cancellation signal",
        )
        self.assertIsNone(current_cancellation_event())

    def test_max_steps_prevents_unbounded_loops(self):
        graph = StateGraph()
        graph.add_node("loop", lambda state: {"count": state.get("count", 0) + 1})
        graph.add_edge(StateGraph.START, "loop").add_edge("loop", "loop")
        with self.assertRaises(RuntimeError):
            graph.compile().invoke({}, max_steps=3)

    def test_runtime_attempt_budget_is_shared_across_graph_nodes(self):
        calls = []
        graph = StateGraph()
        graph.add_node("first", lambda state: (calls.append("first") or {"first": True}))
        graph.add_node("second", lambda state: (calls.append("second") or {"second": True}))
        graph.add_edge(StateGraph.START, "first").add_edge("first", "second")
        graph.add_edge("second", StateGraph.END)
        store = InMemoryCheckpointStore()
        from portable.runtime_recovery import ExecutionBudgetExceeded
        with self.assertRaises(ExecutionBudgetExceeded):
            graph.compile().invoke({}, run_id="budget", checkpoint=store, max_attempts=1)
        self.assertEqual(calls, ["first"])
        self.assertIsNotNone(store.load("budget").runtime_guard)

    def test_repeated_terminal_failure_across_resume_stops_without_false_success(self):
        graph = StateGraph()
        calls = []
        def broken(_state):
            calls.append(1)
            raise ValueError("stable failure")
        graph.add_node("broken", broken, retry_policy=RetryPolicy(1))
        graph.add_edge(StateGraph.START, "broken").add_edge("broken", StateGraph.END)
        store = InMemoryCheckpointStore()
        with self.assertRaisesRegex(ValueError, "stable failure"):
            graph.compile().invoke({}, run_id="repeat", checkpoint=store)
        from portable.runtime_recovery import NoProgressDetected
        with self.assertRaises(NoProgressDetected):
            graph.compile().invoke({}, run_id="repeat", checkpoint=store, resume=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(store.load("repeat").runtime_guard["cancelled"])

    def test_legacy_checkpoint_resumes_with_a_bounded_migrated_budget(self):
        from portable.agency_state_graph import Checkpoint
        store = InMemoryCheckpointStore()
        store.save(Checkpoint("legacy", 0, {}, ("work",)))
        graph = StateGraph()
        graph.add_node("work", lambda _state: {"done": True})
        graph.add_edge(StateGraph.START, "work").add_edge("work", StateGraph.END)
        result = graph.compile().invoke({}, run_id="legacy", checkpoint=store, resume=True)
        self.assertTrue(result.state["done"])
        self.assertIsNotNone(store.load("legacy").runtime_guard)

    def test_deadline_overrun_rejects_late_node_result(self):
        import time
        from portable.runtime_recovery import ExecutionBudgetExceeded
        graph = StateGraph()
        graph.add_node("slow", lambda _state: (time.sleep(0.03) or {"done": True}))
        graph.add_edge(StateGraph.START, "slow").add_edge("slow", StateGraph.END)
        with self.assertRaises(ExecutionBudgetExceeded):
            graph.compile().invoke({}, deadline_seconds=0.005)

    def test_isolated_node_failure_cannot_be_reported_as_success(self):
        graph = StateGraph()
        graph.add_node("broken", lambda _state: (_ for _ in ()).throw(ValueError("failed")))
        graph.add_node("healthy", lambda _state: {"healthy": True})
        graph.add_edge(StateGraph.START, "broken").add_edge(StateGraph.START, "healthy")
        graph.add_edge("broken", StateGraph.END).add_edge("healthy", StateGraph.END)
        result = graph.compile().invoke(
            {}, parallel_nodes=lambda _name: True, max_parallel_nodes=2,
            parallel_failure_mode="isolate",
        )
        self.assertTrue(result.interrupted)
        self.assertTrue(any(event.status == "failed-isolated" for event in result.events))


    def test_supervised_invocation_binds_controller_cancellation_to_nodes(self):
        started = Event()
        cancellation = Event()

        def cooperative_node(_state):
            active = current_cancellation_event()
            self.assertIs(active, cancellation)
            started.set()
            active.wait(timeout=3)
            return {"completed": True}

        graph = StateGraph()
        graph.add_node("cooperative", cooperative_node)
        graph.add_edge(StateGraph.START, "cooperative").add_edge("cooperative", StateGraph.END)
        outcomes = []
        worker = Thread(
            target=lambda: outcomes.append(
                graph.compile().invoke_supervised(
                    {}, execution_payload={"auren_cancel_event": cancellation},
                    run_id="supervised-cancel",
                )
            ),
            daemon=True,
        )
        worker.start()
        self.assertTrue(started.wait(timeout=3), "graph node did not start")
        cancellation.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "cooperative graph node did not stop")
        self.assertEqual(1, len(outcomes))
        self.assertTrue(outcomes[0].interrupted)
        self.assertNotIn("completed", outcomes[0].state)

    def test_supervised_invocation_rejects_expired_deadline_before_dispatch(self):
        calls = []
        graph = StateGraph()
        graph.add_node("must-not-run", lambda _state: (calls.append(True) or {"done": True}))
        graph.add_edge(StateGraph.START, "must-not-run").add_edge("must-not-run", StateGraph.END)
        with self.assertRaisesRegex(TimeoutError, "supervisor deadline expired before graph dispatch"):
            graph.compile().invoke_supervised(
                {}, execution_payload={"auren_deadline_epoch": time.time() - 1},
                run_id="supervised-expired",
            )
        self.assertEqual([], calls)

    def test_supervised_invocation_rejects_invalid_deadline_and_runtime_kwarg_overrides(self):
        graph = StateGraph()
        graph.add_node("work", lambda _state: {"done": True})
        graph.add_edge(StateGraph.START, "work").add_edge("work", StateGraph.END)
        for invalid in (True, float("nan"), "soon"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    graph.compile().invoke_supervised(
                        {}, execution_payload={"auren_deadline_epoch": invalid},
                    )
        with self.assertRaisesRegex(TypeError, "owns cancellation and deadline_seconds"):
            graph.compile().invoke_supervised(
                {}, execution_payload={}, deadline_seconds=1,
            )


if __name__ == "__main__":
    unittest.main()
