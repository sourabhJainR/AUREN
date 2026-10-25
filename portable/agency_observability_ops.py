"""Reviewed trace-to-dataset workflows, evaluator calibration, drift alerts and safe summaries."""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
import hashlib
import hmac
from dataclasses import asdict, dataclass
import json
import math
import sqlite3
import time
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from .agency_observability import Dataset, DatasetItem, _safe
from .identity_compat import canonical_runtime_home


@dataclass(frozen=True)
class ReviewedTraceExample:
    reviewer_id: str
    evidence_ref: str
    minimized_input: Any
    expected_behavior: Any
    approved: bool
    surprising: bool = False
    sealed_holdout: bool = False


def reviewed_trace_to_dataset_item(trace: Any, review: ReviewedTraceExample) -> DatasetItem:
    """Create a regression example only from an explicitly reviewed, non-holdout trace."""
    if not review.approved:
        raise ValueError("trace-to-dataset conversion requires explicit review approval")
    if review.sealed_holdout:
        raise ValueError("sealed holdout traces cannot be exposed to worker-accessible datasets")
    if not review.reviewer_id.strip() or not review.evidence_ref.strip():
        raise ValueError("reviewer identity and review evidence reference are required")
    if not str(getattr(trace, "trace_id", "")).strip() or getattr(trace, "end", None) is None:
        raise ValueError("only completed traces can be reviewed into a dataset")
    if getattr(trace, "status", "unknown") == "ok" and not review.surprising:
        raise ValueError("a successful trace requires an explicit surprising-behavior review")
    if review.minimized_input is None or review.expected_behavior is None:
        raise ValueError("a minimized input and expected behavior are required")
    event = trace.as_dict()
    return DatasetItem(
        input=_safe(review.minimized_input),
        expected=_safe(review.expected_behavior),
        metadata={
            "source_trace_id": trace.trace_id,
            "source_event_digest": event.get("event_digest", ""),
            "reviewer_id": review.reviewer_id,
            "review_evidence_ref": review.evidence_ref,
            "reviewed": True,
            "sealed_holdout": False,
        },
    )


def _append_reviewed_idempotently(
    existing_items: tuple[DatasetItem, ...], item: DatasetItem,
) -> tuple[DatasetItem, ...]:
    """Append a reviewed example once, rejecting conflicting reuse of its review identity."""
    metadata = item.metadata
    identity = (
        metadata.get("source_trace_id"),
        metadata.get("source_event_digest"),
        metadata.get("reviewer_id"),
        metadata.get("review_evidence_ref"),
    )
    if not all(isinstance(part, str) and part for part in identity):
        raise ValueError("reviewed trace item is missing stable ingestion identity")
    for existing in existing_items:
        prior = existing.metadata
        prior_identity = (
            prior.get("source_trace_id"),
            prior.get("source_event_digest"),
            prior.get("reviewer_id"),
            prior.get("review_evidence_ref"),
        )
        if prior_identity != identity:
            continue
        if existing.input != item.input or existing.expected != item.expected or dict(existing.metadata) != dict(item.metadata):
            raise ValueError("reviewed trace ingestion identity conflicts with existing example")
        return existing_items
    return existing_items + (item,)


class DatasetRegistry:
    """In-memory immutable name/version registry; durable deployments must persist it."""

    def __init__(self) -> None:
        self._versions: dict[str, dict[str, Dataset]] = {}

    def register(self, dataset: Dataset) -> Dataset:
        if not dataset.name.strip() or not dataset.version.strip():
            raise ValueError("dataset name and version are required")
        versions = self._versions.setdefault(dataset.name, {})
        existing = versions.get(dataset.version)
        if existing is not None and existing.digest != dataset.digest:
            raise ValueError(f"dataset version is immutable: {dataset.name}:{dataset.version}")
        versions[dataset.version] = existing or dataset
        return versions[dataset.version]

    def get(self, name: str, version: str) -> Dataset:
        try:
            return self._versions[name][version]
        except KeyError as exc:
            raise KeyError(f"dataset version not found: {name}:{version}") from exc

    def append_reviewed(
        self,
        *,
        name: str,
        version: str,
        base_version: str | None,
        trace: Any,
        review: ReviewedTraceExample,
    ) -> Dataset:
        """Create a new immutable version; never mutate an existing dataset revision."""
        existing_items: tuple[DatasetItem, ...] = ()
        if base_version is not None:
            existing_items = self.get(name, base_version).items
        new_item = reviewed_trace_to_dataset_item(trace, review)
        return self.register(Dataset(name, version, _append_reviewed_idempotently(existing_items, new_item)))


