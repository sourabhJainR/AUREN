"""Deterministic Arena fixture with opt-in container-isolation probes."""
import json
import os
import socket
import sys

task = json.loads(sys.stdin.read())
prompt = task.get("prompt", "")
if prompt == "What is 7 + 8?":
    result = 15
elif prompt == "__probe__:unprivileged":
    result = hasattr(os, "geteuid") and os.geteuid() == 65534
elif prompt == "__probe__:root-read-only":
    result = False
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split()
                if len(fields) > 6 and fields[4] == "/" and "ro" in fields[5].split(","):
                    result = True
                    break
    except OSError:
        result = False
elif prompt == "__probe__:tmpfs-hardening":
    result = False
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split()
                if len(fields) > 6 and fields[4] == "/tmp":
                    options = set(fields[5].split(","))
                    separator = fields.index("-")
                    options.update(fields[separator + 3].split(","))
                    result = {"noexec", "nosuid", "nodev"}.issubset(options)
                    break
    except (OSError, ValueError, IndexError):
        result = False
elif prompt == "__probe__:network-disabled":
    result = False
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=0.5):
            result = False
    except OSError:
        result = True
else:
    result = {"abstain": True}
print(json.dumps(result))
