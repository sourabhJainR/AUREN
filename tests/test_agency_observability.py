from pathlib import Path
import json
import multiprocessing
from concurrent.futures import ThreadPoolExecutor

import unittest
import tempfile

from portable.agency_observability import Dataset, DatasetItem, LocalExporter, PromptRegistry, Tracer, retrieval_scores, run_experiment
from portable.agency_runtime import EvidenceItem, TaskProfile, execute



def _export_trace_in_process(path: str, index: int) -> None:
    tracer = Tracer(LocalExporter(Path(path), enabled=True))
    trace = tracer.start("multiprocess-export", {"worker": index}, task_id=f"task-{index}")
    span = trace.span(f"worker-{index}", phase="implementation")
    span.finish({"worker": index})
    tracer.end(trace)


class TestAgencyObservability(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_trace_export_is_local_and_redacts_secrets(self):
        exporter = LocalExporter(self.tmp_path / "traces.jsonl", enabled=True)
        tracer = Tracer(exporter)
        trace = tracer.start("task", {"token": "secret-value"})
        span = trace.span("worker", "agent", {"api_key": "abc123"})
        span.score("quality", 0.9, "good")
        span.finish({"authorization": "Bearer abc"})
        tracer.end(trace)
        text = (self.tmp_path / "traces.jsonl").read_text()
        assert "secret-value" not in text
        assert "abc123" not in text
        assert "Bearer abc" not in text
        assert trace.as_dict()["spans"][0]["scores"][0]["value"] == 0.9
        event = json.loads(text.splitlines()[-1])
        assert event["schema_version"] == 1
        assert event["task_id"] is None
        assert len(event["event_digest"]) == 64



    def test_prompt_registry_versions_and_promotion(self):
        registry = PromptRegistry()
        v1 = registry.register("planner", "1", "plan: {task}")
        v2 = registry.register("planner", "2", "plan safely: {task}")
        assert registry.get("planner", "1").digest == v1.digest
        assert registry.promote("planner", "2") == v2
        assert registry.get("planner").version == "2"



    def test_dataset_experiment_is_reproducible(self):
        dataset = Dataset("coding", "1", (DatasetItem("a", "A"), DatasetItem("b", "B")))
        result = run_experiment(dataset, lambda value: value.upper(), lambda item, output: {"correctness": float(output == item.expected)})
        assert result.passed
        assert result.scores["correctness"] == 1.0
        assert len(result.dataset_digest) == 64



    def test_retrieval_scores(self):
        scores = retrieval_scores(["a.py", "b.py"], ["a.py", "c.py"])
        assert scores == {"precision": 0.5, "recall": 0.5}



    def test_execute_emits_trace_id_without_changing_contract(self):
        tracer = Tracer(LocalExporter(self.tmp_path / "traces.jsonl", enabled=True))
        task = TaskProfile("t1", "fix login", evidence_required=("source",))

        def worker(task, assignments):
            return {
                "deliverables": ["patch"],
                "verification": ["tests pass"],
                "dimensions": {"correctness": 25, "completeness": 15, "evidence": 15, "verification": 15, "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5},
                "hard_gates": {"tests": True},
                "evidence": [EvidenceItem("test", "tests pass")],
            }

        result = execute(task, worker, tracer=tracer)
        assert result.trace_id
        assert result.receipt is not None and result.receipt.passed
        assert "task_quality" in (self.tmp_path / "traces.jsonl").read_text()



    def test_execute_correlates_trace_with_regression(self):
        tracer = Tracer(LocalExporter(self.tmp_path / "traces.jsonl", enabled=True))
        task = TaskProfile("t13", "fix login", evidence_required=("source",))

        def worker(task, assignments):
            return {
                "deliverables": ["patch"],
                "verification": ["tests pass"],
                "dimensions": {"correctness": 25, "completeness": 15, "evidence": 15, "verification": 15, "scope_discipline": 10, "security_and_safety": 10, "clarity": 5, "maintainability": 5},
                "hard_gates": {"tests": True},
                "evidence": [EvidenceItem("test", "tests pass")],
            }

        regression = run_experiment(
            Dataset("coding-regression", "13", (DatasetItem("fix login", "ok"),)),
            lambda value: "ok",
            lambda item, output: {"correctness": float(output == item.expected)},
        )
        result = execute(task, worker, tracer=tracer, regression_id="reg-t13", regression_result=regression)
        assert result.regression_link is not None
        assert result.regression_link.trace_id == result.trace_id
        assert result.regression_link.regression_id == "reg-t13"
        assert result.regression_link.passed
        exported = (self.tmp_path / "traces.jsonl").read_text()
        assert "regression_pass" in exported
        assert "regression_learning_feedback" in exported
        records = [json.loads(line) for line in exported.splitlines()]
        correlated = [record for record in records if record.get("trace_id") == result.trace_id]
        assert correlated
        assert all(record.get("task_id") == task.task_id for record in correlated)
        learning_records = [
            record for record in correlated
            if isinstance(record.get("span"), dict)
            and record["span"].get("name") == "regression_learning_feedback"
        ]
        assert learning_records




    def test_span_parentage_is_explicit_and_invalid_parents_are_rejected(self):
        trace = Tracer().start("nested")
        root = trace.span("root")
        child = trace.span("child", parent_id=root.span_id)
        sibling = trace.span("sibling")
        assert child.parent_id == root.span_id
        assert sibling.parent_id is None
        with self.assertRaisesRegex(ValueError, "parent_id"):
            trace.span("orphan", parent_id="missing-span")



    def test_prompt_versions_cannot_be_rewritten(self):
        registry = PromptRegistry()
        registry.register("planner", "1", "plan carefully")
        with self.assertRaisesRegex(ValueError, "immutable"):
            registry.register("planner", "1", "skip the checks")



    def test_empty_or_inconsistently_scored_datasets_never_pass(self):
        with self.assertRaisesRegex(ValueError, "empty datasets"):
            run_experiment(Dataset("empty", "1", ()), lambda value: value, lambda item, output: {"quality": 1.0})
        dataset = Dataset("inconsistent", "1", (DatasetItem("a", "a"), DatasetItem("b", "b")))
        def judge(item, output):
            return {"quality": 1.0} if item.input == "a" else {}
        with self.assertRaisesRegex(ValueError, "no evaluation dimensions"):
            run_experiment(dataset, lambda value: value, judge)



    def test_score_reasons_and_bearer_tokens_are_redacted(self):
        exporter = LocalExporter(self.tmp_path / "traces.jsonl", enabled=True)
        tracer = Tracer(exporter)
        trace = tracer.start("privacy")
        span = trace.span("provider", input={"prompt": "Authorization: Bearer secret-token"})
        span.score("quality", 0.8, "api_key=topsecret")
        span.finish({"note": "Bearer abcdefghijklmnop"})
        tracer.end(trace)
        text = (self.tmp_path / "traces.jsonl").read_text()
        assert "secret-token" not in text
        assert "topsecret" not in text
        assert "abcdefghijklmnop" not in text
        assert "[REDACTED]" in text



    def test_concurrent_span_creation_preserves_unique_ids(self):
        trace = Tracer().start("concurrent")
        with ThreadPoolExecutor(max_workers=8) as pool:
            spans = list(pool.map(lambda index: trace.span(f"span-{index}"), range(100)))
        ids = [span.span_id for span in spans]
        assert len(ids) == len(set(ids)) == 100




    def test_export_failure_does_not_change_canonical_trace_outcome(self):
        class BrokenExporter:
            def export(self, trace):
                raise OSError("telemetry unavailable")

        tracer = Tracer(BrokenExporter())
        trace = tracer.start("execution", {"task_id": "task-1"})
        ended = tracer.end(trace, "ok")
        assert ended.status == "ok"
        assert ended.metadata["observability_export_status"] == "failed"
        assert ended.metadata["observability_export_error"] == "OSError"




    def test_terminal_trace_state_is_emitted_once_and_cannot_be_rewritten(self):
        path = self.tmp_path / "terminal-events.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        trace = tracer.start("terminal-state", task_id="task-terminal")

        tracer.end(trace, status="cancelled")
        tracer.end(trace, status="cancelled")
        with self.assertRaisesRegex(ValueError, "already finished"):
            trace.finish("ok")

        records = [json.loads(line) for line in path.read_text().splitlines()]
        terminal = [
            record for record in records
            if record.get("event_type") == "trace_finished"
            and record.get("trace_id") == trace.trace_id
        ]
        assert len(terminal) == 1
        assert terminal[0]["status"] == "cancelled"
        assert terminal[0]["end"] is not None

    def test_set_metadata_is_canonical_for_repeatable_digests(self):
        first = Tracer().start("canonical-set").span(
            "worker", metadata={"labels": {"zeta", "alpha", "middle"}},
        )
        second = Tracer().start("canonical-set").span(
            "worker", metadata={"labels": set(["middle", "zeta", "alpha"])},
        )
        assert first.metadata["labels"] == ["alpha", "middle", "zeta"]
        assert first.metadata["labels"] == second.metadata["labels"]

    def test_free_text_provider_tokens_and_embedded_url_credentials_are_redacted(self):
        path = self.tmp_path / "provider-token-redaction.jsonl"
        tracer = Tracer(LocalExporter(path, enabled=True))
        # Repeated-character fixtures are synthetic and contain no real credentials.
        secrets = (
            "ghp_" + "X" * 24,
            "AKIA" + "0" * 16,
            "xoxb-" + "0" * 16,
            "sk-" + "X" * 24,
            "eyJ" + "X" * 12 + ".eyJ" + "Y" * 12 + "." + "Z" * 16,
            "https://synthetic-user:synthetic-password@example.invalid/path",
        )
        trace = tracer.start("provider-token-redaction")
        trace.span("provider", input={"free_text": " ".join(secrets)}).finish({"status": "ok"})
        tracer.end(trace)
        exported = path.read_text(encoding="utf-8")
        for secret in secrets:
            self.assertNotIn(secret, exported)
        self.assertGreaterEqual(exported.count("[REDACTED]"), len(secrets))

    def test_unsupported_objects_never_invoke_arbitrary_repr(self):
        class UnsafeRepresentation:
            def __repr__(self):
                raise AssertionError("repr must not be invoked by telemetry sanitization")

        trace = Tracer().start("safe-serialization")
        span = trace.span("unsupported", input=UnsafeRepresentation())
        assert span.input == "[UNSUPPORTED_TYPE:UnsafeRepresentation]"


    def test_prompt_metadata_is_recursively_immutable(self):
        registry = PromptRegistry()
        prompt = registry.register("planner", "immutable-1", "plan safely", {"labels": {"team": "runtime"}})
        with self.assertRaises(TypeError):
            prompt.metadata["labels"]["team"] = "untrusted"
        with self.assertRaises(TypeError):
            prompt.metadata["new"] = "value"
        assert registry.get("planner", "immutable-1").metadata["labels"]["team"] == "runtime"

    def test_redaction_covers_quoted_multitoken_and_basic_authorization_values(self):
        exporter = LocalExporter(self.tmp_path / "redaction.jsonl", enabled=True)
        tracer = Tracer(exporter)
        trace = tracer.start("redaction-bypass")
        span = trace.span("secrets", input={
            "note": 'api_key="secret phrase with spaces", keep=this',
            "authorization": "Basic abc123",
        })
        span.finish({"message": 'password="my private phrase", next=visible'})
        tracer.end(trace)
        text = (self.tmp_path / "redaction.jsonl").read_text()
        for secret in ("secret phrase", "abc123", "my private phrase"):
            assert secret not in text
        assert "keep=this" in text or "next=visible" in text

    def test_concurrent_exporters_preserve_complete_jsonl_records(self):
        path = self.tmp_path / "concurrent.jsonl"

        def write_one(index):
            tracer = Tracer(LocalExporter(path, enabled=True))
            trace = tracer.start("concurrent", {"task_id": f"task-{index}"}, task_id=f"task-{index}")
            span = trace.span(f"span-{index}", phase="implementation")
            span.finish({"index": index})
            tracer.end(trace)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write_one, range(40)))
        lines = path.read_text().splitlines()
        records = [json.loads(line) for line in lines]
        assert len(records) >= 40
        assert all(record.get("trace_id") for record in records)
        assert len({record["trace_id"] for record in records}) == 40



def test_multiprocess_exporters_preserve_complete_jsonl_records():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "multiprocess.jsonl"
        context = multiprocessing.get_context("spawn")
        processes = [
            context.Process(target=_export_trace_in_process, args=(str(path), index))
            for index in range(4)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(3)
                raise AssertionError("observability exporter process exceeded its time budget")
            assert process.exitcode == 0

        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        assert len(records) >= 16
        assert len({record["trace_id"] for record in records}) == 4
        assert all(record.get("trace_id") for record in records)
