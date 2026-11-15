import os
import tempfile
import unittest
from pathlib import Path

from portable.chatgpt_tunnel import (
    DEFAULT_MCP_URL,
    _ready,
    render_tunnel_config,
    validate_local_mcp_url,
    validate_tunnel_id,
    write_tunnel_config,
)


class ChatGPTTunnelConfigTests(unittest.TestCase):
    def test_accepts_supported_tunnel_id_shapes(self):
        self.assertEqual(validate_tunnel_id("tunnel_" + "a" * 32), "tunnel_" + "a" * 32)
        self.assertEqual(validate_tunnel_id("tunnel_ab12_" + "c" * 32), "tunnel_ab12_" + "c" * 32)

    def test_rejects_invalid_tunnel_ids(self):
        for value in ("", "tunnel_short", "tunnel_" + "A" * 32, "https://example.com"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_tunnel_id(value)

    def test_local_url_must_be_loopback_and_exact_mcp_path(self):
        self.assertEqual(validate_local_mcp_url(DEFAULT_MCP_URL), DEFAULT_MCP_URL)
        for value in (
            "https://127.0.0.1:8765/mcp",
            "http://localhost:8765/mcp",
            "http://192.0.2.1:8765/mcp",
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/mcp?x=1",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_local_mcp_url(value)

    def test_readiness_probe_rejects_non_loopback_targets(self):
        self.assertFalse(_ready("http://192.0.2.1:8765/readyz"))
        self.assertFalse(_ready("http://example.com:8765/readyz"))

    def test_config_uses_secret_references_and_loopback_target(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "operator.token"
            token.write_text("x" * 48, encoding="utf-8")
            config = render_tunnel_config("tunnel_" + "a" * 32, token)
            self.assertIn("api_key: env:CONTROL_PLANE_API_KEY", config)
            self.assertIn('Authorization: "file:' + token.resolve().as_posix() + '"', config)
            self.assertIn('url: "http://127.0.0.1:8765/mcp"', config)
            self.assertNotIn("sk-", config)

    def test_status_returns_success_for_ready_to_start(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Path(directory) / "tunnel-client"
            client.write_text("stub", encoding="utf-8")
            config = Path(directory) / "tunnel.yaml"
            config.write_text("config", encoding="utf-8")
            output = StringIO()
            with patch("portable.chatgpt_tunnel.shutil.which", return_value=str(client)), \
                 patch("portable.chatgpt_tunnel._ready", return_value=False), \
                 patch("portable.chatgpt_tunnel.subprocess.run", return_value=Mock(returncode=0, stdout="healthy", stderr="")):
                with redirect_stdout(output):
                    result = main(["status", "--config", str(config), "--tunnel-client", str(client)])
            self.assertEqual(result, 0)
            self.assertIn('"status": "ready_to_start"', output.getvalue())

    def test_config_rejects_missing_or_malformed_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "missing.token"
            with self.assertRaises(ValueError):
                render_tunnel_config("tunnel_" + "a" * 32, token)
            token.write_text("short", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                render_tunnel_config("tunnel_" + "a" * 32, token)

    def test_write_is_no_overwrite_by_default_and_can_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "tunnel.yaml"
            token = Path(directory) / "token"
            token.write_text("x" * 48, encoding="utf-8")
            first = write_tunnel_config("tunnel_" + "a" * 32, token, config_path=config)
            self.assertTrue(first.is_file())
            if os.name != "nt":
                self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                write_tunnel_config("tunnel_" + "b" * 32, token, config_path=config)
            write_tunnel_config("tunnel_" + "b" * 32, token, config_path=config, overwrite=True)
            self.assertTrue(config.with_name("tunnel.yaml.bak").is_file())
            self.assertIn("tunnel_" + "b" * 32, config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
