import tempfile
import unittest
from pathlib import Path

from portable.windows_acl import enforce_private_acl


class WindowsAclPathValidationTests(unittest.TestCase):
    def test_rejects_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                enforce_private_acl(Path(directory) / "missing.token", directory=False)

    def test_rejects_wrong_path_type(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file_path = root / "token"
            file_path.write_text("credential", encoding="utf-8")
            with self.assertRaises(PermissionError):
                enforce_private_acl(file_path, directory=True)
            with self.assertRaises(PermissionError):
                enforce_private_acl(root, directory=False)


if __name__ == "__main__":
    unittest.main()
