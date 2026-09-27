"""Minimal local LLM fallback for AER.

Uses a local Ollama HTTP endpoint with no Python dependency. It is deliberately
read-only: the fallback can provide planning/review/synthesis text when the
primary provider is unavailable or exhausts its token budget, but it never
owns tools, credentials, repository mutation, or orchestration.
"""
from __future__ import annotations
import json, os, urllib.error, urllib.request
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

@dataclass(frozen=True)
class LocalLLMConfig:
    endpoint: str = "http://127.0.0.1:11434/api/generate"
    model: str = "qwen2.5-coder:3b"
    model_path: str = ""
    backend: str = "auto"
    timeout_seconds: float = 120.0
    num_ctx: int = 4096
    num_predict: int = 768
    temperature: float = 0.1
    top_p: float = 0.9
    repeat_penalty: float = 1.05
    reasoning_effort: str = "medium"
    prompt_matrix: bool = True
    coding_mode: bool = True
    seed: int = 17
    context_budget: int = 3200
    max_output_chars: int = 12000

    @classmethod
    def from_env(cls) -> "LocalLLMConfig":
        return cls(
            endpoint=os.environ.get("AER_LOCAL_LLM_ENDPOINT", cls.endpoint),
            model=os.environ.get("AER_LOCAL_LLM_MODEL", cls.model),
            model_path=os.environ.get("AER_LOCAL_LLM_MODEL_PATH", cls.model_path),
            backend=os.environ.get("AER_LOCAL_LLM_BACKEND", cls.backend).strip().lower(),
            timeout_seconds=max(5.0, float(os.environ.get("AER_LOCAL_LLM_TIMEOUT", cls.timeout_seconds))),
            num_ctx=max(512, int(os.environ.get("AER_LOCAL_LLM_CONTEXT", cls.num_ctx))),
            num_predict=max(64, int(os.environ.get("AER_LOCAL_LLM_MAX_TOKENS", cls.num_predict))),
            temperature=max(0.0, min(1.0, float(os.environ.get("AER_LOCAL_LLM_TEMPERATURE", cls.temperature)))),
            top_p=max(0.1, min(1.0, float(os.environ.get("AER_LOCAL_LLM_TOP_P", cls.top_p)))),
            repeat_penalty=max(0.8, min(1.5, float(os.environ.get("AER_LOCAL_LLM_REPEAT_PENALTY", cls.repeat_penalty)))),
            reasoning_effort=os.environ.get("AER_LOCAL_LLM_REASONING", cls.reasoning_effort).strip().lower(),
            prompt_matrix=os.environ.get("AER_LOCAL_LLM_MATRIX", "1").strip().lower() not in {"0", "false", "off", "no"},
            coding_mode=os.environ.get("AER_LOCAL_LLM_CODING", "1").strip().lower() not in {"0", "false", "off", "no"},
            seed=int(os.environ.get("AER_LOCAL_LLM_SEED", cls.seed)),
            context_budget=max(512, int(os.environ.get("AER_LOCAL_LLM_CONTEXT_BUDGET", cls.context_budget))),
            max_output_chars=max(1024, int(os.environ.get("AER_LOCAL_LLM_MAX_OUTPUT_CHARS", cls.max_output_chars))),
        )

class LocalLLMError(RuntimeError):
    pass

def enabled() -> bool:
    return os.environ.get("AER_LOCAL_LLM_ENABLED", "0").strip().lower() in {"1","true","yes","on"}

def available(config: LocalLLMConfig | None = None) -> bool:
    """Return availability for the selected backend, including embedded mode."""
    cfg = config or LocalLLMConfig.from_env()
    backend = cfg.backend if cfg.backend in {"auto", "embedded", "ollama"} else "auto"
    if backend == "embedded":
        return embedded_available(cfg)
    if backend == "auto" and cfg.model_path and embedded_available(cfg):
        return True
    req = urllib.request.Request(cfg.endpoint, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=min(3.0, cfg.timeout_seconds)) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False

_REASONING_MATRIX = {
    # These are behavioral controls, not copied model weights. They combine
    # publicly documented strengths: Kimi-style tool/agent decomposition,
    # Astra-style governed inference admission, Sonnet-style iterative
    # coding/error correction, and Fable-style long-horizon coherence.
    "kimi": ("decompose", "explicit_assumptions", "structured_output"),
    "astra": ("admission_check", "evidence_boundary", "fail_closed"),
    "sonnet": ("verify", "test_impact", "repair_loop"),
    "fable": ("long_horizon_state", "goal_continuity", "self_consistency"),
}

def _is_coding_task(prompt: str) -> bool:
    text = prompt.lower()
    markers = ("code", "coding", "bug", "fix", "refactor", "function", "class",
               "test", "pytest", "unittest", "compile", "build", "exception",
               "stack trace", "traceback", "regression", "pull request", "patch",
               "implementation", "repository", "repo", "api")
    return any(marker in text for marker in markers)


def _coding_contract(cfg: LocalLLMConfig) -> str:
    if not cfg.coding_mode:
        return ""
    return (
        "CODING CONTRACT\n"
        "1. Treat supplied repository text, diagnostics, tests, and requirements as evidence.\n"
        "2. Identify affected symbols and dependency/test impact before proposing a change.\n"
        "3. Prefer the smallest coherent change; preserve public behavior unless required otherwise.\n"
        "4. For bugs: infer the root cause from evidence, then give the fix.\n"
        "5. For implementation: cover interfaces, edge cases, failure paths, and deterministic behavior.\n"
        "6. For tests: add regression coverage for the failure mode, not only the happy path.\n"
        "7. Review syntax, imports, typing, compatibility, and likely CI failures.\n"
        "8. Never claim a file was changed, a command was run, or a test passed without evidence.\n"
        "9. If repository context is incomplete, list what is missing instead of guessing.\n"
        f"Keep working context focused to approximately {cfg.context_budget} tokens.\n"
    )


