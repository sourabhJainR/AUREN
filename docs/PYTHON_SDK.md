# AUREN Python SDK v1

The SDK is a thin Python facade over the canonical AUREN runtime. It does not create a second execution scheduler, progress journal, memory store, or policy authority.

## Scope

- Durable task registration, submission, execution, lookup and recovery.
- Read-only progress snapshots from the supervisor-owned journal.
- Optional dashboard projections.
- Access to the canonical persistent-memory facade.
- Explicit execution-risk classification at handler registration.

The SDK is included in the portable source tree and portable bundle. It does not yet promise a separately published PyPI wheel. A clean environment can use the portable bundle and its documented installer; do not copy modules out of a development checkout and treat that as a supported distribution.

## Minimal example

```python
from pathlib import Path
from portable.sdk import AurenClient
from my_project.auren_handlers import inspect_repository

client = AurenClient(Path.cwd())
client.register_handler(
    "inspect-repository",
    inspect_repository,
    execution_class="read_only",
)
task = client.submit(
    "inspect-repository",
    {"path": "."},
    goal="Inspect the repository",
)
print(client.progress(task.id))
client.close()
```

Submission persists a pending task. It does not mean that work has started or completed. The run_once and resume_pending methods delegate to the existing supervisor. Task success remains subject to the registered handler's execution class, verification gates and durable receipts.

## API contract

- API version: v1.
- Runtime state belongs under the workspace's .auren/ directory unless an explicit memory path is supplied.
- Handlers must be importable module-level callables or explicit module:qualname references.
- Registration must name one of read_only, context_provider, ci_gated, or consequential. The controller rejects unclassified work.
- The dashboard is observational. Do not use dashboard text as authority.
- Cancellation, deadline, recovery, side-effect reconciliation and promotion remain controller-owned contracts.
- Memory writes require explicit approved=True; the SDK does not bypass memory write approval.
- Closing the client does not delete durable state.

## Distribution status

The supported standalone artifact is the version-pinned portable bundle, with manifest digests, verification, update and rollback handled by portable/auren_runtime.py and auren_cli.py. Separate wheel publication, a formal external isolation audit, and independent sealed-corpus custody remain open release gates. Repository tests do not prove external isolation or independent evaluation.
