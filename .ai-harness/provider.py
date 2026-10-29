#!/usr/bin/env python3
"""Provider bridge with live tool telemetry and interruptible turn control."""
from __future__ import annotations

import argparse
import ast
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from runtime.agent_turn import (
    AgentTurnStateMachine,
    CACHE_PREFIX,
    INTERRUPT_REPAIR_EXIT,
    INTERRUPT_STOP_EXIT,
    OBSERVATION_PREFIX,
    USAGE_PREFIX,
)
from security_gate import SecurityGateError, safe_environment, validate_prompt_file, validate_provider_command
from portable.agent_execution_guard import AgentAssignmentScopeDenied, deny_unscoped_subprocess_for_assigned_agent


def is_analysis_only(prompt: str) -> bool:
    text = prompt.lower()
    markers = (
        "rca analysis-only", "root cause analysis only", "find the root cause",
        "root cause analysis", "do not implement a fix", "do not modify source",
        "patch_allowed: false", "diagnose only",
    )
    return any(marker in text for marker in markers)


def analysis_only_command(command: list[str]) -> list[str]:
    if not command:
        return command
    name = Path(command[0]).name.lower()
    result = list(command)
    if name == "claude":
        result[1:1] = ["--permission-mode", "plan"]
    elif name == "codex" and len(result) >= 2 and result[1] == "exec":
        result[2:2] = ["--sandbox", "read-only"]
    elif name == "gemini":
        result[1:1] = ["--approval-mode", "plan"]
    return result


def provider_name(command: list[str]) -> str:
    return Path(command[0]).name.lower() if command else "unknown"


def streaming_command(command: list[str]) -> list[str]:
    name = provider_name(command)
    result = list(command)
    if name == "claude" and "--output-format" not in result:
        result += ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    elif name == "gemini" and "--output-format" not in result:
        result += ["--output-format", "stream-json"]
    elif name == "codex" and "--json" not in result:
        result += ["--json"]
    return result


def prompt_context(prompt: str) -> tuple[list[str], str | None]:
    marker = "## IO-aware context\n"
    if marker not in prompt:
        return [], None
    try:
        value = ast.literal_eval(prompt.split(marker, 1)[1].strip())
    except (SyntaxError, ValueError):
        return [], None
    if not isinstance(value, dict):
        return [], None
    pages = value.get("pages", [])
    return ([str(item) for item in pages] if isinstance(pages, list) else [], str(value.get("context_digest")) if value.get("context_digest") else None)


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _event_payload(row: dict[str, Any]) -> dict[str, Any]:
    event = row.get("event")
    return event if isinstance(event, dict) else row


def _event_type(row: dict[str, Any]) -> str:
    payload = _event_payload(row)
    return str(_first(payload, "type", "event", "kind") or row.get("type", "")).lower()


def _stable_digest(value: Any) -> str:
    import hashlib
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_tool_event(row: dict[str, Any], sequence: int) -> dict[str, Any] | None:
    payload = _event_payload(row)
    event = _event_type(row)
    block = payload.get("content_block") if isinstance(payload.get("content_block"), dict) else payload
    item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    tool = _first(block, "tool_name", "tool", "name", "function_name") or _first(item, "tool_name", "tool", "name", "function_name") or _first(payload, "tool_name", "tool", "name", "function_name")
    if not tool and event in {"tool_use", "tool_call", "function_call", "mcp_call", "apply_patch", "content_block_start"}:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool = block.get("name")
        elif event != "content_block_start":
            tool = event
    if not tool:
        return None
    status = _first(payload, "status", "state")
    if not status:
        if event in {"tool_result", "tool_completed", "tool_call_completed", "mcp_call_completed", "content_block_stop"}:
            status = "completed"
        elif event in {"tool_error", "tool_failed", "mcp_call_failed"}:
            status = "failed"
        else:
            status = "started"
    result = _first(payload, "result", "output", "content", "tool_output")
    error = _first(payload, "error", "error_message")
    call_id = _first(payload, "call_id", "tool_call_id", "id")
    metadata = {"provider_event": event}
    if call_id:
        metadata["call_id"] = str(call_id)
    arguments = _first(payload, "arguments", "input")
    if arguments is not None:
        metadata["arguments_digest"] = _stable_digest(arguments)
    return {"sequence": sequence, "tool": str(tool), "status": str(status), "duration_ms": float(_first(payload, "duration_ms", "duration") or 0), "result_digest": _stable_digest(result) if result is not None else "", "error": str(error) if error else None, "metadata": metadata}


