import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from portable.project_export import export_project_package


class ProjectExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "AUREN tests"], cwd=self.root, check=True)
        (self.root / ".auren").mkdir()
        (self.root / ".auren" / "project.json").write_text(
            json.dumps({"project_name": "sample", "provider": "local", "api_token": "must-not-export"}),
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# Sample\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.root / ".env").write_text("API_KEY=sk-" + "a" * 30, encoding="utf-8")
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "private.pem").write_text("private", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.root, check=True, capture_output=True)

    def test_exports_source_profile_and_state_without_credentials_or_runtime_databases(self):
        output = self.base / "project-export.zip"
        result = export_project_package(self.root, output)
        self.assertEqual("exported", result["status"])
        with zipfile.ZipFile(output) as archive:
            names = set(archive.namelist())
            self.assertIn("project/README.md", names)
            self.assertIn("project/src/app.py", names)
            self.assertIn("auren-export.json", names)
            self.assertNotIn("project/.auren/project.json", names)
            self.assertFalse(any(".env" in name or "private.pem" in name or name.endswith((".db", ".sqlite", ".sqlite3")) for name in names))
            self.assertNotIn("must-not-export", archive.read("auren-export.json").decode("utf-8"))
            manifest = json.loads(archive.read("auren-export.json"))
            profile = manifest["project_profile"]["profile"]
            self.assertEqual("[REDACTED]", profile["api_token"])
            self.assertEqual("not_configured", manifest["progress"]["status"])
            self.assertEqual("available", manifest["plans"]["status"])

    def test_refuses_existing_output_and_symlink_profile(self):
        output = self.base / "existing.zip"
        output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            export_project_package(self.root, output)
        profile = self.root / ".auren" / "project.json"
        profile.unlink()
        profile.symlink_to(self.root / "README.md")
        with self.assertRaises(PermissionError):
            export_project_package(self.root, self.base / "blocked.zip")

    def test_refuses_file_beneath_symlinked_parent_directory(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("host-secret", encoding="utf-8")
        tracked = self.root / "symlink-source"
        tracked.mkdir()
        (tracked / "safe.txt").write_text("safe", encoding="utf-8")
        import subprocess
        subprocess.run(["git", "add", "symlink-source/safe.txt"], cwd=self.root, check=True)
        tracked.rename(self.root / "symlink-source-original")
        tracked.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PermissionError):
            export_project_package(self.root, self.base / "symlink-parent.zip")

    def test_refuses_symlinked_auren_metadata_directory(self):
        metadata = self.root / ".auren"
        if metadata.exists():
            import shutil
            shutil.rmtree(metadata)
        outside = self.base / "outside-metadata"
        outside.mkdir()
        (outside / "project.json").write_text('{"secret":"host-secret"}', encoding="utf-8")
        metadata.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PermissionError):
            export_project_package(self.root, self.base / "symlink-metadata.zip")

    def test_non_git_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                export_project_package(directory, self.base / "bad.zip")


if __name__ == "__main__":
    unittest.main()
