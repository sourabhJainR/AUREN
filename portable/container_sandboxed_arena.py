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
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from .independent_arena import ArenaError, NonceLedger, run_campaign

_IMAGE_DIGEST_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._/:+-]*@sha256:([0-9a-f]{64})|sha256:([0-9a-f]{64}))$")
_CONTAINER_RUNTIME_NAMES = {"docker", "podman"}
_MAX_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_OUTPUT_BYTES = 65_536


def _validate_inputs(
    container_runtime: str,
    image: str,
    policy_args: Sequence[str],
    timeout_seconds: float,
    policy_digest: str,
) -> tuple[str, ...]:
    if not isinstance(container_runtime, str) or not container_runtime.strip():
        raise ArenaError("container_runtime must name Docker or Podman")
    runtime_basename = container_runtime.replace(chr(92), "/").rsplit("/", 1)[-1].lower()
    if runtime_basename.endswith(".exe"):
        runtime_basename = runtime_basename[:-4]
    if runtime_basename not in _CONTAINER_RUNTIME_NAMES:
        raise ArenaError("container_runtime must be a Docker or Podman executable, not a command string")
    match = _IMAGE_DIGEST_RE.fullmatch(image) if isinstance(image, str) else None
    if match is None:
        raise ArenaError("container_image must be pinned by an immutable @sha256:<64 hex> digest or local sha256:<64 hex> image ID")
    if isinstance(policy_args, (str, bytes)) or not isinstance(policy_args, Sequence):
        raise ArenaError("policy_args must be a sequence of container command arguments")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in policy_args):
        raise ArenaError("policy_args must contain non-empty strings without NUL bytes")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ArenaError("timeout_seconds must be a finite positive number")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > _MAX_TIMEOUT_SECONDS:
        raise ArenaError(f"timeout_seconds must be greater than zero and at most {_MAX_TIMEOUT_SECONDS:g}")
    image_digest = match.group(1) or match.group(2)
    expected_policy_digest = "sha256:" + image_digest
    if policy_digest != expected_policy_digest:
        raise ArenaError("policy_digest must match the pinned container image digest")
    return tuple(policy_args)


def _force_remove_container(container_runtime: str, container_name: str) -> None:
    """Best-effort cleanup after abnormal container-client termination."""
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
        # Cleanup is best-effort; the evaluation remains an error regardless.
        pass


def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
    """Close parent-side pipes after the reader/writer threads have stopped."""
    for stream in (process.stdin, process.stdout):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _run_one_case(
    task: Mapping[str, str],
    *,
    container_runtime: str,
    image: str,
    policy_args: tuple[str, ...],
    timeout_seconds: float,
    max_output_bytes: int,
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
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            shell=False,
            close_fds=True,
        )
    except OSError as exc:
        raise ArenaError("sandbox runtime could not be started") from exc

    output = bytearray()
    output_exceeded = threading.Event()
    stream_error: list[BaseException] = []

    def drain_stdout() -> None:
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    return
                remaining = max_output_bytes - len(output)
                if len(chunk) > remaining:
                    if remaining > 0:
                        output.extend(chunk[:remaining])
                    output_exceeded.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    return
                output.extend(chunk)
        except (OSError, ValueError) as exc:
            stream_error.append(exc)

    reader = threading.Thread(target=drain_stdout, daemon=True)
    reader.start()
    try:
        assert process.stdin is not None
        process.stdin.write(task_json.encode("utf-8"))
        process.stdin.close()
        process.wait(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired as exc:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        reader.join(timeout=1.0)
        _close_process_streams(process)
        _force_remove_container(container_runtime, container_name)
        raise ArenaError("sandboxed policy exceeded its wall-clock timeout") from exc
    except (BrokenPipeError, OSError) as exc:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        reader.join(timeout=1.0)
        _force_remove_container(container_runtime, container_name)
        raise ArenaError("sandbox runtime input stream failed") from exc
    except KeyboardInterrupt:
        # An interactive cancellation must not leave a detached container
        # running. Preserve the interrupt even when cleanup is unsuccessful.
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        reader.join(timeout=1.0)
        _force_remove_container(container_runtime, container_name)
        raise

    reader.join(timeout=1.0)
    _close_process_streams(process)
    if output_exceeded.is_set():
        _force_remove_container(container_runtime, container_name)
        raise ArenaError("sandboxed policy output exceeds configured byte limit")
    if reader.is_alive() or stream_error:
        _force_remove_container(container_runtime, container_name)
        raise ArenaError("sandboxed policy output stream could not be read")
    if process.returncode != 0:
        raise ArenaError("sandboxed policy returned a non-zero exit status")
    try:
        return json.loads(output.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
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
    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
) -> dict[str, Any]:
    """Evaluate each case in a fresh, resource-limited container.

    The image must be pinned by digest and contain the policy entry point. No
    host volumes are mounted, networking is disabled, the root filesystem is
    read-only, and the policy runs as an unprivileged numeric UID/GID. The
    evaluator sends only the prompt to stdin and scores the output
    outside the container. Each case uses a fresh container and unique name.
    """
    args = _validate_inputs(
        container_runtime, container_image, policy_args, timeout_seconds, policy_digest
    )
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ArenaError("max_output_bytes must be a positive integer")

    def policy(task: Mapping[str, str]) -> Any:
        return _run_one_case(
            task,
            container_runtime=container_runtime,
            image=container_image,
            policy_args=args,
            timeout_seconds=float(timeout_seconds),
            max_output_bytes=max_output_bytes,
        )

    return run_campaign(
        sealed,
        policy,
        policy_digest=policy_digest,
        run_nonce=run_nonce,
        nonce_ledger=nonce_ledger,
    )


__all__ = ["run_campaign_container_sandboxed"]
