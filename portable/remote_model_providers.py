"""Bounded, policy-aware adapters for common hosted model APIs.

This module is an inference transport, not an agent runtime. It never executes
provider tool calls. Provider selection is explicit, HTTPS destinations are
pinned to reviewed vendor hosts, credentials stay in process memory, and each
provider attempt is charged by HybridModelRouter's shared execution guard.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .guarded_network import guarded_https_get


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    endpoint: str
    host: str
    api_key_env: str
    model_env: str
    protocol: str = "openai"


@dataclass(frozen=True)
class RemoteModelResponse:
    provider: str
    model: str
    content: str
    usage: Mapping[str, int]
    request_id: str | None
    finish_reason: str | None
    latency_ms: float
    response_digest: str
    tool_calls: tuple[Mapping[str, Any], ...] = ()


class RemoteProviderError(RuntimeError):
    """A provider request failed without exposing credentials or response bodies."""

    def __init__(self, provider: str, message: str, *, status: int | None = None, retryable: bool = True):
        self.provider = provider
        self.status = status
        self.retryable = retryable
        super().__init__(f"{provider}: {message}")


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec("openai", "https://api.openai.com/v1/responses", "api.openai.com", "OPENAI_API_KEY", "OPENAI_MODEL", "responses"),
    "anthropic": ProviderSpec("anthropic", "https://api.anthropic.com/v1/messages", "api.anthropic.com", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "anthropic"),
    "gemini": ProviderSpec("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "generativelanguage.googleapis.com", "GEMINI_API_KEY", "GEMINI_MODEL"),
    "groq": ProviderSpec("groq", "https://api.groq.com/openai/v1/chat/completions", "api.groq.com", "GROQ_API_KEY", "GROQ_MODEL"),
    "xai": ProviderSpec("xai", "https://api.x.ai/v1/responses", "api.x.ai", "XAI_API_KEY", "XAI_MODEL", "responses"),
    "openrouter": ProviderSpec("openrouter", "https://openrouter.ai/api/v1/chat/completions", "openrouter.ai", "OPENROUTER_API_KEY", "OPENROUTER_MODEL"),
    "together": ProviderSpec("together", "https://api.together.xyz/v1/chat/completions", "api.together.xyz", "TOGETHER_API_KEY", "TOGETHER_MODEL"),
    "fireworks": ProviderSpec("fireworks", "https://api.fireworks.ai/inference/v1/chat/completions", "api.fireworks.ai", "FIREWORKS_API_KEY", "FIREWORKS_MODEL"),
    "deepseek": ProviderSpec("deepseek", "https://api.deepseek.com/chat/completions", "api.deepseek.com", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"),
    "mistral": ProviderSpec("mistral", "https://api.mistral.ai/v1/chat/completions", "api.mistral.ai", "MISTRAL_API_KEY", "MISTRAL_MODEL"),
    "cerebras": ProviderSpec("cerebras", "https://api.cerebras.ai/v1/chat/completions", "api.cerebras.ai", "CEREBRAS_API_KEY", "CEREBRAS_MODEL"),
    "perplexity": ProviderSpec("perplexity", "https://api.perplexity.ai/chat/completions", "api.perplexity.ai", "PERPLEXITY_API_KEY", "PERPLEXITY_MODEL"),
    "cohere": ProviderSpec("cohere", "https://api.cohere.com/v2/chat", "api.cohere.com", "COHERE_API_KEY", "COHERE_MODEL", "cohere"),
}

DEFAULT_PROVIDER_ORDER = (
    "openai", "anthropic", "gemini", "groq", "xai", "openrouter",
    "together", "fireworks", "deepseek", "mistral", "cerebras", "perplexity", "cohere",
)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 4096


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
        number = value.get(key)
        if type(number) is int and 0 <= number <= 10**12:
            result[key] = number
    return result


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, Mapping) and item.get("type") in {"text", "output_text"}:
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _tool_calls(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Normalize tool calls for audit/display; never execute them."""
    if not isinstance(value, list):
        return ()
    normalized = []
    for item in value[:32]:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function", item)
        if not isinstance(function, Mapping):
            continue
        name = function.get("name")
        arguments = function.get("arguments", {})
        if not isinstance(name, str) or not name or len(name) > 200:
            continue
        if isinstance(arguments, str):
            if len(arguments) > 32_768:
                continue
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                continue
        if not isinstance(arguments, Mapping):
            continue
        normalized.append({
            "id": str(item.get("id", ""))[:200],
            "name": name,
            "arguments": dict(arguments),
        })
    return tuple(normalized)


