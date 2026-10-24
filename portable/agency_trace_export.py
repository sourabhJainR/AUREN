"""Provider-neutral trace export and trace-to-regression correlation for AUREN v13."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .agency_observability import LocalExporter, _MAX_TRACE_BYTES, _TRACE_LOCK, _safe


class TraceExporter(Protocol):
    """Minimal exporter contract so telemetry backends remain replaceable."""

    def export(self, trace: Any) -> None:
        ...


class CompositeTraceExporter:
    """Fan a completed trace out to zero or more exporters."""

    def __init__(self, exporters: Sequence[TraceExporter] = ()) -> None:
        self.exporters = tuple(exporters)
        self.last_errors: tuple[str, ...] = ()

    def export(self, trace: Any) -> None:
        errors: list[str] = []
        for exporter in self.exporters:
            try:
                exporter.export(trace)
            except Exception as exc:  # Tracer catches this; canonical task state is unchanged.
                errors.append(type(exc).__name__)
        self.last_errors = tuple(errors)
        if errors:
            raise RuntimeError("one or more trace exporters failed")

    def export_event(self, trace: Any, event_type: str, span: Any = None) -> None:
        errors: list[str] = []
        for exporter in self.exporters:
            try:
                export_event = getattr(exporter, "export_event", None)
                if callable(export_event):
                    export_event(trace, event_type, span)
                else:
                    exporter.export(trace)
            except Exception as exc:
                errors.append(type(exc).__name__)
        self.last_errors = tuple(errors)
        if errors:
            raise RuntimeError("one or more trace event exporters failed")


class JsonlTraceExporter:
    """Generic JSONL exporter for any trace implementing ``as_dict``."""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path).expanduser()
        self.enabled = enabled
        self._local_exporter = LocalExporter(self.path, enabled=enabled)

    def export(self, trace: Any) -> None:
        if hasattr(trace, "as_dict"):
            self._local_exporter.export(trace)
            return
        if not self.enabled:
            return
        encoded = json.dumps(_safe(trace), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_TRACE_BYTES:
            raise ValueError("trace exceeds the configured observability event size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = (encoded + "\n").encode("utf-8")
        with _TRACE_LOCK:
            descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(descriptor, line)
                if written != len(line):
                    raise OSError("incomplete observability append")
            finally:
                os.close(descriptor)

    def export_event(self, trace: Any, event_type: str, span: Any = None) -> None:
        self._local_exporter.export_event(trace, event_type, span)


@dataclass(frozen=True)
class RegressionLink:
    """Immutable correlation between one engineering trace and one regression run."""

    trace_id: str
    regression_id: str
    dataset: str
    dataset_version: str
    dataset_digest: str
    passed: bool
    scores: Mapping[str, float]
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def correlate_trace_with_regression(
    trace: Any,
    regression_id: str,
    regression_result: Any,
) -> RegressionLink:
    """Attach a regression result to a trace and return a durable correlation record.

    The correlation is deliberately ID-based. The regression dataset digest is retained
    so a trace cannot later be interpreted against a different regression corpus.
    """
    trace_id = str(getattr(trace, "trace_id", "")).strip()
    if not trace_id:
        raise ValueError("trace must have a trace_id")
    regression_id = regression_id.strip()
    if not regression_id:
        raise ValueError("regression_id is required")

    link = RegressionLink(
        trace_id=trace_id,
        regression_id=regression_id,
        dataset=str(getattr(regression_result, "dataset", "")),
        dataset_version=str(getattr(regression_result, "dataset_version", "")),
        dataset_digest=str(getattr(regression_result, "dataset_digest", "")),
        passed=bool(getattr(regression_result, "passed", False)),
        scores={str(k): float(v) for k, v in dict(getattr(regression_result, "scores", {})).items()},
        failures=tuple(str(x) for x in getattr(regression_result, "failures", ())),
    )
    metadata = getattr(trace, "metadata", None)
    if isinstance(metadata, dict):
        metadata.setdefault("regressions", []).append(link.as_dict())
    return link


def apply_regression_score(trace: Any, link: RegressionLink) -> None:
    """Reflect regression outcome on the trace without changing promotion policy."""
    score = 1.0 if link.passed else 0.0
    trace.score(
        "regression_pass",
        score,
        f"regression={link.regression_id}; dataset={link.dataset}:{link.dataset_version}",
        "regression",
    )
