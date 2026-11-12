from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auren_cli import _run_up
from portable.local_host import main as local_host_main


class ChatGPTLaunchTests(unittest.TestCase):
    def test_local_host_accepts_open_chatgpt_flag(self):
        with patch("portable.local_host.serve_local", return_value=0) as serve:
            self.assertEqual(local_host_main(["--open-chatgpt"]), 0)
        self.assertTrue(serve.call_args.kwargs["open_chatgpt"])

    def test_auren_up_opens_chatgpt_by_default_after_local_services_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".auren").mkdir()
            (root / ".auren" / "project.json").write_text("{}", encoding="utf-8")
            with (
                patch("portable.onboarding.load_project_profile", return_value=(
                    root / ".auren" / "project.json", {"runtime_mode": "single_user_local"},
                )),
                patch("portable.onboarding.doctor", return_value={}),
                patch("portable.local_host.main", return_value=0) as local_main,
            ):
                self.assertEqual(
                    _run_up([
                        "--project-root", str(root),
                        "--no-connect",
                        "--no-browser",
                    ]),
                    0,
                )
            local_args = local_main.call_args.args[0]
            self.assertIn("--open-chatgpt", local_args)

    def test_auren_up_can_suppress_chatgpt_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".auren").mkdir()
            (root / ".auren" / "project.json").write_text("{}", encoding="utf-8")
            with (
                patch("portable.onboarding.load_project_profile", return_value=(
                    root / ".auren" / "project.json", {"runtime_mode": "single_user_local"},
                )),
                patch("portable.onboarding.doctor", return_value={}),
                patch("portable.local_host.main", return_value=0) as local_main,
            ):
                self.assertEqual(
                    _run_up([
                        "--project-root", str(root),
                        "--no-connect",
                        "--no-browser",
                        "--no-chatgpt",
                    ]),
                    0,
                )
            local_args = local_main.call_args.args[0]
            self.assertNotIn("--open-chatgpt", local_args)


if __name__ == "__main__":
    unittest.main()