class SQLiteDatasetRegistry:
    """Durable immutable dataset revisions in AUREN's local runtime state."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or canonical_runtime_home() / "observability" / "datasets.sqlite3").expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS observability_dataset_revisions (
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (name, version)
                )"""
            )

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def register(self, dataset: Dataset) -> Dataset:
        if not dataset.name.strip() or not dataset.version.strip():
            raise ValueError("dataset name and version are required")
        items_json = json.dumps(
            _safe([{"input": item.input, "expected": item.expected, "metadata": item.metadata} for item in dataset.items]),
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        digest = dataset.digest
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT digest FROM observability_dataset_revisions WHERE name=? AND version=?",
                (dataset.name, dataset.version),
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise ValueError(f"dataset version is immutable: {dataset.name}:{dataset.version}")
                return dataset
            connection.execute(
                "INSERT INTO observability_dataset_revisions(name, version, digest, items_json, created_at) VALUES(?,?,?,?,?)",
                (dataset.name, dataset.version, digest, items_json, time.time()),
            )
        return dataset

    def get(self, name: str, version: str) -> Dataset:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT digest, items_json FROM observability_dataset_revisions WHERE name=? AND version=?",
                (name, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"dataset version not found: {name}:{version}")
        try:
            records = json.loads(row[1])
            if not isinstance(records, list):
                raise ValueError("dataset items must be a list")
            items = tuple(
                DatasetItem(input=item["input"], expected=item.get("expected"), metadata=item.get("metadata", {}))
                for item in records if isinstance(item, dict) and "input" in item
            )
            dataset = Dataset(name, version, items)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            raise ValueError("persisted dataset revision is corrupt") from exc
        if dataset.digest != row[0]:
            raise ValueError("persisted dataset digest mismatch")
        return dataset

    def append_reviewed(
        self,
        *,
        name: str,
        version: str,
        base_version: str | None,
        trace: Any,
        review: ReviewedTraceExample,
    ) -> Dataset:
        existing_items = self.get(name, base_version).items if base_version is not None else ()
        item = reviewed_trace_to_dataset_item(trace, review)
        return self.register(Dataset(name, version, _append_reviewed_idempotently(existing_items, item)))


@dataclass(frozen=True)
class CalibrationSample:
    score: float
    expected_pass: bool
    evidence_ref: str


@dataclass(frozen=True)
class CalibrationResult:
    status: str
    threshold: float | None
    training_count: int
    validation_count: int
    training_false_pass_rate: float | None
    validation_false_pass_rate: float | None
    validation_pass_recall: float | None
    reason: str
    max_false_pass_rate: float = 0.02
    min_pass_recall: float = 0.70
    min_training_required: int = 30
    min_validation_required: int = 20
    training_digest: str = ""
    validation_digest: str = ""
    training_evidence_refs: tuple[str, ...] = ()
    validation_evidence_refs: tuple[str, ...] = ()

    @property
    def eligible_for_guardrail(self) -> bool:
        # Calibration is necessary but not sufficient; a separate approval is required.
        return False


@dataclass(frozen=True)
class GuardrailAuthorization:
    metric: str
    threshold: float
    reviewer_id: str
    evidence_ref: str
    calibration_status: str


def _calibration_digest(samples: tuple[CalibrationSample, ...]) -> str:
    payload = json.dumps(_safe([asdict(sample) for sample in samples]), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _rates(samples: tuple[CalibrationSample, ...], threshold: float) -> tuple[float, float]:
    negatives = [sample for sample in samples if not sample.expected_pass]
    positives = [sample for sample in samples if sample.expected_pass]
    false_pass_rate = sum(sample.score >= threshold for sample in negatives) / len(negatives) if negatives else 1.0
    pass_recall = sum(sample.score >= threshold for sample in positives) / len(positives) if positives else 0.0
    return false_pass_rate, pass_recall


def calibrate_evaluator(
    training: Iterable[CalibrationSample],
    validation: Iterable[CalibrationSample],
    *,
    min_training: int = 30,
    min_validation: int = 20,
    max_false_pass_rate: float = 0.02,
    min_pass_recall: float = 0.70,
) -> CalibrationResult:
    """Calibrate on labeled training cases, then independently validate the chosen threshold."""
    train = tuple(training)
    valid = tuple(validation)
    if not 0 <= max_false_pass_rate < 1 or not 0 < min_pass_recall <= 1:
        raise ValueError("invalid calibration targets")
    for collection in (train, valid):
        for sample in collection:
            if type(sample.expected_pass) is not bool:
                raise ValueError("calibration labels must be booleans")
            if not isinstance(sample.evidence_ref, str) or not sample.evidence_ref.strip():
                raise ValueError("calibration samples require evidence references")
            if not math.isfinite(sample.score) or not 0 <= sample.score <= 1:
                raise ValueError("calibration samples require finite scores in [0, 1]")
        references = [sample.evidence_ref for sample in collection]
        if len(references) != len(set(references)):
            raise ValueError("duplicate evidence references cannot inflate calibration sample counts")
    if {sample.evidence_ref for sample in train} & {sample.evidence_ref for sample in valid}:
        raise ValueError("training and validation evidence must be disjoint")
    if len(train) < min_training or len(valid) < min_validation:
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "insufficient labeled samples")
    if any(len({sample.expected_pass for sample in collection}) < 2 for collection in (train, valid)):
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "both classes are required in training and validation")

    candidates = sorted({0.0, 1.0, *(sample.score for sample in train)})
    chosen = None
    train_false = None
    for threshold in candidates:
        false_pass, recall = _rates(train, threshold)
        if false_pass <= max_false_pass_rate and recall >= min_pass_recall:
            chosen, train_false = threshold, false_pass
            break
    if chosen is None:
        return CalibrationResult("unscored", None, len(train), len(valid), None, None, None, "no training threshold satisfies safety and recall targets")
    valid_false, valid_recall = _rates(valid, chosen)
    if valid_false > max_false_pass_rate or valid_recall < min_pass_recall:
        return CalibrationResult("unscored", None, len(train), len(valid), train_false, valid_false, valid_recall, "independent validation did not meet calibration targets")
    return CalibrationResult(
        "calibrated", chosen, len(train), len(valid), train_false, valid_false, valid_recall,
        "validated; separate guardrail approval still required",
        max_false_pass_rate=max_false_pass_rate,
        min_pass_recall=min_pass_recall,
        min_training_required=min_training,
        min_validation_required=min_validation,
        training_digest=_calibration_digest(train),
        validation_digest=_calibration_digest(valid),
        training_evidence_refs=tuple(_safe(sample.evidence_ref) for sample in train),
        validation_evidence_refs=tuple(_safe(sample.evidence_ref) for sample in valid),
    )


