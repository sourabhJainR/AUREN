import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from portable.auren_runtime import main, parser


class ControlCenterCliTests(unittest.TestCase):
    def test_status_parser_defaults(self):
        args = parser().parse_args(["status"])
        self.assertEqual(args.command, "status")
        self.assertFalse(args.json)
        self.assertIsNone(args.auren_home)

    def test_console_parser_defaults_to_loopback(self):
        args = parser().parse_args(["console"])
        self.assertEqual(args.command, "console")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 0)
        self.assertFalse(args.open_browser)

    def test_status_command_emits_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "portable.auren_runtime",
                    "status",
                    "--json",
                    "--auren-home",
                    str(Path(directory) / ".auren"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn("installation", payload)
            self.assertIn("runs", payload)
            self.assertIn("learning", payload)
            self.assertIn("recovery", payload)

    def test_console_rejects_non_loopback_host(self):
        with self.assertRaisesRegex(SystemExit, "console host must be loopback"):
            main(["console", "--host", "0.0.0.0"])


if __name__ == "__main__":
    unittest.main()
