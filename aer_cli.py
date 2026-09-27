#!/usr/bin/env python3
"""Stable, self-bootstrapping AER command-line entry point."""
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
    runtime = root / "portable" / "aer_runtime.py"
    if not runtime.is_file():
        runtime = root / "payload" / "portable" / "aer_runtime.py"
    if not runtime.is_file():
        return None
    runtime_root = str(runtime.parent.parent)
    if runtime_root not in sys.path:
        sys.path.insert(0, runtime_root)
    package = sys.modules.get("portable")
    if package is None:
        package = types.ModuleType("portable")
        package.__path__ = [runtime_root]
        sys.modules["portable"] = package
    spec = importlib.util.spec_from_file_location("portable.aer_runtime", runtime)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load AER runtime: {runtime}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _load_runtime_from_bundle(bundle: Path):
    temp_root = Path(tempfile.mkdtemp(prefix="aer-cli-runtime-"))
    try:
        with zipfile.ZipFile(bundle) as archive:
            names = {name.rstrip("/") for name in archive.namelist()}
            runtime_name = "payload/portable/aer_runtime.py"
            if runtime_name not in names:
                nested = [name for name in names if name.lower().endswith(".zip") and Path(name).name.lower() == "aer-portable.zip"]
                if len(nested) != 1:
                    raise SystemExit("unable to find portable AER runtime; expected payload/portable/aer_runtime.py or an artifact containing aer-portable.zip")
                inner = temp_root / "aer-portable.zip"
                inner.write_bytes(archive.read(nested[0]))
                with zipfile.ZipFile(inner) as nested_archive:
                    if runtime_name not in {name.rstrip("/") for name in nested_archive.namelist()}:
                        raise SystemExit("aer-portable.zip does not contain payload/portable/aer_runtime.py")
                    nested_archive.extract(runtime_name, temp_root)
            else:
                archive.extract(runtime_name, temp_root)
        return _load_runtime_from(temp_root), temp_root
    except (OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise SystemExit(f"invalid AER bundle: {exc}") from exc


def _load_runtime(argv: list[str]):
    module = _load_runtime_from(_ROOT)
    if module is not None:
        return module, None
    bundle_candidates = [Path(arg).expanduser() for arg in argv if not arg.startswith("-")]
    for candidate in bundle_candidates:
        if candidate.is_file() and candidate.suffix.lower() == ".zip":
            return _load_runtime_from_bundle(candidate.resolve())
    raise SystemExit("AER runtime not found. Run from the AER source checkout or portable bundle, or provide an AER .zip bundle.")


def _inject_plugin_payload(bundle: Path, root: Path) -> None:
    """Add Claude metadata to a runtime-built bundle and cover it in the manifest."""
    plugin_root = root / ".claude-plugin"
    if not plugin_root.is_dir():
        return
    with zipfile.ZipFile(bundle, "r") as source:
        entries = {item.filename: source.read(item.filename) for item in source.infolist()}
    manifest = json.loads(entries["aer-bundle.json"].decode("utf-8"))
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
    entries["aer-bundle.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
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

    def build_with_provider_payload(root: Path, output: Path, source_commit: str | None = None, source_ref: str = runtime.AER_BRANCH) -> Path:
        result = original_build(root, output, source_commit=source_commit, source_ref=source_ref)
        _inject_plugin_payload(result, root)
        return result

    runtime.build = build_with_provider_payload


def _run_dashboard(args: list[str]) -> int:
    """Run the local Engineering Console from the active AER installation."""
    parser = argparse.ArgumentParser(description="Run the AER Engineering Console.")
    parser.add_argument("--project-root", default=".", help="Repository whose AER state and code graph should be displayed.")
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
        print("Claude Code not found on PATH; AER Claude integration was not activated.")
        return
    marketplace = current_root / ".claude-plugin" / "marketplace.json"
    plugin_manifest = current_root / ".claude-plugin" / "plugin.json"
    if not marketplace.is_file() or not plugin_manifest.is_file():
        print("AER Claude integration skipped: plugin metadata is missing from the installed bundle.")
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
    """Load WorkReport safely even when aer_cli.py is run as a top-level script."""
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
            spec = importlib.util.spec_from_file_location("aer_work_report", runtime_report)
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
    """Create a durable HTML report for every CLI invocation, including failures."""
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
            references=[{"type": "repository", "path": ".ai-harness/runtime/work_report.py"}, {"type": "entry-point", "path": "aer_cli.py"}],
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
        result = int(runtime.main(args))
        if result == 0 and _should_activate_claude(args):
            aer_home = Path.home() / ".aer"
            current = aer_home / "current"
            if current.exists():
                _claude_plugin_install(current)
        return result
    finally:
        _emit_work_report(args, result, _ROOT)
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