def normalize_usage(row: dict[str, Any]) -> dict[str, Any] | None:
    payload = _event_payload(row)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else row.get("usage")
    if not isinstance(usage, dict):
        return None
    aliases = {
        "input_tokens": ("input_tokens", "input_token_count"),
        "output_tokens": ("output_tokens", "output_token_count"),
        "cached_input_tokens": ("cached_input_tokens", "cache_read_input_tokens", "cached_content_token_count", "cached_tokens"),
        "reasoning_tokens": ("reasoning_tokens", "thoughts_token_count"),
        "total_tokens": ("total_tokens", "total_token_count"),
    }
    if not any(any(name in usage for name in names) for names in aliases.values()):
        return None
    return {target: int(next((usage[name] for name in names if usage.get(name) is not None), 0) or 0) for target, names in aliases.items()}


def normalize_cache(row: dict[str, Any]) -> dict[str, Any] | None:
    payload = _event_payload(row)
    cache = payload.get("cache") if isinstance(payload.get("cache"), dict) else row.get("cache")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else row.get("usage")
    if isinstance(usage, dict) and any(k in usage for k in ("cache_read_input_tokens", "cache_creation_input_tokens")):
        return {"hit": int(usage.get("cache_read_input_tokens", 0) or 0) > 0, "read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0), "write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0), "cache_key": None, "source": "provider-usage"}
    if not isinstance(cache, dict):
        cache = payload
    if not any(key in cache for key in ("hit", "cache_hit", "cached_tokens", "cached_content_token_count", "cache_key")):
        return None
    hit = _first(cache, "hit", "cache_hit")
    return {"hit": bool(hit) if hit is not None else None, "read_tokens": int(_first(cache, "read_tokens", "cached_tokens", "cached_content_token_count") or 0), "write_tokens": int(_first(cache, "write_tokens", "cache_write_tokens") or 0), "cache_key": str(_first(cache, "cache_key", "prompt_cache_key")) if _first(cache, "cache_key", "prompt_cache_key") else None, "source": "provider"}


def transcript_text(row: dict[str, Any]) -> str:
    event = _event_type(row)
    if event in {"tool_use", "tool_call", "tool_result", "tool_completed", "tool_failed", "content_block_start", "content_block_stop"}:
        return ""
    payload = _event_payload(row)
    delta = payload.get("delta") if isinstance(payload.get("delta"), dict) else {}
    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
        return delta["text"]
    for key in ("text", "result", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("text")]
        if parts:
            return "".join(parts)
    message = payload.get("message")
    if isinstance(message, dict):
        return transcript_text(message)
    return ""