def authorize_calibrated_guardrail(
    metric: str,
    result: CalibrationResult,
    *,
    reviewer_id: str,
    evidence_ref: str,
) -> GuardrailAuthorization:
    if result.status != "calibrated" or result.threshold is None:
        raise ValueError("only independently validated calibration can be considered for guardrail approval")
    if (
        result.training_count < result.min_training_required
        or result.validation_count < result.min_validation_required
        or result.training_false_pass_rate is None
        or result.validation_false_pass_rate is None
        or result.validation_pass_recall is None
        or not result.training_digest
        or not result.validation_digest
        or len(result.training_digest) != 64
        or len(result.validation_digest) != 64
        or any(char not in "0123456789abcdef" for char in result.training_digest.lower() + result.validation_digest.lower())
        or not result.training_evidence_refs
        or not result.validation_evidence_refs
        or len(result.training_evidence_refs) != result.training_count
        or len(result.validation_evidence_refs) != result.validation_count
        or len(set(result.training_evidence_refs)) != len(result.training_evidence_refs)
        or len(set(result.validation_evidence_refs)) != len(result.validation_evidence_refs)
        or set(result.training_evidence_refs) & set(result.validation_evidence_refs)
        or not math.isfinite(result.threshold)
        or not 0 <= result.threshold <= 1
        or result.training_false_pass_rate > result.max_false_pass_rate
        or result.validation_false_pass_rate > result.max_false_pass_rate
        or result.validation_pass_recall < result.min_pass_recall
    ):
        raise ValueError("calibration evidence does not meet its recorded acceptance thresholds")
    if not metric.strip() or not reviewer_id.strip() or not evidence_ref.strip():
        raise ValueError("metric, reviewer identity and approval evidence are required")
    return GuardrailAuthorization(metric, result.threshold, reviewer_id, evidence_ref, result.status)


