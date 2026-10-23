#!/usr/bin/env python3
"""Security-gated provider entry point with compaction and feedback telemetry."""
from __future__ import annotations

import argparse
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from security_gate import SecurityGateError, safe_environment, validate_prompt_file, validate_provider_command
from provider import analysis_only_command, is_analysis_only
from runtime.prompting_policy import compose
from runtime.auto_compaction import compact
from runtime.feedback_loop import FeedbackLoop
from portable.local_llm import LocalLLMError, enabled as local_llm_enabled, fallback_allowed as local_llm_fallback_allowed, generate as local_llm_generate
from portable.identity_compat import get_auren_env
from portable.subprocess_provider import SubprocessProviderInvoker, _close_windows_job, _windows_job

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
    print("AER_LOCAL_LLM_FALLBACK=1", file=sys.stderr)  # legacy consumer marker
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
    budget = int(get_auren_env("CONTEXT_BUDGET_CHARS", "12000"))
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


def _provider_env(name: str, default: str) -> str:
    """Read canonical, documented unprefixed, then legacy provider settings."""
    canonical = f"AUREN_{name}"
    legacy = f"AER_{name}"
    if canonical in os.environ:
        return os.environ[canonical]
    if name in os.environ:
        return os.environ[name]
    if legacy in os.environ:
        return os.environ[legacy]
    return default


def _bounded_process_limits() -> tuple[float, int]:
    try:
        timeout = float(_provider_env("HARNESS_PROVIDER_TIMEOUT_SECONDS", "1800"))
        max_bytes = int(_provider_env("HARNESS_PROVIDER_MAX_OUTPUT_BYTES", "1000000"))
    except (TypeError, ValueError) as exc:
        raise ValueError("provider timeout/output limits must be numeric") from exc
    if not 1 <= timeout <= 3600:
        raise ValueError("HARNESS_PROVIDER_TIMEOUT_SECONDS must be between 1 and 3600")
    if not 1024 <= max_bytes <= 16000000:
        raise ValueError("HARNESS_PROVIDER_MAX_OUTPUT_BYTES must be between 1024 and 16000000")
    return timeout, max_bytes

def _provider_total_budget_seconds(*, now_epoch: float | None = None) -> float:
    """Return one shared retry budget, capped by an inherited supervisor deadline."""
    try:
        total = float(_provider_env("HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS", "3600"))
    except (TypeError, ValueError) as exc:
        raise ValueError("HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS must be numeric") from exc
    if not 1 <= total <= 7200:
        raise ValueError("HARNESS_PROVIDER_TOTAL_TIMEOUT_SECONDS must be between 1 and 7200")
    inherited = os.environ.get("AUREN_DEADLINE_EPOCH", "").strip()
    if not inherited:
        return total
    try:
        deadline_epoch = float(inherited)
    except (TypeError, ValueError) as exc:
        raise ValueError("AUREN_DEADLINE_EPOCH must be a Unix timestamp") from exc
    if not math.isfinite(deadline_epoch):
        raise ValueError("AUREN_DEADLINE_EPOCH must be finite")
    remaining = deadline_epoch - (time.time() if now_epoch is None else now_epoch)
    if remaining <= 0:
        raise TimeoutError("inherited supervisor deadline has already expired")
    return min(total, remaining)



def _bounded_process_output(command: list[str], *, cwd: Path, env: dict[str, str],
                            timeout_seconds: float, max_output_bytes: int,
                            output_sink=None) -> tuple[int, str]:
    """Run a provider bridge with a hard deadline, bounded output, and tree cleanup.

    Output is consumed in fixed-size binary chunks. The queue and retained output
    are bounded; a single giant line cannot force an unbounded readline allocation.
    The provider tree is terminated on timeout, overflow, and normal parent exit.
    """
    if not 0.05 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 0.05 and 3600")
    if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 16000000:
        raise ValueError("max_output_bytes must be between 1024 and 16000000")
    if output_sink is not None and not callable(output_sink):
        raise TypeError("output_sink must be callable")
    kwargs = {
        "cwd": cwd, "env": env, "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "bufsize": 0,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    elif os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
    process = subprocess.Popen(command, **kwargs)
    if os.name == "nt":
        _windows_job(process)
    chunks: queue.Queue = queue.Queue(maxsize=8)
    def pump_output() -> None:
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                chunks.put(chunk)
        except BaseException as exc:
            chunks.put(exc)
        finally:
            chunks.put(None)
    reader = threading.Thread(target=pump_output, name="auren-provider-output", daemon=True)
    reader.start()
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    stream_done = False
    leader_cleanup_started = False
    try:
        while not stream_done or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"provider bridge exceeded hard deadline of {timeout_seconds:g}s")
            if process.poll() is not None and not leader_cleanup_started:
                # The parent may exit while a descendant still holds stdout or
                # continues side effects. Kill the entire contained process tree.
                SubprocessProviderInvoker._kill(process)
                leader_cleanup_started = True
            try:
                item = chunks.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if item is None:
                stream_done = True
                continue
            if isinstance(item, BaseException):
                raise OSError("provider output reader failed") from item
            if len(output) + len(item) > max_output_bytes:
                raise ValueError(f"provider output exceeded {max_output_bytes}-byte limit")
            output.extend(item)
            if output_sink is not None:
                output_sink(item)
            elif hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(item)
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(item.decode("utf-8", errors="replace"))
                sys.stdout.flush()
        # The leader can exit while a detached descendant continues. Kill the
        # contained process tree before returning even on a successful exit.
        SubprocessProviderInvoker._kill(process)
        code = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return code, output.decode("utf-8", errors="replace")
    except BaseException:
        SubprocessProviderInvoker._kill(process)
        try:
            process.wait(timeout=3)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
            except OSError:
                pass
        raise
    finally:
        # Drain bounded queued chunks after termination so the reader can exit.
        if reader.is_alive():
            drain_deadline = time.monotonic() + 2
            while reader.is_alive() and time.monotonic() < drain_deadline:
                try:
                    chunks.get(timeout=0.05)
                except queue.Empty:
                    pass
        reader.join(timeout=1)
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        _close_windows_job(process)

