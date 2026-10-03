# AUREN Provider Conformance Harness

The Provider Conformance Harness proves that AUREN preserves its orchestration contract across Claude, Codex, Gemini and ChatGPT surfaces.

## Commands

Static validation requires no provider credentials:

```bash
python scripts/provider_conformance.py
python scripts/provider_conformance.py --json
```

Optional live validation probes locally installed Claude, Codex and Gemini CLIs with a read-only sentinel task:

```bash
python scripts/provider_conformance.py --live
```

Use `--write-report` when a persistent JSON report is desired. Normal runs do not modify the repository.

## What is tested

1. Logical provider coverage and matrix schema.
2. Native instruction surfaces are present in the repository.
3. Explicit local execution transport where applicable.
4. ChatGPT is not incorrectly treated as a local CLI; its execution transport is MCP/app or Codex-in-ChatGPT.
5. Progressive/JIT context behavior is declared.
6. The normalized cross-provider evidence contract is complete.
7. Live provider probes, when explicitly enabled, distinguish unavailable, timeout, failure and successful execution.

## Parity principle

Provider parity does not mean identical prompts, text, tools or model behavior. It means the same AUREN semantics survive provider projection:

`intent -> boundaries -> acceptance -> capability plan -> context evidence -> tool observations -> verification evidence -> outcome`

A provider cannot claim verification merely because it exits successfully. AUREN remains the authority for scope, safety, evidence and acceptance.

## Live probe policy

The live probe is deliberately narrow. It must not edit files, install packages, change credentials, or perform external write actions. Full task conformance should be run in a controlled provider-enabled environment with provider-specific authentication and sandbox permissions.

## CI policy

The deterministic static harness is part of the normal AUREN release gate. Live provider tests are opt-in because CI environments may not have provider credentials, network access, or licensed CLIs.