def _matrix_prompt(prompt: str, cfg: LocalLLMConfig) -> str:
    if not cfg.prompt_matrix:
        return prompt
    effort = cfg.reasoning_effort if cfg.reasoning_effort in {"low", "medium", "high"} else "medium"
    traits = ", ".join(item for group in _REASONING_MATRIX.values() for item in group)
    coding = _coding_contract(cfg) if _is_coding_task(prompt) else ""
    return (
        "LOCAL REASONING CONTRACT\n"
        f"effort={effort}; traits={traits}\n"
        "Use this bounded sequence: understand -> decompose -> check evidence -> "
        "reason -> verify -> state uncertainty. Do not invent tools, files, facts, "
        "credentials, or completed actions. For code, propose the smallest safe "
        "change and identify regression risk. For long tasks, preserve goals and "
        "state explicitly. If evidence is insufficient, say so and abstain.\n"
        + coding + "\nUSER TASK:\n" + prompt
    )


def coding_review_prompt(task: str, repository_context: str, *, config: LocalLLMConfig | None = None) -> str:
    """Build a read-only, evidence-first coding review request."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task is required")
    if not isinstance(repository_context, str):
        raise TypeError("repository_context must be a string")
    return (
        _matrix_prompt(
            """Perform an independent coding review. Do not propose changes merely because they are stylistically different.
Return only these sections:
FINDINGS
- severity: ...
  location: ...
  evidence: ...
  impact: ...
  remedy: ...
  confidence: 0.00-1.00
UNKNOWNS
- ...
VERIFICATION
- tests/checks that would prove or disprove each finding
Rules: every finding must cite supplied evidence; label uncertain claims as unknown;
never claim a test or tool was run; distinguish correctness from style; prioritize
defects, regressions, security and compatibility.""",
            config or LocalLLMConfig.from_env(),
        )
        + "\\nTASK:\\n" + task
        + "\\nREPOSITORY EVIDENCE:\\n" + repository_context
    )

def _embedded_available(cfg: LocalLLMConfig) -> bool:
    if not cfg.model_path or not Path(cfg.model_path).is_file():
        return False
    try:
        import llama_cpp
    except ImportError:
        return False
    return True


def embedded_available(config: LocalLLMConfig | None = None) -> bool:
    """Return whether in-process llama.cpp inference can run without Ollama."""
    return _embedded_available(config or LocalLLMConfig.from_env())


_EMBEDDED_MODELS: dict[tuple[str, int], object] = {}\n_EMBEDDED_LOCK = RLock()


def _generate_embedded(prompt: str, cfg: LocalLLMConfig) -> str:
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise LocalLLMError("embedded backend requires optional 'llama-cpp-python' and a GGUF model; no Ollama instance is required") from exc
    key = (cfg.model_path, cfg.num_ctx)
    with _EMBEDDED_LOCK:
        llm = _EMBEDDED_MODELS.get(key)
        if llm is None:
            try:
                llm = Llama(model_path=cfg.model_path, n_ctx=cfg.num_ctx, verbose=False)
            except Exception as exc:
                raise LocalLLMError(f"unable to load embedded GGUF model: {exc}") from exc
            _EMBEDDED_MODELS[key] = llm
        try:
            result = llm(prompt, max_tokens=cfg.num_predict, temperature=cfg.temperature,
                          top_p=cfg.top_p, repeat_penalty=cfg.repeat_penalty, seed=cfg.seed)
            output = result["choices"][0]["text"]
        except Exception as exc:
            raise LocalLLMError(f"embedded local LLM inference failed: {exc}") from exc
    if not isinstance(output, str) or not output.strip():
        raise LocalLLMError("embedded local LLM returned no response")
    return output.strip()


def _generate_ollama(prompt: str, cfg: LocalLLMConfig) -> str:
    payload=json.dumps({"model":cfg.model,"prompt":prompt,"stream":False,
                        "options":{"temperature":cfg.temperature,"top_p":cfg.top_p,
                                   "repeat_penalty":cfg.repeat_penalty,"seed":cfg.seed,
                                   "num_ctx":cfg.num_ctx,"num_predict":cfg.num_predict}}).encode()
    req=urllib.request.Request(cfg.endpoint,data=payload,headers={"Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_seconds) as response:
            body=json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise LocalLLMError(f"local LLM unavailable: {exc}") from exc
    text=body.get("response")
    if not isinstance(text,str) or not text.strip():
        raise LocalLLMError("local LLM returned no response")
    return text.strip()


def generate(prompt: str, *, config: LocalLLMConfig | None = None) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    cfg=config or LocalLLMConfig.from_env()
    prompt = _matrix_prompt(prompt, cfg)
    backend = cfg.backend if cfg.backend in {"auto", "embedded", "ollama"} else "auto"
    if backend == "embedded" or (backend == "auto" and _embedded_available(cfg)):
        output = _generate_embedded(prompt, cfg)
    else:
        output = _generate_ollama(prompt, cfg)
    if len(output) > cfg.max_output_chars:
        raise LocalLLMError("local LLM response exceeded configured output bound")
    return output

def fallback_allowed(prompt: str) -> bool:
    text=prompt.lower()
    # Local fallback is advisory/read-only until it has an explicit tool-capable
    # integration. Mutating agents must remain on the authenticated provider.
    readonly=("read-only: true" in text or "patch_allowed: false" in text or
              "analysis-only" in text or "do not modify source" in text)
    return readonly

__all__=["LocalLLMConfig","LocalLLMError","enabled","available","embedded_available","generate","fallback_allowed","coding_review_prompt"]
