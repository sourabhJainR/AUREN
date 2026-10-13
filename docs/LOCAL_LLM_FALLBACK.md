# Minimal local LLM fallback

AUREN can use a small local Ollama model as a bounded backup when the configured
provider is unavailable, transiently fails, or exhausts its context/token budget.

The local model is deliberately an advisory/read-only fallback. It does not
receive provider credentials, tool execution authority, merge authority, or
repository mutation permissions. Mutating builder turns remain on the primary
provider path until a future tool-capable local lane is separately validated.

## Enable

Install Ollama separately and make a small model available locally, for example:

```bash
ollama pull qwen2.5:3b
```

Then enable fallback:

```bash
export AUREN_LOCAL_LLM_ENABLED=1
export AUREN_LOCAL_LLM_MODEL=qwen2.5:3b
```

Defaults are intentionally conservative:

- endpoint: `http://127.0.0.1:11434/api/generate`
- context: 4,096 tokens
- output: 768 tokens
- temperature: 0.1
- timeout: 120 seconds

Override with `AUREN_LOCAL_LLM_ENDPOINT`, `AUREN_LOCAL_LLM_CONTEXT`,
`AUREN_LOCAL_LLM_MAX_TOKENS`, `AUREN_LOCAL_LLM_TIMEOUT`, and
`AUREN_LOCAL_LLM_TEMPERATURE`.

## Routing

```
Primary provider
      |
      +-- success --------------------> existing AUREN flow
      |
      +-- transient / token failure
                    |
                    v
             bounded retries
                    |
                    +-- success ------> existing AUREN flow
                    |
                    +-- exhausted ----> local LLM (read-only only)
```

The fallback is still subject to prompt validation, compaction, and the
existing graph/review/evidence path. A successful local response is evidence
for the next stage, not authority to mutate the repository.

## Safety boundary

The local lane is allowed only for prompts explicitly marked read-only,
analysis-only, or `patch_allowed: false`. If a builder/mutating turn fails,
AUREN returns the original provider failure rather than silently converting it
into an unauthenticated local mutation path.
