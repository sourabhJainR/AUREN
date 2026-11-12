#!/usr/bin/env python3
"""Run one deterministic shard of AUREN's cross-platform runtime resilience suite."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _TimedTextTestResult(unittest.TextTestResult):
    """Collect per-test timings without changing test selection or execution order."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._started: dict[int, float] = {}
        self.test_durations: list[tuple[float, str]] = []
        self.module_durations: dict[str, float] = {}

    def startTest(self, test):
        self._started[id(test)] = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        started = self._started.pop(id(test), None)
        if started is not None:
            elapsed = time.monotonic() - started
            test_id = test.id()
            module = test.__class__.__module__
            self.test_durations.append((elapsed, test_id))
            self.module_durations[module] = self.module_durations.get(module, 0.0) + elapsed
        super().stopTest(test)


class _TimedTextTestRunner(unittest.TextTestRunner):
    resultclass = _TimedTextTestResult

    def run(self, test):
        result = super().run(test)
        print("\nSlowest tests (execution time):", flush=True)
        for elapsed, test_id in sorted(result.test_durations, reverse=True)[:10]:
            print(f"  {elapsed:7.2f}s  {test_id}", flush=True)
        print("Module execution totals (sum of test-case times; excludes module fixtures):", flush=True)
        for module, elapsed in sorted(result.module_durations.items(), key=lambda item: item[1], reverse=True)[:10]:
            print(f"  {elapsed:7.2f}s  {module}", flush=True)
        return result


def _run_unittest_modules(modules: list[str]) -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = _TimedTextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_python(arguments: list[str], env_updates: dict[str, str] | None = None) -> int:
    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)
    command = [sys.executable, *arguments]
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _optional_unittest(module: str, path: str) -> int:
    if (ROOT / path).is_file():
        return _run_unittest_modules([module])

    base = os.environ.get("BASE_SHA", "")
    if not base or set(base) == {"0"}:
        base = os.environ.get("GITHUB_SHA", "")
    if not base or set(base) == {"0"}:
        raise RuntimeError(f"Cannot verify whether missing test module was previously tracked: {path}")

    previous = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base, "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if previous.returncode != 0:
        raise RuntimeError(f"Could not verify test-module history for {path} at {base}")
    if previous.stdout.strip():
        raise RuntimeError(f"Previously tracked test module is missing from this candidate: {path}")

    print(f"Deferred {module}: its source change has not added {path} to the candidate yet.", flush=True)
    return 0


def _flatten(suite: unittest.TestSuite | unittest.TestCase):
    if isinstance(suite, unittest.TestSuite):
        for item in suite:
            yield from _flatten(item)
    else:
        yield suite


def _unittest_bucket(module: str, bucket: int, bucket_count: int) -> int:
    if bucket_count < 2 or not 0 <= bucket < bucket_count:
        raise ValueError("invalid unittest bucket")

    loaded = unittest.defaultTestLoader.loadTestsFromName(module)
    cases = sorted(_flatten(loaded), key=lambda case: case.id())
    if not cases:
        raise RuntimeError(f"No unittest cases were collected from {module}")

    selected = [
        case for case in cases
        if int.from_bytes(hashlib.sha256(case.id().encode("utf-8")).digest()[:4], "big") % bucket_count == bucket
    ]
    if not selected:
        raise RuntimeError(f"Unittest bucket {bucket + 1}/{bucket_count} is empty for {module}")

    print(
        f"Running {len(selected)} of {len(cases)} tests from {module} "
        f"(stable bucket {bucket + 1}/{bucket_count})",
        flush=True,
    )
    result = _TimedTextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


