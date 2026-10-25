"""Regression tests for versioned adaptive workflow preferences."""
import unittest

from portable.workflow_profile import (
    PROFILE_VERSION,
    WorkflowProfile,
    WorkflowProfileError,
    delivery_mode_allows,
    infer_delivery_action,
    resolve_profile,
)


class WorkflowProfileTests(unittest.TestCase):
    def test_defaults_are_conservative_and_versioned(self):
        profile = WorkflowProfile()
        self.assertEqual(PROFILE_VERSION, profile.profile_version)
        self.assertEqual("pull_request", profile.delivery_mode)
        self.assertEqual("checkpointed", profile.execution_mode)
        self.assertEqual("all_applicable", profile.review_mode)
        self.assertEqual(64, len(profile.digest))

    def test_canonical_digest_and_round_trip(self):
        profile = WorkflowProfile(delivery_mode="stage_only", execution_mode="autonomous")
        restored = WorkflowProfile.from_mapping(profile.as_dict())
        self.assertEqual(profile, restored)
        self.assertEqual(profile.digest, restored.digest)

    def test_rejects_unknown_fields_and_unsupported_versions(self):
        with self.assertRaisesRegex(WorkflowProfileError, "unknown workflow profile fields"):
            WorkflowProfile.from_mapping({**WorkflowProfile().as_dict(), "skip_ci": True})
        with self.assertRaisesRegex(WorkflowProfileError, "unsupported workflow profile version"):
            WorkflowProfile(profile_version="auren-workflow-v99")

    def test_rejects_invalid_modes(self):
        with self.assertRaisesRegex(WorkflowProfileError, "invalid delivery_mode"):
            WorkflowProfile(delivery_mode="commit_and_force_push")
        with self.assertRaisesRegex(WorkflowProfileError, "invalid approval_mode"):
            WorkflowProfile(approval_mode="never_ask")

    def test_precedence_task_then_project_then_saved_then_default(self):
        saved = WorkflowProfile(delivery_mode="stage_only").as_dict()
        project = WorkflowProfile(delivery_mode="working_tree").as_dict()
        task = WorkflowProfile(delivery_mode="commit_each_change").as_dict()
        self.assertEqual("stage_only", resolve_profile(saved=saved).delivery_mode)
        self.assertEqual("working_tree", resolve_profile(saved=saved, project=project).delivery_mode)
        self.assertEqual("commit_each_change", resolve_profile(saved=saved, project=project, task=task).delivery_mode)
        self.assertEqual("pull_request", resolve_profile().delivery_mode)

    def test_invalid_task_profile_fails_closed_not_fallback(self):
        saved = WorkflowProfile().as_dict()
        with self.assertRaises(WorkflowProfileError):
            resolve_profile(saved=saved, task={"profile_version": PROFILE_VERSION, "skip_ci": True})

    def test_delivery_modes_restrict_repository_actions(self):
        stage_only = WorkflowProfile(delivery_mode="stage_only")
        for action in ("commit", "push", "create_pr", "merge", "publish", "release", "deploy"):
            with self.subTest(mode="stage_only", action=action):
                self.assertFalse(delivery_mode_allows(stage_only, action))
        working_tree = WorkflowProfile(delivery_mode="working_tree")
        self.assertFalse(delivery_mode_allows(working_tree, "commit"))
        self.assertFalse(delivery_mode_allows(working_tree, "create_pr"))
        commit_each = WorkflowProfile(delivery_mode="commit_each_change")
        self.assertTrue(delivery_mode_allows(commit_each, "commit"))
        self.assertFalse(delivery_mode_allows(commit_each, "push"))
        pull_request = WorkflowProfile(delivery_mode="pull_request")
        self.assertTrue(delivery_mode_allows(pull_request, "create_pr"))
        # Delivery mode is not a promotion grant; the controller must still
        # enforce operator-owned approval and exact-head CI for consequential actions.
        self.assertTrue(delivery_mode_allows(pull_request, "merge"))
        self.assertTrue(delivery_mode_allows(pull_request, "release"))

    def test_delivery_action_inference_uses_handler_and_typed_action_fields(self):
        self.assertEqual("merge", infer_delivery_action("merge-pull-request", {}))
        self.assertEqual("create_pr", infer_delivery_action("repo-worker", {"operation": "create pull request"}))
        self.assertEqual("push", infer_delivery_action("repo-worker", {"git_action": "push"}))
        self.assertIsNone(infer_delivery_action("repo-worker", {"description": "please do not merge"}))

    def test_profile_does_not_encode_authority_or_skip_checks(self):
        profile = WorkflowProfile(execution_mode="autonomous", approval_mode="scoped_autonomy")
        self.assertNotIn("permissions", profile.as_dict())
        self.assertNotIn("skip_checks", profile.as_dict())
        self.assertNotIn("force_push", profile.as_dict())


if __name__ == "__main__":
    unittest.main()
