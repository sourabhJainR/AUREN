# Amdahl-aware delivery acceleration

AUREN should optimize the serial fraction of engineering delivery, not maximize the
number of agents. Adding workers cannot shorten unresolved decisions, shared resource
bottlenecks, integration queues, sequential verification or release gates.

## Operating model: critical-path-first engineering

1. Define the outcome contract once. Requirements, decisions, interfaces, acceptance
   criteria, source revision, risk, rollback and budget are pinned to the canonical
   EngineeringPlan digest.
2. Expose the dependency graph. Break work into small, independently verifiable units.
   Identify hard dependencies, shared files, shared environments, scarce services and
   human approval gates before dispatch.
3. Parallelize only independent work. Use isolated worktrees and typed contracts.
   Resource locks serialize work that shares a database, migration, API/schema,
   deployment target or conflicting file set.
4. Move verification left. Each work unit returns its artifact and evidence contract.
   Run targeted tests as soon as the unit is ready; integrate continuously against
   contract tests. Keep a fresh-context reviewer independent of implementers.
5. Keep one integration owner. Compose only compatible artifacts. Detect drift,
   contract mismatch and overlapping edits before adoption. Serialize integration and
   merge operations where they are inherently shared.
6. Continue from evidence. The canonical supervisor reconciles completed units,
   selects the next eligible work, polls CI, repairs only evidenced gaps, and resumes
   from durable checkpoints. Unknown side effects are reconciled, never blindly retried.

## Amdahl and the delivery budget

For a measured serial fraction s and N workers, the theoretical speedup ceiling is
S(N) = 1 / (s + (1 - s) / N).

The serial fraction must be explicitly classified from the work contract; it is not
inferred from the critical path. The planner separately reports the DAG critical-path
bound, because dependency graphs are more general than Amdahl's single serial/parallel
split. portable.delivery_acceleration provides a deterministic planning estimate from
explicit task-duration estimates, serial task IDs, dependencies, resource locks, worker
capacity and coordination overhead. It reports both bounds, the feasible schedule and
estimated speedup. It does not launch
workers or own task state. The canonical ExecutionController remains authoritative.

## New delivery principles

- Critical path over headcount: prioritize tasks that unblock the most downstream
  work and reduce the longest dependency chain.
- Contract-first parallelism: parallelize behind stable API, schema and artifact
  contracts; otherwise parallel work creates integration debt.
- Verification as a stream: test and review completed slices while other slices
  continue, rather than batching all validation at the end.
- One owner per mutable boundary: serialize changes to shared state, migrations,
  release branches and production environments.
- Fresh-context quality gate: implementers do not self-approve; QA/security get
  pinned artifacts and acceptance criteria, not implementer conclusions.
- Bounded coordination: parallelize only while expected saved critical-path time
  exceeds context, handoff, merge and review cost.
- Evidence-driven learning: compare measured end-to-end lead time, rework, escaped
  defects, conflict rate, recovery success, cost and quality against a sequential
  baseline before changing defaults.

## AUREN ownership boundaries

This module is a read-only scheduling projection over EngineeringPlan. It is not a
second planner, scheduler, queue, task ledger, budget owner, policy engine or runtime.
It cannot grant permissions, override dependencies, approve its own output, or mark
tasks complete. Runtime admission, workspace isolation, cancellation, deadlines,
shared budgets, verification, release and rollback remain under existing AUREN owners.

## Rollout and acceptance

Start in advisory mode. Compare predicted and actual durations on repeatable tasks.
Promote parallelism only when paired runs show better end-to-end lead time without
worse correctness, security, recovery or rework. Keep the sequential path available
as rollback. Production isolation and independent evaluation require external evidence.
