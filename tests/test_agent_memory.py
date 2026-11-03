from __future__ import annotations

import json
import tempfile
import unittest

from portable.agent_memory import AgentMemory, ProfileAgentMemory


class ProfileAgentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _memory(self, profile="backend_implementer", revision=1, agent="worker-1", run="run-1"):
        return ProfileAgentMemory(
            self.temp.name,
            profile_id=profile,
            profile_revision=revision,
            agent_id=agent,
            run_id=run,
        )

    def test_profile_memory_is_isolated_by_profile_agent_and_run(self):
        backend = self._memory()
        backend.remember("backend-only decision")
        self.assertEqual("backend-only decision", backend.read())

        architect = self._memory(profile="principal_architect")
        same_profile_other_agent = self._memory(agent="worker-2")
        other_run = self._memory(run="run-2")
        self.assertEqual("", architect.read())
        self.assertEqual("", same_profile_other_agent.read())
        self.assertEqual("", other_run.read())

        self.assertNotEqual(backend.path, architect.path)
        self.assertNotEqual(backend.path, same_profile_other_agent.path)
        self.assertNotEqual(backend.path, other_run.path)

    def test_same_profile_agent_and_run_can_resume_private_memory(self):
        first = self._memory()
        first.remember("resume this worker's private note")
        resumed = self._memory()
        self.assertEqual("resume this worker's private note", resumed.read())
        self.assertEqual(first.path, resumed.path)

    def test_records_are_bound_to_profile_identity_and_digest(self):
        memory = self._memory()
        memory.remember("trusted profile note")
        row = memory.snapshot()[0]
        self.assertEqual("backend_implementer", row["profile_id"])
        self.assertEqual(1, row["profile_revision"])
        self.assertEqual(64, len(row["profile_digest"]))
        self.assertEqual("worker-1", row["agent_id"])
        self.assertEqual("run-1", row["run_id"])

        # A row moved or edited under another profile identity is not readable.
        altered = dict(row, profile_id="principal_architect")
        memory.path.write_text(json.dumps(altered) + "\n", encoding="utf-8")
        self.assertEqual([], memory.snapshot())


    def test_profile_content_digest_change_selects_a_new_memory_partition(self):
        first = self._memory()
        first.remember("memory from the original profile digest")

        from pathlib import Path
        import json
        import shutil

        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as config_dir:
            profile_path = Path(config_dir) / "profiles.json"
            shutil.copy(root / "config" / "agent_profiles.v1.json", profile_path)
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            data["profiles"][0]["provider_preferences"] = ["different_provider_hint"]
            profile_path.write_text(json.dumps(data), encoding="utf-8")
            changed = ProfileAgentMemory(
                self.temp.name,
                profile_id="backend_implementer",
                profile_revision=1,
                agent_id="worker-1",
                run_id="run-1",
                profile_path=profile_path,
                organization_registry_path=root / "config" / "agent_org.v1.json",
            )
        self.assertNotEqual(first.profile_digest, changed.profile_digest)
        self.assertNotEqual(first.path, changed.path)
        self.assertEqual("", changed.read())

    def test_invalid_profile_revision_agent_or_run_is_rejected(self):
        with self.assertRaises(ValueError):
            self._memory(profile="unknown")
        with self.assertRaises(ValueError):
            self._memory(revision=True)
        with self.assertRaises(ValueError):
            self._memory(agent="../worker")
        with self.assertRaises(ValueError):
            self._memory(run="")

    def test_profile_memory_does_not_share_legacy_agent_memory_files(self):
        legacy = AgentMemory(self.temp.name, "worker-1", run_id="run-1")
        legacy.remember("legacy private note")
        profile = self._memory()
        self.assertEqual("", profile.read())
        self.assertNotEqual(legacy.path, profile.path)


if __name__ == "__main__":
    unittest.main()
