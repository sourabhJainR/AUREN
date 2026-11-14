# AUREN provider role profiles

A provider role profile assigns a pinned model route to each graph-team role. It is a routing configuration, not an authority document.

## Quick start

Run this from the AUREN checkout or installed runtime:

```bash
# Read-only inventory of hosts, providers, skills and optional integrations
auren integrations

# Create a local-only profile for all 14 graph-team roles
auren integrations profile init --provider ollama --model <pinned-local-model-id> --apply

# Validate the profile and inspect configured providers
auren integrations profile validate
```

The starter profile assigns the same model to all roles and gives every role an empty tool scope. Edit `.auren/provider_roles.json` to choose different providers and models for individual roles.

For a remote provider with a local model for private work:

```bash
# Keep credentials in the environment or a secret manager
export OPENAI_API_KEY="<secret>"
export AUREN_LOCAL_REMOTE_INFERENCE=enabled
export AUREN_LOCAL_DATA_CLASSIFICATION=public

auren integrations profile init \
  --provider openai \
  --model <pinned-remote-model-id> \
  --local-model <pinned-local-model-id> \
  --apply

auren integrations profile validate
```

Use the provider's exact model ID. Do not use mutable aliases such as `latest`. For Windows PowerShell, use `$env:OPENAI_API_KEY = "..." ` and set the two AUREN environment variables with the same names.

## Profile shape

The default file is `.auren/provider_roles.json`. It contains:

- A schema version, profile ID and revision.
- One provider/model binding for each configured role.
- A bounded timeout.
- An explicit list of read-only tools approved for that role.
- An optional local model ID for private-task routing.

The profile supports the eight core specialist roles and the six software-team leadership, architecture, research and release personas. A profile may contain either complete role family or both. Partial family registration is rejected.

API keys, tokens, endpoints with embedded credentials and other secrets do not belong in the profile. Credentials are loaded from the existing provider environment variables. The `custom` provider also requires an HTTPS endpoint and exact hostname allowlist.

## Register the profile in the graph-team host

The runtime host exposes `register_profiled_crew_adapters` in `.ai-harness/runtime/__init__.py`. Call it during the existing host bootstrap after the canonical controller and approved tool registry are ready:

```python
adapters = register_profiled_crew_adapters(
    project_root / ".auren" / "provider_roles.json",
    tools=approved_tool_registry,
    progress_journal=controller.progress_journal,
)
```

The helper builds every selected role adapter first, then registers the combined set in one operation. If any provider, model, role, tool scope or adapter is invalid, registration fails before the live registry changes.

If no tools are passed, the helper uses an empty `ApprovedToolRegistry`. To expose read-only tools, pass the host's canonical registry and list only those exact tool names in the profile. The registry rejects side-effecting tools. The profile cannot add tools that the host has not approved.

## Privacy and authority rules

- `ollama` profiles use the loopback-only local model endpoint.
- Remote model profiles accept remote work only when the task explicitly declares `public` or `non_sensitive`, `AUREN_LOCAL_REMOTE_INFERENCE=enabled`, and the workspace classification is also `public` or `non_sensitive`.
- Private or restricted tasks use the configured `local_model_id`. If no local model is configured, the remote invoker rejects the task. It does not send private data to the remote provider.
- Remote inference remains proposal-only. The model cannot run tools, approve work, edit a repository, merge, release or deploy.
- Provider calls remain inside the provider-backed crew adapter's bounded wait and shared team-budget enforcement.
- A profile digest and exact model ID are included in the adapter's version identity.

A valid profile does not prove that a model is installed, that credentials authenticate, that a vendor account has access, or that the model meets quality or safety requirements. Use the inventory and a separate live conformance receipt before production use.

## Inventory states

`auren integrations` reports detected, configured, authenticated, live-tested and enabled as separate states. It does not change client configuration or send requests to remote model APIs. Authentication and live acceptance remain `not_tested` until an explicit acceptance flow records evidence.

Provider capability manifests are treated as untrusted metadata. They are never imported or executed by the inventory command.
