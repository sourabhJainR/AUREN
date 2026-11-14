# Provider Cost Accounting

AUREN records provider latency and token usage. It does not ship vendor prices or guess a cost from a model family. Cost estimates require an operator-maintained, versioned catalog with an exact provider ID, exact pinned model ID, USD rates, source URL and validity window.

## 1. Create a catalog

Start from [the empty example](../examples/provider-price-catalog.empty.json) and follow [the JSON schema](../schemas/provider-price-catalog.v1.schema.json).

A priced entry looks like this:

```json
{
  "provider_id": "openai",
  "model_id": "exact-model-version",
  "input_usd_per_million_tokens": 2.0,
  "output_usd_per_million_tokens": 8.0,
  "source_url": "https://platform.openai.com/docs/pricing",
  "retrieved_at": "2026-11-14T00:00:00Z"
}
```

These rates are an illustration of the file format, not current vendor prices. Replace them with values verified against the provider's official pricing page and the account's actual billing terms. Record the exact model identifier and the date you checked the source. Do not copy a family-level or `latest` rate into a pinned-model entry.

The catalog requires:

- A unique catalog version and SHA-256 digest.
- A publication time and expiry time with explicit time zones.
- USD per-million input and output token rates.
- Exact model IDs; aliases such as `latest`, `default` and `auto` are rejected.
- An HTTPS source URL and retrieval timestamp for every entry.
- No duplicate provider/model pairs or unknown fields.

AUREN validates the structure and validity window. It does not fetch, authenticate or independently verify the source URL. The catalog owner is responsible for confirming that the source is official and applicable to the account, region, model, and billing tier.

## 2. Validate the catalog

```powershell
auren providers cost --catalog .ai-harness/provider-prices.json --validate
```

The installed `auren providers cost` command reads a local JSON file only. It does not contact a provider or the source URL. The source-checkout helper `python scripts/provider_costs.py` offers the same validation and estimate operations.

## 3. Estimate a request

Use token counts returned by the provider:

```powershell
auren providers cost --catalog .ai-harness/provider-prices.json --provider openai --model exact-model-version --input-tokens 12000 --output-tokens 2500
```

The estimator uses decimal arithmetic and reports input cost, output cost, total estimated USD, catalog version/digest and source. It refuses to estimate when the exact model is not priced, the catalog is not yet effective or expired, or provider usage is incomplete or ambiguous.

## 4. Attach an estimate to a live API probe

The direct API probe already sends one fixed synthetic prompt and may incur a provider charge. Add `--price-catalog` to estimate that probe's cost from the response usage:

```powershell
$env:AUREN_REMOTE_PROVIDER_ORDER = "openai"
$env:AUREN_LOCAL_REMOTE_INFERENCE = "enabled"
python scripts/provider_conformance.py --live-api --api-provider openai --price-catalog .ai-harness/provider-prices.json --json
```

The probe still reports pass/fail independently from cost estimation. An invalid or missing catalog does not turn a successful API conformance check into a failure; its cost status is `unavailable`. It never invents a zero cost.

## 5. Limits

The estimate uses provider-reported input and output token counts and the selected catalog rates. It does not include taxes, discounts, credits, cached-token tiers, batch pricing, image/audio/video charges, tool charges, minimum commitments or other non-token fees. It is not an invoice or a billing reconciliation.

A catalog digest pins the exact content used for an estimate, but it is not a digital signature. Store approved catalogs in a controlled location, review updates, and retain previous versions for audit. Do not place credentials or account secrets in the catalog.

## Tests

```powershell
python -m unittest tests.test_provider_costs tests.test_provider_conformance -v
```