@dataclass(frozen=True)
class MetricObservation:
    metric: str
    domain: str
    value: float
    run_id: str
    evidence_ref: str
    timestamp: float
    risk_tier: str = "normal"


@dataclass(frozen=True)
class DriftPolicy:
    baseline_count: int = 20
    recent_count: int = 10
    minimum_absolute_shift: float = 0.10
    safety_floor: float | None = None

    def __post_init__(self) -> None:
        if self.baseline_count < 2 or self.recent_count < 2:
            raise ValueError("drift windows must contain at least two observations")
        if not math.isfinite(self.minimum_absolute_shift) or self.minimum_absolute_shift < 0:
            raise ValueError("minimum_absolute_shift must be finite and non-negative")
        if self.safety_floor is not None and (not math.isfinite(self.safety_floor) or not 0 <= self.safety_floor <= 1):
            raise ValueError("safety_floor must be finite and between 0 and 1")


def evaluate_drift(observations: Iterable[MetricObservation], policy: DriftPolicy = DriftPolicy()) -> dict[str, Any]:
    points = tuple(sorted(observations, key=lambda point: point.timestamp))
    if any(not point.metric.strip() or not point.domain.strip() or not point.run_id.strip()
           or not point.evidence_ref.strip() or not math.isfinite(point.value) for point in points):
        raise ValueError("drift observations require provenance and finite values")
    needed = policy.baseline_count + policy.recent_count
    if len(points) < needed:
        return {"status": "insufficient_data", "count": len(points), "required": needed, "alert_reasons": []}
    selected = points[-needed:]
    baseline = selected[:policy.baseline_count]
    recent = selected[policy.baseline_count:]
    if len({point.metric for point in selected}) != 1 or len({point.domain for point in selected}) != 1:
        raise ValueError("a drift window must contain one metric and one domain")
    baseline_mean, recent_mean = mean(point.value for point in baseline), mean(point.value for point in recent)
    shift = recent_mean - baseline_mean
    reasons = []
    if abs(shift) >= policy.minimum_absolute_shift:
        reasons.append("distribution_shift")
    if policy.safety_floor is not None and recent_mean < policy.safety_floor:
        reasons.append("safety_floor_breached")
    return {
        "status": "alert" if reasons else "ok",
        "metric": recent[0].metric,
        "domain": recent[0].domain,
        "baseline_count": len(baseline),
        "recent_count": len(recent),
        "baseline_mean": round(baseline_mean, 6),
        "recent_mean": round(recent_mean, 6),
        "absolute_shift": round(abs(shift), 6),
        "alert_reasons": reasons,
        "run_ids": [point.run_id for point in recent],
        "high_risk_observations": sum(point.risk_tier.lower() in {"high", "critical"} for point in selected),
    }


