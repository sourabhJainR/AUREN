#!/usr/bin/env python3
"""Stable, self-bootstrapping AUREN command-line entry point."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
PLUGIN_NAME = "adaptive-ai-coding-orchestrator"
MARKETPLACE_NAME = "adaptive-ai-engineering"


def _load_runtime_from(root: Path):
    runtime = root / "portable" / "auren_runtime.py"
    if not runtime.is_file():
        runtime = root / "payload" / "portable" / "auren_runtime.py"
    if not runtime.is_file():
        return None
    runtime_root = str(runtime.parent.parent)
    if runtime_root not in sys.path:
        sys.path.insert(0, runtime_root)
    package = sys.modules.get("portable")
    if package is None:
        package = types.ModuleType("portable")
        package.__path__ = [str(runtime.parent)]
        sys.modules["portable"] = package
    spec = importlib.util.spec_from_file_location("portable.auren_runtime", runtime)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load AUREN runtime: {runtime}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_runtime_from_bundle(bundle: Path):
    temp_root = Path(tempfile.mkdtemp(prefix="auren-cli-runtime-"))
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = {name.rstrip("/") for name in archive.namelist()}
            runtime_name = "payload/portable/auren_runtime.py"
            if runtime_name not in names:
                nested = [name for name in names if name.lower().endswith(".zip") and Path(name).name.lower() == "auren-portable.zip"]
                if len(nested) != 1:
                    raise SystemExit("unable to find portable AUREN runtime; expected payload/portable/auren_runtime.py or an artifact containing auren-portable.zip")
                inner = temp_root / "auren-portable.zip"
                inner.write_bytes(archive.read(nested[0]))
                with zipfile.ZipFile(inner) as nested_archive:
                    if runtime_name not in {name.rstrip("/") for name in nested_archive.namelist()}:
                        raise SystemExit("auren-portable.zip does not contain payload/portable/auren_runtime.py")
                    nested_archive.extract(runtime_name, temp_root)
            else:
                archive.extract(runtime_name, temp_root)
        return _load_runtime_from(temp_root), temp_root
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SystemExit(f"invalid AUREN bundle: {exc}") from exc


def _load_runtime(argv: list[str]):
    module = _load_runtime_from(_ROOT)
    if module is not None:
        return module, None
    bundle_candidates = [Path(arg).expanduser() for arg in argv if not arg.startswith("-")]
    for candidate in bundle_candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".zip":
            return _load_runtime_from_bundle(candidate.resolve())
    raise SystemExit("AUREN runtime not found. Run from the AUREN source checkout or portable bundle, or provide an AUREN .zip bundle.")


def _inject_plugin_payload(bundle: Path, root: Path) -> None:
    """Add Claude metadata to a runtime-built bundle and cover it in the manifest."""
    plugin_root = root / ".claude-plugin"
    if not plugin_root.is_dir():
        return
    with zipfile.ZipFile(bundle, "r") as source:
        entries = {item.filename: source.read(item.filename) for item in source.infolist()}
    manifest = json.loads(entries["auren-bundle.json"].decode("utf-8"))
    records = list(manifest.get("files", []))
    recorded = {item.get("path") for item in records if isinstance(item, dict)}
    for path in sorted(plugin_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        archive_name = f"payload/{rel}"
        data = path.read_bytes()
        entries[archive_name] = data
        if rel not in recorded:
            records.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    manifest["files"] = sorted(records, key=lambda item: item["path"])
    entries["auren-bundle.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp = bundle.with_suffix(".plugin.tmp.zip")
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, data in entries.items():
            target.writestr(name, data)
    temp.replace(bundle)


def _prepare_runtime_for_distribution(runtime) -> None:
    """Extend the provider-neutral runtime with bundled Claude metadata."""
    required = tuple(runtime.REQUIRED_PATHS)
    if ".claude-plugin" not in required:
        runtime.REQUIRED_PATHS = (*required, ".claude-plugin")
    original_copy_payload = runtime._copy_payload

    def copy_payload(payload: Path, version_root: Path) -> None:
        original_copy_payload(payload, version_root)
        source = payload / ".claude-plugin"
        if source.is_dir():
            runtime._copy_tree_without_mutable_state(source, version_root / ".claude-plugin")

    runtime._copy_payload = copy_payload
    original_build = runtime.build

    def build_with_provider_payload(root: Path, output: Path, source_commit: str | None = None, source_ref: str = runtime.AUREN_BRANCH) -> Path:
        result = original_build(root, output, source_commit=source_commit, source_ref=source_ref)
        _inject_plugin_payload(result, root)
        return result

    runtime.build = build_with_provider_payload


def _run_dashboard(args: list[str]) -> int:
    """Run the local Engineering Console from the active AUREN installation."""
    parser = argparse.ArgumentParser(description="Run the AUREN Engineering Console.")
    parser.add_argument("--project-root", default=".", help="Repository whose AUREN state and code graph should be displayed.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Keep the default for local-only access.")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args(args)
    from portable.dashboard_server import serve
    serve(options.project_root, options.host, options.port)
    return 0


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args or any(value.startswith(flag + "=") for value in args)


def _claude_available() -> str | None:
    return shutil.which("claude")


def _claude_plugin_install(current_root: Path) -> None:
    """Register and install the bundled Claude plugin at user scope."""
    claude = _claude_available()
    if not claude:
        print("Claude Code not found on PATH; AUREN Claude integration was not activated.")
        return
    marketplace = current_root / ".claude-plugin" / "marketplace.json"
    plugin_manifest = current_root / ".claude-plugin" / "plugin.json"
    if not marketplace.is_file() or not plugin_manifest.is_file():
        print("AUREN Claude integration skipped: plugin metadata is missing from the installed bundle.")
        return

    def run(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([claude, *command], text=True, capture_output=True, timeout=60, check=False)

    try:
        added = run("plugin", "marketplace", "add", str(current_root), "--scope", "user")
        added_output = (added.stdout + added.stderr).strip()
        if added.returncode != 0 and "already" not in added_output.lower():
            print("Claude marketplace registration warning:", added_output)
        installed = run("plugin", "install", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}", "--scope", "user")
        install_output = (installed.stdout + installed.stderr).strip()
        if installed.returncode != 0 and not any(phrase in install_output.lower() for phrase in ("already installed", "already enabled")):
            print("Claude plugin installation warning:", install_output)
            return
        print(f"Claude integration active: {PLUGIN_NAME}@{MARKETPLACE_NAME}")
        print("Restart Claude Code or run /reload-plugins to load the skill and hook.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Claude integration could not be activated: {exc}")


def _should_activate_claude(args: list[str]) -> bool:
    if not args or args[0] not in {"install", "update"}:
        return False
    skill = None
    for index, value in enumerate(args):
        if value == "--skill" and index + 1 < len(args):
            skill = args[index + 1]
        elif value.startswith("--skill="):
            skill = value.split("=", 1)[1]
    return skill in {"claude", "all", "auto"} or (skill is None and _claude_available() is not None)


def _load_work_report_module(root: Path):
    """Load WorkReport safely even when auren_cli.py is run as a top-level script."""
    try:
        from .ai_harness.runtime.work_report import WorkReport, WorkReportGenerator
        return WorkReport, WorkReportGenerator
    except Exception:
        try:
            from ai_harness.runtime.work_report import WorkReport, WorkReportGenerator
            return WorkReport, WorkReportGenerator
        except Exception:
            runtime_report = root / ".ai-harness" / "runtime" / "work_report.py"
            if not runtime_report.is_file():
                return None
            spec = importlib.util.spec_from_file_location("auren_work_report", runtime_report)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            return module.WorkReport, module.WorkReportGenerator


def _emit_work_report(args: list[str], result: int, root: Path) -> None:
    """Create a durable HTML report for work commands, excluding read-only status views."""
    if args and (args[0] == "progress" or (args[0] == "continuous" and len(args) > 1 and args[1] in {"status", "audit", "stop"})):
        # Preserve clean JSON output and avoid reports for inspection/control commands.
        return
    try:
        loaded = _load_work_report_module(root)
        if loaded is None:
            return
        WorkReport, WorkReportGenerator = loaded
        command = args[0] if args else "default"
        work_id = "cli-" + command + "-" + str(abs(hash(tuple(args))) % 10**10)
        report = WorkReport(
            work_id=work_id,
            title=f"Engineering work: {command}",
            status="completed" if result == 0 else "failed",
            objective="Execute the requested orchestrator command with traceable evidence and documented boundaries.",
            summary=f"Command {'completed successfully' if result == 0 else 'failed'} with exit code {result}.",
            scope=["CLI command execution", "runtime distribution / orchestration entry point"],
            out_of_scope=["Changing credentials, permissions, security policy, or approval authority"],
            assumptions=["The invoked runtime remains the authoritative execution component.", "HTML generation must not change command success/failure semantics."],
            constraints=["Report generation is best-effort and must never mask the primary command result."],
            findings=[f"Command: {' '.join(args) if args else '(none)'}", f"Exit code: {result}"],
            risks=["A failed report write can leave documentation incomplete; command outcome is preserved."],
            threats=["Report content must not become an authorization mechanism.", "Sensitive credentials and secrets must never be copied into reports."],
            implementation=["Structured WorkReport is rendered to a self-contained HTML artifact.", "Mermaid source is embedded so diagrams remain inspectable and reproducible."],
            hld=["CLI -> runtime -> work outcome -> reporting subsystem -> .ai-harness/reports/latest.html"],
            lld=["Reporter is isolated, deterministic, dependency-free, and best-effort.", "The report records scope, assumptions, boundaries, findings, risks, threats, verification, regression areas, evidence, and diagrams."],
            references=[{"type": "repository", "path": ".ai-harness/runtime/work_report.py"}, {"type": "entry-point", "path": "auren_cli.py"}],
            evidence=[{"type": "command", "argv": args, "exit_code": result}],
            verification=["Primary CLI result is returned unchanged after report generation.", "Report generation is exception-isolated."],
            regressions=["CLI failure behavior", "portable runtime loading", "Claude plugin activation", "report-write failure isolation"],
            data_flow=["CLI arguments", "Runtime execution", "Exit status", "Structured report model", "HTML artifact"],
            user_flow=["Submit command", "Runtime executes", "Review result", "Open latest.html", "Inspect evidence and risks"],
            uml=["actor User", "participant CLI", "participant Runtime", "participant Reporter", "User->>CLI: command", "CLI->>Runtime: execute", "Runtime-->>CLI: result", "CLI->>Reporter: report outcome", "Reporter-->>User: HTML artifact"],
            metrics={"exit_code": result, "argument_count": len(args)},
        )
        path = WorkReportGenerator(root).write(report)
        print(f"Engineering HTML report: {path}")
    except Exception as exc:
        print(f"Engineering HTML report warning: {exc}")



def _run_continuous(args: list[str]) -> int:
    """Run/status/stop AUREN's durable supervisor without a second task store."""
    parser = argparse.ArgumentParser(
        prog="auren continuous",
        description="Continuously execute durable tasks, poll exact-head CI, and preserve an auditable stop/resume boundary.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start", help="start or recover the continuous worker")
    start.add_argument("--project-root", default=".", help="project checkout (default: current directory)")
    start.add_argument("--poll-seconds", type=float, default=60.0)
    start.add_argument("--batch-size", type=int, default=20)
    start.add_argument("--max-cycles", type=int, default=None, help="bounded diagnostic run; omit for continuous operation")
    start.add_argument("--watch", action="append", default=[], metavar="OWNER/REPO@FULL_SHA", help="poll exact-head GitHub checks; repeat for multiple heads")
    start.add_argument("--required-check", action="append", default=[], help="exact required GitHub check name; repeat as needed")
    start.add_argument("--repair-handler", default=None, help="registered durable handler for bounded repair tasks from failed required checks")
    stop = sub.add_parser("stop", help="persist an interrupt request; pending work and audit remain intact")
    stop.add_argument("--project-root", default=".")
    stop.add_argument("--reason", default="operator_stop_command")
    status = sub.add_parser("status", help="show worker state and task counts")
    status.add_argument("--project-root", default=".")
    audit = sub.add_parser("audit", help="verify the continuous-execution hash chain")
    audit.add_argument("--project-root", default=".")
    options = parser.parse_args(args)

    from portable.execution_controller import ExecutionController
    from portable.continuous_execution import ContinuousExecutionSupervisor, GitHubActionsCheckPoller, discover_git_target

    root = Path(options.project_root).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"project root is not an existing directory: {root}")
    controller = ExecutionController(root)
    try:
        if options.action == "stop":
            supervisor = ContinuousExecutionSupervisor(controller)
            print(json.dumps(supervisor.request_stop(options.reason), indent=2, sort_keys=True))
            return 0
        if options.action == "status":
            print(json.dumps(ContinuousExecutionSupervisor(controller).status(), indent=2, sort_keys=True))
            return 0
        if options.action == "audit":
            valid = ContinuousExecutionSupervisor(controller).verify_audit_chain()
            print(json.dumps({"audit_chain_valid": valid}, indent=2, sort_keys=True))
            return 0 if valid else 2

        if options.watch and not options.required_check:
            parser.error("explicit --watch requires at least one --required-check")
        pollers = {}
        targets = list(options.watch)
        if not targets:
            # Implicit default: observe this checkout's current immutable HEAD.
            # Without configured required checks, this is observation, never a gate pass.
            discovered = discover_git_target(root)
            if discovered is not None:
                repository, head_sha = discovered
                targets.append(f"{repository}@{head_sha}")
            else:
                print("AUREN CI polling: no GitHub origin/HEAD discovered; durable task polling remains active.", file=sys.stderr)
        for target in targets:
            if "@" not in target:
                parser.error("--watch must use OWNER/REPO@FULL_SHA")
            repository, head_sha = target.rsplit("@", 1)
            key = f"ci:{repository}@{head_sha[:12]}"
            if key in pollers:
                parser.error(f"duplicate CI watch: {target}")
            pollers[key] = GitHubActionsCheckPoller(repository, head_sha, options.required_check)
        if options.repair_handler and not options.required_check:
            parser.error("--repair-handler requires explicit --required-check names")
        supervisor = ContinuousExecutionSupervisor(
            controller, pollers=pollers, repair_handler=options.repair_handler,
        )
        import signal
        from threading import Event
        stop_event = Event()
        prior_handlers = {}
        def request_interrupt(signum, _frame):
            stop_event.set()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                prior_handlers[signum] = signal.signal(signum, request_interrupt)
            except (ValueError, OSError):
                pass
        try:
            result = supervisor.run(
                poll_seconds=options.poll_seconds,
                batch_size=options.batch_size,
                max_cycles=options.max_cycles,
                stop_event=stop_event,
            )
        finally:
            for signum, handler in prior_handlers.items():
                signal.signal(signum, handler)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("state") == "stopped" and result.get("audit_chain_valid") else 1
    finally:
        controller.company.close() if hasattr(controller.company, "close") else None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 1 and Path(args[0]).suffix.lower() == ".zip":
        args = ["install", *args, "--skill", "auto"]
    elif args and args[0] in {"install", "update"} and not _has_flag(args, "--skill"):
        args = [*args, "--skill", "auto"]
    if args and args[0] == "dashboard":
        return _run_dashboard(args[1:])
    runtime, temp_root = _load_runtime(args)
    _prepare_runtime_for_distribution(runtime)
    result = 1
    try:
        if args and args[0] == "continuous":
            result = int(_run_continuous(args[1:]))
        else:
            result = int(runtime.main(args))
        if result == 0 and _should_activate_claude(args):
            auren_home = Path.home() / ".auren"
            current = auren_home / "current"
            if current.exists():
                _claude_plugin_install(current)
        return result
    finally:
        _emit_work_report(args, result, _ROOT)
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
