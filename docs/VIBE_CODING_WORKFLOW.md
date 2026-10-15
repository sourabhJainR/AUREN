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
python -m unittest discover -s tests -p 'test_vibe_coding.py' -v
```

The tests cover the full 18-stage graph, missing-handler rejection, security-gate blocking, production authorization, checkpoint tampering and restart/resume without replaying completed stages.


## Active-agent and tool integration

Use `portable.vibe_coding_adapters` to connect the canonical graph to AUREN's live agent runtime and concrete research, design, repository, test, review, deployment, and observability tools. `build_host_adapter_registry` composes one active-agent dispatcher with capability-specific host tools, then `build_integrated_vibe_coding_graph` binds the resulting registrations to all 18 stages.

```python
from portable.vibe_coding_adapters import (
    build_host_adapter_registry,
    build_integrated_vibe_coding_graph,
)

# Wrap AUREN's live GraphAgentTeam/provider runtime in this callable.
# Each tool is a real, permission-scoped host adapter, not a placeholder.
tools = {
    "product": product_discovery_tool,
    "research": research_tool,
    "web_search": web_search_tool,
    "accessibility": accessibility_checker,
    "design": design_tool,
    "repository": repository_tool,
    "security": security_scanner,
    "task_planning": task_planner,
    "environment": environment_inspector,
    "test_execution": test_runner,
    "independent_review": independent_reviewer,
    "deployment": deployment_adapter,
    "preview": preview_adapter,
    "browser": browser_qa_adapter,
    "release": guarded_release_adapter,
    "observability": observability_adapter,
    "rollback": rollback_readiness_adapter,
}
registry = build_host_adapter_registry(
    active_agent=graph_agent_dispatch,
    tools=tools,
    max_risk="critical",
)
graph = build_integrated_vibe_coding_graph(registry, checkpoint_writer=persist)
```

Every tool receives the stage, task intent, sanitized allowlisted context, prior-stage outputs, and adapter identity. Secret-bearing state fields are removed/redacted; production approval is withheld from non-production stages. Research must return source-backed evidence. Design artifacts, repository artifacts, test results, independent review findings, deployment receipts, release revision/approval receipts, and monitoring signals must come from their designated tool adapters; the agent cannot invent these authoritative fields. The host remains responsible for binding actual provider APIs, credentials, sandboxing, repository permissions, and deployment environments.

### Adaptive routing and self-evolution

- Adapter selection ranks eligible registrations by smoothed observed success, evidence quality, cost, and latency, while respecting a configurable maximum risk. Missing capabilities fail fast rather than silently invoking a no-op.
- Record independent workflow outcomes with evidence references. A candidate is proposed only after at least five observations across three distinct workflow IDs and a sufficiently high smoothed success rate.
- Candidate proposals are **not promoted automatically**. Isolated regression, independent review, security review, and holdout-transfer evidence are required before a separately authorized promotion path can act.
- Learning snapshots are versioned and digest-checked. Digests detect accidental edits; they are not signatures or proof of trusted storage.
- The integration makes one adapter call per stage. It does not blindly retry uncertain external side effects. A failure blocks downstream stages and is handled by the existing bounded recovery/supervisor policy.
- Production deployment remains behind the existing explicit approval and exact verified-revision gate. Agent selection and learned success scores never grant merge, approval, release, or Guard authority.

Verify the adapter integration:

```bash
python -m unittest discover -s tests -p 'test_vibe_coding*.py' -v
```

This proves the local adapter contract and simulated end-to-end graph path only. Actual external agent/provider access, cloud deployment, independent Arena evaluation, and operator-run rollback still require evidence from the configured environment.

Persist adapter-learning feedback separately from the workflow checkpoint using a machine-scoped path (for example under `~/.auren/`):

```python
registry.save_snapshot("~/.auren/vibe-coding/adapter-learning.json")
# On the next process/session, after registering the same adapter IDs:
registry.load_snapshot("~/.auren/vibe-coding/adapter-learning.json")
```

The receipt retains evaluation counts, distinct workflow IDs, and evidence references. Use only trusted machine-scoped storage; the digest detects changes but does not authenticate who wrote the snapshot.


## Runnable host integration and real-project validation

The runnable entry point is `scripts/run_vibe_coding_project.py`. It connects AUREN's discovered local agent CLI, actual Git worktree state, configured test commands, local design/accessibility artifacts, and MCP tools using JSON-RPC stdio. It does not depend on the ChatGPT connector being available inside the AUREN process.

### Operator configuration

Create `~/.auren/vibe-coding/host.json` with mode `0600`. The config is executable policy: every command is an explicit argument array, never a shell string. Use pinned, reviewed MCP server commands and the actual tool names exposed by each server.

```json
{
  "schema_version": "auren-vibe-host-config-v1",
  "provider": "codex",
  "reviewer_provider": "claude",
  "allow_workspace_writes": true,
  "test_commands": [
    ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test*.py"]
  ],
  "env_allowlist": [],
  "agent_timeout_seconds": 180,
  "test_timeout_seconds": 300,
  "browser_qa_required": false,
  "browser_qa_rationale": "This target is a CLI/library project without a browser surface",
  "preview_deployment_required": false,
  "preview_deployment_rationale": "No isolated preview environment is configured for this validation",
  "security_tool_required": true,
  "mcp_tools": {
    "web_search": {
      "argv": ["/absolute/path/to/reviewed-search-mcp-server"],
      "tool_name": "actual_search_tool_name",
      "argument_map": {"query": "$intent"},
      "timeout_seconds": 30
    },
    "security": {
      "argv": ["/absolute/path/to/reviewed-security-mcp-server"],
      "tool_name": "actual_security_scan_tool_name",
      "argument_map": {"request": "$request"},
      "timeout_seconds": 120
    }
  }
}
```

The paths and tool names above are schema placeholders, not installed integrations. Replace them with real local MCP servers. If the project has a browser UI, configure `browser_qa_required=true`, `preview_deployment_required=true`, and actual `browser`, `deployment`, and `preview` MCP tools. Preview receipts must identify a non-production environment, return an HTTPS or loopback URL, and report `health_status="passed"`. The research adapter extracts source locators from the configured search tool and refuses to pass with fewer than two distinct public HTTPS sources. A separate `independent_review` MCP tool can be used instead of a reviewer CLI; otherwise configure a reviewer provider different from the implementation provider.

### Run against a real Git project

```bash
python scripts/run_vibe_coding_project.py \
  --project /absolute/path/to/clean/git/checkout \
  --workflow-id project-feature-20261015 \
  --intent "Describe the concrete feature to implement" \
  --config ~/.auren/vibe-coding/host.json
