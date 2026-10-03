# AUREN v13: Trace Export and Regression Correlation

AUREN v13 connects an engineering execution trace to the exact regression evaluation used to validate it.

## Exporter contract

`portable.agency_trace_export.TraceExporter` is the provider-neutral interface:

```python
class TraceExporter(Protocol):
    def export(self, trace) -> None: ...
```

`CompositeTraceExporter` can send the same trace to multiple destinations. `JsonlTraceExporter` is a small reference implementation for local or test use. Existing `LocalExporter` remains compatible because the contract is structural.

Exporters are observational. An exporter failure must not turn a successful coding task into a failed task or bypass AUREN policy gates.

## Trace-to-regression correlation

A regression result is correlated using:

- `trace_id`: the immutable execution trace identifier
- `regression_id`: caller-owned regression run identifier
- dataset name and version
- dataset content digest
- pass/fail result
- metric scores
- failing regression items

The dataset digest is important: it prevents a later reader from treating the trace as evidence for a different regression corpus.

```python
from portable.agency_runtime import execute

result = execute(
    task,
    worker,
    tracer=tracer,
    regression_id="reg-2026-09-14-001",
    regression_result=regression_result,
)

print(result.trace_id)
print(result.regression_link.dataset_digest)
print(result.regression_link.passed)
```

The resulting trace contains a `regression` span and a `regression_pass` score. The `ExecutionResult` also exposes the same `RegressionLink` for downstream ledgers and reporting.

## Control-loop semantics

```text
execution trace
      |
      +--> quality receipt
      |
      +--> regression run
              |
              +--> dataset digest
              +--> metrics
              +--> failures
      |
      +--> correlated trace
      |
      +--> policy / promotion decision
```

Correlation does not override hard gates. A passing regression is evidence; it is not permission to bypass security, verification, or promotion controls.

## Extending exporters

A future Opik, OpenTelemetry, database, or enterprise exporter only needs to implement `export(trace)`. The AUREN runtime does not need to know where telemetry is stored.

For example:

```python
class MyExporter:
    def export(self, trace):
        send_to_backend(trace.as_dict())

tracer = Tracer(CompositeTraceExporter((LocalExporter(), MyExporter())))
```

Keep external exporters optional. The portable runtime must remain usable offline.
