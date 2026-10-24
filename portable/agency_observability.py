"""Dependency-free tracing, scoring, datasets, prompts and experiments for AUREN.

The design is intentionally compatible with Opik-style concepts without making
Opik a runtime dependency: traces contain nested spans, spans can carry scores,
experiments run against versioned datasets, prompts are immutable by version,
and telemetry is append-only and local by default. Secrets and large payloads
are redacted before persistence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from portable.identity_compat import canonical_runtime_home, get_auren_env
from typing import Any, Callable, Iterable, Mapping

_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)")
_SECRET_VALUE = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*(?:Bearer\s+)?[^,\s]+")


_TRACE_LOCK = threading.RLock()
_MAX_TRACE_BYTES = 1_000_000
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")


def _safe(value: Any, limit: int = 4000, _depth: int = 0) -> Any:
    """Return bounded JSON-safe data without invoking user-defined repr methods."""
    if _depth > 12:
        return "[MAX_DEPTH]"
    if isinstance(value, str):
        value = _SECRET_VALUE.sub(r"\1=[REDACTED]", value)
        value = _BEARER_VALUE.sub("Bearer [REDACTED]", value)
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, Mapping):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                result["[TRUNCATED]"] = True
                break
            safe_key = _safe(str(key), 256, _depth + 1)
            result[safe_key] = "[REDACTED]" if _SECRET.search(str(key)) else _safe(item, limit, _depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        result = [_safe(item, limit, _depth + 1) for item in items[:100]]
        if len(items) > 100:
            result.append("[TRUNCATED]")
        return result
    if isinstance(value, float):
        return value if math.isfinite(value) else "[NON_FINITE_NUMBER]"
    if isinstance(value, (int, bool)) or value is None:
        return value
    return f"[UNSUPPORTED_TYPE:{type(value).__name__}]"

@dataclass
class Score:
    name: str
    value: float
    reason: str = ""
    source: str = "system"


@dataclass
class Span:
    name: str
    kind: str = "internal"
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: str | None = None
    start: float = field(default_factory=time.time)
    end: float | None = None
    status: str = "running"
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scores: list[Score] = field(default_factory=list)
    phase: str = "unknown"
    actor_id: str | None = None
    provider: str | None = None
    model: str | None = None
    source_revision: str | None = None
    terminal_reason: str | None = None
    on_change: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def finish(self, output: Any = None, status: str = "ok", terminal_reason: str | None = None) -> "Span":
        with _TRACE_LOCK:
            self.output = _safe(output)
            self.end = time.time()
            self.status = status
            self.terminal_reason = terminal_reason
            if self.on_change is not None:
                self.on_change()
            return self

    @property
    def duration_ms(self) -> float | None:
        return None if self.end is None else round((self.end - self.start) * 1000, 2)

    def score(self, name: str, value: float, reason: str = "", source: str = "system") -> None:
        if not 0 <= float(value) <= 1:
            raise ValueError("score value must be between 0 and 1")
        with _TRACE_LOCK:
            self.scores.append(Score(name, float(value), reason, source))
            if self.on_change is not None:
                self.on_change()

    def as_dict(self) -> dict[str, Any]:
        return _safe({
            "name": self.name,
            "kind": self.kind,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start": self.start,
            "end": self.end,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "input": self.input,
            "output": self.output,
            "metadata": self.metadata,
            "scores": [asdict(score) for score in self.scores],
            "phase": self.phase,
            "actor_id": self.actor_id,
            "provider": self.provider,
            "model": self.model,
            "source_revision": self.source_revision,
            "terminal_reason": self.terminal_reason,
        })


@dataclass
class Trace:
    name: str
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    start: float = field(default_factory=time.time)
    end: float | None = None
    status: str = "running"
    metadata: dict[str, Any] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
    scores: list[Score] = field(default_factory=list)
    schema_version: int = 1
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    source_revision: str | None = None
    config_digest: str | None = None
    on_change: Callable[["Trace"], None] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.run_id is None:
            self.run_id = self.trace_id

    def span(
        self,
        name: str,
        kind: str = "internal",
        input: Any = None,
        metadata: Mapping[str, Any] | None = None,
        parent_id: str | None = None,
        phase: str = "unknown",
        actor_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        source_revision: str | None = None,
    ) -> Span:
        """Create a span with explicit parentage; never infer parent from append order."""
        if not name.strip():
            raise ValueError("span name is required")
        with _TRACE_LOCK:
            known_ids = {span.span_id for span in self.spans}
            if parent_id is not None and parent_id not in known_ids:
                raise ValueError("parent_id must reference a span in this trace")
            item = Span(
                name, kind, parent_id=parent_id, input=_safe(input),
                metadata=_safe(dict(metadata or {})), phase=phase,
                actor_id=actor_id, provider=provider, model=model,
                source_revision=source_revision or self.source_revision,
                on_change=(lambda: self.on_change(self)) if self.on_change is not None else None,
            )
            self.spans.append(item)
            if self.on_change is not None:
                self.on_change(self)
            return item

    def score(self, name: str, value: float, reason: str = "", source: str = "system") -> None:
        if not 0 <= float(value) <= 1:
            raise ValueError("score value must be between 0 and 1")
        with _TRACE_LOCK:
            self.scores.append(Score(name, float(value), reason, source))
            if self.on_change is not None:
                self.on_change(self)

    def finish(self, status: str = "ok") -> "Trace":
        with _TRACE_LOCK:
            self.end = time.time()
            self.status = status
        return self

    def as_dict(self) -> dict[str, Any]:
        with _TRACE_LOCK:
            data = {
                "schema_version": self.schema_version,
                "trace_id": self.trace_id,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "task_id": self.task_id,
                "source_revision": self.source_revision,
                "config_digest": self.config_digest,
                "name": self.name,
                "start": self.start,
                "end": self.end,
                "duration_ms": None if self.end is None else round((self.end - self.start) * 1000, 2),
                "status": self.status,
                "metadata": _safe(self.metadata),
                "spans": [s.as_dict() for s in self.spans],
                "scores": [_safe(asdict(s)) for s in self.scores],
            }
        canonical = json.dumps(_safe(data), sort_keys=True, separators=(",", ":"), allow_nan=False)
        data["event_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return data


class LocalExporter:
    """Append traces to a machine-scoped JSONL file; never writes to a target repo."""

    def __init__(self, path: Path | None = None, enabled: bool | None = None) -> None:
        self.enabled = (get_auren_env("OBSERVABILITY", "0") == "1") if enabled is None else enabled
        self.path = (path or canonical_runtime_home() / "observability" / "traces.jsonl").expanduser()

    def export(self, trace: Trace) -> None:
        if not self.enabled:
            return
        payload = json.dumps(_safe(trace.as_dict()), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > _MAX_TRACE_BYTES:
            raise ValueError("trace exceeds the configured observability event size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (payload + "\n").encode("utf-8")
        # One append write prevents interleaved records between threads in this process.
        with _TRACE_LOCK:
            descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(descriptor, line)
                if written != len(line):
                    raise OSError("incomplete observability append")
            finally:
                os.close(descriptor)


class Tracer:
    def __init__(self, exporter: LocalExporter | None = None) -> None:
        self.exporter = exporter or LocalExporter()

    def start(
        self,
        name: str,
        metadata: Mapping[str, Any] | None = None,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        source_revision: str | None = None,
        config_digest: str | None = None,
    ) -> Trace:
        details = dict(metadata or {})
        trace = Trace(
            name,
            metadata=details,
            run_id=run_id or details.get("run_id"),
            session_id=session_id or details.get("session_id"),
            task_id=task_id or details.get("task_id"),
            source_revision=source_revision or details.get("source_revision"),
            config_digest=config_digest or details.get("config_digest"),
        )
        trace.on_change = self._export_safely
        self._export_safely(trace)
        return trace

    def _export_safely(self, trace: Trace) -> None:
        try:
            self.exporter.export(trace)
        except Exception as exc:
            # Observability is not execution authority: exporter failure must not
            # overwrite the canonical task outcome or trigger side-effect replay.
            trace.metadata["observability_export_status"] = "failed"
            trace.metadata["observability_export_error"] = type(exc).__name__
        else:
            trace.metadata.pop("observability_export_status", None)
            trace.metadata.pop("observability_export_error", None)

    def end(self, trace: Trace, status: str = "ok") -> Trace:
        trace.finish(status)
        self._export_safely(trace)
        return trace


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: str
    template: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.template.encode()).hexdigest()


class PromptRegistry:
    """Immutable-in-use prompt versions with explicit promotion."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, PromptVersion]] = {}
        self._active: dict[str, str] = {}

    def register(self, name: str, version: str, template: str, metadata: Mapping[str, Any] | None = None) -> PromptVersion:
        if not name.strip() or not version.strip() or not template.strip():
            raise ValueError("prompt name, version and template are required")
        item = PromptVersion(name, version, template, _safe(dict(metadata or {})))
        existing = self._versions.get(name, {}).get(version)
        if existing is not None and existing != item:
            raise ValueError(f"prompt version is immutable: {name}:{version}")
        self._versions.setdefault(name, {})[version] = item
        return existing or item

    def promote(self, name: str, version: str) -> PromptVersion:
        item = self.get(name, version)
        self._active[name] = version
        return item

    def get(self, name: str, version: str | None = None) -> PromptVersion:
        selected = version or self._active.get(name)
        if not selected or selected not in self._versions.get(name, {}):
            raise KeyError(f"prompt version not found: {name}:{selected or '<active>'}")
        return self._versions[name][selected]


