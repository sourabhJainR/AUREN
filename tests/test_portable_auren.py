from __future__ import annotations

import hashlib
import io
import os
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from portable.auren_runtime import build, check_update, install, rollback, verify_bundle, _copy_tree_without_mutable_state, _install_skill, _version_install_root


class PortableAurenTests(unittest.TestCase):
    def make_source(self, root: Path, version: str = "20.1.0") -> None:
        (root / ".ai-harness" / "runtime").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "ai-coding-orchestrator").mkdir(parents=True, exist_ok=True)
        (root / "portable").mkdir(parents=True, exist_ok=True)
        (root / "dashboard").mkdir(parents=True, exist_ok=True)
        (root / ".ai-harness" / "config.toml").write_text('[harness]\nversion = 20\n', encoding="utf-8")
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        (root / ".ai-harness" / "runtime" / "engine.py").write_text("print('ok')\n", encoding="utf-8")
        (root / ".ai-harness" / "learning").mkdir(parents=True, exist_ok=True)
        (root / ".ai-harness" / "learning" / "compiler_bootstrap_priors.jsonl").write_text(
            '{"id":"prior-test","kind":"advisory-prior"}' + chr(10), encoding="utf-8"
        )
        (root / ".ai-harness" / "telemetry.jsonl").write_text("machine-state\n", encoding="utf-8")
        (root / "skills" / "ai-coding-orchestrator" / "SKILL.md").write_text("---\nname: ai-coding-orchestrator\ndescription: Repository-aware AI engineering control plane.\n---\n", encoding="utf-8")
        (root / "portable" / "auren_runtime.py").write_text("print('portable')\n", encoding="utf-8")
        (root / "portable" / "__init__.py").write_text("from .auren_runtime import main\n", encoding="utf-8")
        (root / "dashboard" / "index.html").write_text("<html>AUREN Engineering Console</html>\n", encoding="utf-8")
        (root / "auren_cli.py").write_text("print('launcher')\n", encoding="utf-8")
        (root / "auren_env_compat.py").write_text(
            "def get_auren_env(suffix, default=None):\n    return default\n", encoding="utf-8"
        )

    def test_build_rejects_symlinked_source_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            secret = Path(tmp) / "outside-secret.txt"
            secret.write_text("must not be packaged", encoding="utf-8")
            link = root / ".ai-harness" / "runtime" / "linked-secret.txt"
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(secret)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks unavailable on this runner: {exc}")
            with self.assertRaisesRegex(SystemExit, "source contains a symlink"):
                build(root, Path(tmp) / "auren.zip", source_commit="test-commit")

    def test_build_verify_and_exclude_mutable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            state = root / ".ai-harness" / "learning"
            state.mkdir(parents=True, exist_ok=True)
            (state / "learning-journal.jsonl").write_text("private learning state\\n", encoding="utf-8")
            (state / "private-context.json").write_text('{"private": true}\\n', encoding="utf-8")
            runtime_state = root / ".ai-harness" / "runtime"
            runtime_state.mkdir(parents=True, exist_ok=True)
            (runtime_state / "learning-journal.jsonl").write_text("private journal outside state folder\\n", encoding="utf-8")
            (runtime_state / "evidence-ledger.jsonl").write_text("private evidence outside state folder\\n", encoding="utf-8")
            (runtime_state / "task-memory.jsonl.lock").write_text("private lock\\n", encoding="utf-8")
            evidence = root / ".ai-harness" / "evidence"
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "evidence-ledger.jsonl").write_text("private evidence state\\n", encoding="utf-8")
            for name in ("task-memory.sqlite3", "task-memory.sqlite3-shm", "task-memory.sqlite3-wal", "execution.db", "execution.db-journal"):
                (state / name).write_bytes(b"mutable runtime state")
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            manifest = verify_bundle(bundle)
            self.assertEqual(manifest["version"], "20.1.0")
            self.assertEqual(manifest["source_commit"], "test-commit")
            self.assertFalse(any(item["path"].endswith("telemetry.jsonl") for item in manifest["files"]))
            included = {item["path"] for item in manifest["files"]}
            for mutable in ("task-memory.sqlite3", "task-memory.sqlite3-shm", "task-memory.sqlite3-wal", "execution.db", "execution.db-journal"):
                self.assertNotIn(f".ai-harness/learning/{mutable}", included)
            for private_path in (
                ".ai-harness/learning/learning-journal.jsonl",
                ".ai-harness/learning/private-context.json",
                ".ai-harness/evidence/evidence-ledger.jsonl",
                ".ai-harness/runtime/learning-journal.jsonl",
                ".ai-harness/runtime/evidence-ledger.jsonl",
                ".ai-harness/runtime/task-memory.jsonl.lock",
            ):
                self.assertNotIn(private_path, included)
            self.assertTrue(any(item["path"] == "auren_cli.py" for item in manifest["files"]))
            self.assertIn(".ai-harness/learning/compiler_bootstrap_priors.jsonl", included)
            self.assertFalse(manifest["target_repository_mutation"])
            with zipfile.ZipFile(bundle) as archive:
                self.assertIn("auren_cli.py", archive.namelist())
                self.assertIn("payload/portable/auren_runtime.py", archive.namelist())
                self.assertIn("payload/auren_env_compat.py", archive.namelist())

    def test_verify_and_install_accept_github_artifact_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root)
            bundle = Path(tmp) / "auren-portable.zip"
            artifact = Path(tmp) / "artifact-download.zip"
            build(root, bundle, source_commit="artifact-commit")

            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(bundle, "auren-portable.zip")

            manifest = verify_bundle(artifact)
            self.assertEqual(manifest["source_commit"], "artifact-commit")
            install(artifact, "none", auren_home)
            record = json.loads((auren_home / "current" / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(record["source_commit"], "artifact-commit")
            self.assertTrue((auren_home / "current" / "auren_cli.py").is_file())
            self.assertTrue((auren_home / "current" / "auren_env_compat.py").is_file())
            self.assertTrue((auren_home / "current" / ".claude-plugin" / "plugin.json").is_file())


    def test_verify_rejects_archive_exceeding_member_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            with patch("portable.auren_runtime.MAX_ARCHIVE_MEMBERS", 2):
                with self.assertRaisesRegex(SystemExit, "archive exceeds member limit"):
                    verify_bundle(bundle)

    def test_verify_rejects_archive_exceeding_member_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            with patch("portable.auren_runtime.MAX_MEMBER_UNCOMPRESSED_BYTES", 1):
                with self.assertRaisesRegex(SystemExit, "archive member exceeds uncompressed size limit"):
                    verify_bundle(bundle)

    def test_verify_rejects_artifact_wrapper_with_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren-portable.zip"
            build(root, bundle, source_commit="artifact-commit")
            artifact = Path(tmp) / "wrapper-with-extra-file.zip"
            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(bundle, "auren-portable.zip")
                wrapper.writestr("README.txt", "unexpected extra content")
            with self.assertRaisesRegex(SystemExit, "invalid AUREN bundle"):
                verify_bundle(artifact)

    def test_verify_rejects_windows_drive_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            tampered = Path(tmp) / "windows-drive.zip"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as target:
                for item in source.infolist():
                    target.writestr(item, source.read(item.filename))
                target.writestr("payload/C:/escape.txt", b"must be rejected")
            with self.assertRaisesRegex(SystemExit, "non-portable archive member path"):
                verify_bundle(tampered)

    def test_verify_requires_manifest_file_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            tampered = Path(tmp) / "missing-size.zip"
            with zipfile.ZipFile(bundle) as source:
                manifest = json.loads(source.read("auren-bundle.json"))
                manifest["files"][0].pop("size", None)
                with zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as target:
                    for item in source.infolist():
                        data = source.read(item.filename)
                        if item.filename == "auren-bundle.json":
                            data = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
                        target.writestr(item, data)
            with self.assertRaisesRegex(SystemExit, "invalid bundle file record"):
                verify_bundle(tampered)

    def test_verify_rejects_artifact_with_multiple_zip_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren-portable.zip"
            build(root, bundle, source_commit="artifact-commit")
            artifact = Path(tmp) / "ambiguous.zip"
            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(bundle, "auren-portable.zip")
                wrapper.write(bundle, "other.zip")
            with self.assertRaises(SystemExit):
                verify_bundle(artifact)

    def test_verify_rejects_tampered_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            tampered = Path(tmp) / "tampered.zip"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as target:
                for item in source.infolist():
                    data = source.read(item.filename)
                    if item.filename == "auren_cli.py":
                        data += b"tampered\n"
                    target.writestr(item, data)
            with self.assertRaises(SystemExit):
                verify_bundle(tampered)

    def test_verify_rejects_unmanifested_archive_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            tampered = Path(tmp) / "extra-file.zip"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as target:
                for item in source.infolist():
                    target.writestr(item, source.read(item.filename))
                target.writestr("payload/portable/unmanifested.py", b"not covered by the manifest")
            with self.assertRaisesRegex(SystemExit, "inventory"):
                verify_bundle(tampered)

    def test_verify_rejects_duplicate_archive_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            duplicate = Path(tmp) / "duplicate-member.zip"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(duplicate, "w", zipfile.ZIP_DEFLATED) as target:
                for item in source.infolist():
                    target.writestr(item, source.read(item.filename))
                target.writestr("payload/portable/auren_runtime.py", b"shadow copy")
            with self.assertRaisesRegex(SystemExit, "duplicate archive members"):
                verify_bundle(duplicate)

    def test_install_copy_filters_all_mutable_database_suffixes_recursively(self) -> None:
        suffixes = (
            ".sqlite", ".sqlite3", ".db", ".db3",
            ".sqlite-wal", ".sqlite-shm", ".sqlite-journal",
            ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal",
            ".db-wal", ".db-shm", ".db-journal",
            ".db3-wal", ".db3-shm", ".db3-journal",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            destination = Path(tmp) / "destination"
            nested = source / "runtime" / "deep"
            nested.mkdir(parents=True)
            (nested / "settings.json").write_text("safe", encoding="utf-8")
            for suffix in suffixes:
                (nested / f"state{suffix}").write_bytes(b"mutable state")
            harness = source / ".ai-harness"
            (harness / "learning").mkdir(parents=True)
            (harness / "learning" / "private.json").write_text("private", encoding="utf-8")
            (harness / "learning" / "compiler_bootstrap_priors.jsonl").write_text("canonical prior", encoding="utf-8")
            (harness / "evidence").mkdir(parents=True)
            (harness / "evidence" / "ledger.jsonl").write_text("private", encoding="utf-8")
            _copy_tree_without_mutable_state(source, destination)
            self.assertEqual(
                ["settings.json"],
                sorted(path.name for path in (destination / "runtime" / "deep").iterdir()),
            )
            self.assertFalse((destination / ".ai-harness" / "learning" / "private.json").exists())
            self.assertTrue((destination / ".ai-harness" / "learning" / "compiler_bootstrap_priors.jsonl").is_file())
            self.assertFalse((destination / ".ai-harness" / "evidence").exists())

    def test_verify_rejects_unsafe_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="test-commit")
            unsafe = Path(tmp) / "unsafe.zip"
            with zipfile.ZipFile(bundle) as source:
                manifest = json.loads(source.read("auren-bundle.json"))
                manifest["files"].append({"path": "../../outside.txt", "sha256": "0" * 64, "size": 0})
                with zipfile.ZipFile(unsafe, "w", zipfile.ZIP_DEFLATED) as target:
                    for item in source.infolist():
                        data = source.read(item.filename)
                        if item.filename == "auren-bundle.json":
                            data = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
                        target.writestr(item, data)
            with self.assertRaises(SystemExit):
                verify_bundle(unsafe)

    def test_launcher_works_from_unrelated_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            launcher = root / "auren_cli.py"
            result = subprocess.run(
                [sys.executable, str(launcher), "--help"],
                cwd=Path(tmp),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_install_is_repository_isolated_and_version_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            target = Path(tmp) / "target"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root)
            target.mkdir()
            (target / "README.md").write_text("user repository", encoding="utf-8")
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            bundle = Path(tmp) / "auren.zip"
            build(root, bundle, source_commit="commit-20.1.0")
            install(bundle, "none", auren_home)
            after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse((target / ".ai-harness").exists())
            record = json.loads((auren_home / "current" / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(record["version"], "20.1.0")
            self.assertEqual(record["source_commit"], "commit-20.1.0")
            self.assertTrue(record["repository_isolated"])
            self.assertTrue((auren_home / "current" / "auren_cli.py").is_file())

    def test_install_allows_same_version_different_builds_and_keeps_both_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root, "20.1.1")
            first = Path(tmp) / "first.zip"
            second = Path(tmp) / "second.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", auren_home)
            first_record = json.loads((auren_home / "current" / "install.json").read_text(encoding="utf-8"))
            first_root = first_record["install_root"]

            # Same semantic version, different build identity.
            (root / ".ai-harness" / "runtime" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
            build(root, second, source_commit="commit-b")
            install(second, "none", auren_home)
            second_record = json.loads((auren_home / "current" / "install.json").read_text(encoding="utf-8"))
            second_root = second_record["install_root"]

            self.assertEqual(second_record["version"], "20.1.1")
            self.assertEqual(second_record["source_commit"], "commit-b")
            self.assertNotEqual(first_root, second_root)
            self.assertTrue((auren_home / "versions" / first_root / "install.json").is_file())
            self.assertTrue((auren_home / "versions" / second_root / "install.json").is_file())
            self.assertEqual(
                json.loads((auren_home / "versions" / first_root / "install.json").read_text(encoding="utf-8"))["source_commit"],
                "commit-a",
            )

    def test_rollback_selects_previous_immutable_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root, "20.1.0")
            first = Path(tmp) / "first.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", auren_home)
            self.make_source(root, "20.2.0")
            second = Path(tmp) / "second.zip"
            build(root, second, source_commit="commit-b")
            install(second, "none", auren_home)
            rollback(auren_home)
            active = json.loads((auren_home / "active.json").read_text(encoding="utf-8"))
            self.assertEqual(active["version"], "20.1.0")
            self.assertEqual(active["source_commit"], "commit-a")

    def test_install_failure_during_staging_preserves_previous_active_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root, "20.1.0")
            first = Path(tmp) / "first.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", auren_home)
            before = json.loads((auren_home / "active.json").read_text(encoding="utf-8"))
            before_current = (auren_home / "current" / "install.json").read_text(encoding="utf-8")

            self.make_source(root, "20.2.0")
            second = Path(tmp) / "second.zip"
            build(root, second, source_commit="commit-b")
            with patch("portable.auren_runtime._copy_payload", side_effect=OSError("simulated copy interruption")):
                with self.assertRaisesRegex(OSError, "simulated copy interruption"):
                    install(second, "none", auren_home)

            self.assertEqual(before, json.loads((auren_home / "active.json").read_text(encoding="utf-8")))
            self.assertEqual(before_current, (auren_home / "current" / "install.json").read_text(encoding="utf-8"))
            version_root = _version_install_root(auren_home, "20.2.0", hashlib.sha256(second.read_bytes()).hexdigest())
            self.assertFalse(version_root.exists())
            self.assertFalse(any((auren_home / "versions").glob(".*.staging-*")))

    def test_active_metadata_failure_restores_previous_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            auren_home = Path(tmp) / "auren-home"
            self.make_source(root, "20.1.0")
            first = Path(tmp) / "first.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", auren_home)
            previous = json.loads((auren_home / "active.json").read_text(encoding="utf-8"))

            self.make_source(root, "20.2.0")
            second = Path(tmp) / "second.zip"
            build(root, second, source_commit="commit-b")
            import portable.auren_runtime as runtime
            original_atomic_json = runtime._atomic_json

            def fail_active(path: Path, value: object) -> None:
                if path == auren_home / "active.json":
                    raise OSError("simulated active metadata failure")
                original_atomic_json(path, value)

            with patch("portable.auren_runtime._atomic_json", side_effect=fail_active):
                with self.assertRaisesRegex(OSError, "simulated active metadata failure"):
                    install(second, "none", auren_home)

            self.assertEqual(previous, json.loads((auren_home / "active.json").read_text(encoding="utf-8")))
            self.assertEqual(previous["source_commit"], json.loads((auren_home / "current" / "install.json").read_text(encoding="utf-8"))["source_commit"])

    def test_skill_copy_failure_keeps_existing_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "new-skill"
            destination = Path(tmp) / "home" / "skill"
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            (source / "SKILL.md").write_text("new skill", encoding="utf-8")
            (destination / "SKILL.md").write_text("previous skill", encoding="utf-8")
            with patch("portable.auren_runtime.shutil.copytree", side_effect=OSError("simulated skill copy interruption")):
                with self.assertRaisesRegex(OSError, "simulated skill copy interruption"):
                    _install_skill(source, destination)
            self.assertEqual("previous skill", (destination / "SKILL.md").read_text(encoding="utf-8"))
            self.assertFalse((destination.with_name(destination.name + ".staging")).exists())

    def test_check_update_uses_exact_remote_commit_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auren_home = Path(tmp) / "auren-home"
            auren_home.mkdir()
            (auren_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "old"}), encoding="utf-8")
            with patch("portable.auren_runtime._remote_target", return_value=("20.2.0", "new")):
                status = check_update(auren_home)
            self.assertTrue(status["update_available"])
            self.assertEqual(status["latest_version"], "20.2.0")
            self.assertEqual(status["latest_commit"], "new")

    def test_same_version_same_commit_is_not_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auren_home = Path(tmp) / "auren-home"
            auren_home.mkdir()
            (auren_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "same"}), encoding="utf-8")
            with patch("portable.auren_runtime._remote_target", return_value=("20.1.0", "same")):
                status = check_update(auren_home)
            self.assertFalse(status["update_available"])

    def test_same_version_different_commit_is_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            auren_home = Path(tmp) / "auren-home"
            auren_home.mkdir()
            (auren_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "old"}), encoding="utf-8")
            with patch("portable.auren_runtime._remote_target", return_value=("20.1.0", "new")):
                status = check_update(auren_home)
            self.assertTrue(status["update_available"])
            self.assertEqual(status["latest_version"], "20.1.0")
            self.assertEqual(status["latest_commit"], "new")


    def test_main_push_always_builds_portable_artifacts_and_pr_remains_filtered(self) -> None:
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "portable-bundle.yml"
        ).read_text(encoding="utf-8")
        section = workflow.split(
            "      - name: Classify changed-file impact\n", 1
        )[1]
        shell = section.split("        run: |\n", 1)[1].split("\n  bundle:", 1)[0]
        start = shell.index("python - <<'PY'\n") + len("python - <<'PY'\n")
        end = shell.index("\n          PY", start)
        classifier = textwrap.dedent(shell[start:end])

        def evaluate(
            event: str, ref: str, changed_files: str, *, forbid_diff: bool = False
        ) -> str:
            with tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "github-output"
                output.write_text("", encoding="utf-8")
                environment = {
                    "EVENT_NAME": event,
                    "GITHUB_REF": ref,
                    "BASE_SHA": "base-sha",
                    "HEAD_SHA": "head-sha",
                    "GITHUB_OUTPUT": str(output),
                }
                diff_patch = (
                    patch(
                        "subprocess.check_output",
                        side_effect=AssertionError("main push must not depend on diff"),
                    )
                    if forbid_diff
                    else patch("subprocess.check_output", return_value=changed_files)
                )
                with (
                    patch.dict(os.environ, environment, clear=False),
                    diff_patch,
                    redirect_stdout(io.StringIO()),
                ):
                    exec(
                        compile(classifier, "portable-bundle-impact", "exec"),
                        {"__name__": "__main__"},
                    )
                return output.read_text(encoding="utf-8")

        self.assertIn(
            "required=true",
            evaluate("push", "refs/heads/main", "", forbid_diff=True),
        )
        self.assertIn(
            "required=false",
            evaluate("pull_request", "refs/pull/42/merge", "README.md\n"),
        )

    def test_module_entrypoint_runs_without_preload_warning(self) -> None:
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "-Werror",
                "-m",
                "portable.auren_runtime",
                "--help",
            ],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root)},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        self.assertIn("usage:", completed.stdout.lower())
        self.assertNotIn("RuntimeWarning", completed.stderr)


if __name__ == "__main__":
    unittest.main()
