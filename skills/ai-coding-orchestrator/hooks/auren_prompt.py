#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook for AUREN."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return 0

    home = Path(os.environ.get("AUREN_HOME", Path.home() / ".auren")).expanduser()
    version = "unknown"
    try:
        active = json.loads((home / "active.json").read_text(encoding="utf-8"))
        version = str(active.get("version") or version)
    except (OSError, json.JSONDecodeError):
        pass

    context = (
        f"AUREN control plane is active (version {version}). "
        "For repository engineering work, use the AUREN skill "
        "`/adaptive-ai-coding-orchestrator:ai-coding-orchestrator` when appropriate. "
        "Follow repository instructions first; preserve user intent and scope; collect repository evidence before editing; "
        "make the smallest safe change; verify the result; and report evidence, verification, risks, and incomplete checks. "
        "For investigation/RCA without an explicit fix request, diagnose without editing. "
        "Do not claim commands or tests were run unless observed."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
