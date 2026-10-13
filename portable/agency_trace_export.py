"""Provider-neutral trace export and trace-to-regression correlation for AUREN v13."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


class TraceExporter(Protocol):
    """Minimal exporter contract so telemetry backends remain replaceable."""

    def export(self, trace: Any) -> None:
        ...


class CompositeTraceExporter:
    """Fan a completed trace out to zero or more exporters."""

    def __init__(self, exporters: Sequence[TraceExporter] = ()) -> None:
        self.exporters = tuple(exporters)

    def export(self, trace: Any) -> None:
        errors: list[Exception] = []
        for exporter in self.exporters:
            try:
                exporter.export(trace)
            except Exception as exc:  # exporters must not corrupt task execution
                errors.append(exc)
        if errors:
            # Observability is best-effort; callers can inspect exporter-specific
            # health without turning a successful engineering task into a failure.
            return


class JsonlTraceExporter:
    """Generic JSONL exporter for any trace implementing ``as_dict``."""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path).expanduser()
        self.enabled = enabled

    def export(self, trace: Any) -> None:
        if not self.enabled:
            return
        payload = trace.as_dict() if hasattr(trace, "as_dict") else trace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


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