def _run_step(step: tuple, env: dict[str, str] | None = None) -> int:
    kind, arguments = step
    if kind == "unittest":
        return _run_unittest_modules(arguments)
    if kind == "pytest":
        return _run_python(["-m", "pytest", "-q", "--durations=10", *arguments], env)
    if kind == "optional-unittest":
        return _optional_unittest(*arguments)
    if kind == "unittest-bucket":
        return _unittest_bucket(*arguments)
    if kind == "repeat-unittest-filter":
        module, pattern, repeats = arguments
        for attempt in range(1, repeats + 1):
            print(f"Concurrency regression pass {attempt}/{repeats}", flush=True)
            result = _run_python(["-m", "unittest", module, "-k", pattern, "-v"], env)
            if result:
                return result
        return 0
    raise ValueError(f"Unknown test step kind: {kind}")


def U(*targets: str) -> tuple:
    return ("unittest", targets, {})


def P(*paths: str, env: dict[str, str] | None = None) -> tuple:
    return ("pytest", paths, env or {})


def O(module: str, path: str) -> tuple:
    return ("optional-unittest", (module, path), {})


def B(module: str, bucket: int, bucket_count: int) -> tuple:
    return ("unittest-bucket", (module, bucket, bucket_count), {})


def R(module: str, pattern: str, repeats: int) -> tuple:
    return ("repeat-unittest-filter", (module, pattern, repeats), {})


