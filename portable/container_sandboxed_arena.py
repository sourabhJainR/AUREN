"""Container-backed policy execution for the Independent Generalization Arena.

Unlike the subprocess-only runner, this adapter asks Docker/Podman to enforce
network, filesystem, privilege, process, memory, and CPU limits. The evaluator
keeps the manifest/oracle in the parent process and sends only one task JSON
object to the container's stdin. This still trusts the host kernel, container
engine, image supply chain, and operator; it is not proof of evaluator
independence or a substitute for a separately controlled evaluation host.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from .independent_arena import ArenaError, NonceLedger, run_campaign

_IMAGE_DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:([0-9a-f]{64})$")
_CONTAINER_RUNTIME_NAMES = {"docker", "podman"}
_MAX_TIMEOUT_SECONDS = 300.0


def _validate_inputs(
    container_runtime: str,
    image: str,
    policy_args: Sequence[str],
    timeout_seconds: float,
    policy_digest: str,
) -> tuple[str, ...]:
    if not isinstance(container_runtime, str) or not container_runtime.strip():
        raise ArenaError("container_runtime must name Docker or Podman")
    if Path(container_runtime).name not in _CONTAINER_RUNTIME_NAMES or any(ch.isspace() for ch in container_runtime):
        raise ArenaError("container_runtime must be a Docker or Podman executable, not a command string")
    if not isinstance(image, str) or not _IMAGE_DIGEST_RE.fullmatch(image):
        raise ArenaError("container_image must be pinned by an immutable @sha256:<64 hex> digest")
    if isinstance(policy_args, (str, bytes)) or not isinstance(policy_args, Sequence):
        raise ArenaError("policy_args must be a sequence of container command arguments")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in policy_args):
        raise ArenaError("policy_args must contain non-empty strings without NUL bytes")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ArenaError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise ArenaError(f"timeout_seconds must be greater than zero and at most {_MAX_TIMEOUT_SECONDS:g}")
    expected_policy_digest = "sha256:" + image.rsplit("@sha256:", 1)[1]
    if policy_digest != expected_policy_digest:
        raise ArenaError("policy_digest must match the pinned container image digest")
    return tuple(policy_args)


def _run_one_case(
    task: Mapping[str, str],
    *,
    container_runtime: str,
    image: str,
    policy_args: tuple[str, ...],
    timeout_seconds: float,
) -> Any:
    container_name = "auren-arena-" + uuid4().hex
    command = [
        container_runtime, "run", "--rm", "--interactive",
        "--name", container_name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=64",
        "--memory=256m",
        "--memory-swap=256m",
        "--cpus=1",
        "--user=65534:65534",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--env=PYTHONIOENCODING=utf-8",
        "--env=PYTHONUTF8=1",
        image,
        *policy_args,
    ]
    task_json = json.dumps(
        dict(task), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    try:
        completed = subprocess.run(
            command,
            input=task_json,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=float(timeout_seconds),
            check=False,
            shell=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired as exc:
        # The CLI process timing out does not guarantee that a daemon-side
        # container has stopped. Explicitly request forced removal by unique name.
        try:
            subprocess.run(
                [container_runtime, "rm", "--force", container_name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5.0,
                check=False,
                shell=False,
                close_fds=True,
            )
        except Exception:
            pass
        raise ArenaError("sandboxed policy exceeded its wall-clock timeout") from exc
    if completed.returncode != 0:
        raise ArenaError("sandboxed policy returned a non-zero exit status")
    try:
        return json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ArenaError("sandboxed policy did not return one valid JSON value") from exc


def run_campaign_container_sandboxed(
    sealed: Mapping[str, Any],
    *,
    container_image: str,
    policy_digest: str,
    run_nonce: str,
    nonce_ledger: NonceLedger,
    policy_args: Sequence[str] = (),
    container_runtime: str = "docker",
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Evaluate each case in a fresh, resource-limited container.

    The image must be pinned by digest and contain the policy entry point. No
    host volumes are mounted, networking is disabled, the root filesystem is
    read-only, and the policy runs as an unprivileged numeric UID/GID. The
    evaluator sends only case_id/domain/prompt to stdin and scores the output
    outside the container. Each case uses a fresh container and unique name.
    """
    args = _validate_inputs(
        container_runtime, container_image, policy_args, timeout_seconds, policy_digest
    )

    def policy(task: Mapping[str, str]) -> Any:
        return _run_one_case(
            task,
            container_runtime=container_runtime,
            image=container_image,
            policy_args=args,
            timeout_seconds=float(timeout_seconds),
        )

    return run_campaign(
        sealed,
        policy,
        policy_digest=policy_digest,
        run_nonce=run_nonce,
        nonce_ledger=nonce_ledger,
    )


__all__ = ["run_campaign_container_sandboxed"]
