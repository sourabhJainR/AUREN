# AUREN local offload layer

AUREN already has the planner, graph runtime, capability routing, memory and verification boundaries. The local offload layer adds a small execution pool underneath those boundaries.

It is intended for work that does not need to occupy the coordinator's context:

- independent tests
- lint and static analysis
- repository scans
- repeatable evidence collection
- CPU-bound command-line work that can be split safely

## Architecture

    AUREN task / graph
            |
      capability route
            |
      LocalOffloadBroker
        /      |      \
     worker  worker  worker
        \      |      /
      bounded local commands
            |
      compact evidence
            |
        graph handoff

The broker does not become a second orchestrator. It only executes bounded jobs and returns structured results.

## Resource rules

The worker count defaults to local CPU capacity and is capped at the detected CPU count. Every job has a timeout and output budget. POSIX hosts can also enforce CPU and address-space limits.

run_many() is intentionally bounded: a caller cannot enqueue an unbounded local swarm.

The default program allow-list is:

python, python3, pytest, ruff, mypy, pyright, git

The broker does not invoke a shell. Secret-like environment variables are removed from worker processes.

## Isolation

Use isolate=True when a job may write temporary files or run code that should not share the main workspace. A temporary project copy is created for the job and removed after completion.

The isolated copy is not a security sandbox. It is workspace isolation. High-risk operations should still go through AUREN's existing policy and sandbox gates.

## CLI

Run a single bounded local job:

    python -m portable.local_offload --project-root . --workers 3 -- pytest -q

Inspect detected capacity through the Python API:

    from portable.local_offload import LocalOffloadBroker

    broker = LocalOffloadBroker(".")
    print(broker.capacity())

Run two independent checks in parallel:

    from portable.local_offload import LocalOffloadBroker, OffloadJob

    broker = LocalOffloadBroker(".")
    results = broker.run_many([
        OffloadJob("tests", ("pytest", "-q", "tests/test_state_graph_hardening.py")),
        OffloadJob("lint", ("ruff", "check", "portable")),
    ])

This keeps bulky command output outside the main agent context. Only the bounded result is handed back to the caller.

## Relationship to AUREN

This layer preserves AUREN's existing ownership:

- TaskPlan still owns dependency planning.
- StateGraph still owns graph execution.
- GraphAgentTeam still owns agent-team orchestration.
- CapabilityFabric still owns capability semantics and routing.
- verification and review remain authoritative.
- durable learning remains outside active execution.

The local broker is an execution resource, not a new source of truth.
