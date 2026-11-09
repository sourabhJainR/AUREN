# AUREN Remote MCP Privacy and Security Summary

This document describes the data handled by AUREN's remote MCP operator service. It is an operational disclosure, not a legal privacy-policy certification. The deployment owner must review it, set retention periods and publish any organization-specific legal terms before making the service public.

## Data processed

### Authentication and identity
- The service receives OAuth bearer access tokens and sends them to the configured identity provider's HTTPS introspection endpoint.
- Raw access tokens are not written to AUREN task records or operator audit events.
- The in-memory introspection cache is keyed by a SHA-256 token digest and defaults to a 15-second lifetime.
- The validated issuer and subject identify the caller. AUREN derives a separate SHA-256 principal key for ownership, rate limiting and operator audit records.
- Role and project claims are taken only from the trusted token introspection response. Caller-supplied roles are ignored.
- The service does not request email, phone number or profile attributes. Its profile tool returns a stable opaque identifier and the AUREN workspace label.

### Task and workspace data
- Task text and approved context are stored in AUREN's durable trigger/runtime stores.
- Task records can include repository names, acceptance criteria, references and execution status.
- The workspace root is fixed by the server configuration. A model cannot choose an arbitrary project path.
- Task status and evidence tools return bounded, redacted summaries. They do not return full prompts, raw task payloads, environment variables, provider credentials or raw command output.
- AUREN sends task content to the model/provider configured by the workspace runtime. That provider's retention and training terms apply separately.

### Approvals and audit
- Approval tools expose decision state, required role and immutable digests. They do not expose approval checkpoints or secret material.
- Operator admission changes are recorded in a local append-only hash-chained control event table. The event stores the actor's principal digest and a digest of the operator reason, not the full reason.
- Rate-limit counters and opaque profile identifiers are stored in the local control database.
- The deployment owner is responsible for backup, access control, encryption at rest, retention and secure deletion of local AUREN state.

## Logging and telemetry

- Uvicorn access logging is disabled by the provided launcher.
- Do not enable reverse-proxy logging of Authorization headers, cookies, request bodies or response bodies.
- The service returns exception type names rather than stack traces or provider exception messages.
- The service does not include a third-party analytics or advertising SDK.
- The deployment owner may add monitoring, but must redact tokens, task prompts, repository content and approval payloads.

## Data retention

The remote MCP layer does not currently impose a time-to-live on durable task and evidence history. Records remain in AUREN's local durable stores until the deployment's configured retention/cleanup process removes them. Do not advertise a fixed retention period until the deployment owner has implemented and tested it.

OAuth access-token revocation is observed through introspection, subject to the configured cache lifetime. The default cache lifetime is 15 seconds.

## Security boundaries

- OAuth authorization is delegated to an external identity provider. AUREN is a resource server, not an OAuth authorization server.
- HTTPS, exact host allowlists, browser-origin allowlists, request-size limits, scoped tools and server-side input validation are enforced by the service.
- AUREN exposes no generic shell, arbitrary file-write or arbitrary URL-fetch tool.
- Approval decisions are delegated to AUREN's host-configured approver authorizer. If that authorizer is absent or denies the caller, the decision fails closed.
- SQLite-backed task and control stores are single-host state. Do not use NFS or claim multi-host high availability.
- Public deployment still requires independent security review, penetration testing, secret rotation, backups, disaster recovery and live client acceptance.

## Support and reporting

For implementation issues, open a GitHub issue at:

https://github.com/sourabhJainR/AUREN/issues

Do not include access tokens, client secrets, private repository content or personal data in a public issue. Report suspected security vulnerabilities privately through GitHub's repository security reporting channel when available.

## Example user prompts

1. "Submit this task to AUREN and return the durable task ID. Do not claim it is complete until its evidence shows acceptance criteria passed."
2. "Show my AUREN tasks that are blocked or waiting, including the next action and latest evidence reference."
3. "Pause new MCP task admissions for maintenance. Do not cancel or alter tasks that are already running."
