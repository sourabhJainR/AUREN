#!/usr/bin/env python3
"""Provider launcher that retries interrupted model responses until completion.

This layer sits outside the security gate: every attempt is still executed by
safe_provider.py, so retries cannot bypass provider validation or permissions.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TRANSIENT_MARKERS = (
    "response stopped arriving",
    "response stopped",
    "api error",
    "connection reset",
    "connection closed",
    "connection error",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "service unavailable",
    "internal server error",
    "overloaded",
    "rate limit",
    "stream disconnected",
    "stream interrupted",
)

# Provider-neutral terminal markers. A normal successful run should contain at
# least one of these when using the configured streaming formats.
COMPLETION_MARKERS = (
    '"type":"result"',
    '"type": "result"',
    '"type":"task_complete"',
    '"type": "task_complete"',
    '"subtype":"success"',
    '"subtype": "success"',
    '"status":"completed"',
    '"status": "completed"',
    '"status":"success"',
    '"status": "success"',
)


def looks_transient(output: str, return_code: int) -> bool:
    text = output.lower()
    return return_code != 0 and any(marker in text for marker in TRANSIENT_MARKERS)


def looks_complete(output: str) -> bool:
    text = output.lower()
    return any(marker in text for marker in COMPLETION_MARKERS)


def continuation_prompt(original: str, previous_output: str, attempt: int) -> str:
    # Keep the continuation context bounded. The provider has its own context
    # window; this is only a recovery bridge, not a second memory store.
    tail = previous_output[-24000:]
    return (
        original
        + "\n\n# AUREN RESPONSE RECOVERY\n"
        + f"Attempt {attempt} is continuing a response that was interrupted before completion.\n"
        + "Do not restart completed work. Continue from the last confirmed state, "
          "preserve all correct conclusions and finish the requested response.\n"
        + "Previous partial provider output follows:\n---\n"
        + tail
        + "\n---\n"
        + "Return the complete remaining response. Do not report that the previous "
          "response was interrupted unless it is relevant to the task.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Resilient AUREN provider launcher")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("HARNESS_PROVIDER_MAX_RETRIES", "4")))
    parser.add_argument("--retry-delay", type=float, default=float(os.environ.get("HARNESS_PROVIDER_RETRY_DELAY", "1.0")))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("No provider command supplied", file=sys.stderr)
        return 2

    prompt_path = Path(args.prompt_file).resolve()
    original = prompt_path.read_text(encoding="utf-8")
    run_dir = Path(os.environ.get("HARNESS_RUN_DIR", str(prompt_path.parent))).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript = ""
    max_retries = max(0, min(10, args.max_retries))
    delay = max(0.0, min(30.0, args.retry_delay))

    for attempt in range(max_retries + 1):
        effective = original if attempt == 0 else continuation_prompt(original, transcript, attempt)
        temp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix="resilient-prompt-", suffix=".md", dir=run_dir)
            os.close(fd)
            temp_path = Path(name)
            temp_path.write_text(effective, encoding="utf-8")

            cmd = [sys.executable, str(Path(__file__).resolve().with_name("safe_provider.py")), "--prompt-file", str(temp_path), "--", *command]
            env = os.environ.copy()
            env["HARNESS_PROVIDER_ATTEMPT"] = str(attempt + 1)
            process = subprocess.Popen(
                cmd,
                cwd=Path(os.environ.get("HARNESS_WORKSPACE", str(Path(__file__).resolve().parent.parent))),
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
                return 0
            if not looks_transient(output, code) or attempt >= max_retries:
                return code

            print(
                f"AUREN provider response interrupted; continuing attempt {attempt + 2}/{max_retries + 1}",
                file=sys.stderr,
            )
            if delay:
                time.sleep(min(30.0, delay * (2 ** attempt)))
        except OSError as exc:
            print(f"AUREN resilient provider error: {exc}", file=sys.stderr)
            if attempt >= max_retries:
                return 127
            if delay:
                time.sleep(min(30.0, delay * (2 ** attempt)))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