def _run_resilient(command: list[str], prompt: str, run_dir: Path, feedback: FeedbackLoop) -> int:
    max_retries = max(0, min(8, int(os.environ.get("HARNESS_PROVIDER_MAX_RETRIES", "4"))))
    delay = max(0.0, min(30.0, float(os.environ.get("HARNESS_PROVIDER_RETRY_DELAY", "1.0"))))
    transcript = ""
    strategy = get_auren_env("STRATEGY", "provider-default")
    task_id = get_auren_env("TASK_ID", f"provider-{int(time.time())}")
    try:
        per_attempt_timeout, max_output_bytes = _bounded_process_limits()
        total_budget = _provider_total_budget_seconds()
    except TimeoutError as exc:
        feedback.observe(task_id=task_id, outcome="timeout", verified=False, strategy=strategy, evidence=[])
        print(f"AUREN provider hard deadline exceeded: {exc}", file=sys.stderr)
        return 124
    except ValueError as exc:
        feedback.observe(task_id=task_id, outcome="output_limit_or_invalid_config", verified=False, strategy=strategy, evidence=[])
        print(f"AUREN provider output/configuration rejected: {exc}", file=sys.stderr)
        return 125
    total_deadline = time.monotonic() + total_budget
    last_digest = ""

    for attempt in range(max_retries + 1):
        remaining = total_deadline - time.monotonic()
        if remaining <= 0.05:
            feedback.observe(task_id=task_id, outcome="timeout", verified=False, strategy=strategy, evidence=[last_digest] if last_digest else [])
            print("AUREN provider total retry budget exhausted", file=sys.stderr)
            return 124
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
                "AER_SANDBOX": "enforced-for-local-execution",  # legacy provider contract
                "AER_COMPACTION_DIGEST": compaction_meta["digest"],  # legacy provider contract
            })
            last_digest = compaction_meta["digest"]
            remaining = total_deadline - time.monotonic()
            if remaining <= 0.05:
                feedback.observe(task_id=task_id, outcome="timeout", verified=False, strategy=strategy, evidence=[last_digest])
                print("AUREN provider total retry budget exhausted before dispatch", file=sys.stderr)
                return 124
            try:
                code, output = _bounded_process_output(
                    [sys.executable, str(Path(__file__).resolve().with_name("provider.py")), "--prompt-file", str(effective_path), "--", *command],
                    cwd=Path(os.environ.get("HARNESS_WORKSPACE", Path(__file__).resolve().parent.parent)),
                    env=env, timeout_seconds=min(per_attempt_timeout, remaining), max_output_bytes=max_output_bytes,
                )
            except TimeoutError as exc:
                feedback.observe(task_id=task_id, outcome="timeout", verified=False, strategy=strategy, evidence=[compaction_meta["digest"]])
                print(f"AUREN provider hard deadline exceeded: {exc}", file=sys.stderr)
                return 124
            except ValueError as exc:
                feedback.observe(task_id=task_id, outcome="output_limit_or_invalid_config", verified=False, strategy=strategy, evidence=[compaction_meta["digest"]])
                print(f"AUREN provider output/configuration rejected: {exc}", file=sys.stderr)
                return 125
            transcript = (transcript + output)[-24000:]
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
            remaining = total_deadline - time.monotonic()
            if remaining <= 0:
                feedback.observe(task_id=task_id, outcome="timeout", verified=False, strategy=strategy, evidence=[last_digest])
                print("AUREN provider total retry budget exhausted before retry", file=sys.stderr)
                return 124
            if delay:
                time.sleep(min(30.0, delay * (2 ** attempt), remaining))
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
