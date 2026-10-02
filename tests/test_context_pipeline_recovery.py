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
