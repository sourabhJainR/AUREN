#!/usr/bin/env python3
"""Security-gated provider entry point with compaction and feedback telemetry."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from security_gate import SecurityGateError, safe_environment, validate_prompt_file, validate_provider_command
from provider import analysis_only_command, is_analysis_only
from runtime.prompting_policy import compose
from runtime.auto_compaction import compact
from runtime.feedback_loop import FeedbackLoop
from portable.local_llm import LocalLLMError, enabled as local_llm_enabled, fallback_allowed as local_llm_fallback_allowed, generate as local_llm_generate

_TRANSIENT_MARKERS = (
    "response stopped arriving", "response stopped", "api error", "connection reset",
    "connection closed", "connection error", "timed out", "timeout",
    "temporarily unavailable", "service unavailable", "internal server error",
    "overloaded", "rate limit", "stream disconnected", "stream interrupted",
    "context length", "maximum context", "too many tokens", "token limit", "max tokens",
    "model overloaded", "provider unavailable", "unable to connect",
)


def _transient_failure(output: str, return_code: int) -> bool:
    if return_code == 0:
        return False
    text = output.lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _local_fallback(prompt: str, output: str, return_code: int) -> int | None:
    """Use the minimal local model only for bounded read-only/advisory turns."""
    if not local_llm_enabled() or not local_llm_fallback_allowed(prompt):
        return None
    if not _transient_failure(output, return_code):
        return None
    try:
        recovered = local_llm_generate(
            prompt
            + "\n\n# AUREN LOCAL FALLBACK\n"
            + "The primary provider was unavailable or exhausted its budget. "
              "Answer only with evidence-backed, read-only findings. Do not edit files, "
              "run tools, commit, merge, or invent repository facts."
        )
    except LocalLLMError as exc:
        print(f"AUREN local LLM fallback unavailable: {exc}", file=sys.stderr)
        return None
    print("AUREN_LOCAL_LLM_FALLBACK=1", file=sys.stderr)
    print(recovered)
    return 0


def _continuation_prompt(original: str, previous: str, attempt: int) -> str:
    tail = previous[-24000:]
    return (
        original
        + "\n\n# AUREN RESPONSE RECOVERY\n"
        + f"Attempt {attempt} continues a response interrupted before completion.\n"
        + "Do not restart completed work. Continue from the last confirmed state and finish the requested response. Preserve correct prior conclusions.\n"
        + "Previous partial provider output:\n---\n"
        + tail
        + "\n---\n"
        + "Return the complete remaining response.\n"
    )


def _prepare_prompt(prompt: str, run_dir: Path) -> tuple[str, dict]:
    budget = int(os.environ.get("AUREN_CONTEXT_BUDGET_CHARS", "12000"))
    result = compact(prompt, budget_chars=budget)
    metadata = {
        "compacted": result.compacted,
        "original_chars": result.original_chars,
        "compacted_chars": result.compacted_chars,
        "removed_chars": result.removed_chars,
        "digest": result.digest,
    }
    if result.compacted:
        print(f"AUREN context compaction: {result.original_chars} -> {result.compacted_chars} chars", file=sys.stderr)
    return compose(result.text), metadata


def _run_resilient(command: list[str], prompt: str, run_dir: Path, feedback: FeedbackLoop) -> int:
    max_retries = max(0, min(8, int(os.environ.get("HARNESS_PROVIDER_MAX_RETRIES", "4"))))
    delay = max(0.0, min(30.0, float(os.environ.get("HARNESS_PROVIDER_RETRY_DELAY", "1.0"))))
    transcript = ""
    strategy = os.environ.get("AUREN_STRATEGY", "provider-default")
    task_id = os.environ.get("AUREN_TASK_ID", f"provider-{int(time.time())}")

    for attempt in range(max_retries + 1):
        effective = prompt if attempt == 0 else _continuation_prompt(prompt, transcript, attempt)
        effective_prompt, compaction_meta = _prepare_prompt(effective, run_dir)
        fd, effective_name = tempfile.mkstemp(prefix="effective-prompt-", suffix=".md", dir=run_dir)
        os.close(fd)
        effective_path = Path(effective_name)
        try:
            effective_path.write_text(effective_prompt, encoding="utf-8")
            env = safe_environment()
            env.update({
                "HARNESS_PROVIDER_ATTEMPT": str(attempt + 1),
                "HARNESS_SECURITY_GATE": "enforced",
                "HARNESS_PROMPT_ROOT": str(run_dir),
                "HARNESS_CANONICAL_PROMPT": str(Path(os.environ.get("HARNESS_CANONICAL_PROMPT", effective_path)).resolve()),
                "HARNESS_LIVE_MIN_PROGRESS_GAIN": "-1",
                "AUREN_SANDBOX": "enforced-for-local-execution",
                "AUREN_COMPACTION_DIGEST": compaction_meta["digest"],
            })
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve().with_name("provider.py")), "--prompt-file", str(effective_path), "--", *command],
                cwd=Path(os.environ.get("HARNESS_WORKSPACE", Path(__file__).resolve().parent.parent)),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            output_parts: list[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                output_parts.append(line)
                sys.stdout.write(line)
                sys.stdout.flush()
            code = process.wait()
            output = "".join(output_parts)
            transcript += output
            if code == 0:
                feedback.observe(task_id=task_id, outcome="success", verified=False, strategy=strategy, evidence=[compaction_meta["digest"]])
                return 0
            if not _transient_failure(output, code) or attempt >= max_retries:
                fallback_code = _local_fallback(prompt, output, code)
                if fallback_code is not None:
                    feedback.observe(task_id=task_id, outcome="local_fallback", verified=False, strategy="local-llm", evidence=[compaction_meta["digest"]])
                    return fallback_code
                feedback.observe(task_id=task_id, outcome="failure", verified=False, strategy=strategy, evidence=[compaction_meta["digest"]])
                return code
            feedback.observe(task_id=task_id, outcome="transient_failure", verified=False, strategy=strategy, evidence=[compaction_meta["digest"]])
            print(f"AUREN provider response interrupted; continuing attempt {attempt + 2}/{max_retries + 1}", file=sys.stderr)
            if delay:
                time.sleep(min(30.0, delay * (2 ** attempt)))
        finally:
            try:
                effective_path.unlink(missing_ok=True)
            except OSError:
                pass
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Security-gated AI provider launcher")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("No provider command supplied", file=sys.stderr)
        return 2

    effective_prompt = None
    try:
        prompt_file = validate_prompt_file(
            Path(args.prompt_file),
            expected_root=Path(os.environ.get("HARNESS_RUN_DIR", Path(args.prompt_file).parent)).resolve(),
        )
        prompt = prompt_file.read_text(encoding="utf-8")
        analysis_only = is_analysis_only(prompt)
        validate_provider_command(command, analysis_only=analysis_only)
        if analysis_only:
            command = analysis_only_command(command)

        fd, effective_path = tempfile.mkstemp(prefix="effective-prompt-", suffix=".md", dir=prompt_file.parent)
        os.close(fd)
        effective_prompt = Path(effective_path)
        prepared, _ = _prepare_prompt(prompt, prompt_file.parent)
        effective_prompt.write_text(prepared, encoding="utf-8")

        env_run_dir = Path(os.environ.get("HARNESS_RUN_DIR", str(prompt_file.parent))).resolve()
        env_run_dir.mkdir(parents=True, exist_ok=True)
        feedback = FeedbackLoop(Path(os.environ.get("HARNESS_WORKSPACE", str(env_run_dir))))
        return _run_resilient(command, prompt, env_run_dir, feedback)
    except SecurityGateError as exc:
        print(f"SECURITY GATE: {exc}", file=sys.stderr)
        return 78
    except (OSError, UnicodeError) as exc:
        print(f"SECURITY GATE ERROR: {exc}", file=sys.stderr)
        return 78
    finally:
        if effective_prompt is not None:
            try:
                effective_prompt.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