def interrupt_provider(process: subprocess.Popen[str], turn: AgentTurnStateMachine, decision: dict[str, Any], run_dir: Path, phase: str) -> int:
    """Terminate the provider after the current observable event; no later tool/model step is allowed."""
    action = decision.get("action")
    exit_code = INTERRUPT_REPAIR_EXIT if action == "repair" else INTERRUPT_STOP_EXIT
    interrupt = {"phase": phase, "turn_id": turn.turn.turn_id, "action": action, "reason": decision.get("reason"), "utility": decision.get("utility"), "observed_tool_calls": decision.get("observed_tool_calls"), "exit_code": exit_code, "interrupted": True}
    (run_dir / "live-interrupt.json").write_text(json.dumps(interrupt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic AI CLI provider bridge")
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("No provider command supplied", file=sys.stderr)
        return 2

    try:
        deny_unscoped_subprocess_for_assigned_agent()
    except AgentAssignmentScopeDenied as exc:
        print(f"SECURITY GATE: {exc}", file=sys.stderr)
        return 78

    run_dir = Path(os.environ.get("HARNESS_RUN_DIR", str(Path(args.prompt_file).parent))).resolve()
    try:
        prompt_file = validate_prompt_file(Path(args.prompt_file), expected_root=run_dir)
        prompt = prompt_file.read_text(encoding="utf-8")
        analysis_only = is_analysis_only(prompt)
        validate_provider_command(command, analysis_only=analysis_only)
    except SecurityGateError as exc:
        print(f"SECURITY GATE: {exc}", file=sys.stderr)
        return 78

    if analysis_only:
        command = analysis_only_command(command)
        prompt = "RCA ANALYSIS-ONLY ENFORCEMENT\nDo not edit files, create patches, commit, merge, or perform destructive actions. Investigate deeply and return evidence-backed findings only. Separate facts, inferences, contradictions, unknowns, hypotheses, root cause, and follow-up.\n\n" + prompt

    command = streaming_command(command)
    phase = os.environ.get("HARNESS_PHASE", prompt_file.stem.replace(".prompt", ""))
    turn_id = os.environ.get("HARNESS_TURN_ID", f"{phase}-{int(time.time() * 1000)}")
    turn = AgentTurnStateMachine(phase, run_dir, turn_id)
    pages, context_digest = prompt_context(prompt)
    turn.transition("planning")
    turn.transition("acting")
    turn.set_context(pages, context_digest)

    max_tool_calls = int(os.environ.get("HARNESS_LIVE_MAX_TOOL_CALLS", "0") or 0)
    max_tokens = int(os.environ.get("HARNESS_LIVE_MAX_TOKENS", "0") or 0)
    min_progress_gain = float(os.environ.get("HARNESS_LIVE_MIN_PROGRESS_GAIN", "0.03") or 0.03)
    previous_utility: float | None = None
    raw_path = run_dir / f"{phase}.stream.jsonl"
    transcript: list[str] = []
    sequence = 0
    started = time.monotonic()
    interrupt_code: int | None = None
    try:
        workspace = run_dir.parents[2] if len(run_dir.parents) >= 3 else run_dir.parent
        kwargs: dict[str, Any] = {
            "cwd": workspace, "env": safe_environment(), "text": True,
            "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "bufsize": 1,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command + [prompt], **kwargs)
        with raw_path.open("w", encoding="utf-8") as raw_handle:
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip("\n")
                raw_handle.write(line + "\n")
                raw_handle.flush()
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    if line.strip():
                        transcript.append(line)
                    continue
                if not isinstance(row, dict):
                    continue

                tool = normalize_tool_event(row, sequence + 1)
                if tool:
                    sequence += 1
                    if turn.turn.state == "acting":
                        turn.transition("observing")
                    turn.observe_tool(tool)
                    if tool["status"] in {"completed", "failed", "error"}:
                        decision = turn.decide_live(event="tool_result", max_tool_calls=max_tool_calls, max_tokens=max_tokens, min_progress_gain=min_progress_gain, previous_utility=previous_utility)
                        previous_utility = decision.get("utility")
                        turn.transition("deciding")
                        if decision["action"] == "repair":
                            turn.transition("repairing")
                            interrupt_code = interrupt_provider(process, turn, decision, run_dir, phase)
                            turn.finish("stopped")
                            break
                        if decision["action"] == "stop":
                            interrupt_code = interrupt_provider(process, turn, decision, run_dir, phase)
                            turn.finish("stopped")
                            break
                        turn.transition("acting")
                    else:
                        turn.transition("acting")

                usage = normalize_usage(row)
                if usage:
                    turn.observe_usage(prompt, USAGE_PREFIX + json.dumps(usage))
                    if max_tokens > 0 and turn.turn.usage.total_tokens >= max_tokens and turn.turn.state == "acting":
                        decision = turn.decide_live(event="usage", max_tool_calls=max_tool_calls, max_tokens=max_tokens, min_progress_gain=min_progress_gain, previous_utility=previous_utility)
                        previous_utility = decision.get("utility")
                        turn.transition("deciding")
                        interrupt_code = interrupt_provider(process, turn, decision, run_dir, phase)
                        turn.finish("stopped")
                        break
                cache = normalize_cache(row)
                if cache:
                    turn.observe_cache(CACHE_PREFIX + json.dumps(cache), context_digest)
                text = transcript_text(row)
                if text:
                    transcript.append(text)
        code = interrupt_code if interrupt_code is not None else process.wait()
    except OSError as exc:
        if turn.turn.state not in {"failed", "completed", "stopped"}:
            turn.transition("failed")
        turn.finish("failed")
        print(f"Unable to start provider: {exc}", file=sys.stderr)
        return 127

    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    if interrupt_code is None:
        if turn.turn.state == "acting":
            turn.transition("verifying")
        elif turn.turn.state == "observing":
            turn.transition("acting")
            turn.transition("verifying")
        if turn.turn.usage.estimated:
            turn.observe_usage(prompt, "\n".join(transcript))
        if not turn.turn.cache.provider_reported:
            turn.observe_cache("", context_digest)
        turn.decide(verification_score=1.0 if code == 0 else 0.0, evidence_score=1.0 if transcript else 0.0, uncertainty=0.0 if code == 0 else 0.6, regressions=0 if code == 0 else 1)
        turn.transition("deciding")
        turn.finish("completed" if code == 0 else "failed")

    source = run_dir / "agent-turns.jsonl"
    live_path = run_dir / "live-agent-turns.jsonl"
    if source.exists():
        source.replace(live_path)
    print("\n".join(transcript), end="" if not transcript else "\n")
    print(f"HARNESS_PROVIDER_ELAPSED_MS={elapsed_ms}", file=sys.stderr)
    if interrupt_code is not None:
        print(f"HARNESS_LIVE_INTERRUPT={json.dumps(turn.turn.decision, sort_keys=True)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
