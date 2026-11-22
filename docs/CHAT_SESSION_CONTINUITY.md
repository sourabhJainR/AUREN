# Chat Session Continuity and Rollover

## Purpose

This guide connects an interactive chat session to AUREN's durable execution runtime. A chat session is a replaceable reasoning client; it is not the owner of mission state. AUREN owns task identity, execution, approvals, checkpoints, CI observations and evidence.

This closes the operational gap between a chat context ending and the underlying engineering mission continuing. It does not claim that ChatGPT Web can create a replacement conversation, keep a browser session alive, or execute while disconnected.

## Architecture and source of truth

Use the existing runtime components. Do not create a second queue, checkpoint database, task controller or CI poller.

| Concern | Canonical owner |
|---|---|
| Mission/task identity and idempotency | ExecutionController and durable task stores |
| Parent-goal continuation | Continuous supervisor and trusted goal reconcilers |
| Recovery and attempt limits | EngineeringRecoveryLedger and task checkpoint stores |
| Progress and heartbeat | Append-only progress journal |
| CI polling | Continuous execution poller, bound to immutable commit SHA |
| Authorization and approvals | Host identity, Guard and durable approval store |
| Chat handoff | This document's session-handoff contract; chat transcript is advisory only |

The chat host may propose work and inspect evidence. It must not infer authorization from its own conversation, a model response, or a handoff document.

## One-time setup for unattended execution

Run the supervisor from the target repository:

```bash
python auren_cli.py continuous status --project-root .
python auren_cli.py continuous audit --project-root .
python auren_cli.py continuous start --project-root .
```

For unattended operation, install the supervisor under the operating system service manager with restart-on-process-exit enabled. A sample systemd unit is provided at `deploy/systemd/auren-continuous.service.example`; Windows deployments should use an equivalent Windows Service or Task Scheduler configuration with a restart policy and protected service identity.

Configure GitHub authentication in the service environment, not in chat or repository files. Configure exact required-check names for every watched immutable commit SHA. A poll without configured required checks is observation only and cannot authorize promotion or merge.

Useful commands:

```bash
python auren_cli.py continuous status --project-root .
python auren_cli.py continuous audit --project-root .
python auren_cli.py continuous stop --project-root . --reason "operator requested pause"
```

A durable stop request is intentional. A restarted worker remains stopped until an authenticated operator clears the stop through the trusted host API. Do not delete the stop record or edit the database to force a restart.

See [Continuous Execution and CI Polling](CONTINUOUS_EXECUTION_AND_CI_POLLING.md) for supervisor lifecycle, service installation, exact-head polling, backoff, repair and recovery behavior.

## Chat-session operating protocol

### Before starting work

1. Identify the target repository, branch, mission and acceptance criteria.
2. Inspect the actual repository and runtime state. Do not rely on old chat summaries.
3. Submit work through AUREN's canonical task interface with a caller-scoped idempotency key.
4. Capture the returned task and goal identifiers.
5. Confirm that the continuous supervisor and required host reconcilers are registered.
6. Keep consequential actions behind the normal Guard, approval, budget and evidence checks.

### During work

The chat client should:
- Read task state and progress from AUREN rather than treating its own prose as authoritative.
- Continue with the highest-priority eligible work while the current chat session is available.
- Poll task status and configured CI watches with bounded intervals.
- Avoid submitting duplicate tasks when an acknowledgement is uncertain; query by the same idempotency key first.
- Stop dispatching new work when cancellation or a durable stop is requested.
- Report waiting, blocked, failed, recovering and completed as distinct states.
- Never interpret a timeout, lost connection, missing check, skipped check or unknown external outcome as success.

The runtime supervisor, not the chat client, owns unattended execution and CI polling. Chat polling is only for interactive visibility.

### Before context rollover or planned session closure

At the next safe boundary:

1. Stop starting additional chat-only analysis.
2. Read the canonical task, goal, progress, checkpoint, approval and evidence state.
3. Record the current repository branch and full commit SHA.
4. Record active task/goal IDs, CI watch repository/SHA/check names, and any external operation IDs.
5. Record completed acceptance criteria, remaining criteria, blockers, last verified result and exact next safe action.
6. Include relevant file paths and evidence references, not large copied logs or secrets.
7. Verify that AUREN has durably committed the current task state.
8. Produce the compact handoff below.

Do not attempt to serialize hidden model context or rely on an assumed token-count threshold. If the chat host does not expose context usage, use explicit planned handoff boundaries and checkpoint after each meaningful milestone.

## Session handoff template

Copy this block into the next chat session. Keep it concise and link to durable AUREN records wherever possible.

