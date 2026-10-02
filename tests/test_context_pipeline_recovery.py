from __future__ import annotations

import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".ai-harness" / "runtime"))

from context_pipeline import ContextAcquisitionPipeline
from context_planner import EvidenceCandidate
from task_memory import relevant


def test_pipeline_pivots_after_a_failed_retrieval_path() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "app.py").write_text("def working_path():\n    return 'ok'\n", encoding="utf-8")
        pipeline = ContextAcquisitionPipeline(root, budget_chars=5000, max_items=8)

        calls: list[str] = []

        def fake_retrieve(mode: str, query: str, budget: int, max_items: int):
            calls.append(mode)
            if mode == "semantic":
                raise RuntimeError("simulated fetch failure")
            if mode == "structural":
                return {
                    "evidence": [
                        EvidenceCandidate(
                            "structural:app.py",
                            "structural",
                            "def working_path(): return 'ok'",
                            relevance=0.95,
                            confidence=0.95,
                            freshness=1.0,
                            cost=40,
                            source="app.py",
                        )
                    ],
                    "snapshot": pipeline.repository.digest(),
                    "paths": ("app.py",),
                    "graph_paths": (),
                }
            raise AssertionError(f"unexpected retry mode: {mode}")

        pipeline._retrieve = fake_retrieve  # type: ignore[method-assign]
        evidence = pipeline.acquire(
            task_id="recovery-test",
            query="working_path",
            phase="investigate",
            intent_digest="intent-1",
            uncertainty="high",
        )

        assert calls == ["semantic", "structural"]
        assert len(evidence.items) == 1
        assert "semantic retrieval failed" in "\n".join(evidence.unknowns)

        history = relevant(root, "context retrieval", limit=10)
        approaches = [row.get("approach") for row in history]
        assert "context-retrieval:semantic" in approaches
        assert "context-retrieval:structural" in approaches


def test_requested_pack_is_preserved_as_supplemental_context() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "app.py").write_text("def working_path():\n    return 'ok'\n", encoding="utf-8")
        pipeline = ContextAcquisitionPipeline(root, budget_chars=5000, max_items=8)
        calls: list[str] = []

        def fake_retrieve(mode: str, query: str, budget: int, max_items: int):
            calls.append(mode)
            candidate = EvidenceCandidate(
                f"{mode}:evidence",
                "repository-pack" if mode == "pack" else "semantic",
                f"{mode} evidence",
                relevance=0.95,
                confidence=0.95,
                freshness=1.0,
                cost=10,
                source=mode,
            )
            return {
                "evidence": [candidate],
                "snapshot": pipeline.repository.digest(),
                "paths": (),
                "graph_paths": (),
                "unknowns": (),
            }

        pipeline._retrieve = fake_retrieve  # type: ignore[method-assign]
        evidence = pipeline.acquire(
            task_id="pack-test",
            query="working_path",
            phase="investigate",
            intent_digest="intent-1",
            uncertainty="high",
            pack=True,
        )

        assert calls[:2] == ["pack", "semantic"]
        assert {item.kind for item in evidence.items} >= {"repository-pack", "semantic"}


def test_provider_unknowns_are_preserved() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "app.py").write_text("def working_path():\n    return 'ok'\n", encoding="utf-8")
        pipeline = ContextAcquisitionPipeline(root, budget_chars=5000, max_items=8)

        def fake_retrieve(mode: str, query: str, budget: int, max_items: int):
            return {
                "evidence": [
                    EvidenceCandidate(
                        "semantic:app",
                        "semantic",
                        "safe evidence",
                        relevance=0.95,
                        confidence=0.95,
                        freshness=1.0,
                        cost=10,
                        source="app.py",
                    )
                ],
                "snapshot": pipeline.repository.digest(),
                "paths": ("app.py",),
                "graph_paths": (),
                "unknowns": ("provider omitted an unreadable file",),
            }

        pipeline._retrieve = fake_retrieve  # type: ignore[method-assign]
        evidence = pipeline.acquire(
            task_id="unknown-test",
            query="working_path",
            phase="investigate",
            intent_digest="intent-1",
            uncertainty="high",
        )

        assert "provider omitted an unreadable file" in evidence.unknowns


def test_typed_decision_advisor_can_select_a_mode_without_overriding_policy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "app.py").write_text("def working_path():\n    return 'ok'\n", encoding="utf-8")
        calls: list[str] = []

        def advisor(state: dict[str, object]) -> tuple[str, float] | None:
            assert "failed_modes" in state
            assert "available_modes" in state
            return "structural", 0.90

        pipeline = ContextAcquisitionPipeline(
            root,
            budget_chars=5000,
            max_items=8,
            decision_advisor=advisor,
            decision_confidence_threshold=0.75,
        )

        def fake_retrieve(mode: str, query: str, budget: int, max_items: int):
            calls.append(mode)
            return {
                "evidence": [
                    EvidenceCandidate(
                        "structural:app",
                        "structural",
                        "safe evidence",
                        relevance=0.95,
                        confidence=0.95,
                        freshness=1.0,
                        cost=10,
                        source="app.py",
                    )
                ],
                "snapshot": pipeline.repository.digest(),
                "paths": ("app.py",),
                "graph_paths": (),
                "unknowns": (),
            }

        pipeline._retrieve = fake_retrieve  # type: ignore[method-assign]
        pipeline.acquire(
            task_id="advisor-test",
            query="working_path",
            phase="implement",
            intent_digest="intent-1",
            uncertainty="high",
        )
        assert calls == ["structural"]


def test_retrieved_context_redacts_credential_like_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "app.py").write_text("def working_path():\n    return 'ok'\n", encoding="utf-8")
        pipeline = ContextAcquisitionPipeline(root, budget_chars=5000, max_items=8)
        secret_value = "X" * 32

        def fake_retrieve(mode: str, query: str, budget: int, max_items: int):
            return {
                "evidence": [
                    EvidenceCandidate(
                        "semantic:secret",
                        "semantic",
                        "password=" + secret_value,
                        relevance=0.95,
                        confidence=0.95,
                        freshness=1.0,
                        cost=10,
                        source="fixture",
                    )
                ],
                "snapshot": pipeline.repository.digest(),
                "paths": ("app.py",),
                "graph_paths": (),
                "unknowns": (),
            }

        pipeline._retrieve = fake_retrieve  # type: ignore[method-assign]
        evidence = pipeline.acquire(
            task_id="redaction-test",
            query="working_path",
            phase="investigate",
            intent_digest="intent-1",
            uncertainty="high",
        )

        assert secret_value not in "\n".join(item.text for item in evidence.items)
        assert "[REDACTED]" in "\n".join(item.text for item in evidence.items)
