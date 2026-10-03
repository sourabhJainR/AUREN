"""Local bounded workbench for offloading independent engineering work.

This is an additive execution layer. It does not replace TaskPlan, StateGraph,
GraphAgentTeam, AdaptiveRuntime, TriggerRuntime, or policy/verification gates.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence


EFFECTS = {"read_only", "mutating"}
DEFAULT_WORKERS = max(1, min(8, os.cpu_count() or 1))


@dataclass(frozen=True)
class WorkPacket:
    id: str
    mission_id: str
    title: str
    argv: tuple[str, ...]
    effect: str = "read_only"
    priority: int = 100
    dependencies: tuple[str, ...] = ()
    timeout_seconds: float = 300.0
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.mission_id or not self.title:
            raise ValueError("id, mission_id and title are required")
        if not self.argv or any(not isinstance(value, str) or not value for value in self.argv):
            raise ValueError("argv must contain non-empty strings")
        if self.effect not in EFFECTS:
            raise ValueError("effect must be read_only or mutating")
        if self.priority < 0:
            raise ValueError("priority must be non-negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.id in self.dependencies:
            raise ValueError("a work packet cannot depend on itself")


@dataclass(frozen=True)
class WorkReceipt:
    packet_id: str
    mission_id: str
    status: str
    return_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    started_at: float
    finished_at: float
    worker: str
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.return_code == 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkbenchReceipt:
    mission_id: str
    status: str
    packets: tuple[WorkReceipt, ...]
    workers: int
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "status": self.status,
            "packets": [packet.as_dict() for packet in self.packets],
            "workers": self.workers,
            "digest": self.digest,
        }


Runner = Callable[[WorkPacket], WorkReceipt]


class LocalWorkbench:
    """Bounded local worker pool with explicit mutation serialization."""

    def __init__(
        self,
        project_root: Path | str,
        *,
        max_workers: int = DEFAULT_WORKERS,
        runner: Runner | None = None,
    ) -> None:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.project_root = root
        self.max_workers = max_workers
        self._runner = runner or self._run_command
        self._mutation_lock = Lock()

    def run(self, packets: Sequence[WorkPacket], *, mission_id: str | None = None) -> WorkbenchReceipt:
        items = list(packets)
        if not items:
            raise ValueError("at least one work packet is required")
        mission = mission_id or items[0].mission_id
        if any(item.mission_id != mission for item in items):
            raise ValueError("all packets must belong to the same mission")

        self._validate_dependencies(items)
        pending = {item.id: item for item in items}
        completed: dict[str, WorkReceipt] = {}
        failed = False

        while pending:
            ready = [
                item for item in pending.values()
                if all(dep in completed and completed[dep].succeeded for dep in item.dependencies)
            ]
            if not ready:
                for packet_id in sorted(pending):
                    item = pending.pop(packet_id)
                    completed[packet_id] = self._blocked_receipt(item, "dependency failed or dependency cycle")
                failed = True
                break

            ready.sort(key=lambda item: (item.priority, item.id))
            read_only = [item for item in ready if item.effect == "read_only"]
            mutating = [item for item in ready if item.effect == "mutating"]

            if read_only:
                with ThreadPoolExecutor(
                    max_workers=min(self.max_workers, len(read_only)),
                    thread_name_prefix="auren-local-read",
                ) as pool:
                    futures = {item.id: pool.submit(self._execute, item) for item in read_only}
                    for item in read_only:
                        receipt = futures[item.id].result()
                        completed[item.id] = receipt
                        pending.pop(item.id, None)
                        failed = failed or not receipt.succeeded

            if mutating and not failed:
                for item in mutating:
                    receipt = self._execute(item)
                    completed[item.id] = receipt
                    pending.pop(item.id, None)
                    if not receipt.succeeded:
                        failed = True
                        break

            if failed:
                for item in list(pending.values()):
                    if any(dep in completed and not completed[dep].succeeded for dep in item.dependencies):
                        completed[item.id] = self._blocked_receipt(item, "dependency failed")
                        pending.pop(item.id, None)
                if pending:
                    continue

        packets_out = tuple(completed[key] for key in sorted(completed))
        status = "failed" if any(not item.succeeded for item in packets_out) else "completed"
        digest = self._digest(mission, packets_out)
        return WorkbenchReceipt(mission, status, packets_out, self.max_workers, digest)

    def _execute(self, packet: WorkPacket) -> WorkReceipt:
        if packet.effect == "mutating":
            with self._mutation_lock:
                return self._runner(packet)
        return self._runner(packet)

    def _run_command(self, packet: WorkPacket) -> WorkReceipt:
        started = time.time()
        try:
            cwd = self._safe_cwd(packet.cwd)
            env = os.environ.copy()
            env.update({str(key): str(value) for key, value in packet.env.items()})
            result = subprocess.run(
                list(packet.argv),
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=packet.timeout_seconds,
                check=False,
                shell=False,
            )
            now = time.time()
            status = "completed" if result.returncode == 0 else "failed"
            return WorkReceipt(
                packet.id, packet.mission_id, status, result.returncode,
                result.stdout, result.stderr, int((now - started) * 1000),
                started, now, "local",
            )
        except subprocess.TimeoutExpired as exc:
            now = time.time()
            return WorkReceipt(
                packet.id, packet.mission_id, "timeout", None,
                self._decode_output(exc.stdout), self._decode_output(exc.stderr),
                int((now - started) * 1000), started, now, "local",
                f"command exceeded {packet.timeout_seconds:g}s",
            )
        except (OSError, ValueError) as exc:
            now = time.time()
            return WorkReceipt(
                packet.id, packet.mission_id, "failed", None, "", "",
                int((now - started) * 1000), started, now, "local",
                f"{type(exc).__name__}: {exc}",
            )

    def _safe_cwd(self, requested: str | None) -> str:
        target = self.project_root if requested is None else Path(requested).expanduser()
        if not target.is_absolute():
            target = self.project_root / target
        target = target.resolve()
        try:
            target.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("work packet cwd must remain inside project_root") from exc
        if not target.is_dir():
            raise ValueError("work packet cwd must be a directory")
        return str(target)

    @staticmethod
    def _decode_output(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _validate_dependencies(items: Sequence[WorkPacket]) -> None:
        ids = {item.id for item in items}
        if len(ids) != len(items):
            raise ValueError("duplicate work packet id")
        for item in items:
            missing = sorted(set(item.dependencies) - ids)
            if missing:
                raise ValueError(f"packet {item.id} has missing dependencies: {missing}")
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {item.id: item for item in items}

        def visit(packet_id: str) -> None:
            if packet_id in visiting:
                raise ValueError("work packet dependency cycle detected")
            if packet_id in visited:
                return
            visiting.add(packet_id)
            for dependency in by_id[packet_id].dependencies:
                visit(dependency)
            visiting.remove(packet_id)
            visited.add(packet_id)

        for item in items:
            visit(item.id)

    @staticmethod
    def _blocked_receipt(packet: WorkPacket, reason: str) -> WorkReceipt:
        now = time.time()
        return WorkReceipt(packet.id, packet.mission_id, "blocked", None, "", "", 0, now, now, "local", reason)

    @staticmethod
    def _digest(mission_id: str, receipts: Sequence[WorkReceipt]) -> str:
        import hashlib
        payload = json.dumps(
            {"mission_id": mission_id, "packets": [receipt.as_dict() for receipt in receipts]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]


def packet(
    title: str,
    argv: Sequence[str],
    *,
    mission_id: str = "local-mission",
    effect: str = "read_only",
    priority: int = 100,
    dependencies: Sequence[str] = (),
    timeout_seconds: float = 300.0,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    packet_id: str | None = None,
) -> WorkPacket:
    return WorkPacket(
        id=packet_id or uuid.uuid4().hex[:12],
        mission_id=mission_id,
        title=title,
        argv=tuple(argv),
        effect=effect,
        priority=priority,
        dependencies=tuple(dependencies),
        timeout_seconds=timeout_seconds,
        cwd=cwd,
        env=dict(env or {}),
    )


def _cli() -> int:
    parser = argparse.ArgumentParser(description="AUREN bounded local workbench")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--mission", default="local-cli")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--effect", choices=sorted(EFFECTS), default="read_only")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="command and arguments after --")
    args = parser.parse_args()
    if args.argv and args.argv[0] == "--":
        args.argv = args.argv[1:]
    if not args.argv:
        parser.error("provide a command after --")
    work = packet("local command", args.argv, mission_id=args.mission, effect=args.effect, timeout_seconds=args.timeout)
    receipt = LocalWorkbench(args.project_root, max_workers=args.workers).run([work])
    print(json.dumps(receipt.as_dict(), indent=2, sort_keys=True))
    return 0 if receipt.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = ["EFFECTS", "LocalWorkbench", "WorkPacket", "WorkReceipt", "WorkbenchReceipt", "packet"]
