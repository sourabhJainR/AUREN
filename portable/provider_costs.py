"""Versioned, exact-model provider price catalog and conservative cost estimates.

AUREN ships no assumed or hard-coded vendor prices. Callers supply a reviewed
catalog with official source links and an explicit validity window.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


class ProviderPriceCatalogError(ValueError):
    """The supplied provider price catalog is invalid or unsafe to use."""


_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_CATALOG_SCHEMA = "auren.provider-price-catalog.v1"
_MAX_CATALOG_BYTES = 1_000_000
_MAX_TOKEN_COUNT = 10**12
_COST_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class ProviderPriceCatalog:
    version: str
    currency: str
    published_at: datetime
    valid_until: datetime
    entries: Mapping[tuple[str, str], Mapping[str, Any]]
    digest: str
    source_policy: str


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ProviderPriceCatalogError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProviderPriceCatalogError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderPriceCatalogError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _price(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        raise ProviderPriceCatalogError(f"{field} must be a non-negative decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProviderPriceCatalogError(f"{field} must be a non-negative decimal") from exc
    if not result.is_finite() or result < 0 or result > Decimal("1000000"):
        raise ProviderPriceCatalogError(f"{field} is outside the supported price range")
    return result


def _token_count(usage: Mapping[str, Any], names: tuple[str, ...], field: str) -> int | None:
    values = []
    for name in names:
        if name not in usage:
            continue
        value = usage[name]
        if type(value) is not int or not 0 <= value <= _MAX_TOKEN_COUNT:
            raise ProviderPriceCatalogError(f"usage.{name} must be a non-negative integer")
        values.append(value)
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise ProviderPriceCatalogError(f"usage contains conflicting aliases for {field}")
    return values[0]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderPriceCatalogError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_provider_price_catalog(
    path: str | Path,
    *,
    now: datetime | None = None,
) -> ProviderPriceCatalog:
    """Load and validate a small local catalog; never fetch prices from the network."""
    target = Path(path).expanduser()
    if target.is_symlink() or not target.is_file():
        raise ProviderPriceCatalogError("price catalog must be an existing regular file, not a symlink")
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ProviderPriceCatalogError("price catalog could not be read") from exc
    if not raw or len(raw) > _MAX_CATALOG_BYTES:
        raise ProviderPriceCatalogError("price catalog must be between 1 byte and 1 MB")
    try:
        document = json.loads(
            raw.decode("utf-8"), parse_float=Decimal, object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderPriceCatalogError("price catalog must be valid UTF-8 JSON") from exc
    if not isinstance(document, dict) or document.get("schema_version") != _CATALOG_SCHEMA:
        raise ProviderPriceCatalogError(f"schema_version must be {_CATALOG_SCHEMA}")
    allowed_catalog_keys = {
        "schema_version", "catalog_version", "currency", "published_at",
        "valid_until", "source_policy", "entries",
    }
    if set(document) != allowed_catalog_keys:
        raise ProviderPriceCatalogError("price catalog contains missing or unknown top-level fields")
    version = document.get("catalog_version")
    if not isinstance(version, str) or not version.strip() or version != version.strip() or len(version) > 100:
        raise ProviderPriceCatalogError("catalog_version is required")
    if document.get("currency") != "USD":
        raise ProviderPriceCatalogError("only USD price catalogs are currently supported")
    source_policy = document.get("source_policy")
    if source_policy != "official-provider-pricing":
        raise ProviderPriceCatalogError("source_policy must be official-provider-pricing")
    published_at = _timestamp(document.get("published_at"), "published_at")
    valid_until = _timestamp(document.get("valid_until"), "valid_until")
    if valid_until <= published_at:
        raise ProviderPriceCatalogError("valid_until must be later than published_at")
    entries_raw = document.get("entries")
    if not isinstance(entries_raw, list) or len(entries_raw) > 5000:
        raise ProviderPriceCatalogError("entries must be an array with at most 5000 items")

    entries: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, entry in enumerate(entries_raw):
        prefix = f"entries[{index}]"
        if not isinstance(entry, dict):
            raise ProviderPriceCatalogError(f"{prefix} must be an object")
        allowed_entry_keys = {
            "provider_id", "model_id", "input_usd_per_million_tokens",
            "output_usd_per_million_tokens", "source_url", "retrieved_at",
        }
        if set(entry) != allowed_entry_keys:
            raise ProviderPriceCatalogError(f"{prefix} contains missing or unknown fields")
        provider = entry.get("provider_id")
        model = entry.get("model_id")
        if not isinstance(provider, str) or not _PROVIDER_ID.fullmatch(provider):
            raise ProviderPriceCatalogError(f"{prefix}.provider_id is invalid")
        if (
            not isinstance(model, str) or not model or len(model) > 200
            or any(character.isspace() for character in model)
            or model.casefold() in {"latest", "default", "auto"}
        ):
            raise ProviderPriceCatalogError(f"{prefix}.model_id must be an exact pinned model ID")
        key = (provider, model)
        if key in entries:
            raise ProviderPriceCatalogError(f"{prefix} duplicates a provider/model entry")
        source_url = entry.get("source_url")
        try:
            parsed_url = urlsplit(source_url) if isinstance(source_url, str) else None
            valid_source = bool(
                parsed_url and parsed_url.scheme == "https" and parsed_url.hostname
                and not parsed_url.username and not parsed_url.password
                and not parsed_url.query and not parsed_url.fragment
            )
        except ValueError:
            valid_source = False
        if not valid_source:
            raise ProviderPriceCatalogError(f"{prefix}.source_url must be an HTTPS source link")
        retrieved_at = _timestamp(entry.get("retrieved_at"), f"{prefix}.retrieved_at")
        if retrieved_at < published_at or retrieved_at > valid_until:
            raise ProviderPriceCatalogError(f"{prefix}.retrieved_at must be within the catalog validity window")
        input_rate = _price(entry.get("input_usd_per_million_tokens"), f"{prefix}.input_usd_per_million_tokens")
        output_rate = _price(entry.get("output_usd_per_million_tokens"), f"{prefix}.output_usd_per_million_tokens")
        entries[key] = {
            "input_usd_per_million_tokens": input_rate,
            "output_usd_per_million_tokens": output_rate,
            "source_url": source_url,
            "retrieved_at": retrieved_at,
        }

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ProviderPriceCatalogError("now must be timezone-aware")
    return ProviderPriceCatalog(
        version=version,
        currency="USD",
        published_at=published_at,
        valid_until=valid_until,
        entries=entries,
        digest=hashlib.sha256(raw).hexdigest(),
        source_policy=source_policy,
    )


def estimate_provider_cost(
    catalog: ProviderPriceCatalog,
    *,
    provider_id: str,
    model_id: str,
    usage: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Estimate USD cost only for an exact model and complete, unambiguous usage."""
    if not isinstance(catalog, ProviderPriceCatalog):
        raise TypeError("catalog must be a validated ProviderPriceCatalog")
    if not isinstance(usage, Mapping):
        raise ProviderPriceCatalogError("usage must be a mapping")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ProviderPriceCatalogError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)
    base = {
        "status": "unavailable",
        "currency": catalog.currency,
        "catalog_version": catalog.version,
        "catalog_digest": catalog.digest,
        "provider_id": provider_id,
        "model_id": model_id,
    }
    if current < catalog.published_at:
        return {**base, "reason": "catalog_not_yet_effective"}
    if current > catalog.valid_until:
        return {**base, "reason": "catalog_expired"}
    entry = catalog.entries.get((provider_id, model_id))
    if entry is None:
        return {**base, "reason": "exact_model_price_not_found"}
    input_tokens = _token_count(usage, ("input_units", "input_tokens", "prompt_tokens"), "input_tokens")
    output_tokens = _token_count(usage, ("output_units", "output_tokens", "completion_tokens"), "output_tokens")
    if input_tokens is None or output_tokens is None:
        return {**base, "reason": "provider_usage_incomplete"}
    input_cost = Decimal(input_tokens) * entry["input_usd_per_million_tokens"] / Decimal(1_000_000)
    output_cost = Decimal(output_tokens) * entry["output_usd_per_million_tokens"] / Decimal(1_000_000)
    total = (input_cost + output_cost).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
    return {
        **base,
        "status": "estimated",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_rate_usd_per_million_tokens": str(entry["input_usd_per_million_tokens"]),
        "output_rate_usd_per_million_tokens": str(entry["output_usd_per_million_tokens"]),
        "input_cost_usd": str(input_cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)),
        "output_cost_usd": str(output_cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)),
        "estimated_total_usd": str(total),
        "source_url": entry["source_url"],
        "price_retrieved_at": entry["retrieved_at"].isoformat(),
        "catalog_valid_until": catalog.valid_until.isoformat(),
        "estimate_note": "Estimate uses provider-reported token counts and the supplied catalog; it excludes taxes, credits, discounts, cache tiers and non-token charges.",
    }


__all__ = [
    "ProviderPriceCatalog",
    "ProviderPriceCatalogError",
    "estimate_provider_cost",
    "load_provider_price_catalog",
]
