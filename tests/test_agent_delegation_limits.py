from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from portable.agent_org_runtime import REGISTRY_PATH, submit_agent_task
from portable.execution_controller import AgentDelegationLimitExceeded, ExecutionController


def delegation_limit_noop_handler(payload):
    return {"success": True, "resource": payload["resource"]}


class AgentDelegationLimitTests(unittest.TestCase):
    def test_concurrent_submissions_cannot_overrun_pending_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True,
            )
            handler = "tests.test_agent_delegation_limits.delegation_limit_noop_handler"
            first.register_handler(
                handler, delegation_limit_noop_handler,
                execution_class="read_only",
                agent_action_class="edit_assigned_paths",
            )
            second = ExecutionController(
                root, agent_assignment_verifier=lambda _request: True,
            )

            def submit(controller, index):
                resource = f"portable/race-{index}.py"
                return submit_agent_task(
                    controller, handler,
                    {"resource": resource, "path": resource},
                    role_id="BE-01",
                    manager_id="EM-ENG-01",
                    action_class="edit_assigned_paths",
                    scope=[resource],
                    authorizer=lambda _request: True,
                    work_key=f"AUREN:test:delegation-race:{index}",
                    registry_path=REGISTRY_PATH,
                )

            for index in range(3):
                submit(first, index)

            barrier = Barrier(2)

            def race(controller, index):
                barrier.wait()
                try:
                    return submit(controller, index)
                except AgentDelegationLimitExceeded:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda item: race(*item),
                    ((first, 3), (second, 4)),
                ))
            self.assertEqual(1, sum(result is not None for result in results))

    def test_cto_has_a_separate_bounded_manager_delegation_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = ExecutionController(
                Path(directory), agent_assignment_verifier=lambda _request: True,
            )
            handler = "tests.test_agent_delegation_limits.delegation_limit_noop_handler"
            controller.register_handler(
                handler, delegation_limit_noop_handler,
                execution_class="read_only",
                agent_action_class="assign_scoped_tasks",
            )

            def submit(index):
                resource = f"portable/manager-{index}.py"
                return submit_agent_task(
                    controller, handler,
                    {"resource": resource, "path": resource},
                    role_id="EM-ENG-01",
                    manager_id="CTO-01",
                    action_class="assign_scoped_tasks",
                    scope=[resource],
                    authorizer=lambda _request: True,
                    work_key=f"AUREN:test:cto-manager-limit:{index}",
                    registry_path=REGISTRY_PATH,
                )

            admitted = [submit(index) for index in range(2)]
            self.assertEqual(2, len(admitted))
            with self.assertRaisesRegex(
                AgentDelegationLimitExceeded, "pending delegated-task limit"
            ):
                submit(2)

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
