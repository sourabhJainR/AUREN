"""Tiny deterministic policy used only by the real-container smoke test."""
import json
import sys

task = json.loads(sys.stdin.read())
prompt = task.get("prompt", "")
if prompt == "What is 7 + 8?":
    print(json.dumps(15))
else:
    print(json.dumps({"abstain": True}))
