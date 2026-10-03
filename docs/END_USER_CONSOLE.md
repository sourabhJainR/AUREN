# AUREN Console

AUREN has a large internal control loop, but the easiest way to use it should be simple: inspect the system without learning its storage layout.

## Status

From the repository root or any environment where the portable package is available:

```bash
python -m portable.auren_console status
python -m portable.auren_console status --json
```

The status command reports installation, recent run data, learning state, recovery checkpoints, health checks, and installed skill surfaces. JSON output is intended for scripts and diagnostics.

## Console

Start the local read-only console:

```bash
python -m portable.auren_console console --port 0
```

Open it automatically when a desktop browser is available:

```bash
python -m portable.auren_console console --open --port 0
```

The server binds to `127.0.0.1` only. It exposes:

```text
GET /           dashboard
GET /api/status JSON snapshot
GET /health     lightweight liveness response
```

There are no mutating endpoints and no repository file browser.

## What the console is for

Use Overview to answer whether AUREN is installed and healthy. Use Runs to understand the recent execution shape and trace state. Use Learning to see whether machine-scoped memory and automation state exist. Use Recovery to find durable checkpoints that may be relevant to resumed work.

The console deliberately reports unknown or unavailable data when a store is missing or malformed. It does not invent conclusions from partial state.

## Privacy boundary

The console is a view over AUREN's machine-scoped state under `~/.auren`. It does not serve repository source files. Secret-like values are redacted before presentation, and task/prompt payloads are not exposed as full bodies.

## Why this exists

AUREN's existing execution path already includes repository intelligence, capability routing, evidence, verification, review, observability, evaluation, learning, maintenance, and recovery. The console gives a human a single place to understand those layers without replacing any of their ownership or policy gates.
