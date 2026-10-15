# Customer security and data-flow worksheet

**Status:** Template only. Complete and review for the actual AUREN version, configuration, providers and deployment before a customer pilot. Blank fields are unresolved—not assurances.

## Deployment profile

- Customer / environment:
- AUREN revision and package digest:
- Deployment mode: local / customer-hosted / cloud-assisted / other:
- Workspace and temporary-file locations:
- Runtime identity and OS privileges:
- Execution backend: host / sandboxed repository / OpenShell / other:
- OpenShell CLI and gateway versions, if used:
- Policy bundle path and SHA-256:
- Network egress policy and enforcement point:
- Evidence and log storage:
- Retention and deletion process:
- Incident contact and stop procedure:

## Data-flow inventory

| Data / operation | Source | Destination / recipient | Purpose | Sensitive fields possible? | Retention | Approved? |
|---|---|---|---|---|---|---|
| Repository files / snippets | | | Context and code changes | Yes | | |
| Prompts and task instructions | | | Agent task execution | Yes | | |
| Model/provider requests | | | Inference | Yes | | |
| Tool and command input | | | Build/test/engineering actions | Yes | | |
| Tool stdout/stderr | | | Debugging and verification | Yes | | |
| Credentials / provider handles | | | Authenticated access | Yes | | |
| Traces and evidence receipts | | | Audit and regression | Possibly | | |
| Crash dumps / temporary files | | | Recovery and diagnostics | Possibly | | |
| Telemetry / analytics | | | Product operations | Possibly | | |

List every provider, API, telemetry endpoint, storage system, subprocess and external integration actually enabled. Do not assume that “local-first” means no data leaves the host; verify runtime configuration and network behavior.

## Control checklist

- [ ] Customer-approved repository and task scope.
- [ ] Least-privilege credentials; secrets are not embedded in prompts, traces or artifacts.
- [ ] Explicit model/provider and data-processing approvals.
- [ ] Network egress reviewed and enforced at a real boundary.
- [ ] Curated workspace and path allowlist defined.
- [ ] Consequential actions pass through the host-configured governance gateway.
- [ ] Merge, release and deployment require trusted approval and evidence.
- [ ] OpenShell or other isolation is verified in the actual environment; a mocked test is not an isolation audit.
- [ ] Raw command output is access-controlled and excluded from low-leakage evidence unless explicitly needed.
- [ ] Retention, deletion, backup and access controls are documented.
- [ ] Failure, timeout, cancellation and rollback procedures have been rehearsed.
- [ ] Customer can stop the run and revoke credentials.
- [ ] Any external evaluation or security assurance is accurately labeled by who operated it.

## Approval record

- Customer technical owner:
- Customer security/privacy approver:
- AUREN operator:
- Review date:
- Exceptions accepted and by whom:
- Unresolved risks:
- Go / no-go decision:

This worksheet is not legal advice, a certification, or a completed security assessment.
