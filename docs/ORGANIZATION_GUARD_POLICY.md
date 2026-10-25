# Organization Guard Policy

AUREN Guard accepts an optional organization-owned policy at
`.auren/guard_policy.json`. The policy adds restrictions to the existing
execution-controller, approval, repository, and exact-head CI gates. It never
grants authority or bypasses those gates.

## Format

```json
{
  "schema_version": "auren-guard-v1",
  "default_effect": "deny",
  "rules": [
    {
      "id": "deny-prod-shell",
      "effect": "deny",
      "tool": "portable.handlers.shell",
      "resource": "prod/*"
    },
    {
      "id": "approve-release",
      "effect": "require_approval",
      "action": "release",
      "risk": "high"
    },
    {
      "id": "allow-read-only",
      "effect": "allow",
      "action": "read"
    }
  ]
}
```

Supported matching fields are `action`, `tool`, `resource`,
`organization_id`, `agent_id`, and `risk`. Values support exact matches and `*` wildcards within a field (for example,
`prod/*`). Shell character classes and regular expressions are not supported.
A rule must match at least one field. Rule IDs must be unique.

## CLI inspection

- \`auren guard-policy show\` prints the normalized policy and canonical digest.
- \`auren guard-policy validate\` validates the saved policy and lists rule IDs.
- \`auren guard-policy dry-run --tool trusted.shell\` explains a simulated decision.
  Add \`--action-name\`, \`--resource\`, \`--organization-id\`, \`--agent-id\`, or
  \`--risk\` to supply other context selectors.

Dry-run is explanatory only. It does not call a tool, create an approval, or grant
authority. Put \`--project-root PATH\` before the subcommand when inspecting a
project other than the current directory.

## Enforcement rules

- `deny` always overrides `allow`, independent of rule order.
- `require_approval` blocks dispatch unless the registered handler is classified
  as approval-gated and has the trusted durable approval gateway/ledger policy.
  The handler must still complete the existing approval flow.
- `default_effect: deny` requires an explicit matching `allow` rule.
- An `allow` rule cannot override platform authorization, workflow delivery
  mode restrictions, branch protection, exact-head CI, promotion evidence, or
  other existing mandatory gates.
- Missing policy uses the built-in baseline. A configured but malformed,
  oversized, incompatible, unreadable, or symlinked policy blocks guarded work.
  It is never silently replaced with defaults.
- Policy JSON rejects duplicate keys and unknown fields. Keep the file under
  source control only when the organization intends its policy to be repository
  managed; do not put secrets or personal data in it.
- `AUREN_ORGANIZATION_ID` supplies the organization match context. Agent
  identity is derived from the registered handler identity, not task-supplied
  identity. Risk class is derived from the trusted handler execution class.

## Evidence and recovery

Durable lifecycle evidence includes the effective workflow-profile version,
digest, delivery mode, execution/approval/review/recovery modes, and Guard policy
digest. The evidence does not include the task's business payload. The
supervisor validates the profile digest before dispatch and re-evaluates the
configured Guard policy before each task execution. If a policy changes between
submission and recovery, the current policy is applied; a denied or invalid
policy blocks the task rather than replaying under old permissions.

This is a local policy enforcement layer, not a claim of external identity
attestation, kernel-level sandboxing, complete network egress enforcement, or
independent compliance certification. Tools and provider adapters must continue
to enforce resource boundaries at their own execution boundary.
