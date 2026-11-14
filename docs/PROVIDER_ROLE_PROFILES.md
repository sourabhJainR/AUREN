# AUREN Integration Inventory and Provider Role Profiles

AUREN separates integration discovery, configuration, trust and activation. The integration inventory is read-only. A provider role profile selects a pinned provider/model for each graph-team role; it does not grant tools or action authority.

## 1. Inspect integrations

From the AUREN checkout:

```powershell
python auren_cli.py integrations --project-root .
```

Or use the installed CLI:

```bash
auren integrations --project-root .
```

The JSON report covers local MCP hosts, Agent Skills, provider CLIs, remote model APIs, the local Ollama-compatible runtime, provider capability manifests and the graph-team provider profile.

Readiness states are separate:

| State | Meaning |
|---|---|
| Detected | A local executable, config file, endpoint or manifest was found |
| Configured | Required local configuration is present |
| Authenticated | A credential check succeeded; otherwise `not_tested` |
| Live tested | A versioned host/provider acceptance receipt exists; otherwise `not_tested` |
| Enabled | Current AUREN configuration and policy permit use; this is not a quality or safety verdict |

The command does not edit client files or send remote model requests. It uses the existing local readiness checks, which may inspect the local Ollama-compatible endpoint. API key values are never returned. VS Code JSONC comments and trailing commas are supported. Provider capability manifests are reported as untrusted metadata and are never imported or executed.

## 2. Create and validate a profile

Preview a local-first profile for all 14 graph-team roles:

```powershell
python auren_cli.py integrations profile init --project-root . --provider ollama --model llama3.2:3b
```

Create the file only after reviewing the preview:

```powershell
python auren_cli.py integrations profile init --project-root . --provider ollama --model llama3.2:3b --apply
```

The default file is `.auren/provider_roles.json`. The command will not overwrite an existing file. Use a reviewed new revision and keep a backup when changing an existing profile.

Inspect and validate:

```powershell
python auren_cli.py integrations profile show --project-root .
python auren_cli.py integrations profile validate --project-root .
```

Validation checks the schema, pinned model IDs, provider configuration and remote-routing policy. It does not call a vendor API. The report uses `live_activation: not_performed`; validation is not a live acceptance receipt.

## 3. Configure models by role

Each role entry has this shape:

```json
{
  "provider_id": "openai",
  "model_id": "gpt-model-version",
  "timeout_seconds": 90,
  "allowed_tools": [],
  "local_model_id": "llama3.2:3b"
}
```

You can select a different provider and model for each role. The supported role IDs are:

- Specialists: `backend_api`, `database_schema`, `dotnet`, `end_user_validation`, `frontend_ui`, `product_pm`, `qa_reviewer`, `security_privacy`.
- Leadership and research: `ceo_sponsor`, `cto_sponsor`, `engineering_manager`, `release_operations`, `research_evaluation`, `solution_architect`.

A profile may contain either complete role family or both. Partial family registration is rejected. The starter profile writes both complete families with empty tool scopes.

Use pinned model IDs. Mutable aliases such as `latest` are rejected. API keys, tokens and secrets do not belong in profile JSON. Keep credentials in the runtime environment or a secret manager.

## 4. Enable remote providers

A remote role is eligible only when all of these conditions hold:

1. The provider has a configured credential and model.
2. The provider ID is included in the explicit `AUREN_REMOTE_PROVIDER_ORDER` pool.
3. `AUREN_LOCAL_REMOTE_INFERENCE=enabled` is set.
4. The workspace classification is `public` or `non_sensitive`.
5. The individual task request is explicitly marked `public` or `non_sensitive`.

For example, in a controlled test environment:

```powershell
$env:AUREN_REMOTE_PROVIDER_ORDER = "openai"
$env:AUREN_LOCAL_REMOTE_INFERENCE = "enabled"
$env:AUREN_LOCAL_DATA_CLASSIFICATION = "public"
$env:OPENAI_API_KEY = "<secret-manager-injected-value>"
$env:OPENAI_MODEL = "<current-model-id>"
```

The exact model ID in each role profile is the pinned selection used by that role. Never put a real key in the profile, repository, task prompt or logs.

Private and restricted tasks never go to a remote provider. If a role has a `local_model_id`, it can fall back to that local model. Without a local fallback, the remote call is denied when privacy or remote-policy checks fail. A configured local fallback is not proof that the model is installed or suitable; check local model status separately.

## 5. Register the profile in a trusted host

Creating a profile does not silently activate providers. Register it during trusted runtime bootstrap after the canonical controller and approved read-only tool registry are ready:

```python
from runtime import register_supervised_profiled_crew_adapters

adapters = register_supervised_profiled_crew_adapters(
    controller,
    project_root / ".auren" / "provider_roles.json",
    tools=approved_read_only_tools,
)
```

The controller must expose its canonical `ProgressJournal`. The host supplies the `ApprovedToolRegistry`; profile tool names must be a subset of that registry. Use the same host-owned registry and shared team-budget enforcer as the rest of the graph runtime when shared team-budget settlement is required.

Registration builds the complete selected role family and validates all adapters before updating the runtime registry. The adapters remain proposal-only. They cannot add tools, write files, approve actions, bypass Guard, change execution budgets or authorize deployment. The supervisor remains responsible for dispatch, approvals, verification and adoption.

The lower-level `register_profiled_crew_adapters` helper is intended for trusted bootstrap code that explicitly owns progress-journal wiring. Prefer the supervised wrapper for normal host composition. A running runtime does not hot-reload profile changes; restart or explicitly re-bootstrap after a revision.

## 6. Readiness and limitations

- Provider order is checked against the configured remote provider pool. Invalid or duplicate provider order disables remote routing.
- Profile tool scopes can narrow the host's approved read-only tools but cannot expand the registry.
- Model and profile identity are pinned in the provider-backed adapter version.
- Provider calls use the adapter's bounded wait. Shared team-budget settlement is enforced when the host supplies the canonical registry with its team-budget enforcer.
- Profile validation does not authenticate a vendor account, test model quality or verify privacy terms.
- This is not a universal executable plugin loader. Third-party code remains outside the profile format and must use a reviewed integration boundary.

See [Plug-and-Play Integrations](PLUG_AND_PLAY_INTEGRATIONS.md), [Model Provider Integration](MODEL_PROVIDER_INTEGRATION.md), [Unified Graph/Crew Runtime](UNIFIED_GRAPH_CREW_RUNTIME.md) and [Extension Contract](EXTENSION_CONTRACT.md).
