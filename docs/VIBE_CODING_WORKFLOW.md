# AUREN Vibe-Coding Lifecycle

AUREN provides a first-class, provider-neutral workflow for taking a natural-language product idea through discovery, design, engineering, launch and operational learning. The workflow has **18 explicit stages**; it is not merely a prompt template.

## Canonical stages

| # | Stage | Required completion evidence |
|---:|---|---|
| 1 | IDEA | Problem statement and measurable success metrics |
| 2 | RESEARCH | Market/domain evidence and competitor/alternative review |
| 3 | DEFINE THE USER | Personas and user needs |
| 4 | PRD | Requirements and acceptance criteria |
| 5 | CHOOSE TECH STACK | Technology decisions and trade-offs |
| 6 | ARCHITECTURE | Architecture decisions and boundaries |
| 7 | DESIGN | UI/UX artifacts, wireframes or equivalent design outputs |
| 8 | PROJECT RULES | Coding, test, security and repository rules |
| 9 | TASK BREAKDOWN | Verifiable tasks with dependencies |
| 10 | SETUP | Repository and reproducible environment evidence |
| 11 | DEVELOPMENT | Implementation artifacts |
| 12 | TESTING | Test inventory and explicit pass result |
| 13 | SECURITY REVIEW | Findings (including an explicit empty list when none), pass status, no unresolved critical findings |
| 14 | CODE REVIEW | Review summary, named reviewer distinct from author when author is supplied, no blocking findings |
| 15 | PREVIEW DEPLOYMENT | Deployment/health receipt, or an explicit scoped not-applicable decision |
| 16 | QA TESTING | QA results, user feedback, and explicit pass status |
| 17 | PRODUCTION DEPLOY | Release receipt, explicit authorization, and deployed revision matching the reviewed revision; or an explicit scoped not-applicable decision |
| 18 | MONITORING | Health signals, alerts and rollback thresholds |

## Runtime contract

- `portable.vibe_coding.STAGE_ORDER` and `STAGE_SPECS` are the canonical machine-readable stage registry.
- `build_vibe_coding_graph(handlers)` returns a dependency-ordered AUREN `Graph`. All 18 handlers must be supplied; missing handlers fail fast instead of becoming no-op stages.
- Each handler receives the accumulated orchestration state and returns a mapping containing stage-specific required outputs plus non-empty `evidence`.
- Stage output is validated before the dependent stage can run. A failed or invalid stage blocks all dependent stages; it is never converted to a success by retries or a vague textual claim.
- Preview and production deployment can be marked not applicable only with a recorded rationale and evidence. The workflow itself creates that receipt only when the corresponding `*_deployment_required` state flag is explicitly `false` and a rationale is supplied.
- Production deployment handler invocation is blocked unless `production_approval` contains `approved: true`, `approved_by`, and `approved_at`. The handler's release receipt must prove that `deployed_revision` exactly matches `reviewed_revision`. This runtime gate supplements, and does not replace, deployment credentials, environment protection, change management, or Guard policies.
- Stage outputs are copied into JSON-safe evidence envelopes before being consumed downstream.

## Restart and resume

Pass a `checkpoint_writer` callback to persist a receipt after each completed stage:

```python
from portable.orchestration import Orchestrator
from portable.vibe_coding import build_vibe_coding_graph

def persist_checkpoint(snapshot):
    # Store this JSON-safe snapshot in the caller's durable checkpoint store.
    durable_store.write(snapshot["workflow_id"], snapshot)

graph = build_vibe_coding_graph(stage_handlers, checkpoint_writer=persist_checkpoint)
run = Orchestrator(graph, max_total_attempts=32).run(
    "feature-123",
    "Build the requested product",
    context={"vibe_coding_workflow_id": "feature-123"},
)
```

For a restart, pass the last saved receipt as `context["vibe_coding_checkpoint"]` and keep the same workflow ID. Completed stages are revalidated and their stored outputs reused without replaying their handlers. The checkpoint is versioned, digest-checked, and validated for known stages, valid output contracts, and an ordered completion prefix. A digest detects accidental/tampered payload changes; it is not a cryptographic signature or proof of trusted storage.

## Product behavior and safety

- Natural-language requests should be routed to this lifecycle when they describe a new product or substantial end-to-end feature. Small bug fixes and investigation-only requests should continue to use their narrower workflow when appropriate.
- Discovery outputs must distinguish observed evidence from assumptions and unknowns. Research does not assert market validation without sources or captured research.
- Human/user feedback is recorded rather than fabricated. If a user is unavailable, record the actual feedback status and why the gate remains blocked or what scoped validation was performed; do not invent feedback.
- Deployment stages are explicit side-effect boundaries. Production release needs affirmative authorization and exact reviewed-revision binding.
- Monitoring is part of the workflow, not an optional afterthought. Define ownership, alert conditions and rollback thresholds before declaring the lifecycle complete.
- Existing AUREN Arena independence, evidence provenance, security, authority, exact-head verification, regression and rollback rules remain authoritative. A green local graph is not a substitute for independent evaluation or an operator-run deployment/rollback exercise.

## Verification

Run:

```bash
python -m unittest tests.test_vibe_coding -v
```

The tests cover the full 18-stage graph, missing-handler rejection, security-gate blocking, production authorization, checkpoint tampering and restart/resume without replaying completed stages.
