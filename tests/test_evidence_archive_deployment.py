"""Static release checks for the hardened evidence archive deployment package."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "evidence-archive"


class EvidenceArchiveDeploymentTests(unittest.TestCase):
    def test_container_requires_pinned_base_image_and_exact_crypto_version(self):
        dockerfile = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG PYTHON_BASE_IMAGE", dockerfile)
        self.assertIn("FROM ${PYTHON_BASE_IMAGE}", dockerfile)
        self.assertIn('cryptography==${CRYPTOGRAPHY_VERSION}', dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", dockerfile)

    def test_compose_restricts_listener_and_container_privileges(self):
        compose = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
        for required in (
            '"127.0.0.1:8765:8765"',
            "read_only: true",
            "cap_drop:",
            "no-new-privileges:true",
            "pids_limit: 64",
            "mem_limit: 256m",
            "cpus: 1.0",
            "trusted-public-keys.json:/run/auren/trusted-public-keys.json:ro",
            "revoked-signers.json:/run/auren/revoked-signers.json:ro",
        ):
            self.assertIn(required, compose)

    def test_templates_do_not_contain_real_credentials_or_private_keys(self):
        env = (DEPLOY / ".env.archive.example").read_text(encoding="utf-8")
        keyring = (DEPLOY / "trusted-public-keys.example.json").read_text(encoding="utf-8")
        ignore = (DEPLOY / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("REPLACE_WITH_RANDOM_SECRET", env)
        self.assertIn("REPLACE_WITH_BASE64URL", keyring)
        self.assertIn(".env.archive", ignore)
        self.assertIn("trusted-public-keys.json", ignore)
        self.assertIn("revoked-signers.json", ignore)

    def test_runbook_requires_external_immutable_retention_and_independent_custody(self):
        readme = (DEPLOY / "README.md").read_text(encoding="utf-8")
        self.assertIn("not a managed WORM service", readme)
        self.assertIn("independent custody", readme)
        self.assertIn("restore", readme)
        self.assertIn("revocation", readme)


if __name__ == "__main__":
    unittest.main()
