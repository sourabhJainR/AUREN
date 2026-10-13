import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "behavioral_conformance.py"
TASKS = ROOT / ".ai-harness" / "conformance" / "tasks.jsonl"


def load_module():
    spec = importlib.util.spec_from_file_location("auren_behavioral_conformance_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_corpus_has_exactly_ten_representative_tasks():
    module = load_module()
    tasks = module.load_tasks()
    module.validate_tasks(tasks)
    assert len(tasks) == 10
    assert {t["mode"] for t in tasks} == {"read_only", "write"}


def test_objective_scoring_does_not_trust_provider_claims(tmp_path):
    module = load_module()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    (checkout / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-qm", "baseline"], cwd=checkout, check=True)
    (checkout / "README.md").write_text("changed\n", encoding="utf-8")
    claim = {key: "perfect" for key in module.REQUIRED_FIELDS}
    claim["recovery"] = "successful recovery"
    dims, evidence, missing = module.objective_score({"id": "BC-TEST", "task": "read repository", "mode": "read_only"}, checkout, [], 0, claim)
    assert not missing
    assert dims["scope_adherence"] == 0.0
    assert evidence["provider_claims_used_for_scoring"] is False


def test_objective_scoring_uses_real_command_trace(tmp_path):
    module = load_module()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess = __import__("subprocess")
    subprocess.run(["git", "init", "-q"], cwd=checkout, check=True)
    (checkout / "x.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=checkout, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-qm", "baseline"], cwd=checkout, check=True)
    trace = [{"command": "pytest", "args": ["tests/test_x.py"], "cwd": str(checkout), "returncode": 0}]
    claim = {key: "evidence" for key in module.REQUIRED_FIELDS}
    dims, evidence, _ = module.objective_score({"id": "BC-TEST", "task": "run focused test", "mode": "write"}, checkout, trace, 0, claim)
    assert dims["tool_usage"] == 1.0
    assert dims["verification_evidence"] == 1.0
    assert evidence["successful_verification_count"] == 1


def test_pairwise_report_shape_is_provider_neutral():
    module = load_module()
    assert "claude" in module.load_json(module.MATRIX)["providers"]
    assert "codex" in module.load_json(module.MATRIX)["providers"]
    assert "gemini" in module.load_json(module.MATRIX)["providers"]
    assert "chatgpt" in module.load_json(module.MATRIX)["providers"]
