# AUREN Behavioral Conformance Suite

The Behavioral Conformance Suite measures engineering behavior, not prompt-answer quality.

## Ten-task corpus

The same ten tasks live in `.ai-harness/conformance/tasks.jsonl`: repository orientation, targeted feature change, defect RCA, context minimization, regression detection, recovery, security boundary, architecture decision, maintenance refactor and release readiness.

## Ground truth

The ground-truth runner creates a deterministic fixture in a disposable checkout and evaluates it independently of provider claims. `scripts/conformance_oracles.py` supplies task-specific acceptance oracles; `scripts/benchmark_oracles.py` adds benchmark-strength evidence; `scripts/conformance_trace.py` provides invocation-level tracing and deterministic BC-06 failure injection.

The benchmark evidence pipeline is:

`fixture -> baseline fingerprint -> baseline tests -> provider execution -> post fingerprint -> post tests -> task oracle -> hidden acceptance -> AST/static invariants -> mutation testing -> recovery ordering -> Context Broker telemetry -> separate scores`.

### Evidence gates

1. **Baseline/post fingerprints** — hashes every non-Git file and separately records focused-test output digests so pre-existing state is distinguishable from post-change state.
2. **Independent mutation testing** — deterministic defects are injected into applicable implementations; the benchmark requires the independent checks to kill those mutants rather than merely pass the happy path.
3. **Hidden acceptance cases** — additional cases are evaluator-only and are not included in the provider task prompt.
4. **AST/static invariants** — structural requirements are checked independently of runtime behavior, including required symbols and security-sensitive source invariants.
5. **Deterministic failure injection** — BC-06 injects a one-shot verification failure through the benchmark trace boundary, without asking the provider to manufacture a failure.
6. **Exact recovery ordering** — a recovery pass requires an observed failure followed later by a successful verification event; success alone is insufficient.
7. **Context Broker telemetry** — the broker can emit lease/discover/release events to an isolated JSONL sink via `AUREN_CONTEXT_BROKER_TELEMETRY`. Missing telemetry is reported as missing observability, never converted into behavioral credit.
8. **Separate scores** — `behavior_score`, `oracle_coverage` and `observability_score` are independent. A provider can behave correctly while having incomplete telemetry, and that distinction is preserved.

Provider JSON remains diagnostic only and cannot override an oracle failure.

## Task-specific examples

- **BC-02:** imports `slugify_name`, executes hidden normalization cases and mutation-tests the implementation.
- **BC-03:** verifies the retry-counter/attempt invariant, including hidden eventual-success behavior and an off-by-one mutant.
- **BC-04:** checks requested behavior and command-trace evidence that the unrelated document was not accessed.
- **BC-05:** compares focused and broader verification outcomes and preserves the deliberate pre-existing failure.
- **BC-06:** requires deterministic injected failure, subsequent recovery and exact event ordering.
- **BC-07:** verifies secret removal, configuration wiring and source-level absence of the benchmark secret.
- **BC-08:** verifies read-only behavior and decision evidence without implementing the design.
- **BC-09:** independently exercises behavior after the refactor.
- **BC-10:** verifies tests, diff hygiene and generated-state gates.

## Progressive skill context

The orchestrator skill is deliberately small. Detailed lifecycle, verification, frameworks, providers, learning and benchmark methodology live in `.agents/skills/ai-coding-orchestrator/context/` and are retrieved only when relevant. `context/INDEX.md` is the entry point. The skill itself must remain below the 9,000-character active-context budget.

## Commands

```bash
python scripts/run_evals.py
python scripts/behavioral_conformance.py --write-report
python scripts/behavioral_conformance_ground_truth.py --write-report
python scripts/behavioral_conformance_ground_truth.py --task BC-03 --providers claude,codex
```

Behavioral release readiness is live-only: every requested provider must complete every requested task and satisfy the independent gates. ChatGPT is represented through an executable Codex/MCP surface rather than a fictitious local subprocess.

## Safety

Execution uses a disposable checkout. Reports are written only with `--write-report`. Secret material must never be printed. Benchmark evidence is not permission to activate AUREN self-modification; normal regression, safety, shadow/canary, promotion and rollback gates remain authoritative.
