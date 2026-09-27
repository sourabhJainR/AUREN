# SkillOpt Reference Integration

## Review basis

This integration was compared against Microsoft SkillOpt's public implementation
and documentation, including its ReflACT training loop, SkillOpt-Sleep
consolidation flow, held-out validation gate, bounded add/delete/replace edits,
rejected-edit buffer, and slow/meta update.

## Seven-hat review

| Hat | Finding | HWS action |
|---|---|---|
| Architecture | HWS already owns evidence, memory, regression, shadow, canary, capability and promotion policy. SkillOpt should not become a second control plane. | Added a maintenance-lane SkillOptimizer behind existing AER ownership boundaries. |
| Senior implementation | SkillOpt has an explicit skill-document state and bounded edit representation; HWS had SkillGraph and learning but no equivalent candidate skill epoch. | Added SkillEdit, SkillScore, SkillOptimizationResult and SkillOptimizer. |
| Quality/security/performance | Training data must not overlap the validation set; malformed/unmatched edits must be visible; rejected changes must not enter the live artifact. | Disjoint train/holdout check, bounded edit budget, unmatched-edit reporting, rejected buffer, no live mutation. |
| End user | Skill improvement should be explainable and reversible rather than silently changing behavior. | Result exposes baseline/candidate scores, exact accepted/rejected edits, holdout IDs and a deterministic digest. |
| Product/stakeholder | SkillOpt optimizes a compact deployable skill artifact; HWS must preserve its stronger evidence/promotion controls. | Candidate skill remains a maintenance artifact; activation still requires normal AER regression/shadow/canary/promotion gates. |
| Learning/science | A single observed improvement is insufficient; the useful SkillOpt pattern is train/reflection -> bounded update -> disjoint holdout gate. | Epoch requires separate train and holdout IDs and strict improvement on the configured gate metric. |
| Operations | SkillOpt-Sleep separates offline harvesting/replay/consolidation from live execution. HWS already has deferred learning and DreamMemory. | Added optimize_skill to AdaptiveLearningStore so SkillOpt-style epochs run only through the deferred maintenance lane. |

## What HWS already had

HWS was already stronger than SkillOpt in several control-plane areas:

- immutable evidence envelopes and evidence graphs;
- fail-closed verification and promotion gates;
- historical failure gates and explicit anti-pattern learning;
- capability acquisition and autonomy graduation;
- empirical provider/resource calibration;
- cross-project validation;
- rollback-aware adaptive policy;
- durable project-scoped memory;
- repository intelligence and graph execution.

## What was missing

The main gap was the representation and evaluation of a skill as a trainable
text artifact. HWS could learn policy and capabilities, but did not have a
small reusable primitive for:

1. proposing bounded textual edits;
2. applying only a limited number of edits;
3. scoring the incumbent and candidate on a disjoint holdout;
4. rejecting a non-improving candidate;
5. retaining rejected edits as negative learning evidence;
6. producing an auditable epoch digest.

Those pieces are now implemented in `portable.skill_optimization`.

## Deliberately not copied

The following remain outside this integration:

- SkillOpt's benchmark-specific environment adapters;
- its model/provider implementation;
- its experiment CLI and WebUI;
- direct replacement of HWS's evidence/promotion authority;
- automatic mutation of live harness policy;
- greedy "accept all edits" behavior.

HWS keeps the stronger fail-closed path. Skill optimization is an input to
learning, not an authority over execution.

## Resulting flow

```
verified experience
       |
       v
deferred maintenance lane
       |
       v
propose bounded skill edits
       |
       v
train / holdout split check
       |
       v
baseline -> candidate holdout scoring
       |
   improve?
    /   \
  no     yes
  |       |
reject   candidate artifact
  |       |
negative  AER regression
buffer    -> shadow -> canary -> promote
```

This preserves the useful SkillOpt learning mechanism while keeping AER's
evidence and safety architecture authoritative.
