import json
import tempfile
import unittest
from pathlib import Path

from portable.benchmark_manifest import BenchmarkManifest
from portable.generalization_arena import IndependentGeneralizationArena


class IndependentGeneralizationArenaTests(unittest.TestCase):
    def _corpus(self, root: Path) -> None:
        manifest = BenchmarkManifest.seal(
            "independent-generalization", "1",
            train_domains=("logic", "coding", "research"),
            holdout_domains=("planning", "science", "design"),
            oracle_ids=("oracle-v1",),
        )
        (root / "manifest.json").write_text(json.dumps({
            "benchmark_id": manifest.benchmark_id,
            "version": manifest.version,
            "train_domains": manifest.train_domains,
            "holdout_domains": manifest.holdout_domains,
            "oracle_ids": manifest.oracle_ids,
            "digest": manifest.digest,
        }), encoding="utf-8")
        cases = []
        for i, (domain, holdout) in enumerate((
            ("logic", False), ("coding", False), ("research", False),
            ("planning", True), ("science", True), ("design", True),
        )):
            cases.append({
                "case_id": f"case-{i}",
                "kind": ("reasoning", "transfer", "memory", "causal", "novel", "calibration")[i],
                "domain": domain,
                "holdout": holdout,
                "task": {"answer": i},
                "oracle_id": "oracle-v1",
            })
        (root / "cases.json").write_text(json.dumps(cases), encoding="utf-8")

    def test_requires_corpus_outside_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            corpus = runtime / "corpus"
            corpus.mkdir(parents=True)
            with self.assertRaises(ValueError):
                IndependentGeneralizationArena(
                    corpus_root=corpus, runtime_root=runtime, oracles={"oracle-v1": lambda _: True}
                )

    def test_in_process_policy_execution_requires_explicit_test_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            arena = IndependentGeneralizationArena(
                corpus_root=corpus,
                runtime_root=runtime,
                oracles={"oracle-v1": lambda observed: True},
            )
            called = False

            def runner(task):
                nonlocal called
                called = True
                return task

            with self.assertRaisesRegex(RuntimeError, "in-process policy runners are disabled"):
                arena.evaluate(runners={"case-0": runner})
            self.assertFalse(called)

    def test_sealed_external_corpus_evaluates_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            arena = IndependentGeneralizationArena(
                corpus_root=corpus,
                runtime_root=runtime,
                oracles={"oracle-v1": lambda observed: observed["answer"] >= 0},
                allow_in_process_runners=True,
            )
            report = arena.evaluate(
                runners={f"case-{i}": (lambda task: task) for i in range(6)}
            )
            self.assertTrue(report.gate_passed)
            self.assertEqual(set(report.holdout_domains), {"planning", "science", "design"})

    def test_rejects_corpus_ancestor_of_runtime_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            with self.assertRaises(ValueError):
                IndependentGeneralizationArena(
                    corpus_root=root, runtime_root=runtime, oracles={"oracle-v1": lambda _: True}
                )

    def test_rejects_non_boolean_holdout_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            cases = json.loads((corpus / "cases.json").read_text(encoding="utf-8"))
            cases[0]["holdout"] = "false"
            (corpus / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "holdout must be a boolean"):
                IndependentGeneralizationArena(
                    corpus_root=corpus, runtime_root=runtime, oracles={"oracle-v1": lambda _: True}
                ).load()

    def test_tampered_manifest_fails_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            data = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
            data["holdout_domains"] = ["not-sealed"]
            (corpus / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                IndependentGeneralizationArena(
                    corpus_root=corpus, runtime_root=runtime, oracles={"oracle-v1": lambda _: True}
                ).load()

    def test_detects_corpus_mutation_during_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            arena = IndependentGeneralizationArena(
                corpus_root=corpus,
                runtime_root=runtime,
                oracles={"oracle-v1": lambda observed: observed["answer"] >= 0},
                allow_in_process_runners=True,
            )
            mutated = False

            def mutating_runner(task):
                nonlocal mutated
                if not mutated:
                    cases_path = corpus / "cases.json"
                    cases = json.loads(cases_path.read_text(encoding="utf-8"))
                    cases[0]["task"]["answer"] = 999
                    cases_path.write_text(json.dumps(cases), encoding="utf-8")
                    mutated = True
                return task

            with self.assertRaisesRegex(ValueError, "changed during evaluation"):
                arena.evaluate(runners={f"case-{i}": mutating_runner for i in range(6)})

    def test_missing_runner_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime, corpus = root / "runtime", root / "sealed"
            runtime.mkdir()
            corpus.mkdir()
            self._corpus(corpus)
            report = IndependentGeneralizationArena(
                corpus_root=corpus,
                runtime_root=runtime,
                oracles={"oracle-v1": lambda observed: observed["answer"] >= 0},
                allow_in_process_runners=True,
            ).evaluate(runners={})
            self.assertFalse(report.gate_passed)
            self.assertTrue(any(result.error for result in report.results))


if __name__ == "__main__":
    unittest.main()
