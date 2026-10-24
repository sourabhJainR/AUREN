# AUREN Autonomous Executive Loop

AUREN can run substantial work as one durable company-level work unit instead of a single process-bound attempt.

## Control model

```
Goal
  -> Work Unit
  -> Executive Team
     -> planner / builder / verifier / reviewer / recovery / learner
  -> execute
  -> verify
  -> CI gate
  -> self-review
  -> trust score
  -> complete
       or
     blocker -> alternate -> confirm -> continue
```

State is persisted in `~/.auren/executive.sqlite3` by default when the project root is supplied. A process restart does not erase the work-unit, decision, event, recipe, or evidence history.

## Iteration contract

Every iteration records:

- execution outcome
- verification/self-review outcome
- CI state
- blocker and alternate decisions
- evidence references
- recovery attempts
- current iteration number
- final trust score

A failed path is not retried blindly. A caller supplies alternate candidates and a confirmation function. AUREN tests candidates in order, records rejected alternatives, and selects the first confirmed path.

## CI waiting

The supervisor has an explicit `waiting_ci` state. CI is treated as an external evidence gate, not as a best-effort status message. The loop waits until the check passes, fails, or reaches its configured deadline. A failed/expired gate can enter the recovery path.

## Trust score

`AutonomousCompany.trust()` produces a shareable score from execution, verification, review, CI, recovery, and evidence components. The score is evidence-derived and includes a confidence value and digest. It is not a claim of general intelligence.

## Skill-specific executive agents

`ExecutiveTeam` keeps specialist agents separate from execution authority. Each agent declares its skills and reports results to the same work unit. This lets AUREN build specialist roles without creating fragmented task state.

## Recipe extraction

`RecipeMiner` extracts reusable patterns from Python docstrings and operational Markdown rules. It stores only the recipe and source reference, not copied source code. Recipes remain evidence-linked and can be promoted later by the existing learning/evaluation controls.

## Long-running operation

The intended pattern is to start one work unit and repeatedly invoke the supervisor until it reaches a terminal state. Supply a stable `work_key` (for example, a repository-and-issue key) when creating recurring engineering work: repeated starts return the same durable unit, and attempts to reuse that key with a different goal, scope, or iteration budget fail closed. Existing work units are migrated in place with an optional key. ExecutionController session mappings use deterministic keys to recover a unit if mapping persistence is interrupted; a terminal unit for the same goal remains terminal rather than silently creating another retry generation. A different explicit goal can start one new generation keyed to the previous unit. Each invocation is bounded by a finite iteration slice; when the slice is exhausted, the work unit remains resumable and the next invocation continues from the durable iteration. Repeated execution/review failures terminate after the configured consecutive-failure limit, and an unhandled review failure does not spin forever. The persistent ledger makes the boundary restart-safe; an OS scheduler/service can invoke the same work unit again after crashes, machine restarts, or planned maintenance windows.

## Running the durable worker

From the repository root, run a bounded worker slice with:

```bash
python -m portable.auren_runtime worker --project-root . --batch-size 20 --max-cycles 100 --max-idle-cycles 5 --json
```

The worker reconstructs registered importable handlers from the durable controller
database, resumes eligible tasks, and exits on cancellation, its cycle cap, or the
idle cap. JSON reports distinguish newly completed and failed tasks from remaining
nonterminal work. Handler exceptions are included only after both the task row and
executive event ledger persist terminal failure; lease loss or partial persistence remains
fail-fast. Exit code 1 means a task failed in this slice, 2 means work remains
after the slice budget, and 130 means cancellation; a zero exit means this slice ended
without new task failures or remaining nonterminal tasks. Use a service manager or
scheduler to invoke another slice against the same project root. Keep handler registration and provider credentials in the trusted host
environment; task payloads cannot register or replace handlers.

## Continuous execution controller

The durable worker service can use `ExecutionController.run_continuously()` to
drain bounded batches from the same SQLite task queue. It returns a
`ContinuousRunReport` with processed-work count, remaining nonterminal task count,
trust-score summaries, cycle/idle counts, and an explicit stop reason. It exits on cancellation, cycle budget, or the
idle budget; an empty batch is never interpreted as successful task completion.
A host scheduler can invoke another bounded slice against the same durable database.
Task handlers must still cooperate with the controller's existing lease heartbeat and
cancellation contracts; the loop only checks its stop event between batches.

The module entrypoint must run without a `RuntimeWarning` that the target module was
preloaded through package initialization; keep `portable.auren_runtime` imported lazily.