def read_drift_alerts(path: Path, *, limit: int = 50) -> dict[str, Any]:
    """Read alert evidence without creating or mutating the SQLite store."""
    if limit < 1 or limit > 500:
        raise ValueError("drift alert limit must be between 1 and 500")
    if not path.exists():
        return {"status": "unavailable", "alerts": []}
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluator_drift_alerts'"
            ).fetchone()
            if table is None:
                return {"status": "unavailable", "alerts": []}
            rows = connection.execute(
                "SELECT payload_json FROM evaluator_drift_alerts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        alerts = []
        for (payload,) in rows:
            item = json.loads(payload)
            if isinstance(item, dict):
                alerts.append({
                    "metric": str(item.get("metric", ""))[:120],
                    "domain": str(item.get("domain", ""))[:120],
                    "alert_reasons": [reason[:120] for reason in item.get("alert_reasons", []) if isinstance(reason, str)][:20],
                    "baseline_mean": item.get("baseline_mean"),
                    "recent_mean": item.get("recent_mean"),
                    "absolute_shift": item.get("absolute_shift"),
                    "run_ids": [run_id[:120] for run_id in item.get("run_ids", []) if isinstance(run_id, str)][:50],
                })
        return {"status": "ok", "alerts": alerts}
    except (sqlite3.Error, json.JSONDecodeError, OSError, ValueError):
        return {"status": "corrupt_or_unavailable", "alerts": []}


def observability_snapshot(path: Path, *, limit: int = 500) -> dict[str, Any]:
    """Return a safe aggregate view; never return raw prompts, span inputs or outputs."""
    if limit < 1 or limit > 5000:
        raise ValueError("limit must be between 1 and 5000")
    if not path.exists():
        drift = read_drift_alerts(path.parent / "datasets.sqlite3", limit=50)
        return {
            "status": "unavailable",
            "reason": "trace_store_missing",
            "trace_count": 0,
            "drift_alert_status": drift["status"],
            "drift_alerts": drift["alerts"],
            "live_drift_alerts": [],
            "score_trends": [],
            "raw_payloads_exposed": False,
        }
    lines: deque[str] = deque(maxlen=limit)
    corrupt_lines = 0
    max_scan_bytes = 10_000_000
    with path.open("rb") as handle:
        size = path.stat().st_size
        scan_truncated = size > max_scan_bytes
        if scan_truncated:
            handle.seek(size - max_scan_bytes)
            handle.readline()  # discard a possibly partial first line
        for raw_line in handle:
            if raw_line.strip():
                try:
                    lines.append(raw_line.decode("utf-8"))
                except UnicodeDecodeError:
                    corrupt_lines += 1
    latest_by_trace: dict[str, dict[str, Any]] = {}
    span_maps: dict[str, dict[str, dict[str, Any]]] = {}
    last_sequences: dict[str, int] = {}
    sequence_gaps = 0
    span_parentage_errors = 0
    for line in lines:
        try:
            item = json.loads(line)
            if not isinstance(item, dict) or not item.get("trace_id"):
                corrupt_lines += 1
                continue
            trace_id = str(item["trace_id"])
            if item.get("event_record_version") == 1:
                supplied_digest = item.get("event_digest")
                unsigned_record = dict(item)
                unsigned_record.pop("event_digest", None)
                canonical_record = json.dumps(_safe(unsigned_record), sort_keys=True, separators=(",", ":"), allow_nan=False)
                expected_digest = hashlib.sha256(canonical_record.encode("utf-8")).hexdigest()
                if (
                    not isinstance(supplied_digest, str)
                    or len(supplied_digest) != 64
                    or not hmac.compare_digest(supplied_digest, expected_digest)
                ):
                    corrupt_lines += 1
                    continue
                sequence = item.get("event_sequence")
                if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
                    corrupt_lines += 1
                    continue
                previous_sequence = last_sequences.get(trace_id, 0)
                if sequence <= previous_sequence:
                    corrupt_lines += 1
                    continue
                if sequence > previous_sequence + 1:
                    sequence_gaps += sequence - previous_sequence - 1
                last_sequences[trace_id] = sequence
                state = latest_by_trace.get(trace_id)
                if state is None:
                    state = {
                        "schema_version": item.get("schema_version", 1),
                        "trace_id": trace_id,
                        "spans": [],
                        "scores": [],
                        "metadata": {},
                    }
                if trace_id not in span_maps:
                    existing_spans = state.get("spans", ())
                    span_maps[trace_id] = {
                        str(span["span_id"]): span for span in existing_spans
                        if isinstance(span, dict) and span.get("span_id")
                    } if isinstance(existing_spans, (list, tuple)) else {}
                for field_name in (
                    "run_id", "session_id", "task_id", "name", "start", "end",
                    "status", "source_revision", "config_digest",
                ):
                    if field_name in item:
                        state[field_name] = item[field_name]
                if isinstance(item.get("metadata"), dict):
                    state["metadata"] = item["metadata"]
                if isinstance(item.get("scores"), list):
                    state["scores"] = item["scores"]
                span = item.get("span")
                if isinstance(span, dict) and span.get("span_id"):
                    span_id = str(span["span_id"])
                    event_type = item.get("event_type")
                    if event_type == "span_started" and span_id in span_maps[trace_id]:
                        span_parentage_errors += 1
                    elif event_type == "span_updated" and span_id not in span_maps[trace_id]:
                        span_parentage_errors += 1
                    span_maps[trace_id][span_id] = span
                state["spans"] = list(span_maps[trace_id].values())
                state["event_sequence"] = item.get("event_sequence", state.get("event_sequence", 0))
                state["event_digest"] = item.get("event_digest", state.get("event_digest"))
                state["duration_ms"] = (
                    round((state["end"] - state["start"]) * 1000, 2)
                    if isinstance(state.get("end"), (int, float))
                    and isinstance(state.get("start"), (int, float)) else None
                )
                latest_by_trace[trace_id] = state
            else:
                latest_by_trace[trace_id] = item
                snapshot_sequence = item.get("event_sequence", 0)
                if isinstance(snapshot_sequence, int) and not isinstance(snapshot_sequence, bool) and snapshot_sequence > last_sequences.get(trace_id, 0):
                    last_sequences[trace_id] = snapshot_sequence
                span_records = item.get("spans", ())
                span_maps[trace_id] = {
                    str(span["span_id"]): span for span in span_records
                    if isinstance(span, dict) and span.get("span_id")
                } if isinstance(span_records, (list, tuple)) else {}
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError):
            corrupt_lines += 1
    traces = list(latest_by_trace.values())
    # Validate explicit span parentage after replay. The append-only event stream
    # may end mid-run, but it must never silently accept orphaned, self-parented,
    # duplicate, or cyclic span identities.
    for trace in traces:
        raw_spans = trace.get("spans", ())
        if not isinstance(raw_spans, (list, tuple)):
            span_parentage_errors += 1
            continue
        spans_by_id: dict[str, dict[str, Any]] = {}
        for span in raw_spans:
            if not isinstance(span, dict) or not isinstance(span.get("span_id"), str) or not span["span_id"]:
                span_parentage_errors += 1
                continue
            span_id = span["span_id"]
            if span_id in spans_by_id:
                span_parentage_errors += 1
                continue
            spans_by_id[span_id] = span
        for span_id, span in spans_by_id.items():
            parent_id = span.get("parent_id")
            if parent_id is None:
                continue
            if not isinstance(parent_id, str) or parent_id == span_id or parent_id not in spans_by_id:
                span_parentage_errors += 1
        # Detect cycles without relying on span append order.
        for span_id in spans_by_id:
            visited: set[str] = set()
            current = span_id
            while current in spans_by_id:
                if current in visited:
                    span_parentage_errors += 1
                    break
                visited.add(current)
                parent_id = spans_by_id[current].get("parent_id")
                if not isinstance(parent_id, str):
                    break
                current = parent_id
    phase_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    recent = []
    durations = []
    score_observations: dict[tuple[str, str], list[MetricObservation]] = {}
    now = time.time()
    for trace in traces:
        recorded_status = str(trace.get("status", "unknown"))
        status = recorded_status
        started = trace.get("start")
        if recorded_status == "running" and isinstance(started, (int, float)) and math.isfinite(started) and now - started > 300:
            status = "stale"
        status_counts[status] += 1
        duration = trace.get("duration_ms")
        if isinstance(duration, (int, float)) and math.isfinite(duration):
            durations.append(float(duration))
        phases = []
        metadata = trace.get("metadata") if isinstance(trace.get("metadata"), dict) else {}
        domain_value = metadata.get("domain", "general")
        domain = domain_value[:120] if isinstance(domain_value, str) else "general"
        risk_value = metadata.get("risk", "normal")
        risk_tier = risk_value[:40] if isinstance(risk_value, str) else "normal"
        timestamp = trace.get("end") or trace.get("start") or now
        run_id_value = trace.get("run_id") or trace.get("trace_id")
        run_id = run_id_value[:160] if isinstance(run_id_value, str) else str(trace.get("trace_id", "unknown"))
        evidence_value = trace.get("event_digest") or trace.get("trace_id")
        evidence_ref = evidence_value[:160] if isinstance(evidence_value, str) else run_id
        trace_scores: dict[str, float] = {}
        span_records = trace.get("spans", ())
        if not isinstance(span_records, (list, tuple)):
            span_records = ()
            corrupt_lines += 1
        for span in span_records[:100]:
            if not isinstance(span, dict):
                continue
            phase = str(span.get("phase", "unknown"))[:80]
            phase_counts[phase] += 1
            phases.append({"phase": phase, "status": str(span.get("status", "unknown"))[:40]})
            provider = span.get("provider")
            if isinstance(provider, str) and provider:
                provider_counts[provider[:120]] += 1
            span_scores = span.get("scores", ())
            if isinstance(span_scores, (list, tuple)):
                for score in span_scores[:100]:
                    if isinstance(score, dict) and isinstance(score.get("name"), str):
                        value = score.get("value")
                        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
                            trace_scores[score["name"][:120]] = float(value)
        top_scores = trace.get("scores", ())
        if isinstance(top_scores, (list, tuple)):
            for score in top_scores[:100]:
                if isinstance(score, dict) and isinstance(score.get("name"), str):
                    value = score.get("value")
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1:
                        trace_scores[score["name"][:120]] = float(value)
        if isinstance(timestamp, (int, float)) and math.isfinite(timestamp):
            for metric, value in trace_scores.items():
                score_observations.setdefault((metric, domain), []).append(
                    MetricObservation(metric, domain, value, run_id, evidence_ref, float(timestamp), risk_tier)
                )
        open_span_count = sum(
            1 for span in span_records[:100]
            if isinstance(span, dict) and span.get("end") is None
        )
        recent.append({
            "trace_id": str(trace.get("trace_id", "")),
            "run_id": str(trace.get("run_id", "")),
            "task_id": str(trace.get("task_id", "")),
            "status": status,
            "recorded_status": recorded_status,
            "duration_ms": duration if isinstance(duration, (int, float)) and math.isfinite(duration) else None,
            "source_revision": trace.get("source_revision")[:128] if isinstance(trace.get("source_revision"), str) else None,
            "phases": phases,
            "open_span_count": open_span_count,
            "phase_coverage_gaps": [
                gap[:120] for gap in metadata.get("phase_coverage_gaps", [])
                if isinstance(gap, str)
            ][:50] if isinstance(metadata.get("phase_coverage_gaps", []), list) else [],
            "review_mode": metadata.get("review_mode", "unknown"),
            "export_status": metadata.get("observability_export_status", "no_known_export_failure"),
            "export_failure_count": metadata.get("observability_export_failures", 0),
        })
    score_trends = []
    live_drift_alerts = []
    for (metric, domain), points in sorted(score_observations.items()):
        points = sorted(points, key=lambda point: point.timestamp)
        floor = 1.0 if metric == "regression_pass" else 0.8 if metric in {
            "task_quality", "correctness", "security", "safety", "policy_compliance"
        } else None
        report = evaluate_drift(points, DriftPolicy(safety_floor=floor))
        recent_values = [point.value for point in points[-10:]]
        score_trends.append({
            "metric": metric,
            "domain": domain,
            "count": len(points),
            "latest": points[-1].value,
            "recent_mean": round(mean(recent_values), 6),
            "drift_status": report["status"],
            "alert_reasons": report.get("alert_reasons", []),
        })
        if report["status"] == "alert":
            live_drift_alerts.append({
                **report,
                "origin": "derived_from_traces",
            })
    drift = read_drift_alerts(path.parent / "datasets.sqlite3", limit=50)
    return {
        "status": ("partial" if scan_truncated or corrupt_lines or sequence_gaps or span_parentage_errors else "ok") if traces else ("corrupt" if corrupt_lines else "empty"),
        "trace_count": len(traces),
        "corrupt_line_count": corrupt_lines,
        "event_sequence_gap_count": sequence_gaps,
        "span_parentage_error_count": span_parentage_errors,
        "scan_truncated": scan_truncated,
        "status_counts": dict(status_counts),
        "phase_counts": dict(phase_counts),
        "provider_counts": dict(provider_counts),
        "mean_duration_ms": round(mean(durations), 3) if durations else None,
        "score_trends": score_trends[:100],
        "drift_alert_status": drift["status"],
        "drift_alerts": drift["alerts"],
        "live_drift_alerts": live_drift_alerts[:50],
        "recent_traces": recent[-100:],
        "raw_payloads_exposed": False,
    }