class OpenAICompatibleProvider:
    """One fixed-host provider adapter with bounded, redirect-free HTTPS."""

    def __init__(
        self,
        spec: ProviderSpec,
        *,
        api_key: str,
        model: str,
        transport=guarded_https_get,
        allowed_hosts: Sequence[str] | None = None,
    ):
        if not isinstance(spec, ProviderSpec):
            raise TypeError("spec must be a ProviderSpec")
        if spec.name == "custom":
            parsed = urlsplit(spec.endpoint)
            normalized_allowlist = tuple(sorted({
                item.strip().lower() for item in (allowed_hosts or ()) if isinstance(item, str) and item.strip()
            }))
            if (
                parsed.scheme != "https" or parsed.hostname != spec.host
                or parsed.port not in {None, 443} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/chat/completions")
                or normalized_allowlist != (spec.host,)
            ):
                raise ValueError("custom provider must use an HTTPS Chat Completions endpoint with an exact host allowlist")
        else:
            registered_spec = PROVIDER_SPECS.get(spec.name)
            if registered_spec is None or spec != registered_spec:
                raise ValueError("provider endpoint and host must match the reviewed provider registry")
            if allowed_hosts is not None and tuple(allowed_hosts) != (spec.host,):
                raise ValueError("built-in provider host allowlist cannot be overridden")
        if not isinstance(api_key, str) or len(api_key) < 8 or len(api_key) > 4096:
            raise ValueError("provider API key is missing or invalid")
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("provider model ID is required")
        if not callable(transport):
            raise TypeError("transport must be callable")
        self.spec = spec
        self._api_key = api_key
        self.model = model.strip()
        self.transport = transport
        self.allowed_hosts = (spec.host,)

    @classmethod
    def from_environment(cls, name: str, *, environ: Mapping[str, str] | None = None, transport=guarded_https_get):
        env = os.environ if environ is None else environ
        if name == "custom":
            endpoint = env.get("AUREN_CUSTOM_PROVIDER_URL", "").strip()
            parsed = urlsplit(endpoint)
            allowlist = {
                item.strip().lower()
                for item in env.get("AUREN_CUSTOM_PROVIDER_ALLOWED_HOSTS", "").split(",")
                if item.strip()
            }
            if (
                parsed.scheme != "https" or not parsed.hostname
                or parsed.port not in {None, 443} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.rstrip("/").endswith("/chat/completions")
                or parsed.hostname.lower() not in allowlist
            ):
                raise ValueError("custom provider URL must be HTTPS and its hostname must be explicitly allowlisted")
            spec = ProviderSpec(
                "custom", endpoint, parsed.hostname.lower(),
                "AUREN_CUSTOM_PROVIDER_API_KEY", "AUREN_CUSTOM_PROVIDER_MODEL",
            )
        else:
            spec = PROVIDER_SPECS.get(name)
            if spec is None:
                raise ValueError(f"unsupported remote model provider: {name}")
        key = env.get(spec.api_key_env, "")
        model = env.get("AUREN_MODEL_" + name.upper(), env.get(spec.model_env, ""))
        if not key or not model:
            raise ValueError(f"{name} requires {spec.api_key_env} and {spec.model_env} (or AUREN_MODEL_{name.upper()})")
        return cls(
            spec, api_key=key, model=model, transport=transport,
            allowed_hosts=(spec.host,) if name == "custom" else None,
        )

    def _request_payload(self, prompt: str) -> tuple[dict[str, Any], dict[str, str]]:
        system = (
            "You are an inference provider inside AUREN. Return a useful answer to the requested task. "
            "Do not claim to have accessed files, tools, systems, or evidence that were not supplied. "
            "Do not execute or authorize actions. AUREN owns all tools, approvals, verification and execution."
        )
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if self.spec.protocol == "cohere":
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "stream": False,
            }
            headers = {
                "authorization": "Bearer " + self._api_key,
                "content-type": "application/json",
                "accept": "application/json",
            }
        elif self.spec.protocol == "anthropic":
            payload = {"model": self.model, "max_tokens": MAX_OUTPUT_TOKENS, "messages": messages[1:], "system": system}
            headers = {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "accept": "application/json",
            }
        elif self.spec.protocol == "responses":
            payload = {
                "model": self.model,
                "instructions": system,
                "input": prompt,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "stream": False,
            }
            headers = {
                "authorization": "Bearer " + self._api_key,
                "content-type": "application/json",
                "accept": "application/json",
            }
        else:
            payload = {"model": self.model, "messages": messages, "max_tokens": MAX_OUTPUT_TOKENS, "stream": False}
            headers = {
                "authorization": "Bearer " + self._api_key,
                "content-type": "application/json",
                "accept": "application/json",
            }
        return payload, headers

    def complete(self, prompt: str, *, timeout_seconds: float = 30.0) -> RemoteModelResponse:
        """Run inference and emit a privacy-safe span when a canonical trace is bound.

        Prompt and completion contents, credentials, and provider error text are
        never copied into telemetry. Observability failures cannot change the
        provider result or its caller-visible exception.
        """
        span = None
        # Avoid allocating an encoded copy of an obviously oversized prompt just
        # to create telemetry; the untraced validator remains authoritative.
        if isinstance(prompt, str) and len(prompt) <= 256 * 1024:
            try:
                from .agency_observability import current_trace_context
                active = current_trace_context()
                if active is not None:
                    trace, parent_span_id = active
                    prompt_bytes = prompt.encode("utf-8")
                    span = trace.span(
                        "provider.inference." + self.spec.name,
                        kind="provider",
                        input={"prompt_bytes": len(prompt_bytes)},
                        metadata={
                            "provider": self.spec.name,
                            "model": self.model,
                            "prompt_digest": hashlib.sha256(prompt_bytes).hexdigest(),
                        },
                        parent_id=parent_span_id,
                        phase="provider",
                        provider=self.spec.name,
                        model=self.model,
                    )
            except Exception:
                span = None
        try:
            result = self._complete_untraced(prompt, timeout_seconds=timeout_seconds)
        except BaseException as exc:
            if span is not None:
                try:
                    span.finish(
                        output={"outcome": "error", "error_type": type(exc).__name__},
                        status="error",
                        terminal_reason=type(exc).__name__,
                    )
                except Exception:
                    pass
            raise
        if span is not None:
            try:
                raw_finish_reason = result.finish_reason
                safe_finish_reason = (
                    raw_finish_reason
                    if isinstance(raw_finish_reason, str)
                    and 0 < len(raw_finish_reason) <= 40
                    and all(char.isalnum() or char in "._-" for char in raw_finish_reason)
                    else ("other" if raw_finish_reason else None)
                )
                span.finish(
                    output={
                        "outcome": "completed",
                        "response_digest": result.response_digest,
                        "response_bytes": len(result.content.encode("utf-8")),
                        "usage": dict(result.usage),
                        "latency_ms": result.latency_ms,
                        "finish_reason": safe_finish_reason,
                        "tool_call_count": len(result.tool_calls),
                    },
                    status="ok",
                )
            except Exception:
                pass
        return result

    def _complete_untraced(self, prompt: str, *, timeout_seconds: float = 30.0) -> RemoteModelResponse:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > 256 * 1024:
            raise ValueError("prompt must be non-empty UTF-8 text within 256 KiB")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0.1 <= float(timeout_seconds) <= 120:
            raise ValueError("provider timeout must be between 0.1 and 120 seconds")
        payload, headers = self._request_payload(prompt)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        started = time.monotonic()
        try:
            status, raw = self.transport(
                self.spec.endpoint,
                allowed_hosts=self.allowed_hosts,
                method="POST",
                body=body,
                headers=headers,
                timeout=float(timeout_seconds),
                max_bytes=MAX_RESPONSE_BYTES,
            )
        except Exception as exc:
            raise RemoteProviderError(self.spec.name, "HTTPS transport failed", retryable=True) from exc
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise RemoteProviderError(self.spec.name, "provider response exceeded the configured size limit", status=status, retryable=False)
        if status != 200:
            retryable = status in {408, 409, 425, 429} or status >= 500
            raise RemoteProviderError(self.spec.name, "provider returned HTTP " + str(status), status=status, retryable=retryable)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteProviderError(self.spec.name, "provider returned invalid JSON", status=status, retryable=False) from exc
        if not isinstance(decoded, Mapping):
            raise RemoteProviderError(self.spec.name, "provider response must be a JSON object", status=status, retryable=False)

        if self.spec.protocol == "cohere":
            message = decoded.get("message", {})
            blocks = message.get("content", []) if isinstance(message, Mapping) else []
            content = _text_content(blocks)
            tool_calls = ()
            finish_reason = decoded.get("finish_reason")
            usage_value = decoded.get("usage", {})
            billed = usage_value.get("billed_units", {}) if isinstance(usage_value, Mapping) else {}
            usage = _usage({
                "input_tokens": billed.get("input_tokens"),
                "output_tokens": billed.get("output_tokens"),
            })
            request_id = decoded.get("id")
        elif self.spec.protocol == "anthropic":
            blocks = decoded.get("content", [])
            content = _text_content(blocks)
            tool_calls = tuple(
                {"id": str(block.get("id", ""))[:200], "name": block.get("name", ""),
                 "arguments": block.get("input", {})}
                for block in blocks if isinstance(block, Mapping) and block.get("type") == "tool_use"
                and isinstance(block.get("name"), str) and isinstance(block.get("input", {}), Mapping)
            ) if isinstance(blocks, list) else ()
            finish_reason = decoded.get("stop_reason")
            usage = _usage(decoded.get("usage"))
            request_id = decoded.get("id")
        elif self.spec.protocol == "responses":
            output = decoded.get("output", [])
            text_parts = []
            calls = []
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, Mapping):
                        continue
                    if item.get("type") == "message" and isinstance(item.get("content"), list):
                        text_parts.append(_text_content(item["content"]))
                    elif item.get("type") == "function_call":
                        calls.extend(_tool_calls([{
                            "id": item.get("call_id", item.get("id", "")),
                            "name": item.get("name", ""),
                            "arguments": item.get("arguments", {}),
                        }]))
            content = "".join(text_parts) or _text_content(decoded.get("output_text"))
            tool_calls = tuple(calls)
            finish_reason = decoded.get("status")
            usage = _usage(decoded.get("usage"))
            request_id = decoded.get("id")
        else:
            choices = decoded.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise RemoteProviderError(self.spec.name, "provider response has no completion choice", status=status, retryable=False)
            message = choices[0].get("message", {})
            if not isinstance(message, Mapping):
                raise RemoteProviderError(self.spec.name, "provider completion message is invalid", status=status, retryable=False)
            content = _text_content(message.get("content"))
            tool_calls = _tool_calls(message.get("tool_calls"))
            finish_reason = choices[0].get("finish_reason")
            usage = _usage(decoded.get("usage"))
            request_id = decoded.get("id")
        if not content and not tool_calls:
            raise RemoteProviderError(self.spec.name, "provider returned an empty completion", status=status, retryable=False)
        return RemoteModelResponse(
            provider=self.spec.name,
            model=self.model,
            content=content,
            usage=usage,
            request_id=request_id[:200] if isinstance(request_id, str) else None,
            finish_reason=finish_reason[:100] if isinstance(finish_reason, str) else None,
            latency_ms=round((time.monotonic() - started) * 1000, 2),
            response_digest=hashlib.sha256(raw).hexdigest(),
            tool_calls=tool_calls,
        )


