from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.agent_org_runtime import REGISTRY_PATH, submit_agent_task
from portable.execution_controller import ExecutionController


_DELEGATION_CONTROLLER: ExecutionController | None = None
_CHILD_TASK_IDS: list[str] = []
_CHILD_AUTHORIZATIONS: list[object] = []


def manager_worker_handler(payload):
    return {"success": True, "evidence": ["worker-task-completed"]}


def cto_manager_handler(payload):
    if _DELEGATION_CONTROLLER is None:
        raise RuntimeError("test controller is not configured")
    resource = payload["resource"]
    child = submit_agent_task(
        _DELEGATION_CONTROLLER,
        "tests.test_agent_manager_chain.manager_worker_handler",
        {"resource": resource, "path": resource},
        role_id="BE-01",
        manager_id="EM-ENG-01",
        action_class="edit_assigned_paths",
        scope=[resource],
        authorizer=lambda request: _CHILD_AUTHORIZATIONS.append(request) or True,
        work_key="AUREN:test:cto-manager-worker:child",
        registry_path=REGISTRY_PATH,
    )
    _CHILD_TASK_IDS.append(child.id)
    return {"success": True, "evidence": ["worker-assignment-persisted"]}


class CtoManagerDelegationChainTests(unittest.TestCase):
    def test_cto_assigns_manager_and_manager_delegates_within_inherited_scope(self):
        global _DELEGATION_CONTROLLER
        _CHILD_TASK_IDS.clear()
        _CHILD_AUTHORIZATIONS.clear()
        with tempfile.TemporaryDirectory() as directory:
            controller = ExecutionController(
                Path(directory), agent_assignment_verifier=lambda _request: True,
            )
            manager_handler = "tests.test_agent_manager_chain.cto_manager_handler"
            worker_handler = "tests.test_agent_manager_chain.manager_worker_handler"
            controller.register_handler(
                manager_handler,
                cto_manager_handler,
                execution_class="read_only",
                agent_action_class="assign_scoped_tasks",
            )
            controller.register_handler(
                worker_handler,
                manager_worker_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            _DELEGATION_CONTROLLER = controller
            resource = "portable/cto-delegated.py"
            manager_task = submit_agent_task(
                controller,
                manager_handler,
                {"resource": resource, "path": resource},
                role_id="EM-ENG-01",
                manager_id="CTO-01",
                action_class="assign_scoped_tasks",
                scope=[resource],
                authorizer=lambda _request: True,
                work_key="AUREN:test:cto-manager-worker:manager",
                registry_path=REGISTRY_PATH,
            )

            controller.run_once(manager_task.id)

            self.assertEqual("completed", controller.task(manager_task.id).state)
            self.assertEqual(1, len(_CHILD_TASK_IDS))
            self.assertEqual(1, len(_CHILD_AUTHORIZATIONS))
            child = controller.task(_CHILD_TASK_IDS[0])
            self.assertIsNotNone(child)
            self.assertEqual("pending", child.state)

            controller.run_once(child.id)
            self.assertEqual("completed", controller.task(child.id).state)
            _DELEGATION_CONTROLLER = None


if __name__ == "__main__":
    unittest.main()