SHARDS = {
    "durable-state-core": [
        P("tests/test_continuous_execution.py"),
        O("tests.test_durable_routines", "tests/test_durable_routines.py"),
        U("tests.test_dashboard_sessions"),
        P("tests/test_lifecycle_projection.py"),
        O("tests.test_control_center_api", "tests/test_control_center_api.py"),
        U("tests.test_graph_workspace_registry"),
        U("tests.test_state_paths"),
    ],
    "durable-state-integration": [
        U("tests.test_progress_journal", "tests.test_progress_journal_integration", "tests.test_autonomous_company", "tests.test_engineering_evidence_capture"),
        P("tests/test_execution_controller_cancellation.py", "tests/test_execution_controller_restart.py"),
        P(".ai-harness/tests/test_task_memory.py", env={"PYTHONPATH": ".ai-harness"}),
    ],
    "authority-core": [
        U("tests.test_remote_mcp_service.OAuthIntrospectionVerifierTests", "tests.test_remote_mcp_service.RemoteMCPASGITests"),
        U("tests.test_agent_execution_guard", "tests.test_guarded_filesystem", "tests.test_assigned_subprocess_scope", "tests.test_auren_sdk"),
        U("tests.test_agent_profiles", "tests.test_agent_profile_registry_manager"),
    ],
    "authority-integration": [
        U("tests.test_durable_approval", "tests.test_execution_boundary", "tests.test_governed_sandbox_execution", "tests.test_ai_coding_agency_bridge", "tests.test_agency_team_orchestrator", "tests.test_impact_policy"),
        B("tests.test_trusted_plan_dispatch", 0, 3),
    ],
    "durable-state": [
        P("tests/test_continuous_execution.py"),
        O("tests.test_durable_routines", "tests/test_durable_routines.py"),
        U("tests.test_dashboard_sessions"),
        P("tests/test_lifecycle_projection.py"),
        O("tests.test_control_center_api", "tests/test_control_center_api.py"),
        U("tests.test_graph_workspace_registry"),
        U(
            "tests.test_progress_journal",
            "tests.test_progress_journal_integration",
            "tests.test_autonomous_company",
            "tests.test_engineering_evidence_capture",
        ),
        P("tests/test_execution_controller_cancellation.py", "tests/test_execution_controller_restart.py"),
        P(".ai-harness/tests/test_task_memory.py", env={"PYTHONPATH": ".ai-harness"}),
        U("tests.test_state_paths"),
    ],
    "mcp-runtime": [
        U("tests.test_remote_mcp_service.RemoteMCPServiceTests"),
        U("tests.test_local_mcp_host"),
        U("tests.test_adaptive_trigger", "tests.test_adaptive_runtime_recovery_integration"),
        U("tests.test_local_composition"),
        U("tests.test_remote_mcp_network_acceptance"),
    ],
    "authority": [
        U(
            "tests.test_remote_mcp_service.OAuthIntrospectionVerifierTests",
            "tests.test_remote_mcp_service.RemoteMCPASGITests",
        ),
        U(
            "tests.test_agent_execution_guard",
            "tests.test_guarded_filesystem",
            "tests.test_assigned_subprocess_scope",
            "tests.test_auren_sdk",
        ),
        U("tests.test_agent_profiles", "tests.test_agent_profile_registry_manager"),
        U(
            "tests.test_durable_approval",
            "tests.test_execution_boundary",
            "tests.test_governed_sandbox_execution",
            "tests.test_ai_coding_agency_bridge",
            "tests.test_agency_team_orchestrator",
            "tests.test_impact_policy",
        ),
        B("tests.test_trusted_plan_dispatch", 0, 3),
    ],
    "planning": [
        P(
            "tests/test_workflow_profile.py",
            "tests/test_workflow_profile_integration.py",
            "tests/test_pr_integration_guard.py",
        ),
        U("tests.test_engineering_plan", "tests.test_engineering_plan_store"),
        U("tests.test_engineering_plan_recovery.EngineeringPlanRecoveryBridgeTests"),
        U("tests.test_engineering_plan_recovery.ContinuousEngineeringIntegrationTests"),
        U("tests.test_engineering_plan_dispatch"),
        U(
            "tests.test_persistent_experiment_learning",
            "tests.test_persistent_learning_observability",
            "tests.test_experiment_learning_cycle",
        ),
        B("tests.test_trusted_plan_dispatch", 1, 3),
    ],
    "evidence-faults": [
        U("tests.test_agency_state_graph", "tests.test_state_graph_hardening"),
        U("tests.test_hybrid_model_router"),
        U("tests.test_runtime_recovery", "tests.test_graph_agent_team", "tests.test_graph_agent_crews"),
        U("tests.test_canonical_evidence_ledger"),
        U("tests.test_agency_evolution_gates", "tests.test_agency_evolution_runtime"),
        U(
            "tests.test_vibe_coding_adapters",
            "tests.test_vibe_coding_host",
            "tests.test_provider_operation_lifecycle",
            "tests.test_side_effect_ledger",
            "tests.test_subprocess_provider",
        ),
        U("tests.test_harness_isolation_verifier"),
        U(
            "tests.test_agency_release_lifecycle",
            "tests.test_agency_runtime_release_integration",
            "tests.test_release_rollback_observability",
        ),
        U("tests.test_crash_recovery_fault_injection"),
        U("tests.test_side_effect_ledger_crash_fault_injection", "tests.test_consequential_outer_receipt_crash"),
        U(
            "tests.test_promotion_evidence_chain",
            "tests.test_external_decision_evidence_ingestion",
            "tests.test_evidence_freshness",
        ),
        U("tests.test_persistent_evidence_graph_crash"),
        R("tests.test_promotion_evidence_chain", "concurrent_receipt_claim", 5),
        U("tests.test_bounded_subprocess_fault_injection"),
        U("tests.test_process_isolated_crew_dispatch"),
        U("tests.test_evidence_gate"),
        U("tests.test_harness_task_deadline"),
        B("tests.test_trusted_plan_dispatch", 2, 3),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", choices=sorted(SHARDS))
    args = parser.parse_args()

    print(f"Running Runtime Resilience shard: {args.shard}", flush=True)
    for index, (kind, arguments, env) in enumerate(SHARDS[args.shard], start=1):
        started = time.monotonic()
        print(f"Step {index}/{len(SHARDS[args.shard])}: {kind} {arguments}", flush=True)
        result = _run_step((kind, arguments), env)
        elapsed = time.monotonic() - started
        print(f"Step {index} completed in {elapsed:.1f}s (exit {result})", flush=True)
        if result:
            print(f"Shard failed: {args.shard}", file=sys.stderr, flush=True)
            return result

    print(f"Shard passed: {args.shard}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
