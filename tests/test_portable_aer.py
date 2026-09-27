from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from portable.aer_runtime import build, check_update, install, rollback, verify_bundle


class PortableAerTests(unittest.TestCase):
    def make_source(self, root: Path, version: str = "20.1.0") -> None:
        (root / ".ai-harness" / "runtime").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "ai-coding-orchestrator").mkdir(parents=True, exist_ok=True)
        (root / "portable").mkdir(parents=True, exist_ok=True)
        (root / "dashboard").mkdir(parents=True, exist_ok=True)
        (root / ".ai-harness" / "config.toml").write_text('[harness]\nversion = 20\n', encoding="utf-8")
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        (root / ".ai-harness" / "runtime" / "engine.py").write_text("print('ok')\n", encoding="utf-8")
        (root / ".ai-harness" / "telemetry.jsonl").write_text("machine-state\n", encoding="utf-8")
        (root / "skills" / "ai-coding-orchestrator" / "SKILL.md").write_text("---\nname: ai-coding-orchestrator\ndescription: Repository-aware AI engineering control plane.\n---\n", encoding="utf-8")
        (root / "portable" / "aer_runtime.py").write_text("print('portable')\n", encoding="utf-8")
        (root / "portable" / "__init__.py").write_text("from .aer_runtime import main\n", encoding="utf-8")
        (root / "dashboard" / "index.html").write_text("<html>AER Engineering Console</html>\n", encoding="utf-8")
        (root / "aer_cli.py").write_text("print('launcher')\n", encoding="utf-8")

    def test_build_verify_and_exclude_mutable_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "aer.zip"
            build(root, bundle, source_commit="test-commit")
            manifest = verify_bundle(bundle)
            self.assertEqual(manifest["version"], "20.1.0")
            self.assertEqual(manifest["source_commit"], "test-commit")
            self.assertFalse(any(item["path"].endswith("telemetry.jsonl") for item in manifest["files"]))
            self.assertTrue(any(item["path"] == "aer_cli.py" for item in manifest["files"]))
            self.assertFalse(manifest["target_repository_mutation"])
            with zipfile.ZipFile(bundle) as archive:
                self.assertIn("aer_cli.py", archive.namelist())
                self.assertIn("payload/portable/aer_runtime.py", archive.namelist())

    def test_verify_and_install_accept_github_artifact_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            aer_home = Path(tmp) / "aer-home"
            self.make_source(root)
            bundle = Path(tmp) / "aer-portable.zip"
            artifact = Path(tmp) / "artifact-download.zip"
            build(root, bundle, source_commit="artifact-commit")

            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(bundle, "aer-portable.zip")

            manifest = verify_bundle(artifact)
            self.assertEqual(manifest["source_commit"], "artifact-commit")
            install(artifact, "none", aer_home)
            record = json.loads((aer_home / "current" / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(record["source_commit"], "artifact-commit")
            self.assertTrue((aer_home / "current" / "aer_cli.py").is_file())

    def test_verify_rejects_artifact_with_multiple_zip_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "aer-portable.zip"
            build(root, bundle, source_commit="artifact-commit")
            artifact = Path(tmp) / "ambiguous.zip"
            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as wrapper:
                wrapper.write(bundle, "aer-portable.zip")
                wrapper.write(bundle, "other.zip")
            with self.assertRaises(SystemExit):
                verify_bundle(artifact)

    def test_verify_rejects_tampered_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "aer.zip"
            build(root, bundle, source_commit="test-commit")
            tampered = Path(tmp) / "tampered.zip"
            with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w", zipfile.ZIP_DEFLATED) as target:
                for item in source.infolist():
                    data = source.read(item.filename)
                    if item.filename == "aer_cli.py":
                        data += b"tampered\n"
                    target.writestr(item, data)
            with self.assertRaises(SystemExit):
                verify_bundle(tampered)

    def test_verify_rejects_unsafe_manifest_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            bundle = Path(tmp) / "aer.zip"
            build(root, bundle, source_commit="test-commit")
            unsafe = Path(tmp) / "unsafe.zip"
            with zipfile.ZipFile(bundle) as source:
                manifest = json.loads(source.read("aer-bundle.json"))
                manifest["files"].append({"path": "../../outside.txt", "sha256": "0" * 64, "size": 0})
                with zipfile.ZipFile(unsafe, "w", zipfile.ZIP_DEFLATED) as target:
                    for item in source.infolist():
                        data = source.read(item.filename)
                        if item.filename == "aer-bundle.json":
                            data = (json.dumps(manifest, sort_keys=True) + "\n").encode("utf-8")
                        target.writestr(item, data)
            with self.assertRaises(SystemExit):
                verify_bundle(unsafe)

    def test_launcher_works_from_unrelated_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            self.make_source(root)
            launcher = root / "aer_cli.py"
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
            aer_home = Path(tmp) / "aer-home"
            self.make_source(root)
            target.mkdir()
            (target / "README.md").write_text("user repository", encoding="utf-8")
            before = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            bundle = Path(tmp) / "aer.zip"
            build(root, bundle, source_commit="commit-20.1.0")
            install(bundle, "none", aer_home)
            after = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertFalse((target / ".ai-harness").exists())
            record = json.loads((aer_home / "current" / "install.json").read_text(encoding="utf-8"))
            self.assertEqual(record["version"], "20.1.0")
            self.assertEqual(record["source_commit"], "commit-20.1.0")
            self.assertTrue(record["repository_isolated"])
            self.assertTrue((aer_home / "current" / "aer_cli.py").is_file())

    def test_install_allows_same_version_different_builds_and_keeps_both_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            aer_home = Path(tmp) / "aer-home"
            self.make_source(root, "20.1.1")
            first = Path(tmp) / "first.zip"
            second = Path(tmp) / "second.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", aer_home)
            first_record = json.loads((aer_home / "current" / "install.json").read_text(encoding="utf-8"))
            first_root = first_record["install_root"]

            # Same semantic version, different build identity.
            (root / ".ai-harness" / "runtime" / "engine.py").write_text("print('changed')\n", encoding="utf-8")
            build(root, second, source_commit="commit-b")
            install(second, "none", aer_home)
            second_record = json.loads((aer_home / "current" / "install.json").read_text(encoding="utf-8"))
            second_root = second_record["install_root"]

            self.assertEqual(second_record["version"], "20.1.1")
            self.assertEqual(second_record["source_commit"], "commit-b")
            self.assertNotEqual(first_root, second_root)
            self.assertTrue((aer_home / "versions" / first_root / "install.json").is_file())
            self.assertTrue((aer_home / "versions" / second_root / "install.json").is_file())
            self.assertEqual(
                json.loads((aer_home / "versions" / first_root / "install.json").read_text(encoding="utf-8"))["source_commit"],
                "commit-a",
            )

    def test_rollback_selects_previous_immutable_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            aer_home = Path(tmp) / "aer-home"
            self.make_source(root, "20.1.0")
            first = Path(tmp) / "first.zip"
            build(root, first, source_commit="commit-a")
            install(first, "none", aer_home)
            self.make_source(root, "20.2.0")
            second = Path(tmp) / "second.zip"
            build(root, second, source_commit="commit-b")
            install(second, "none", aer_home)
            rollback(aer_home)
            active = json.loads((aer_home / "active.json").read_text(encoding="utf-8"))
            self.assertEqual(active["version"], "20.1.0")
            self.assertEqual(active["source_commit"], "commit-a")

    def test_check_update_uses_exact_remote_commit_pin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aer_home = Path(tmp) / "aer-home"
            aer_home.mkdir()
            (aer_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "old"}), encoding="utf-8")
            with patch("portable.aer_runtime._remote_target", return_value=("20.2.0", "new")):
                status = check_update(aer_home)
            self.assertTrue(status["update_available"])
            self.assertEqual(status["latest_version"], "20.2.0")
            self.assertEqual(status["latest_commit"], "new")

    def test_same_version_same_commit_is_not_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aer_home = Path(tmp) / "aer-home"
            aer_home.mkdir()
            (aer_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "same"}), encoding="utf-8")
            with patch("portable.aer_runtime._remote_target", return_value=("20.1.0", "same")):
                status = check_update(aer_home)
            self.assertFalse(status["update_available"])

    def test_same_version_different_commit_is_an_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            aer_home = Path(tmp) / "aer-home"
            aer_home.mkdir()
            (aer_home / "active.json").write_text(json.dumps({"version": "20.1.0", "source_commit": "old"}), encoding="utf-8")
            with patch("portable.aer_runtime._remote_target", return_value=("20.1.0", "new")):
                status = check_update(aer_home)
            self.assertTrue(status["update_available"])
            self.assertEqual(status["latest_version"], "20.1.0")
            self.assertEqual(status["latest_commit"], "new")


if __name__ == "__main__":
    unittest.main()
