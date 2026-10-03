#!/usr/bin/env python3
"""Run deterministic, dependency-free harness routing and policy evaluations."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / ".ai-harness"
CASES = HARNESS / "evals/cases.jsonl"
ARTIFACT_CONTRACT = HARNESS / "ARTIFACT_UPGRADE_CONTRACT.json"
PROVIDER_HARNESS = ROOT / "scripts/provider_conformance.py"
BEHAVIORAL_HARNESS = ROOT / "scripts/behavioral_conformance.py"
GROUND_TRUTH_HARNESS = ROOT / "scripts/behavioral_conformance_ground_truth.py"
ORACLE_MODULE = ROOT / "scripts/conformance_oracles.py"
EVIDENCE_MODULE = ROOT / "scripts/benchmark_oracles.py"
TRACE_MODULE = ROOT / "scripts/conformance_trace.py"
BEHAVIORAL_TASKS = HARNESS / "conformance/tasks.jsonl"
SKILLS = [
    ROOT / "skills/ai-coding-orchestrator/SKILL.md",
    ROOT / ".agents/skills/ai-coding-orchestrator/SKILL.md",
    ROOT / ".claude/skills/ai-coding-orchestrator/SKILL.md",
]


def load_cases():
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_heuristic_route(task):
    sys.path.insert(0, str(HARNESS))
    from engine import heuristic_route
    return heuristic_route(task)


def policy_checks():
    failures = []
    shared = (
        "Engineering State Ledger",
        "repository-aware",
        "minimal safe change",
        "regression",
        "evidence",
    )
    for path in SKILLS:
        if not path.exists():
            failures.append(f"missing skill: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            failures.append(f"missing frontmatter: {path}")
            continue
        if not re.search(r"(?m)^name:\s*ai-coding-orchestrator\s*$", text):
            failures.append(f"invalid skill name: {path}")
        if not re.search(r"(?m)^description:\s*\S", text):
            failures.append(f"missing skill description: {path}")
        if len(text) > 9000:
            failures.append(f"skill context budget exceeded: {path} ({len(text)} chars)")
        for marker in shared:
            if marker.lower() not in text.lower():
                failures.append(f"shared contract marker missing: {path}: {marker}")
    if not ARTIFACT_CONTRACT.is_file():
        failures.append(f"missing artifact upgrade contract: {ARTIFACT_CONTRACT}")
    else:
        try:
            contract = json.loads(ARTIFACT_CONTRACT.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"invalid artifact upgrade contract: {exc}")
        else:
            expected = {
                "contract_version": 2,
                "artifact_type": "auren-portable",
                "upgrade_mode": "side-by-side",
                "state_policy": "preserve",
                "activation": "atomic",
                "rollback": "required",
                "downgrade": "explicit_install_only",
                "automatic_update_downgrade": "forbidden",
                "same_version_different_hash": "new_build",
                "compatible_previous_artifacts": "supported",
                "migration": "versioned",
                "verification": "required",
                "behavior_activation": "validated_then_active",
            }
            for key, value in expected.items():
                if contract.get(key) != value:
                    failures.append(f"artifact contract mismatch: {key}={contract.get(key)!r}")
    plugin = ROOT / ".claude-plugin/plugin.json"
    marketplace = ROOT / ".claude-plugin/marketplace.json"
    if not plugin.exists() or not marketplace.exists():
        failures.append("plugin or marketplace manifest missing")
    if not PROVIDER_HARNESS.exists():
        failures.append(f"missing provider conformance harness: {PROVIDER_HARNESS}")
    else:
        p = subprocess.run([sys.executable, str(PROVIDER_HARNESS), "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        if p.returncode != 0:
            failures.append("provider conformance harness failed static contract checks")
        else:
            try:
                if not json.loads(p.stdout).get("release_ready"):
                    failures.append("provider conformance harness is not release-ready")
            except json.JSONDecodeError:
                failures.append("provider conformance harness did not emit valid JSON")
    for path, label in (
        (BEHAVIORAL_HARNESS, "behavioral conformance harness"),
        (GROUND_TRUTH_HARNESS, "ground-truth behavioral harness"),
        (ORACLE_MODULE, "behavioral oracle module"),
        (EVIDENCE_MODULE, "benchmark evidence module"),
        (TRACE_MODULE, "benchmark trace module"),
    ):
        if not path.exists():
            failures.append(f"missing {label}: {path}")
        else:
            check = subprocess.run([sys.executable, "-m", "py_compile", str(path)], cwd=ROOT, capture_output=True, check=False)
            if check.returncode != 0:
                failures.append(f"{label} does not compile")
    if not BEHAVIORAL_TASKS.exists():
        failures.append(f"missing behavioral task corpus: {BEHAVIORAL_TASKS}")
    else:
        try:
            tasks = [json.loads(line) for line in BEHAVIORAL_TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            failures.append(f"invalid behavioral task corpus: {exc}")
            tasks = []
        if len(tasks) != 10:
            failures.append(f"behavioral conformance corpus must contain exactly 10 tasks; found {len(tasks)}")
        ids = [task.get("id") for task in tasks]
        if len(ids) != len(set(ids)):
            failures.append("behavioral conformance corpus contains duplicate task ids")
        for task in tasks:
            missing = sorted({"id", "name", "task", "mode", "required_capabilities", "acceptance"} - set(task))
            if missing:
                failures.append(f"behavioral task {task.get('id', '<unknown>')} missing fields: {missing}")
    context_index = ROOT / ".agents/skills/ai-coding-orchestrator/context/INDEX.md"
    if not context_index.exists():
        failures.append(f"missing progressive context index: {context_index}")
    return failures


def evaluate_case(case):
    route = load_heuristic_route(case["prompt"])
    problems = []
    selected = set(route.get("capabilities", []))
    required = set(case.get("required_capabilities", case.get("capabilities", [])))
    forbidden = set(case.get("forbidden_capabilities", []))
    if route["mode"] != case["expected_mode"]:
        problems.append(f"mode expected={case['expected_mode']} actual={route['mode']}")
    if required - selected:
        problems.append(f"missing capabilities={sorted(required - selected)}")
    if selected & forbidden:
        problems.append(f"forbidden capabilities={sorted(selected & forbidden)}")
    return not problems, problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = []
    for case in load_cases():
        passed, problems = evaluate_case(case)
        results.append({"id": case["id"], "passed": passed, "problems": problems})
    policy_failures = policy_checks()
    passed = sum(item["passed"] for item in results)
    total = len(results)
    report = {
        "cases": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "policy_failures": policy_failures,
        "release_ready": passed == total and not policy_failures,
        "results": results,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Routing evals: {passed}/{total} passed ({report['accuracy']:.1%})")
        for item in results:
            print(("PASS" if item["passed"] else "FAIL") + " " + item["id"] + (f": {'; '.join(item['problems'])}" if item["problems"] else ""))
        if policy_failures:
            print("Policy failures:")
            for item in policy_failures:
                print("- " + item)
        print("RELEASE READY" if report["release_ready"] else "NOT RELEASE READY")
    return 0 if report["release_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