class SQLiteObservabilityEvidenceStore:
    """Append-only storage for calibration reports, guardrail approvals and drift alerts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or canonical_runtime_home() / "observability" / "datasets.sqlite3").expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS evaluator_calibration_reports (
                    report_id TEXT PRIMARY KEY,
                    metric TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS evaluator_guardrail_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL REFERENCES evaluator_calibration_reports(report_id),
                    metric TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS evaluator_drift_alerts (
                    alert_id TEXT PRIMARY KEY,
                    metric TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_calibration(self, metric: str, result: CalibrationResult) -> str:
        if not metric.strip():
            raise ValueError("calibration metric is required")
        payload = json.dumps(asdict(result), sort_keys=True, separators=(",", ":"), allow_nan=False)
        report_id = hashlib.sha256(f"{metric}|{payload}".encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO evaluator_calibration_reports(report_id, metric, status, report_json, created_at) VALUES(?,?,?,?,?)",
                (report_id, metric, result.status, payload, time.time()),
            )
            existing = connection.execute(
                "SELECT report_json FROM evaluator_calibration_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if existing is None or existing[0] != payload:
                raise ValueError("calibration report persistence conflict")
        return report_id

    def authorize_guardrail(
        self,
        metric: str,
        result: CalibrationResult,
        *,
        reviewer_id: str,
        evidence_ref: str,
    ) -> GuardrailAuthorization:
        authorization = authorize_calibrated_guardrail(
            metric, result, reviewer_id=reviewer_id, evidence_ref=evidence_ref,
        )
        report_id = self.record_calibration(metric, result)
        payload = {
            "report_id": report_id,
            "metric": authorization.metric,
            "threshold": authorization.threshold,
            "reviewer_id": authorization.reviewer_id,
            "evidence_ref": authorization.evidence_ref,
            "calibration_status": authorization.calibration_status,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        authorization_id = __import__("hashlib").sha256(encoded.encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluator_guardrail_authorizations
                   (authorization_id, report_id, metric, threshold, reviewer_id, evidence_ref, payload_json, created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (authorization_id, report_id, metric, authorization.threshold, reviewer_id, evidence_ref, encoded, time.time()),
            )
            existing = connection.execute(
                "SELECT payload_json FROM evaluator_guardrail_authorizations WHERE authorization_id=?",
                (authorization_id,),
            ).fetchone()
            if existing is None or existing[0] != encoded:
                raise ValueError("guardrail authorization persistence conflict")
        return authorization

    def record_drift_alert(self, report: Mapping[str, Any]) -> str:
        if report.get("status") != "alert":
            raise ValueError("only evaluated drift alerts can be persisted as alerts")
        metric, domain = report.get("metric"), report.get("domain")
        if not isinstance(metric, str) or not metric.strip() or not isinstance(domain, str) or not domain.strip():
            raise ValueError("drift alert requires metric and domain")
        encoded = json.dumps(_safe(dict(report)), sort_keys=True, separators=(",", ":"), allow_nan=False)
        alert_id = __import__("hashlib").sha256(encoded.encode("utf-8")).hexdigest()
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO evaluator_drift_alerts(alert_id, metric, domain, payload_json, created_at) VALUES(?,?,?,?,?)",
                (alert_id, metric, domain, encoded, time.time()),
            )
            existing = connection.execute(
                "SELECT payload_json FROM evaluator_drift_alerts WHERE alert_id=?",
                (alert_id,),
            ).fetchone()
            if existing is None or existing[0] != encoded:
                raise ValueError("drift alert persistence conflict")
        return alert_id

    def list_drift_alerts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("alert limit must be between 1 and 1000")
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM evaluator_drift_alerts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        results = []
        for (payload,) in rows:
            try:
                item = json.loads(payload)
                if isinstance(item, dict):
                    results.append(item)
            except json.JSONDecodeError:
                raise ValueError("persisted drift alert is corrupt")
        return results
