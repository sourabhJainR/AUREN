from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portable.agent_org_runtime import REGISTRY_PATH, submit_agent_task
from portable.execution_controller import AgentDelegationLimitExceeded, ExecutionController


def delegation_limit_noop_handler(payload):
    return {"success": True, "resource": payload["resource"]}


class AgentDelegationLimitTests(unittest.TestCase):
    def test_pending_limit_is_atomic_and_completed_work_releases_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = ExecutionController(
                Path(directory), agent_assignment_verifier=lambda _request: True,
            )
            handler = "tests.test_agent_delegation_limits.delegation_limit_noop_handler"
            controller.register_handler(
                handler, delegation_limit_noop_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )

            def submit(index):
                resource = f"portable/delegated-{index}.py"
                return submit_agent_task(
                    controller, handler,
                    {"resource": resource, "path": resource},
                    role_id="BE-01",
                    manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths",
                    scope=[resource],
                    authorizer=lambda _request: True,
                    work_key=f"AUREN:test:delegation-limit:{index}",
                    registry_path=REGISTRY_PATH,
                )

            admitted = [submit(index) for index in range(4)]
            self.assertEqual(4, len(admitted))

            # Idempotent replay of an existing work key does not consume another
            # manager slot or get rejected just because the queue is at capacity.
            replay = submit(0)
            self.assertEqual(admitted[0].id, replay.id)

            with self.assertRaisesRegex(
                AgentDelegationLimitExceeded, "pending delegated-task limit"
            ):
                submit(4)

            controller.run_once(admitted[0].id)
            self.assertEqual("completed", controller.task(admitted[0].id).state)

            # Completion releases one pending slot for a new assignment.
            next_task = submit(4)
            self.assertEqual("pending", controller.task(next_task.id).state)


if __name__ == "__main__":
    unittest.main()
