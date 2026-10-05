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

The intended pattern is to start one work unit and repeatedly invoke the supervisor until it reaches a terminal state. The persistent ledger makes the boundary restart-safe; an OS scheduler/service can invoke the same work unit again after crashes, machine restarts, or planned maintenance windows.
