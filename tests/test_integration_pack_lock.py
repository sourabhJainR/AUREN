"""Tests for signed, pinned and data-only integration pack lock management."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from portable.integration_pack_lock import (
    IntegrationPackError,
    IntegrationPackLock,
    IntegrationPackManifest,
)


def _verify(key_id: str, payload: bytes, signature: str) -> bool:
    return key_id == "test_signer" and signature == "a" * 128 and b'"name"' in payload


class IntegrationPackLockTests(unittest.TestCase):
    def manifest(self, **overrides):
        values = {
            "name": "sample_pack",
            "version": "1.0.0",
            "source_url": "https://github.com/example/sample-pack",
            "source_revision": "b" * 40,
            "artifact_sha256": hashlib.sha256(b"sample-pack-archive").hexdigest(),
            "capabilities": ("code.review", "test.run"),
            "permissions": ("repository.read",),
            "signer_key_id": "test_signer",
            "signature": "a" * 128,
            "min_auren_version": "1.0.0",
            "max_auren_version": "2.0.0",
            "dependencies": (),
        }
        values.update(overrides)
        return IntegrationPackManifest(**values)

    def manager(self, directory, *, version="1.4.0", verifier=_verify):
        return IntegrationPackLock(
            Path(directory) / ".auren" / "integration-packs.lock.json",
            auren_version=version,
            signature_verifier=verifier,
        )

    def test_manifest_schema_matches_the_canonical_manifest_fields(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "integration-pack-manifest.v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        manifest = self.manifest()
        self.assertEqual(set(manifest.as_dict()), set(schema["properties"]))
        self.assertTrue(set(schema["required"]).issubset(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])

    def test_manifest_digest_and_signature_payload_are_deterministic(self):
        item = self.manifest()
        restored = IntegrationPackManifest.from_mapping(item.as_dict())
        self.assertEqual(item.digest, restored.digest)
        self.assertEqual(item.signed_payload(), restored.signed_payload())
        self.assertTrue(item.verify_signature(_verify))
        self.assertFalse(item.verify_signature(None))

    def test_rejects_unpinned_or_unsafe_manifest_values(self):
        for field, value, message in (
            ("source_url", "http://github.com/example/pack", "HTTPS"),
            ("source_url", "https://user:pass@github.com/example/pack", "HTTPS"),
            ("source_revision", "main", "pinned"),
            ("artifact_sha256", "latest", "SHA-256"),
            ("signature", "abc", "Ed25519"),
            ("capabilities", ("*",), "invalid identifier"),
            ("permissions", ("admin",), "forbidden wildcard"),
            ("min_auren_version", "latest", "stable major"),
            ("dependencies", (["unhashable"],), "exact name@version"),
            ("dependencies", tuple(f"dep_{index}@1.0.0" for index in range(257)), "maximum of 256"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(IntegrationPackError, message):
                    self.manifest(**{field: value})

    def test_preview_reports_compatibility_signature_and_permission_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            first = self.manifest()
            manager.stage(first)
            updated = self.manifest(
                version="1.1.0",
                permissions=("repository.read", "network.read"),
                capabilities=("code.review", "test.run", "docs.search"),
            )
            preview = manager.preview(updated)
        self.assertEqual("update", preview["action"])
        self.assertTrue(preview["signature_valid"])
        self.assertTrue(preview["compatible"])
        self.assertEqual(["docs.search"], preview["capabilities_added"])
        self.assertEqual(["network.read"], preview["permissions_added"])
        self.assertFalse(preview["will_execute_code"])

    def test_stage_requires_verified_signature_and_compatible_auren_version(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory, verifier=lambda *_: False)
            with self.assertRaisesRegex(IntegrationPackError, "signature is not verified"):
                manager.stage(self.manifest())
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory, version="3.0.0")
            with self.assertRaisesRegex(IntegrationPackError, "incompatible"):
                manager.stage(self.manifest())

    def test_stage_is_pinned_idempotent_and_never_enables_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            item = self.manifest()
            first = manager.stage(item)
            second = manager.stage(item)
            state = manager.snapshot()
            lockfile = json.loads(manager.path.read_text(encoding="utf-8"))
        self.assertEqual(first["manifest_digest"], second["manifest_digest"])
        self.assertFalse(first["activation_approved"])
        self.assertEqual([], first["granted_permissions"])
        self.assertEqual(1, state["revision"])
        self.assertFalse(state["will_execute_code"])
        self.assertNotIn("entrypoint", lockfile["packs"]["sample_pack"]["manifest"])

    def test_update_requires_compare_and_swap_digest_and_resets_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            first = manager.stage(self.manifest())
            manager.approve_activation(
                "sample_pack",
                expected_manifest_digest=first["manifest_digest"],
                granted_permissions=["repository.read"],
                approval_granted=True,
            )
            updated_manifest = self.manifest(
                version="1.1.0",
                source_revision="c" * 40,
                permissions=("repository.read", "network.read"),
            )
            with self.assertRaisesRegex(IntegrationPackError, "requires the current manifest digest"):
                manager.stage(updated_manifest)
            updated = manager.stage(
                updated_manifest,
                expected_current_digest=first["manifest_digest"],
            )
        self.assertFalse(updated["activation_approved"])
        self.assertEqual([], updated["granted_permissions"])
        self.assertNotEqual(first["manifest_digest"], updated["manifest_digest"])

    def test_enable_requires_explicit_approval_and_only_grants_declared_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            staged = manager.stage(self.manifest())
            with self.assertRaisesRegex(IntegrationPackError, "explicit activation approval"):
                manager.approve_activation(
                    "sample_pack",
                    expected_manifest_digest=staged["manifest_digest"],
                    granted_permissions=["repository.read"],
                    approval_granted=False,
                )
            with self.assertRaisesRegex(IntegrationPackError, "exceed the manifest"):
                manager.approve_activation(
                    "sample_pack",
                    expected_manifest_digest=staged["manifest_digest"],
                    granted_permissions=["network.read"],
                    approval_granted=True,
                )
            enabled = manager.approve_activation(
                "sample_pack",
                expected_manifest_digest=staged["manifest_digest"],
                granted_permissions=["repository.read"],
                approval_granted=True,
            )
        self.assertTrue(enabled["activation_approved"])
        self.assertEqual(["repository.read"], enabled["granted_permissions"])

    def test_disable_and_rollback_restore_version_but_not_old_permission_grants(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            first = manager.stage(self.manifest())
            manager.approve_activation(
                "sample_pack",
                expected_manifest_digest=first["manifest_digest"],
                granted_permissions=["repository.read"],
                approval_granted=True,
            )
            updated_manifest = self.manifest(
                version="1.1.0",
                source_revision="c" * 40,
            )
            updated = manager.stage(
                updated_manifest,
                expected_current_digest=first["manifest_digest"],
            )
            restored = manager.rollback(
                "sample_pack",
                expected_current_state_digest=updated["state_digest"],
            )
        self.assertEqual("1.0.0", restored["version"])
        self.assertFalse(restored["activation_approved"])
        self.assertEqual([], restored["granted_permissions"])
        self.assertTrue(restored["signature_valid"])

    def test_rollback_of_initial_stage_removes_pack_and_stale_rollback_is_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            staged = manager.stage(self.manifest())
            with self.assertRaisesRegex(IntegrationPackError, "state changed"):
                manager.rollback("sample_pack", expected_current_state_digest="0" * 64)
            removed = manager.rollback(
                "sample_pack",
                expected_current_state_digest=staged["state_digest"],
            )
            self.assertIsNone(removed)
            self.assertNotIn("sample_pack", manager.snapshot()["packs"])

    def test_dependencies_require_exact_staged_version_and_activation_approval_state(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            dependency = self.manifest(name="base_tools")
            manager.stage(dependency)
            dependent = self.manifest(
                name="review_pack",
                dependencies=("base_tools@1.0.0",),
            )
            staged = manager.stage(dependent)
            with self.assertRaisesRegex(IntegrationPackError, "dependency activation is not approved"):
                manager.approve_activation(
                    "review_pack",
                    expected_manifest_digest=staged["manifest_digest"],
                    granted_permissions=[],
                    approval_granted=True,
                )
            manager.approve_activation(
                "base_tools",
                expected_manifest_digest=dependency.digest,
                granted_permissions=["repository.read"],
                approval_granted=True,
            )
            enabled = manager.approve_activation(
                "review_pack",
                expected_manifest_digest=staged["manifest_digest"],
                granted_permissions=[],
                approval_granted=True,
            )
        self.assertTrue(enabled["activation_approved"])

    def test_dependency_cannot_change_while_an_approved_dependent_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            base = self.manifest(name="base_tools")
            base_staged = manager.stage(base)
            dependent = self.manifest(
                name="review_pack",
                dependencies=("base_tools@1.0.0",),
            )
            dependent_staged = manager.stage(dependent)
            manager.approve_activation(
                "base_tools",
                expected_manifest_digest=base_staged["manifest_digest"],
                granted_permissions=["repository.read"],
                approval_granted=True,
            )
            manager.approve_activation(
                "review_pack",
                expected_manifest_digest=dependent_staged["manifest_digest"],
                granted_permissions=[],
                approval_granted=True,
            )
            with self.assertRaisesRegex(IntegrationPackError, "dependent pack approvals"):
                manager.revoke_activation(
                    "base_tools",
                    expected_manifest_digest=base_staged["manifest_digest"],
                )
            changed_base = self.manifest(
                name="base_tools",
                version="1.1.0",
                source_revision="c" * 40,
            )
            with self.assertRaisesRegex(IntegrationPackError, "dependent pack approvals"):
                manager.stage(
                    changed_base,
                    expected_current_digest=base_staged["manifest_digest"],
                )
            manager.revoke_activation(
                "review_pack",
                expected_manifest_digest=dependent_staged["manifest_digest"],
            )
            manager.revoke_activation(
                "base_tools",
                expected_manifest_digest=base_staged["manifest_digest"],
            )
            updated = manager.stage(
                changed_base,
                expected_current_digest=base_staged["manifest_digest"],
            )
        self.assertEqual("1.1.0", updated["version"])
        self.assertFalse(updated["activation_approved"])

    def test_dependency_cycle_is_rejected_when_updating_an_existing_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            root = self.manifest(name="root_tools")
            root_staged = manager.stage(root)
            child = self.manifest(
                name="child_tools",
                dependencies=("root_tools@1.0.0",),
            )
            manager.stage(child)
            cycle = self.manifest(
                name="root_tools",
                source_revision="c" * 40,
                capabilities=("code.review", "test.run", "docs.search"),
                dependencies=("child_tools@1.0.0",),
            )
            with self.assertRaisesRegex(IntegrationPackError, "dependency cycle"):
                manager.stage(
                    cycle,
                    expected_current_digest=root_staged["manifest_digest"],
                )

    def test_dependency_graph_depth_is_bounded(self):
        manager = self.manager(".")
        state = {"packs": {}}
        previous_name = None
        for index in range(65):
            name = f"pack_{index}"
            dependencies = (f"{previous_name}@1.0.0",) if previous_name else ()
            manifest = self.manifest(
                name=name,
                source_revision=f"{index:040x}",
                dependencies=dependencies,
            )
            state["packs"][name] = {
                "manifest": manifest.as_dict(),
                "manifest_digest": manifest.digest,
                "activation_approved": False,
                "granted_permissions": [],
            }
            previous_name = name
        candidate = self.manifest(
            name="pack_65",
            source_revision=f"{65:040x}",
            dependencies=("pack_64@1.0.0",),
        )
        with self.assertRaisesRegex(IntegrationPackError, "maximum depth"):
            manager._validate_dependencies(candidate, state)

    def test_rollback_refuses_to_restore_a_pack_with_missing_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            base = manager.stage(self.manifest(name="base_tools"))
            dependent = manager.stage(self.manifest(
                name="review_pack",
                dependencies=("base_tools@1.0.0",),
            ))
            updated = manager.stage(
                self.manifest(
                    name="review_pack",
                    version="1.1.0",
                    source_revision="c" * 40,
                ),
                expected_current_digest=dependent["manifest_digest"],
            )
            manager.rollback(
                "base_tools",
                expected_current_state_digest=base["state_digest"],
            )
            with self.assertRaisesRegex(IntegrationPackError, "missing pinned dependency"):
                manager.rollback(
                    "review_pack",
                    expected_current_state_digest=updated["state_digest"],
                )

    def test_artifact_digest_check_is_exact(self):
        data = b"verified archive bytes"
        digest = hashlib.sha256(data).hexdigest()
        self.assertTrue(IntegrationPackLock.verify_artifact(data, digest))
        self.assertFalse(IntegrationPackLock.verify_artifact(data, "0" * 64))

    def test_lockfile_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.path.parent.mkdir(parents=True)
            manager.path.write_text(
                '{"schema_version":1,"schema_version":1,"revision":0,"packs":{},"history":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IntegrationPackError, "duplicate object keys"):
                manager.snapshot()

    def test_symlinked_lockfile_and_parent_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.path.parent.mkdir(parents=True)
            target = Path(directory) / "target.json"
            target.write_text(
                '{"schema_version":1,"revision":0,"packs":{},"history":[]}',
                encoding="utf-8",
            )
            try:
                manager.path.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("file symlinks are unavailable on this platform")
            with self.assertRaisesRegex(IntegrationPackError, "symlinked lockfile"):
                manager.snapshot()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_directory = root / "target"
            target_directory.mkdir()
            linked_directory = root / ".auren"
            try:
                linked_directory.symlink_to(target_directory, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this platform")
            manager = self.manager(directory)
            with self.assertRaisesRegex(IntegrationPackError, "symlinked lockfile directory"):
                manager.snapshot()

    def test_lockfile_revision_must_match_latest_history_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.stage(self.manifest())
            state = json.loads(manager.path.read_text(encoding="utf-8"))
            state["revision"] += 1
            manager.path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(IntegrationPackError, "revision does not match"):
                manager.snapshot()

    def test_lockfile_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.path.parent.mkdir(parents=True)
            manager.path.write_bytes(b" " * (4 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(IntegrationPackError, "bounded regular file"):
                manager.snapshot()

    def test_stale_lockfile_writer_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self.manager(directory)
            manager.path.parent.mkdir(parents=True)
            lock_path = manager.path.with_name(manager.path.name + ".lock")
            lock_path.write_text("active", encoding="utf-8")
            with self.assertRaisesRegex(IntegrationPackError, "another pack operation"):
                manager.stage(self.manifest())
            self.assertFalse(manager.path.exists())


if __name__ == "__main__":
    unittest.main()
