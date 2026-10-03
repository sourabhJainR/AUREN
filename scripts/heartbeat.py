#!/usr/bin/env python3
"""Run a safe local AUREN second-brain heartbeat.

This command only reads local JSON state and prints suggestions. It does not
send messages, mutate external systems, execute code, or change permissions.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".ai-harness" / "runtime" / "second_brain.py"
spec = importlib.util.spec_from_file_location("auren_second_brain", MODULE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load second-brain runtime")
second_brain = importlib.util.module_from_spec(spec)
spec.loader.exec_module(second_brain)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only AUREN second-brain heartbeat")
    parser.add_argument("--tasks", type=Path, default=ROOT / ".ai-harness" / "state" / "tasks.json")
    parser.add_argument("--outcomes", type=Path, default=ROOT / ".ai-harness" / "state" / "outcomes.json")
    parser.add_argument("--max-suggestions", type=int, default=5)
    args = parser.parse_args()

    tasks = json.loads(args.tasks.read_text(encoding="utf-8")) if args.tasks.exists() else []
    outcomes = json.loads(args.outcomes.read_text(encoding="utf-8")) if args.outcomes.exists() else []
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks", [])
    if isinstance(outcomes, dict):
        outcomes = outcomes.get("outcomes", [])
    suggestions = second_brain.heartbeat_suggestions(
        tasks=tasks if isinstance(tasks, list) else [],
        recent_outcomes=outcomes if isinstance(outcomes, list) else [],
        max_suggestions=args.max_suggestions,
    )
    print(json.dumps({"mode": "suggestion", "suggestions": suggestions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
