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
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)")
_SECRET_VALUE = re.compile(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^,\s]+")


def _safe(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str):
        value = _SECRET_VALUE.sub(r"\1=[REDACTED]", value)
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, Mapping):
        return {
            str(k): "[REDACTED]" if _SECRET.search(str(k)) else _safe(v, limit)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe(v, limit) for v in value[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)[:limit]


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

    def finish(self, output: Any = None, status: str = "ok") -> "Span":
        self.output = _safe(output)
        self.end = time.time()
        self.status = status
        return self

    @property
    def duration_ms(self) -> float | None:
        return None if self.end is None else round((self.end - self.start) * 1000, 2)

    def score(self, name: str, value: float, reason: str = "", source: str = "system") -> None:
        if not 0 <= float(value) <= 1:
            raise ValueError("score value must be between 0 and 1")
        self.scores.append(Score(name, float(value), reason, source))


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

    def span(self, name: str, kind: str = "internal", input: Any = None, metadata: Mapping[str, Any] | None = None) -> Span:
        parent = self.spans[-1].span_id if self.spans and self.spans[-1].end is None else None
        item = Span(name, kind, parent_id=parent, input=_safe(input), metadata=dict(metadata or {}))
        self.spans.append(item)
        return item

    def score(self, name: str, value: float, reason: str = "", source: str = "system") -> None:
        if not 0 <= float(value) <= 1:
            raise ValueError("score value must be between 0 and 1")
        self.scores.append(Score(name, float(value), reason, source))

    def finish(self, status: str = "ok") -> "Trace":
        self.end = time.time()
        self.status = status
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "duration_ms": None if self.end is None else round((self.end - self.start) * 1000, 2),
            "status": self.status,
            "metadata": _safe(self.metadata),
            "spans": [{**asdict(s), "duration_ms": s.duration_ms} for s in self.spans],
            "scores": [asdict(s) for s in self.scores],
        }


class LocalExporter:
    """Append traces to a machine-scoped JSONL file; never writes to a target repo."""

    def __init__(self, path: Path | None = None, enabled: bool | None = None) -> None:
        self.enabled = (os.getenv("AUREN_OBSERVABILITY", "0") == "1") if enabled is None else enabled
        self.path = (path or Path.home() / ".auren" / "observability" / "traces.jsonl").expanduser()

    def export(self, trace: Trace) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.as_dict(), sort_keys=True) + "\n")


class Tracer:
    def __init__(self, exporter: LocalExporter | None = None) -> None:
        self.exporter = exporter or LocalExporter()

    def start(self, name: str, metadata: Mapping[str, Any] | None = None) -> Trace:
        return Trace(name, metadata=dict(metadata or {}))

    def end(self, trace: Trace, status: str = "ok") -> Trace:
        trace.finish(status)
        self.exporter.export(trace)
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
        item = PromptVersion(name, version, template, dict(metadata or {}))
        self._versions.setdefault(name, {})[version] = item
        return item

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
        payload = json.dumps([asdict(x) for x in self.items], sort_keys=True, default=str)
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
    totals: dict[str, list[float]] = {}
    failures: list[str] = []
    for index, item in enumerate(dataset.items):
        output = fn(item.input)
        for name, value in judge(item, output).items():
            value = float(value)
            if not 0 <= value <= 1:
                raise ValueError(f"judge score must be between 0 and 1: {name}")
            totals.setdefault(name, []).append(value)
            if value < threshold:
                failures.append(f"item[{index}].{name}={value:.3f}")
    scores = {name: round(sum(values) / len(values), 4) for name, values in totals.items() if values}
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