@dataclass(frozen=True)
class DatasetItem:
    input: Any
    expected: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Dataset:
    name: str
    version: str
    items: tuple[DatasetItem, ...]

    @property
    def digest(self) -> str:
        payload = json.dumps(_safe([asdict(x) for x in self.items]), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ExperimentResult:
    dataset: str
    dataset_version: str
    dataset_digest: str
    scores: Mapping[str, float]
    passed: bool
    failures: tuple[str, ...] = ()


def run_experiment(dataset: Dataset, fn: Callable[[Any], Any], judge: Callable[[DatasetItem, Any], Mapping[str, float]], threshold: float = 0.8) -> ExperimentResult:
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    if not dataset.name.strip() or not dataset.version.strip():
        raise ValueError("dataset name and version are required")
    if not dataset.items:
        raise ValueError("empty datasets are unscored and cannot pass")
    totals: dict[str, list[float]] = {}
    failures: list[str] = []
    expected_dimensions: set[str] | None = None
    for index, item in enumerate(dataset.items):
        output = fn(item.input)
        judged = judge(item, output)
        if not isinstance(judged, Mapping) or not judged:
            raise ValueError(f"item[{index}] has no evaluation dimensions; result is unscored")
        dimensions = {str(name) for name in judged}
        if any(not name.strip() for name in dimensions):
            raise ValueError(f"item[{index}] has an empty evaluation dimension")
        if expected_dimensions is None:
            expected_dimensions = dimensions
        elif dimensions != expected_dimensions:
            raise ValueError(f"item[{index}] evaluation dimensions differ; result is unscored")
        for name, raw_value in judged.items():
            value = float(raw_value)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"judge score must be finite and between 0 and 1: {name}")
            totals.setdefault(str(name), []).append(value)
            if value < threshold:
                failures.append(f"item[{index}].{name}={value:.3f}")
    scores = {name: round(sum(values) / len(values), 4) for name, values in totals.items() if values}
    if not scores or any(len(values) != len(dataset.items) for values in totals.values()):
        raise ValueError("incomplete evaluation coverage; result is unscored")
    return ExperimentResult(dataset.name, dataset.version, dataset.digest, scores, not failures, tuple(failures))


def retrieval_scores(expected_paths: Iterable[str], selected_paths: Iterable[str]) -> dict[str, float]:
    expected = set(expected_paths)
    selected = set(selected_paths)
    if not expected:
        return {"precision": 1.0 if not selected else 0.0, "recall": 1.0}
    return {
        "precision": len(expected & selected) / len(selected) if selected else 0.0,
        "recall": len(expected & selected) / len(expected),
    }
