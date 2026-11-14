#!/usr/bin/env python3
"""Validate a pinned provider price catalog or estimate one request's token cost.

This command never fetches pricing and never calls a model provider.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portable.provider_costs import (  # noqa: E402
    ProviderPriceCatalogError,
    estimate_provider_cost,
    load_provider_price_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, help="Path to a reviewed local JSON price catalog")
    parser.add_argument("--validate", action="store_true", help="Validate the catalog without estimating")
    parser.add_argument("--provider", help="Exact provider ID")
    parser.add_argument("--model", help="Exact pinned model ID")
    parser.add_argument("--input-tokens", type=int, help="Provider-reported input token count")
    parser.add_argument("--output-tokens", type=int, help="Provider-reported output token count")
    args = parser.parse_args()
    try:
        catalog = load_provider_price_catalog(args.catalog)
        if args.validate:
            report = {
                "status": "valid",
                "catalog_version": catalog.version,
                "catalog_digest": catalog.digest,
                "currency": catalog.currency,
                "entry_count": len(catalog.entries),
                "validity_status": (
                    "not_yet_effective" if datetime.now(timezone.utc) < catalog.published_at
                    else "expired" if datetime.now(timezone.utc) >= catalog.valid_until
                    else "effective"
                ),
                "valid_until": catalog.valid_until.isoformat(),
            }
            print(json.dumps(report, sort_keys=True))
            return 0
        if not args.provider or not args.model:
            parser.error("cost estimation requires --provider and --model")
        if args.input_tokens is None or args.output_tokens is None:
            parser.error("cost estimation requires --input-tokens and --output-tokens")
        report = estimate_provider_cost(
            catalog,
            provider_id=args.provider,
            model_id=args.model,
            usage={"input_tokens": args.input_tokens, "output_tokens": args.output_tokens},
        )
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "estimated" else 2
    except ProviderPriceCatalogError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
