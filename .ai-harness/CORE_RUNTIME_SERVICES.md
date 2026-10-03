# AUREN Core Runtime Services

AUREN 20.x treats sandboxing, LSP/navigation, feedback and auto compaction as runtime services. The portable/plugin release authority is `.claude-plugin/plugin.json`.

## Sandbox

Run repository-local commands through:

```bash
python .ai-harness/runtime/tool_runner.py --workspace . -- <command> <args>
```

The runner uses `shell=False`, workspace confinement, a filtered environment, bounded output/time and Unix resource limits where supported. Network access is disabled by default at the sandbox policy boundary. The environment marker `AUREN_SANDBOX_NETWORK=disabled` is advisory; a VM/container is required when hostile code must be prevented from networking or reading host files.

## LSP

Start the fallback server:

```bash
python .ai-harness/runtime/lsp_server.py --workspace .
```

It speaks stdio JSON-RPC/LSP and provides `initialize`, `textDocument/documentSymbol`, `textDocument/definition` and `textDocument/references`. AUREN prefers a real language-specific server when one is available.

## Auto compaction

Every provider prompt is passed through:

```text
.ai-harness/runtime/auto_compaction.py
```

The default budget is 12,000 characters. Goal, boundaries, acceptance, security, current state, task contract, evidence, verification and risks receive priority. The compaction digest is propagated as `AUREN_COMPACTION_DIGEST`.

## Feedback loop

Provider attempts are recorded in:

```text
.ai-harness/learning/feedback-events.jsonl
```

Candidates are never activated by the feedback loop. A candidate needs repeated evidence, deterministic regression, safety validation, shadow evaluation, canary evaluation and monitoring before promotion.

## Provider boundary

`safe_provider.py` is the integration point. It compacts prompts before provider execution and records success, failure and transient-retry observations after provider attempts. Provider network access remains behind the existing security gate; the local command sandbox is a separate boundary for repository tools and tests.

## Invariants

- Security and permission policy outrank learned behavior.
- No unrestricted autonomous loop is introduced.
- Compaction must not discard protected acceptance/security/verification evidence when it fits within the configured budget.
- LSP fallback must never read outside the selected workspace.
- Learning can propose; regression and safety gates decide activation.
