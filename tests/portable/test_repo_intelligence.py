from pathlib import Path

from portable.repository_intelligence import RepositoryMap, render_compact


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "def process(value):\n    return value + 1\n\n\ndef run():\n    return process(1)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_app.py").write_text(
        "from app import process\n\ndef test_process():\n    assert process(1) == 2\n",
        encoding="utf-8",
    )
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "noise.py").write_text("def noise(): pass\n", encoding="utf-8")
    return tmp_path


def test_index_is_deterministic_and_has_graph(tmp_path):
    root = _repo(tmp_path)
    first = RepositoryMap.build(root)
    second = RepositoryMap.build(root)
    assert first.digest() == second.digest()
    assert "app.py" in first.files
    assert any(edge.kind == "calls" for edge in first.edges)


def test_task_answer_is_budgeted_and_honest(tmp_path):
    repo = RepositoryMap.build(_repo(tmp_path))
    answer = repo.answer("process", token_budget=30, max_files=4)
    assert answer.token_estimate <= 30
    assert answer.snapshot == repo.digest()
    assert answer.unknowns
    assert "app.py" in answer.files


def test_callers_and_affected_tests(tmp_path):
    repo = RepositoryMap.build(_repo(tmp_path))
    callers = repo.callers("process")
    assert any(edge.source_path == "app.py" for edge in callers)
    tests = repo.affected_tests(["app.py"])
    assert "test_app.py" in tests


def test_compact_output_is_machine_readable(tmp_path):
    answer = RepositoryMap.build(_repo(tmp_path)).answer("process")
    rendered = render_compact(answer)
    assert rendered.startswith("AUREN-REPO-MAP v1")
    assert "FILE app.py" in rendered