```

The source checkout must be clean. With workspace writes explicitly enabled, AUREN creates a detached worktree under `~/.auren/vibe-coding/worktrees/`; it never edits the source checkout, pushes a branch, creates a PR, merges, or publishes. The isolated worktree is retained for inspection and review. If the run stops at a gate, the JSON report identifies the blocked stage; uncertain tool side effects are not silently retried.

### Cross-session receipts

- Adapter outcomes and independent evaluation evidence auto-persist to `~/.auren/vibe-coding/adapter-learning-<config-digest>.json`, with atomic replacement and private permissions. Receipts are configuration-bound so results from a different provider/tool setup are never silently mixed. The same registered adapter IDs must be present when restoring a receipt.
- Workflow checkpoints are stored under `~/.auren/vibe-coding/checkpoints/` and bind the exact workflow ID, project identity, intent digest, base commit, worktree path, and adapter configuration digest. A changed binding fails closed rather than replaying old stage outputs.
- Candidate promotion remains proposal-only. The local host does not turn ordinary successful runs into independent Arena evidence, and adapter scores never authorize a release.
- Production deployment is intentionally disabled by the local validation runner. A production release must use the canonical graph with an explicit approver/timestamp, independent security and code-review receipts, a verified full commit SHA, and release receipts matching that exact revision. The configured deployment tool must also enforce its own environment protection and rollback controls.

### Host integration tests

```bash
PYTHONPATH=.:.ai-harness python -m unittest \
  tests.test_vibe_coding tests.test_vibe_coding_adapters tests.test_vibe_coding_host -v
python -m py_compile portable/vibe_coding_host.py scripts/run_vibe_coding_project.py
```

These tests exercise actual Git worktrees, subprocess execution, MCP stdio requests, checkpoint binding, and receipt persistence. They do not by themselves prove that an operator's specific provider, search, browser, security, cloud, or rollback service is configured and live; the real-project run report is the evidence for that host configuration.


### Uncertain side effects and rollback

The SQLite journal at `~/.auren/vibe-coding/operations.sqlite3` records a side-effect intent before development or deployment operations and stores the receipt after success. If the process stops between those points, AUREN blocks automatic replay. After inspecting the isolated diff and checking the external system's state, an operator can explicitly authorize a retry against the exact worktree fingerprint:

```python
from portable.vibe_coding_host import SideEffectJournal, fingerprint_worktree

workflow_id = "project-feature-20261015"
worktree = "/home/user/.auren/vibe-coding/worktrees/project-feature-20261015"
journal = SideEffectJournal("~/.auren/vibe-coding/operations.sqlite3")
journal.resolve_uncertain(
    workflow_id, "development", "agent",
    action="retry",  # or "abort"
    workspace_root=worktree,
    expected_worktree_digest=fingerprint_worktree(worktree),
    external_state_evidence="operator://incident-or-change-record",
    reason="Reviewed the partial worktree and verified no external release occurred",
    authorized=True,
)
```

Do not authorize a retry until the referenced external-state evidence has been checked. To discard the isolated worktree, use `IsolatedGitWorktree.rollback(authorized=True)` only after reviewing the report and confirming the workflow ID/base revision; this does not roll back a production service. Production rollback remains the configured deployment provider's responsibility and must be exercised under its own authorization.