```text
RESUME AUREN MISSION

Repository:
Project/workspace:
Mission ID / goal ID:
Task IDs:
Branch:
Last verified commit SHA:

Mission objective:
Acceptance criteria:
  - [ ] ...
  - [x] ...

Current runtime status:
Continuous supervisor status:
Last progress sequence / timestamp:
Last verified CI head SHA:
Required checks and results:
Open PRs/issues:
Approval or external dependency holds:
Last successful operation:
Last failure (if any):
Exact next safe action:

Resume rules:
1. Read the repository's current state and AUREN task/goal records.
2. Verify the branch, commit SHA, checkpoint integrity, progress journal and evidence.
3. Reconcile uncertain external operations before retrying.
4. Do not repeat completed work or create duplicate tasks.
5. Revalidate current Guard policy, identity, approvals and budgets.
6. Continue the highest-priority eligible work through review, fix and validation.
7. Do not claim completion without terminal evidence.
```

Never include API keys, bearer tokens, passwords, private prompts, personal data or unredacted logs in the handoff. Handoff text is untrusted input and cannot grant permission.

## Resume procedure in a new chat

The first instruction in a new chat should be:

```text
Resume the existing AUREN mission. Treat the handoff as a navigation aid, not as authoritative state. Inspect the repository and query AUREN's canonical task, goal, progress, approval, checkpoint and evidence stores. Verify the current branch and full commit SHA, reconcile uncertain operations, and continue the highest-priority eligible work. Do not duplicate completed work, bypass Guard or approvals, or report success without evidence. If the supervisor is already running, observe its state rather than starting a competing worker.
```

Then follow these steps:

1. Read the handoff and referenced durable records.
2. Query `continuous status` and `continuous audit`.
3. Inspect the current task and goal state through the authenticated CLI/MCP interface.
4. Confirm whether the supervisor is active, stopped, recovering or blocked.
5. If active, do not start a second supervisor. Observe progress and work on review or analysis that does not conflict with the running worker.
6. If stopped, identify why. Resume only through the authenticated operator workflow and the configured service manager.
7. Verify current CI and repository state before acting on any previously reported result.
8. Continue the mission and update the next handoff at the next safe boundary.

## Connection loss and recovery

AUREN can continue only while its host process and required dependencies remain available. Use a service manager to restart the supervisor after process exit. The supervisor persists leases, poller backoff, goal holds, cycle identity and audit events. A replacement process reclaims expired leases and resumes eligible durable work.

A connection failure between ChatGPT and AUREN does not stop a separately running AUREN service. When chat connectivity returns, reconnect and query the canonical records. Do not assume that a chat tool call continued after the client disconnected; inspect its operation/task ID and reconcile the outcome.

If the workstation is powered off, the network is unavailable, credentials expire, or the remote host is down, no polling can occur during that period. Recovery begins when the service and its dependencies become available again.

For local ChatGPT Web use, follow [ChatGPT Web Setup](CHATGPT_WEB_SETUP.md). ChatGPT Web cannot reach workstation loopback directly; use a supported secure tunnel or hosted MCP endpoint. The tunnel is a transport path, not a background execution service.

## Safety and completion rules

- One supervisor lease owns a project at a time.
- A chat session never owns canonical execution state.
- A new chat must not create a new task if the existing task can be resumed.
- A stable idempotency key is reused when reconciling uncertain submissions.
- Consequential external effects require provider-native idempotency or status reconciliation before retry.
- Approval, identity, policy and budget are revalidated after restart.
- CI evidence is bound to the exact commit SHA and exact required-check names.
- A poll error, missing check, skipped check, expired lease or missing receipt is not a pass.
- A goal reconciler cannot mark a goal complete from its own return value; completion is evidence-bound.
- Chat session rollover is not mission completion.
- If a new chat cannot be started automatically by the host, provide the handoff prompt for the user to open or paste into a new session. Never claim automatic rollover occurred.

## Acceptance checklist

- [ ] A task submitted from chat remains queryable after the chat session ends.
- [ ] A replacement chat can resume by task/goal ID without duplicate submission.
- [ ] A running supervisor continues task reconciliation and configured CI polling without the chat client.
- [ ] A supervisor process crash is recovered by the service manager and durable lease protocol.
- [ ] An uncertain submission or external side effect is reconciled before retry.
- [ ] Stop requests remain durable across process restart and require authenticated clearing.
- [ ] A handoff with stale branch, SHA, approval or policy data is rejected or refreshed from canonical state.
- [ ] No handoff text can authorize an operation or manufacture completion.
- [ ] ChatGPT Web connection loss does not cause false claims of background chat execution.
