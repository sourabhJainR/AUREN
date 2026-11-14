#!/usr/bin/env python3
"""AUREN provider conformance harness.

Static mode validates the cross-provider contract without requiring provider
credentials. Live mode is opt-in and probes installed local CLIs read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MATRIX = ROOT / ".ai-harness" / "PROVIDER_MATRIX.json"
REPORT = ROOT / ".ai-harness" / "provider-conformance.json"


@dataclass
class Check:
    id: str
    status: str
    detail: str
    evidence: dict[str, Any]


@dataclass
class ProviderResult:
    provider: str
    surface: str
    mode: str
    status: str
    checks: list[Check]


def load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def native_surface_checks(provider: str, spec: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    instruction_files = [ROOT / p for p in spec.get("instruction_files", [])]
    existing = [str(p.relative_to(ROOT)) for p in instruction_files if p.exists()]
    checks.append(Check(
        "instruction-surface", "pass" if existing else "fail",
        "native instruction surface is discoverable" if existing else "no native instruction surface found",
        {"expected": spec.get("instruction_files", []), "existing": existing},
    ))
    if provider == "chatgpt":
        checks.append(Check(
            "execution-transport", "pass",
            "ChatGPT uses MCP/app or Codex-in-ChatGPT rather than a local subprocess",
            {"transport": spec.get("execution_transport")},
        ))
    else:
        checks.append(Check(
            "local-transport", "pass" if spec.get("supports_local_execution") else "fail",
            "provider declares an explicit local execution transport",
            {"command_template": spec.get("command_template")},
        ))
    checks.append(Check(
        "jit-context", "pass" if spec.get("native_context_is_jit") else "info",
        "provider context-loading behavior is explicitly recorded",
        {"native_context_is_jit": spec.get("native_context_is_jit")},
    ))
    return checks


def contract_checks(matrix: dict[str, Any]) -> list[Check]:
    checks: list[Check] = []
    providers = matrix.get("providers", {})
    required = {"claude", "codex", "gemini", "chatgpt"}
    missing = sorted(required - set(providers))
    checks.append(Check(
        "provider-coverage", "pass" if not missing else "fail",
        "all required logical providers are represented" if not missing else "provider missing from matrix",
        {"required": sorted(required), "missing": missing},
    ))
    required_fields = ["surfaces", "instruction_files", "optional_mechanisms"]
    for provider, spec in providers.items():
        absent = [field for field in required_fields if not spec.get(field)]
        checks.append(Check(
            f"schema-{provider}", "pass" if not absent else "fail",
            "provider capability declaration is complete" if not absent else "provider declaration is incomplete",
            {"missing": absent},
        ))
    expected = {
        "intent_digest", "goal", "boundaries", "acceptance", "risk",
        "capability_plan", "context_lease_digests", "tool_observations",
        "verification_evidence", "outcome",
    }
    normalized = set(matrix.get("normalized_contract", []))
    missing = sorted(expected - normalized)
    checks.append(Check(
        "normalized-contract", "pass" if not missing else "fail",
        "normalized contract contains required cross-provider evidence" if not missing else "normalized contract is incomplete",
        {"missing": missing},
    ))
    return checks


def live_probe(provider: str) -> Check:
    executable = {"claude": "claude", "codex": "codex", "gemini": "gemini"}.get(provider)
    if not executable:
        return Check("live-probe", "unsupported", "no local subprocess is defined for this surface", {})
    if not shutil.which(executable):
        return Check("live-probe", "unavailable", f"{executable} is not installed", {"executable": executable})
    prompt = "Reply with exactly AER_CONFORMANCE_OK and nothing else. Do not modify files."
    command = {
        "claude": [executable, "-p", prompt],
        "codex": [executable, "exec", "--sandbox", "read-only", prompt],
        "gemini": [executable, "-p", prompt],
    }[provider]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, timeout=30,
            env={**os.environ, "AER_CONFORMANCE_PROBE": "1"},
        )
    except subprocess.TimeoutExpired:
        return Check("live-probe", "timeout", "read-only probe exceeded 30 seconds", {"duration_ms": 30000})
    duration_ms = round((time.monotonic() - started) * 1000)
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        return Check("live-probe", "fail", "provider returned a non-zero exit status", {"returncode": completed.returncode, "duration_ms": duration_ms})
    return Check(
        "live-probe", "pass" if output == "AER_CONFORMANCE_OK" else "fail",
        "exact conformance sentinel returned" if output == "AER_CONFORMANCE_OK" else "unexpected provider output",
        {"returncode": completed.returncode, "duration_ms": duration_ms, "output_digest": hashlib.sha256(output.encode()).hexdigest()},
    )



def live_api_probe(
    provider: str,
    *,
    timeout_seconds: float = 30.0,
    environ: dict[str, str] | None = None,
    transport=None,
    trace_path: Path | None = None,
) -> Check:
    """Send one synthetic public prompt to one explicitly allowlisted model API."""
    from portable.remote_model_providers import OpenAICompatibleProvider, PROVIDER_SPECS, provider_configuration_report

    env = os.environ if environ is None else environ
    if provider not in set(PROVIDER_SPECS) | {"custom"}:
        return Check("api-live-probe", "unsupported", "provider is not in the reviewed adapter registry", {})
    if env.get("AUREN_LOCAL_REMOTE_INFERENCE", "").strip().lower() != "enabled":
        return Check("api-live-probe", "blocked", "remote inference opt-in is not enabled", {})
    configuration = provider_configuration_report(environ=env)
    if configuration.get("errors") or provider not in configuration.get("provider_order", []):
        return Check(
            "api-live-probe", "blocked",
            "provider must be present in a valid explicit AUREN_REMOTE_PROVIDER_ORDER",
            {"configuration_status": configuration.get("status"), "configuration_errors": configuration.get("errors", [])},
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0.1 <= float(timeout_seconds) <= 120
    ):
        return Check("api-live-probe", "blocked", "probe timeout must be between 0.1 and 120 seconds", {})

    try:
        adapter = OpenAICompatibleProvider.from_environment(
            provider, environ=env, **({"transport": transport} if transport is not None else {}),
        )
    except Exception as exc:
        return Check(
            "api-live-probe", "unavailable",
            "provider credentials, model or endpoint configuration is incomplete",
            {"error_type": type(exc).__name__},
        )

    from portable.agency_observability import LocalExporter, Tracer, bind_trace_context, telemetry_usage_metrics

    prompt = (
        "Reply with exactly AUREN_PROVIDER_PROBE_OK and no other text. "
        "This is a read-only public API conformance check. Do not use tools, "
        "access external data, or request any action."
    )
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    tracer = Tracer(LocalExporter(path=trace_path, enabled=True))
    trace = tracer.start(
        "provider-api-conformance",
        metadata={"provider": provider, "model": adapter.model, "probe_kind": "synthetic_public"},
    )
    probe_span = trace.span(
        "provider.api_acceptance_probe",
        kind="acceptance",
        input={"prompt_digest": prompt_digest, "prompt_bytes": len(prompt.encode("utf-8"))},
        metadata={"provider": provider, "model": adapter.model, "privacy": "public"},
        phase="provider_acceptance",
    )
    try:
        with bind_trace_context(trace, probe_span.span_id):
            response = adapter.complete(prompt, timeout_seconds=float(timeout_seconds))
        provider_identity_match = response.provider == provider
        model_identity_match = response.model == adapter.model
        sentinel_match = (
            response.content.strip() == "AUREN_PROVIDER_PROBE_OK"
            and provider_identity_match and model_identity_match
        )
        evidence = {
            "provider": provider,
            "model": adapter.model,
            "reported_model_digest": (
                hashlib.sha256(response.model.encode("utf-8")).hexdigest()
                if isinstance(response.model, str) else None
            ),
            "sentinel_match": sentinel_match,
            "provider_identity_match": provider_identity_match,
            "model_identity_match": model_identity_match,
            "latency_ms": response.latency_ms,
            "usage": telemetry_usage_metrics(response.usage),
            "response_digest": response.response_digest,
            "prompt_digest": prompt_digest,
            "request_id_digest": (
                hashlib.sha256(response.request_id.encode("utf-8")).hexdigest()
                if isinstance(response.request_id, str) and response.request_id else None
            ),
            "trace_id": trace.trace_id,
            "cost_estimate": "not_available_no_pinned_price_catalog",
        }
        probe_span.finish(
            output={
                key: value
                for key, value in evidence.items()
                if key not in {"trace_id", "request_id_digest"}
            },
            status="ok" if sentinel_match else "error",
            terminal_reason=None if sentinel_match else "sentinel_mismatch",
        )
        tracer.end(trace, status="ok" if sentinel_match else "error")
        evidence["telemetry_status"] = trace.metadata.get("observability_export_status", "written")
        return Check(
            "api-live-probe",
            "pass" if sentinel_match else "fail",
            "provider accepted the synthetic request and returned the exact sentinel"
            if sentinel_match else "provider accepted the request but returned an unexpected response",
            evidence,
        )
    except Exception as exc:
        try:
            probe_span.finish(
                output={"outcome": "error", "error_type": type(exc).__name__},
                status="error",
                terminal_reason=type(exc).__name__,
            )
        except Exception:
            pass
        try:
            tracer.end(trace, status="error")
        except Exception:
            pass
        return Check(
            "api-live-probe", "fail",
            "provider API probe failed; response bodies and provider error text are not retained",
            {"provider": provider, "model": adapter.model, "error_type": type(exc).__name__, "trace_id": trace.trace_id},
        )

def run(
    live: bool = False,
    write_report: bool = False,
    *,
    live_api: bool = False,
    api_providers: tuple[str, ...] | list[str] = (),
    api_timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    matrix = load_matrix()
    static = contract_checks(matrix)
    providers: list[ProviderResult] = []
    for provider, spec in matrix["providers"].items():
        checks = native_surface_checks(provider, spec)
        if live:
            checks.append(live_probe(provider))
        failed = any(c.status in {"fail", "timeout"} for c in checks)
        providers.append(ProviderResult(provider, spec["surfaces"][0], "live" if live else "static", "fail" if failed else "pass", checks))
    static_pass = not any(c.status == "fail" for c in static)
    selected_api_providers = tuple(dict.fromkeys(api_providers))
    api_probes = (
        [asdict(live_api_probe(provider, timeout_seconds=api_timeout_seconds)) for provider in selected_api_providers]
        if live_api else []
    )
    api_probe_ready = bool(api_probes) and all(item["status"] == "pass" for item in api_probes)
    cli_live_ready = (
        live and static_pass
        and all(p.status == "pass" for p in providers)
        and all(check.status == "pass" for provider in providers for check in provider.checks if check.id == "live-probe")
    )
    report = {
        "schema_version": 1,
        "generated_at": time.time(),
        "mode": "live_and_api" if live and live_api else "live_api" if live_api else "live" if live else "static",
        "static_contract": {"status": "pass" if static_pass else "fail", "checks": [asdict(c) for c in static]},
        "providers": [asdict(p) for p in providers],
        "api_providers": api_probes,
        "api_probe_ready": api_probe_ready if live_api else False,
        "release_ready": cli_live_ready and (not live_api or api_probe_ready),
    }
    if write_report:
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    from portable.remote_model_providers import PROVIDER_SPECS

    parser = argparse.ArgumentParser(description="Run AUREN cross-provider conformance checks")
    parser.add_argument("--live", action="store_true", help="run read-only probes against installed provider CLIs")
    parser.add_argument("--live-api", action="store_true", help="send one synthetic public prompt to each selected direct model API; may incur provider charges")
    parser.add_argument("--api-provider", action="append", choices=sorted(set(PROVIDER_SPECS) | {"custom"}), default=[], help="select a direct API provider for --live-api; repeat to test more than one")
    parser.add_argument("--api-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--write-report", action="store_true", help="persist the JSON report under .ai-harness")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    if args.live_api and not args.api_provider:
        parser.error("--live-api requires at least one --api-provider")
    if args.api_provider and not args.live_api:
        parser.error("--api-provider requires --live-api")
    if len(set(args.api_provider)) != len(args.api_provider):
        parser.error("duplicate --api-provider values would repeat billable API probes")
    report = run(
        live=args.live, write_report=args.write_report, live_api=args.live_api,
        api_providers=args.api_provider, api_timeout_seconds=args.api_timeout_seconds,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Provider conformance ({report['mode']}):")
        print(f"- static contract: {report['static_contract']['status']}")
        for provider in report["providers"]:
            print(f"- {provider['provider']}: {provider['status']}")
            for check in provider["checks"]:
                print(f"  {check['status'].upper():11} {check['id']}: {check['detail']}")
        for probe in report["api_providers"]:
            print(f"- {probe['evidence'].get('provider', 'api')}: {probe['status']}")
            print(f"  {probe['detail']}")
        print("RELEASE READY" if report["release_ready"] else "NOT RELEASE READY")
    if args.live and not report["release_ready"]:
        return 1
    if args.live_api and not report["api_probe_ready"]:
        return 1
    return 0 if report["static_contract"]["status"] == "pass" and all(p.get("status") == "pass" for p in report["providers"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
