# OpenHands and Task Master adaptation

This document records the patterns adopted from OpenHands and Claude Task Master and the boundaries intentionally kept different in AUREN.

## Adopted from OpenHands

| Pattern | AUREN mapping | Why |
|---|---|---|
| Explicit ownership and repository boundaries | Repository-first rules plus provider-neutral AUREN runtime | Prevents agent logic, project code, and deployment state from being mixed. |
| Multiple agent backends/capabilities | `ProviderFabric` capability discovery | Native provider features are used when available without changing AUREN semantics. |
| Workspace safety | Machine-scoped `~/.auren` installation and repository isolation | Installing or switching AUREN does not copy runtime files into a project. |
| Clear verification commands and CI | Portable tests, bundle verification, plugin payload verification | A portable artifact must be usable, inspectable and reproducible. |
| Human review boundaries | Shared-path impact analysis and critical review gate | A common contract should not be changed as if it were an isolated file. |

## Adopted from Task Master

| Pattern | AUREN mapping | Why |
|---|---|---|
| Stable task identity | `portable.task_planner.Task.id` | Evidence and recovery can refer to the same task across sessions. |
| Explicit status and priority | `pending/in-progress/blocked/done/cancelled` plus high/medium/low | Makes task readiness deterministic. |
| Dependencies | `Task.dependencies` and graph validation | Prevents an agent from starting work whose prerequisites are incomplete. |
| Subtasks | `Task.subtasks` | Large requests can be decomposed into reviewable units. |
| Tags/workstreams | `Task.tags` | Supports separate workstreams without duplicating the task store. |
| Acceptance and files | `Task.acceptance` and `Task.files` | Connects planning to verification and impact analysis. |

## Shared-path change protocol

1. Build or update the task plan.
2. Identify the intended files.
3. Run `python -m portable.impact_analysis --root . <paths...>`.
4. Inspect inbound consumers and contract surfaces.
5. Split the work if the impact is too broad for one review.
6. Serialize writes to shared paths.
7. Run focused consumer tests and the broad regression suite.
8. Record the impact report in the work evidence.

The analyzer is deliberately conservative. It is a review aid, not a proof of correctness.

## Bidirectional artifact deployment

AUREN has two different operations by design:

- `update` follows the configured remote channel and is forward-only. This avoids an unattended update silently downgrading a runtime.
- `install <artifact>` is an explicit deployment switch and accepts a verified artifact of an older or newer version. Every build remains immutable under `~/.auren/versions/` and `current` is moved to the selected build.
- `rollback` selects a previous local build without downloading anything.

The result is a clear safety boundary: automatic update is conservative, while an explicit artifact deployment can move in either direction.

## Artifact compatibility

The bundle keeps the existing format version for compatibility with existing artifacts. New optional manifest data must remain additive. A loader must verify the artifact before activation, preserve the exact source commit and SHA-256, and never overwrite a different build that already occupies the same immutable build directory.

## Trade-offs

### Deterministic static impact analysis instead of a language-server dependency
AUREN uses lightweight AST/import and text-reference analysis so the portable runtime stays dependency-free. A full language server would improve precision for overloaded symbols, generated code and dynamic imports, but would make deployment heavier and provider-specific. The analyzer therefore produces review signals rather than pretending to provide a complete call graph.

### Task store instead of copying Task Master wholesale
AUREN only needs the durable planning primitives: IDs, dependencies, status, priority, tags, subtasks and acceptance. Importing the full Task Master workflow would add another orchestration authority and duplicate state. Keeping a small model lets AUREN remain provider-neutral.

### Explicit downgrade instead of making `update` bidirectional
A bidirectional unattended updater is risky. A human deliberately installing an older artifact is a different operation and can be audited. This preserves rollback speed without making normal update behavior surprising.

### Parallel read-only analysis, serialized mutation
Parallel exploration improves throughput, but concurrent edits to shared files create merge and reasoning races. AUREN therefore allows parallel evidence gathering and requires one owner for mutations on a shared surface.

## Non-goals

These changes do not:

- copy OpenHands or Task Master source code;
- make Claude Code the only supported provider;
- allow agents to bypass repository rules or human review;
- claim that static impact analysis finds every runtime dependency;
- make automatic updates downgrade installations.
