#!/usr/bin/env python3
"""Run AUREN's 18-stage workflow on a real Git project in an isolated worktree."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portable.agent_capabilities import redact
from portable.vibe_coding_host import LocalVibeCodingHost, load_host_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="top-level Git checkout to work on")
    parser.add_argument("--intent", required=True, help="product/feature request for the 18-stage workflow")
    parser.add_argument("--workflow-id", required=True, help="stable ID used to resume this exact workflow")
    parser.add_argument("--config", help="private host config (default: ~/.auren/vibe-coding/host.json)")
    args = parser.parse_args()
    try:
        config = load_host_config(args.config)
        report = LocalVibeCodingHost(args.project, config=config).run(args.workflow_id, args.intent)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        # Avoid printing raw provider/tool output, which may contain sensitive content.
        message = redact(str(exc))[:1000]
        print(f"Vibe-coding workflow blocked: {type(exc).__name__}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
