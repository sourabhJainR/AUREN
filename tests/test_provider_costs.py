from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from portable.provider_costs import (
    ProviderPriceCatalogError,
    estimate_provider_cost,
    load_provider_price_catalog,
)


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def catalog_document(*, entries=None):
    return {
        "schema_version": "auren.provider-price-catalog.v1",
        "catalog_version": "test-catalog-v1",
        "currency": "USD",
        "published_at": "2026-11-01T00:00:00Z",
        "valid_until": "2026-12-01T00:00:00Z",
        "source_policy": "official-provider-pricing",
        "entries": entries if entries is not None else [{
            "provider_id": "openai",
            "model_id": "pinned-model-v1",
            "input_usd_per_million_tokens": 2,
            "output_usd_per_million_tokens": 8,
            "source_url": "https://platform.openai.com/docs/pricing",
            "retrieved_at": "2026-11-10T00:00:00Z",
        }],
    }


class TestProviderCosts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "prices.json"
        self.addCleanup(self.temp.cleanup)

    def write(self, document):
        self.path.write_text(json.dumps(document), encoding="utf-8")
        return load_provider_price_catalog(self.path, now=NOW)

    def test_exact_model_cost_uses_decimal_rates_and_token_counts(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="pinned-model-v1",
            usage={"input_tokens": 1_000_000, "output_tokens": 500_000},
            now=NOW,
        )
        self.assertEqual("estimated", result["status"])
        self.assertEqual("2.000000", result["input_cost_usd"])
        self.assertEqual("4.000000", result["output_cost_usd"])
        self.assertEqual("6.000000", result["estimated_total_usd"])
        self.assertEqual("test-catalog-v1", result["catalog_version"])
        self.assertEqual(64, len(result["catalog_digest"]))
        self.assertIn("platform.openai.com", result["source_url"])

    def test_telemetry_unit_aliases_are_supported(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="pinned-model-v1",
            usage={"input_units": 1000, "output_units": 200},
            now=NOW,
        )
        self.assertEqual("estimated", result["status"])
        self.assertEqual(1000, result["input_tokens"])
        self.assertEqual(200, result["output_tokens"])

    def test_exact_model_match_is_required(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="different-model",
            usage={"input_tokens": 100, "output_tokens": 100},
            now=NOW,
        )
        self.assertEqual("unavailable", result["status"])
        self.assertEqual("exact_model_price_not_found", result["reason"])

    def test_expired_catalog_never_produces_a_cost(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="pinned-model-v1",
            usage={"input_tokens": 100, "output_tokens": 100},
            now=datetime(2026, 12, 2, tzinfo=timezone.utc),
        )
        self.assertEqual("catalog_expired", result["reason"])

    def test_catalog_not_yet_effective_never_produces_a_cost(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="pinned-model-v1",
            usage={"input_tokens": 100, "output_tokens": 100},
            now=datetime(2026, 10, 31, tzinfo=timezone.utc),
        )
        self.assertEqual("catalog_not_yet_effective", result["reason"])

    def test_missing_usage_is_not_treated_as_zero(self):
        catalog = self.write(catalog_document())
        result = estimate_provider_cost(
            catalog,
            provider_id="openai",
            model_id="pinned-model-v1",
            usage={"input_tokens": 100},
            now=NOW,
        )
        self.assertEqual("provider_usage_incomplete", result["reason"])

    def test_conflicting_usage_aliases_are_rejected(self):
        catalog = self.write(catalog_document())
        with self.assertRaisesRegex(ProviderPriceCatalogError, "conflicting aliases"):
            estimate_provider_cost(
                catalog,
                provider_id="openai",
                model_id="pinned-model-v1",
                usage={"input_tokens": 100, "prompt_tokens": 101, "output_tokens": 2},
                now=NOW,
            )

    def test_negative_and_boolean_token_counts_are_rejected(self):
        catalog = self.write(catalog_document())
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ProviderPriceCatalogError):
                    estimate_provider_cost(
                        catalog,
                        provider_id="openai",
                        model_id="pinned-model-v1",
                        usage={"input_tokens": value, "output_tokens": 1},
                        now=NOW,
                    )

    def test_mutable_model_alias_is_rejected(self):
        document = catalog_document()
        document["entries"][0]["model_id"] = "latest"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ProviderPriceCatalogError, "exact pinned"):
            load_provider_price_catalog(self.path, now=NOW)

    def test_http_source_url_is_rejected(self):
        document = catalog_document()
        document["entries"][0]["source_url"] = "http://example.com/pricing"
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ProviderPriceCatalogError, "HTTPS"):
            load_provider_price_catalog(self.path, now=NOW)

    def test_unknown_fields_are_rejected(self):
        document = catalog_document()
        document["entries"][0]["discount_percent"] = 99
        self.path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(ProviderPriceCatalogError, "unknown fields"):
            load_provider_price_catalog(self.path, now=NOW)

    def test_duplicate_json_keys_are_rejected(self):
        self.path.write_text(
            '{"schema_version":"auren.provider-price-catalog.v1",'
            '"schema_version":"auren.provider-price-catalog.v1"}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ProviderPriceCatalogError, "duplicate JSON key"):
            load_provider_price_catalog(self.path, now=NOW)

    def test_catalog_file_symlink_is_rejected(self):
        target = Path(self.temp.name) / "target.json"
        target.write_text(json.dumps(catalog_document()), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        with self.assertRaisesRegex(ProviderPriceCatalogError, "not a symlink"):
            load_provider_price_catalog(self.path, now=NOW)

    def test_catalog_cli_validates_and_estimates_without_network(self):
        self.path.write_text(json.dumps(catalog_document()), encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "scripts" / "provider_costs.py"
        validate = subprocess.run(
            [sys.executable, str(script), "--catalog", str(self.path), "--validate"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, validate.returncode, validate.stderr)
        self.assertEqual("valid", json.loads(validate.stdout)["status"])
        estimate = subprocess.run(
            [
                sys.executable, str(script), "--catalog", str(self.path),
                "--provider", "openai", "--model", "pinned-model-v1",
                "--input-tokens", "1000000", "--output-tokens", "500000",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, estimate.returncode, estimate.stderr)
        self.assertEqual("6.000000", json.loads(estimate.stdout)["estimated_total_usd"])

    def test_cli_returns_nonzero_when_exact_price_is_missing(self):
        self.path.write_text(json.dumps(catalog_document(entries=[])), encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / "scripts" / "provider_costs.py"
        result = subprocess.run(
            [
                sys.executable, str(script), "--catalog", str(self.path),
                "--provider", "openai", "--model", "pinned-model-v1",
                "--input-tokens", "10", "--output-tokens", "10",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("exact_model_price_not_found", json.loads(result.stdout)["reason"])


if __name__ == "__main__":
    unittest.main()
