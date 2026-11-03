from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import unittest
from urllib.parse import quote
import uuid

from portable.adaptive_runtime import AdaptiveRuntime
from portable.agent_org_runtime import authorize_agent_assignment
from portable.durable_approval import DurableApprovalStore
from portable.engineering_context_packet import build_context_packet
from portable.engineering_design_guard import EngineeringDesignGuard
from portable.engineering_plan import EngineeringPlan, PlanTask
from portable.engineering_plan_recovery import EngineeringPlanRecoveryBridge
from portable.engineering_plan_store import EngineeringPlanStore
from portable.orchestration import Graph, Node, NodeKind
from portable.runtime_recovery import RuntimeExecutionGuard


TEST_FILES = (
    "tests/test_one_page_brief.py",
    "tests/test_news_sentiment.py",
    "tests/test_research_graph.py",
    "tests/test_universe_screener.py",
    "tests/test_point_in_time.py",
    "tests/test_market_intelligence.py",
)
_HANDLER_REPORTS: dict[str, dict[str, object]] = {}


class _DockerUnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


class _ContainerizedStockResearchRunner:
    """Host-owned fixed-command runner; the app executes in a no-network container."""

    def __init__(self, *, image_ref: str, image_id: str, source_revision: str) -> None:
        if not image_ref or not image_id.startswith("sha256:"):
            raise ValueError("a pinned container image reference and immutable image ID are required")
        if not re.fullmatch(r"[0-9a-f]{40}", source_revision):
            raise ValueError("TradingDYOR source revision must be a full Git commit SHA")
        self.image_ref = image_ref
        self.image_id = image_id
        self.source_revision = source_revision
        self.socket_path = os.environ.get("AUREN_DOCKER_SOCKET", "/var/run/docker.sock")
        if not Path(self.socket_path).exists():
            raise RuntimeError("Docker Engine Unix socket is unavailable")

    def _request(self, method: str, path: str, body: dict | None = None, *, timeout: float = 900.0):
        connection = _DockerUnixConnection(self.socket_path, timeout=timeout)
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        connection.request(
            method,
            path,
            body=encoded,
            headers={"Host": "localhost", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read()
        status = response.status
        connection.close()
        if not 200 <= status < 300:
            detail = payload.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Docker Engine API {method} returned HTTP {status}: {detail}")
        return status, payload

    @staticmethod
    def _verify_container_config(container: dict, expected_env: dict[str, str]) -> dict[str, object]:
        config = container.get("Config") or {}
        host = container.get("HostConfig") or {}
        mounts = container.get("Mounts") or []
        security = set(host.get("SecurityOpt") or ())
        cap_drop = set(host.get("CapDrop") or ())
        tmpfs = host.get("Tmpfs") or {}
        actual_env = {
            item.split("=", 1)[0]: item.split("=", 1)[1]
            for item in (config.get("Env") or []) if "=" in item
        }
        extra_env = set(actual_env) - set(expected_env)
        environment_matches = (
            all(actual_env.get(name) == value for name, value in expected_env.items())
            and extra_env.issubset({"HOSTNAME"})
            and ("HOSTNAME" not in actual_env or bool(actual_env["HOSTNAME"]))
        )
        expected_tmpfs = {
            "/tmp", "/opt/TradingDYOR/data", "/opt/TradingDYOR/.tradingdyor",
        }
        only_temporary_mounts = all(
            mount.get("Type") == "tmpfs" and mount.get("Destination") in expected_tmpfs
            for mount in mounts
        )
        checks = {
            "user": config.get("User") == "65534:65534",
            "entrypoint_fixed": config.get("Entrypoint") == ["/usr/local/bin/python"],
            "command_fixed": config.get("Cmd") == ["-m", "pytest", "-q", "-p", "no:cacheprovider", *TEST_FILES],
            "working_directory_fixed": config.get("WorkingDir") == "/opt/TradingDYOR",
            "network_none": host.get("NetworkMode") == "none",
            "read_only_root": host.get("ReadonlyRootfs") is True,
            "capabilities_dropped": "ALL" in cap_drop,
            "no_new_privileges": "no-new-privileges:true" in security,
            "pids_limit": host.get("PidsLimit") == 64,
            "memory_limit": host.get("Memory") == 2 * 1024 * 1024 * 1024,
            "swap_disabled": host.get("MemorySwap") == 2 * 1024 * 1024 * 1024,
            "cpu_limit": host.get("NanoCpus") == 2_000_000_000,
            "no_host_binds": not host.get("Binds") and not host.get("VolumesFrom") and not host.get("Devices"),
            "no_host_mounts": not host.get("Binds") and not host.get("Mounts") and only_temporary_mounts,
            "temporary_paths_only": set(tmpfs) == expected_tmpfs,
            "host_environment_not_inherited": environment_matches,
            "not_privileged": host.get("Privileged") is not True,
        }
        if not all(checks.values()):
            failed = [name for name, passed in checks.items() if not passed]
            raise PermissionError("Docker did not enforce required acceptance isolation: " + ", ".join(failed))
        return checks

    def run(self, *, work_key: str, source_revision: str) -> dict[str, object]:
        if source_revision != self.source_revision:
            raise PermissionError("task source revision differs from the host-pinned test image")
        _, image_payload = self._request(
            "GET", "/v1.43/images/" + quote(self.image_ref, safe=":") + "/json", timeout=15,
        )
        image_info = json.loads(image_payload)
        labels = (image_info.get("Config") or {}).get("Labels") or {}
        if image_info.get("Id") != self.image_id:
            raise PermissionError("acceptance image tag no longer resolves to the host-pinned image ID")
        if labels.get("org.opencontainers.image.revision") != self.source_revision:
            raise PermissionError("acceptance image does not contain the pinned TradingDYOR source revision")
        if labels.get("org.opencontainers.image.source") != "https://github.com/sourabhJainR/TradingDYOR":
            raise PermissionError("acceptance image source repository label is not trusted")

        name = "auren-stock-acceptance-" + uuid.uuid4().hex
        create_payload = {
            "Image": self.image_id,
            "Entrypoint": ["/usr/local/bin/python"],
            "Cmd": ["-m", "pytest", "-q", "-p", "no:cacheprovider", *TEST_FILES],
            "WorkingDir": "/opt/TradingDYOR",
            "User": "65534:65534",
            "Tty": True,
            "OpenStdin": False,
            "Env": [
                "PYTHONDONTWRITEBYTECODE=1",
                "HOME=/tmp/auren-home",
                "TMPDIR=/tmp",
                "TRADINGDYOR_PIT_DB=/tmp/tradingdyor-pit.sqlite3",
            ],
            "Labels": {
                "com.auren.acceptance": "stock-research",
                "com.auren.work-key-digest": hashlib.sha256(work_key.encode()).hexdigest(),
            },
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "PidsLimit": 64,
                "Memory": 2 * 1024 * 1024 * 1024,
                "MemorySwap": 2 * 1024 * 1024 * 1024,
                "NanoCpus": 2_000_000_000,
                "RestartPolicy": {"Name": "no"},
                "Tmpfs": {
                    "/tmp": "rw,noexec,nosuid,size=256m,mode=1777",
                    "/opt/TradingDYOR/data": "rw,nosuid,size=64m,mode=1777",
                    "/opt/TradingDYOR/.tradingdyor": "rw,nosuid,size=64m,mode=1777",
                },
            },
        }
        container_id = None
        cleanup_error = None
        sandbox_checks = None
        output = ""
        exit_code = 125
        try:
            _, created = self._request(
                "POST", "/v1.43/containers/create?name=" + quote(name, safe=""), create_payload,
            )
            container_id = json.loads(created)["Id"]
            _, inspected = self._request("GET", f"/v1.43/containers/{container_id}/json", timeout=15)
            image_env = (image_info.get("Config") or {}).get("Env") or []
            expected_env = {
                item.split("=", 1)[0]: item.split("=", 1)[1]
                for item in [*image_env, *create_payload["Env"]] if "=" in item
            }
            sandbox_checks = self._verify_container_config(json.loads(inspected), expected_env)
            self._request("POST", f"/v1.43/containers/{container_id}/start")
            _, waited = self._request(
                "POST", f"/v1.43/containers/{container_id}/wait?condition=not-running",
                timeout=900,
            )
            exit_code = int(json.loads(waited).get("StatusCode", 125))
            _, logs = self._request(
                "GET",
                f"/v1.43/containers/{container_id}/logs?stdout=1&stderr=1&timestamps=0",
                timeout=30,
            )
            output = logs.decode("utf-8", errors="replace")[-20000:]
        finally:
            if container_id:
                try:
                    self._request("DELETE", f"/v1.43/containers/{container_id}?force=true&v=true", timeout=15)
                except Exception as exc:
                    cleanup_error = f"{type(exc).__name__}: {exc}"
        if cleanup_error:
            raise RuntimeError("acceptance container cleanup failed: " + cleanup_error)

        passed_match = re.search(r"(\d+) passed", output)
        failed_match = re.search(r"(\d+) failed", output)
        skipped_match = re.search(r"(\d+) skipped", output)
        counts = {
            "passed": int(passed_match.group(1)) if passed_match else 0,
            "failed": int(failed_match.group(1)) if failed_match else 0,
            "skipped": int(skipped_match.group(1)) if skipped_match else 0,
        }
        output_digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
        success = exit_code == 0 and counts["failed"] == 0 and counts["passed"] > 0
        evidence = [
            "tradingdyor-source-sha:" + self.source_revision,
            "container-image-id:" + self.image_id,
            "pytest-exit-code:" + str(exit_code),
            "pytest-output-sha256:" + output_digest,
            "pytest-passed:" + str(counts["passed"]),
            "pytest-failed:" + str(counts["failed"]),
            "pytest-skipped:" + str(counts["skipped"]),
        ]
        return {
            "success": success,
            "source_revision": self.source_revision,
            "image_ref": self.image_ref,
            "image_id": self.image_id,
            "container_id": container_id[:12] if container_id else None,
            "container_removed": True,
            "sandbox_checks": sandbox_checks,
            "exit_code": exit_code,
            "counts": counts,
            "output_digest": output_digest,
            "output_tail": output[-3000:],
            "evidence": evidence,
            "network_mode": "none",
            "root_filesystem_read_only": True,
            "host_mounts": [],
            "container_user": "65534:65534",
            "capabilities_dropped": ["ALL"],
            "no_new_privileges": True,
            "pids_limit": 64,
            "memory_limit_bytes": 2 * 1024 * 1024 * 1024,
            "cpu_limit_nano_cpus": 2_000_000_000,
        }


def run_stock_research_acceptance(payload):
    """Importable QA handler dispatched only by AUREN's trusted plan root."""
    try:
        runner = _ContainerizedStockResearchRunner(
            image_ref=os.environ["AUREN_STOCK_RESEARCH_IMAGE_REF"],
            image_id=os.environ["AUREN_STOCK_RESEARCH_IMAGE_ID"],
            source_revision=os.environ["TRADINGDYOR_SOURCE_SHA"],
        )
    except Exception as exc:
        return {"success": False, "detail": f"host-pinned container runner unavailable: {type(exc).__name__}: {exc}"}
    work_key = payload.get("work_key")
    source_revision = payload.get("source_revision")
    if not isinstance(work_key, str) or not work_key:
        return {"success": False, "detail": "stable work key is missing"}
    if payload.get("image_ref") != runner.image_ref or payload.get("image_id") != runner.image_id:
        return {"success": False, "detail": "task image binding differs from the host-pinned image"}
    try:
        report = runner.run(work_key=work_key, source_revision=source_revision)
    except Exception as exc:
        report = {
            "success": False,
            "source_revision": source_revision,
            "error": f"{type(exc).__name__}: {exc}",
            "evidence": ["stock-research-container-runner:failed"],
        }
    _HANDLER_REPORTS[work_key] = report
    return {
        "success": report["success"] is True,
        "detail": (
            f"TradingDYOR isolated contract suite: {report.get('counts', {})}; "
            f"source={source_revision}; image={report.get('image_id', 'unavailable')}"
        ),
        "evidence": report.get("evidence", ()),
    }


@unittest.skipUnless(
    all(os.environ.get(name) for name in (
        "AUREN_SOURCE_SHA", "TRADINGDYOR_REPO_PATH", "TRADINGDYOR_SOURCE_SHA",
        "AUREN_STOCK_RESEARCH_IMAGE_REF", "AUREN_STOCK_RESEARCH_IMAGE_ID",
    )),
    "requires the dedicated stock research runtime acceptance workflow",
)
class StockResearchCanonicalRuntimeAcceptanceTests(unittest.TestCase):
    def test_stock_research_application_runs_through_trusted_plan_dispatch(self):
        repo_root = Path(__file__).resolve().parents[1]
        expected_repo = (repo_root / "external" / "TradingDYOR").resolve()
        configured_repo = Path(os.environ["TRADINGDYOR_REPO_PATH"]).resolve()
        self.assertEqual(expected_repo, configured_repo)
        self.assertTrue((configured_repo / "pyproject.toml").is_file())
        self.assertTrue((configured_repo / "tradingdyor" / "api.py").is_file())

        source_revision = os.environ["TRADINGDYOR_SOURCE_SHA"]
        image_ref = os.environ["AUREN_STOCK_RESEARCH_IMAGE_REF"]
        image_id = os.environ["AUREN_STOCK_RESEARCH_IMAGE_ID"]
        requirements = (
            "The screener and ticker research contract must expose market, company and source evidence.",
            "The one-page brief must combine fundamentals, recommendation, news, sentiment and provenance.",
            "Every price and research observation must retain source timing and freshness context.",
            "Missing or stale evidence must remain visible and must not be promoted to a confident recommendation.",
            "Provider errors and partial research must produce explicit unavailable or partial states.",
            "The isolated acceptance suite must use deterministic fixtures and must not call live market providers.",
        )
        acceptance = (
            "tests/test_one_page_brief.py passes, including the one-page UI and partial-evidence behavior.",
            "tests/test_news_sentiment.py passes, including source timestamps, duplicate handling and provider failure.",
            "tests/test_research_graph.py passes, including dependency ordering and partial synthesis.",
            "tests/test_universe_screener.py passes, including stale-cache fallback and filter behavior.",
            "tests/test_point_in_time.py passes, including source-availability ordering.",
            "tests/test_market_intelligence.py passes, including insider, earnings, corporate-action and sector signals.",
            "The test image is pinned to the exact TradingDYOR source SHA and runs with no network, no host mounts and a read-only root filesystem.",
        )
        review = EngineeringDesignGuard.review_request(
            intent="Exercise the TradingDYOR one-page stock research application through AUREN's canonical supervisor and trusted plan dispatch.",
            requirements=requirements,
            assumptions=(
                "The checked-out TradingDYOR revision is the candidate application under test.",
                "The acceptance image is built from that exact revision and contains no provider credentials.",
            ),
            acceptance_criteria=acceptance,
            constraints=(
                "The test container has no network and uses deterministic provider fixtures.",
                "The container receives no host mounts, credentials or inherited environment secrets.",
                "A failed or skipped test cannot be reported as a passed acceptance run.",
            ),
            non_goals=(
                "Live market data, provider credentials, provider licensing and redistribution approval are not exercised by this fixture acceptance.",
                "This CI run does not prove production deployment or physical multi-host isolation.",
            ),
        )
        self.assertTrue(review["implementation_may_start"], review["unresolved_contradictions"])

        state_root = Path(self._testMethodName + "-" + uuid.uuid4().hex)
        state_root = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / state_root
        runtime = AdaptiveRuntime(
            Graph([Node("stock-acceptance", NodeKind.DETERMINISTIC, lambda state: {"ready": True})]),
            state_root=state_root,
        )
        controller = runtime.execution_controller
        guard_policy_path = controller.root / ".auren" / "guard_policy.json"
        guard_policy_path.parent.mkdir(parents=True, exist_ok=True)
        guard_policy_path.write_text(
            json.dumps({"schema_version": "auren-guard-v1", "default_effect": "allow", "rules": []}),
            encoding="utf-8",
        )
        controller.register_handler(
            "stock-research-acceptance",
            f"{__name__}:run_stock_research_acceptance",
            execution_class="read_only",
            agent_action_class="run_tests",
        )

        run_id = "stock-research-" + source_revision[:12] + "-" + uuid.uuid4().hex[:8]
        task_id = "stock_research_acceptance"
        plan = EngineeringPlan(
            run_id=run_id,
            goal="Verify the one-page stock research application through the canonical AUREN runtime",
            requirements=tuple(requirements),
            tasks=(
                PlanTask(
                    task_id=task_id,
                    title="Run the pinned stock research contract suite in an isolated container",
                    acceptance_criteria=tuple(acceptance),
                    rollback_plan=(
                        "Stop the acceptance task, remove the disposable container, preserve the failure report, "
                        "and leave the external application repository unchanged."
                    ),
                    risk="high",
                ),
            ),
            source_revision=source_revision,
            policy_digest=str(review["digest"]),
            budget={"attempts": 1, "dispatches": 1},
        )
        store = EngineeringPlanStore(state_root / "engineering-plans.sqlite3")
        recovery = EngineeringPlanRecoveryBridge(
            store=store,
            ledger=runtime.recovery_ledger,
        )
        run = recovery.start(plan)
        run = recovery.transition_task(
            run_id, task_id, "ready", reason="requirements review passed; pinned app source and test image are available",
        )
        run = recovery.transition_task(
            run_id, task_id, "running", reason="QA specialist assigned under the canonical controller",
        )
        packet = build_context_packet(
            plan=plan,
            run=run,
            task_id=task_id,
            policy_constraints=(
                "Use the host-pinned test image only.",
                "Do not enable network access or mount host files into the test container.",
                "Do not treat fixture results as live market-data validation.",
            ),
            budget_remaining={"attempts": 1, "dispatches": 1},
            next_safe_action="Run the pinned TradingDYOR contract suite in the isolated no-network container.",
        )

        approval_store = DurableApprovalStore(controller)
        guard = RuntimeExecutionGuard(max_steps=8, max_attempts=4, deadline_seconds=900)
        admission_digest = guard.snapshot()["digest"]
        assignment_scope = ("external/TradingDYOR",)

        def assignment_resolver(context):
            deadline = context.get("assignment_deadline_seconds", context.get("deadline_seconds"))
            return authorize_agent_assignment(
                role_id="QA-01",
                manager_id="EM-ENG-01",
                action_class="run_tests",
                scope=assignment_scope,
                authorizer=lambda request: (
                    request.get("role_id") == "QA-01"
                    and request.get("manager_id") == "EM-ENG-01"
                    and request.get("action_class") == "run_tests"
                    and request.get("scope") == assignment_scope
                    and request.get("work_key") == context.get("dispatch_key")
                ),
                policy_path=guard_policy_path,
                work_key=context["dispatch_key"],
                task_payload=context["payload"],
                deadline_seconds=deadline,
            )

        runtime.configure_trusted_plan_dispatch(
            store=store,
            recovery_bridge=recovery,
            approval_store=approval_store,
            budget_resolver=lambda _run, _task, _handler: {
                "budget_id": "stock-research:" + run_id,
                "guard": guard,
                "checkpoint_digest": admission_digest,
                "policy_digest": str(review["digest"]),
            },
            approval_resolver=lambda _run, _task, _handler: None,
            assignment_resolver=assignment_resolver,
        )
        dispatch_key = "stock-research-acceptance:" + source_revision
        task = runtime.dispatch_engineering_plan_task(
            run_id=run_id,
            task_id=task_id,
            dispatch_key=dispatch_key,
            handler="stock-research-acceptance",
            packet=packet,
            payload={
                "resource": "external/TradingDYOR",
                "source_revision": source_revision,
                "image_ref": image_ref,
                "image_id": image_id,
            },
            deadline_seconds=900,
        )

        execution_error = None
        try:
            controller.run_once(task.id)
        except Exception as exc:
            execution_error = f"{type(exc).__name__}: {exc}"
        durable_task = controller.task(task.id)
        handler_report = _HANDLER_REPORTS.pop(dispatch_key, None)
        successful = (
            durable_task is not None
            and durable_task.state == "completed"
            and isinstance(handler_report, dict)
            and handler_report.get("success") is True
        )
        evidence_refs = tuple(handler_report.get("evidence", ())) if isinstance(handler_report, dict) else ()
        if successful:
            recovery.transition_task(
                run_id, task_id, "verifying",
                reason="controller task completed; host is checking the isolated test receipt",
            )
            recovery.transition_task(
                run_id, task_id, "passed",
                reason="isolated stock research acceptance passed",
                evidence_refs=evidence_refs,
            )
        else:
            recovery.transition_task(
                run_id, task_id, "failed",
                reason=execution_error or str((handler_report or {}).get("error", "stock research acceptance failed")),
            )
        _, verified_run = recovery.load_verified(run_id)
        checkpoint = recovery.ledger.get_checkpoint(run_id, recovery.PHASE)
        requirement_evidence = [
            {"requirement": requirements[0], "acceptance_criteria": [acceptance[0], acceptance[3]], "test_files": [TEST_FILES[0], TEST_FILES[3]]},
            {"requirement": requirements[1], "acceptance_criteria": [acceptance[0], acceptance[1]], "test_files": [TEST_FILES[0], TEST_FILES[1]]},
            {"requirement": requirements[2], "acceptance_criteria": [acceptance[1], acceptance[4]], "test_files": [TEST_FILES[1], TEST_FILES[4]]},
            {"requirement": requirements[3], "acceptance_criteria": [acceptance[0], acceptance[1]], "test_files": [TEST_FILES[0], TEST_FILES[1]]},
            {"requirement": requirements[4], "acceptance_criteria": [acceptance[1], acceptance[2], acceptance[3]], "test_files": [TEST_FILES[1], TEST_FILES[2], TEST_FILES[3]]},
            {"requirement": requirements[5], "acceptance_criteria": [acceptance[6]], "test_files": list(TEST_FILES)},
        ]
        for item in requirement_evidence:
            item["status"] = "passed" if successful else "failed"
        report = {
            "schema_version": "auren-stock-research-acceptance-v1",
            "status": "PASS" if successful else "FAIL",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "application": "sourabhJainR/TradingDYOR",
            "execution_path": [
                "AdaptiveRuntime", "TrustedPlanDispatchRoot", "EngineeringPlanDispatch",
                "ExecutionController.run_once", "QA-01", "isolated Docker test container",
            ],
            "auren_candidate_sha": os.environ["AUREN_SOURCE_SHA"],
            "tradingdyor_source_sha": source_revision,
            "requirements": list(requirements),
            "assumptions": list(review["assumptions"]),
            "acceptance_criteria": list(acceptance),
            "requirement_evidence": requirement_evidence,
            "release_decision": "fixture_acceptance_only; production release not authorized",
            "live_provider_validation": "not_exercised; provider credentials and licensing remain external gates",
            "learning_promotion": "not_attempted; one application run is not generalization evidence",
            "independent_reviewer": "not included in this slice; independent QA/security review remains open",
            "container_image_ref": image_ref,
            "container_image_id": image_id,
            "plan_run_id": run_id,
            "plan_digest": plan.digest,
            "plan_run_digest": verified_run.digest,
            "plan_run_revision": verified_run.revision,
            "plan_task_status": next(item.status for item in verified_run.tasks if item.task_id == task_id),
            "controller_task_id": task.id,
            "controller_task_state": durable_task.state if durable_task else "missing",
            "assignment": {
                "role_id": task.payload["_auren_agent_assignment"]["role_id"],
                "manager_id": task.payload["_auren_agent_assignment"]["manager_id"],
                "action_class": task.payload["_auren_agent_assignment"]["action_class"],
                "scope": task.payload["_auren_agent_assignment"]["scope"],
                "assignment_digest": task.payload["_auren_agent_assignment"]["assignment_digest"],
            },
            "dispatch_binding_digest": task.payload["auren_dispatch"]["binding_digest"],
            "recovery_checkpoint_status": checkpoint.status if checkpoint else "missing",
            "test_result": handler_report or {"error": execution_error or "handler report missing"},
            "limitations": [
                "Fixture-based contract tests only; no live provider or quote validation.",
                "No provider credentials, licensing or redistribution claims are included.",
                "This CI container run is not production deployment or physical multi-host isolation evidence.",
            ],
        }
        artifact_path = repo_root / "artifacts" / "stock-research-acceptance.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        self.assertTrue(successful, json.dumps(report, indent=2))
        self.assertEqual("passed", next(item.status for item in verified_run.tasks if item.task_id == task_id))
        self.assertEqual("verified", checkpoint.status if checkpoint else None)


if __name__ == "__main__":
    unittest.main()