class RemoteProviderPool:
    """Ordered provider failover. One provider is attempted per supervisor charge."""

    def __init__(self, providers: Sequence[OpenAICompatibleProvider]):
        if not isinstance(providers, Sequence) or not providers:
            raise ValueError("at least one configured remote provider is required")
        if not all(isinstance(provider, OpenAICompatibleProvider) for provider in providers):
            raise TypeError("providers must contain OpenAICompatibleProvider adapters")
        names = [provider.spec.name for provider in providers]
        if len(names) != len(set(names)):
            raise ValueError("remote provider order must not contain duplicates")
        self.providers = tuple(providers)
        self.available = True
        self.max_attempts = len(self.providers)

    @classmethod
    def from_environment(cls, *, environ: Mapping[str, str] | None = None, transport=guarded_https_get):
        env = os.environ if environ is None else environ
        configured = env.get("AUREN_REMOTE_PROVIDER_ORDER", "").strip()
        order = tuple(item.strip().lower() for item in configured.split(",") if item.strip()) if configured else DEFAULT_PROVIDER_ORDER
        if not order:
            return None
        unknown = sorted(set(order) - (set(PROVIDER_SPECS) | {"custom"}))
        if unknown:
            raise ValueError("unsupported providers in AUREN_REMOTE_PROVIDER_ORDER: " + ", ".join(unknown))
        providers = []
        for name in order:
            if name == "custom":
                try:
                    providers.append(OpenAICompatibleProvider.from_environment(
                        name, environ=env, transport=transport,
                    ))
                except ValueError as exc:
                    if env.get("AUREN_CUSTOM_PROVIDER_API_KEY") or env.get("AUREN_CUSTOM_PROVIDER_URL"):
                        raise
                continue
            spec = PROVIDER_SPECS[name]
            key = env.get(spec.api_key_env, "")
            model = env.get("AUREN_MODEL_" + name.upper(), env.get(spec.model_env, ""))
            if key and model:
                providers.append(OpenAICompatibleProvider(spec, api_key=key, model=model, transport=transport))
        return cls(providers) if providers else None

    def _providers_for(self, request: Any) -> tuple[OpenAICompatibleProvider, ...]:
        requested = getattr(request, "provider_order", None)
        if requested is None:
            return self.providers
        if (
            not isinstance(requested, (tuple, list))
            or not 1 <= len(requested) <= len(self.providers)
            or not all(isinstance(name, str) and name.strip() for name in requested)
        ):
            raise RemoteProviderError("provider-pool", "per-request provider order is invalid", retryable=False)
        names = tuple(name.strip().lower() for name in requested)
        if len(names) != len(set(names)):
            raise RemoteProviderError("provider-pool", "per-request provider order contains duplicates", retryable=False)
        configured = {provider.spec.name: provider for provider in self.providers}
        missing = [name for name in names if name not in configured]
        if missing:
            raise RemoteProviderError(
                "provider-pool",
                "requested provider is not configured: " + ", ".join(missing),
                retryable=False,
            )
        return tuple(configured[name] for name in names)

    def max_attempts_for(self, request: Any) -> int:
        """Return the available retry count for the caller-selected configured chain."""
        return len(self._providers_for(request))

    def __call__(self, request: Any) -> RemoteModelResponse:
        index = getattr(request, "provider_attempt", 0)
        if type(index) is not int or index < 0:
            raise ValueError("provider_attempt must be a non-negative integer")
        providers = self._providers_for(request)
        if index >= len(providers):
            raise RemoteProviderError("provider-pool", "configured provider chain is exhausted", retryable=False)
        provider = providers[index]
        return provider.complete(request.task, timeout_seconds=request.timeout_seconds)


def provider_pool_from_environment(*, environ: Mapping[str, str] | None = None):
    """Return the explicitly configured remote provider pool, or None when disabled."""
    env = os.environ if environ is None else environ
    if not env.get("AUREN_REMOTE_PROVIDER_ORDER", "").strip():
        return None
    return RemoteProviderPool.from_environment(environ=env)


__all__ = [
    "DEFAULT_PROVIDER_ORDER", "OpenAICompatibleProvider", "PROVIDER_SPECS",
    "ProviderSpec", "RemoteModelResponse", "RemoteProviderError",
    "RemoteProviderPool", "provider_pool_from_environment",
]
