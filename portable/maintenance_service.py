#!/usr/bin/env python3
"""Cross-platform host for AUREN's durable adaptive-learning maintenance lane."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time as time_module
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .adaptive_runtime import AdaptiveRuntime
from .autonomous_daemon import PersistentSupervisorDaemon
from .automation_scheduler import AutomationScheduler
from .orchestration import Graph

DEFAULT_SERVICE_NAME = "AURENMaintenance"
DEFAULT_DISPLAY_NAME = "AUREN Adaptive Learning Maintenance"
DEFAULT_RUN_TIME = "02:00"
DEFAULT_TIMEZONE = "local"
DEFAULT_POLL_SECONDS = 60
DEFAULT_MAINTENANCE_BUDGET = 20
DEFAULT_SCOPE = "user"


@dataclass(frozen=True)
class MaintenanceServiceConfig:
    project_root: Path
    at_time: str = DEFAULT_RUN_TIME
    timezone: str = DEFAULT_TIMEZONE
    poll_seconds: int = DEFAULT_POLL_SECONDS
    maintenance_budget: int = DEFAULT_MAINTENANCE_BUDGET
    enabled: bool = True
    service_name: str = DEFAULT_SERVICE_NAME
    display_name: str = DEFAULT_DISPLAY_NAME
    scope: str = DEFAULT_SCOPE

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "MaintenanceServiceConfig":
        root = project_root or Path(os.environ.get("AUREN_PROJECT_ROOT", Path.cwd())).expanduser().resolve()
        at_time = os.environ.get("AUREN_MAINTENANCE_TIME", DEFAULT_RUN_TIME)
        timezone_name = os.environ.get("AUREN_MAINTENANCE_TIMEZONE", DEFAULT_TIMEZONE)
        poll_seconds = int(os.environ.get("AUREN_MAINTENANCE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
        budget = int(os.environ.get("AUREN_MAINTENANCE_BUDGET", str(DEFAULT_MAINTENANCE_BUDGET)))
        enabled = os.environ.get("AUREN_MAINTENANCE_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
        scope = os.environ.get("AUREN_SERVICE_SCOPE", DEFAULT_SCOPE).strip().lower()
        if poll_seconds < 1:
            raise ValueError("AUREN_MAINTENANCE_POLL_SECONDS must be positive")
        if budget < 1:
            raise ValueError("AUREN_MAINTENANCE_BUDGET must be positive")
        if scope not in {"user", "system"}:
            raise ValueError("AUREN_SERVICE_SCOPE must be user or system")
        AutomationScheduler.last_day_datetime(at_time=at_time, timezone_name=timezone_name)
        return cls(root, at_time, timezone_name, poll_seconds, budget, enabled, DEFAULT_SERVICE_NAME, DEFAULT_DISPLAY_NAME, scope)

    def environment(self) -> dict[str, str]:
        return {
            "AUREN_PROJECT_ROOT": str(self.project_root),
            "AUREN_MAINTENANCE_TIME": self.at_time,
            "AUREN_MAINTENANCE_TIMEZONE": self.timezone,
            "AUREN_MAINTENANCE_POLL_SECONDS": str(self.poll_seconds),
            "AUREN_MAINTENANCE_BUDGET": str(self.maintenance_budget),
            "AUREN_MAINTENANCE_ENABLED": "1" if self.enabled else "0",
        }


class MaintenanceService:
    """Run the existing AdaptiveRuntime maintenance lane under an OS supervisor."""

    def __init__(self, config: MaintenanceServiceConfig) -> None:
        self.config = config
        self.runtime = AdaptiveRuntime(Graph([]), maintenance_budget=config.maintenance_budget)
        self._stop = False

    def stop(self, *_signals: object) -> None:
        self._stop = True

    def run_once(self):
        if not self.config.enabled:
            return None
        checkpoint = PersistentSupervisorDaemon(self.config.project_root, max_tasks_per_tick=self.config.maintenance_budget).checkpoint()
        resumed = self.runtime.resume_tasks(limit=self.config.maintenance_budget, project_root=self.config.project_root)
        maintenance = self.runtime.maintenance_tick(self.config.project_root, budget=self.config.maintenance_budget)
        return {"resumed": len(resumed), "checkpoint": checkpoint.__dict__, "maintenance": maintenance.__dict__ if maintenance is not None else None}

    def loop(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stop:
            try:
                receipt = self.run_once()
                if receipt is not None:
                    print(json.dumps(receipt if isinstance(receipt, dict) else receipt.__dict__, sort_keys=True), flush=True)
            except Exception as exc:
                print(f"AUREN maintenance error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            for _ in range(self.config.poll_seconds):
                if self._stop:
                    break
                time_module.sleep(1)
        return 0


def _python_executable() -> str:
    return sys.executable or "python3"


def _module_command(config: MaintenanceServiceConfig) -> list[str]:
    return [
        _python_executable(), "-m", "portable.maintenance_service",
        "--project-root", str(config.project_root),
        "--time", config.at_time,
        "--timezone", config.timezone,
        "--poll-seconds", str(config.poll_seconds),
        "--budget", str(config.maintenance_budget),
        "--scope", config.scope,
        "run",
    ]


def _windows_service_args(config: MaintenanceServiceConfig) -> str:
    return subprocess.list2cmdline(_module_command(config)[3:-1])


def _service_home() -> Path:
    return Path.home() / ".auren" / "service"


def _service_state_path(config: MaintenanceServiceConfig) -> Path:
    return _service_home() / f"{config.service_name}.json"


def _write_state(config: MaintenanceServiceConfig, native_path: Path) -> None:
    state = {
        "service_name": config.service_name,
        "scope": config.scope,
        "platform": sys.platform,
        "definition": str(native_path),
        "environment": config.environment(),
        "schedule": {"kind": "last_day_of_month", "at_time": config.at_time, "timezone": config.timezone},
    }
    path = _service_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(command: Sequence[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), text=True, capture_output=True, check=check)


def _linux_unit(config: MaintenanceServiceConfig) -> str:
    command = " ".join(_shell_quote(item) for item in _module_command(config))
    lines = [
        "[Unit]",
        f"Description={config.display_name}",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={_shell_quote(str(Path.home() / '.auren' / 'current'))}",
        f"ExecStart={command}",
        "Restart=on-failure",
        "RestartSec=30",
    ]
    for key, value in config.environment().items():
        lines.append(f"Environment={key}={_shell_quote(value)}")
    lines.extend(["", "[Install]", "WantedBy=default.target" if config.scope == "user" else "WantedBy=multi-user.target"])
    return "\n".join(lines) + "\n"


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _linux_definition_path(config: MaintenanceServiceConfig) -> Path:
    if config.scope == "system":
        return Path("/etc/systemd/system") / f"{config.service_name}.service"
    return Path.home() / ".config" / "systemd" / "user" / f"{config.service_name}.service"


def _linux_control(config: MaintenanceServiceConfig, action: str) -> int:
    if not shutil.which("systemctl"):
        raise SystemExit("systemctl is not available on this Linux system")
    cmd = ["systemctl"] + (["--user"] if config.scope == "user" else []) + [action, config.service_name]
    result = _run(cmd)
    print(result.stdout or result.stderr)
    return result.returncode


def _install_linux(config: MaintenanceServiceConfig) -> int:
    if not shutil.which("systemctl"):
        raise SystemExit("systemctl is not available on this Linux system")
    path = _linux_definition_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_linux_unit(config), encoding="utf-8")
    cmd = ["systemctl"] + (["--user"] if config.scope == "user" else [])
    _run(cmd + ["daemon-reload"], check=True)
    _run(cmd + ["enable", "--now", config.service_name], check=True)
    _write_state(config, path)
    print(f"Installed {config.service_name}: {path}")
    return 0


def _uninstall_linux(config: MaintenanceServiceConfig) -> int:
    if not shutil.which("systemctl"):
        return 0
    cmd = ["systemctl"] + (["--user"] if config.scope == "user" else [])
    _run(cmd + ["disable", "--now", config.service_name])
    _linux_definition_path(config).unlink(missing_ok=True)
    _run(cmd + ["daemon-reload"])
    _service_state_path(config).unlink(missing_ok=True)
    return 0


def _launchd_label(config: MaintenanceServiceConfig) -> str:
    return f"com.auren.{config.service_name}"


def _launchd_plist(config: MaintenanceServiceConfig) -> str:
    args = "\n".join(f"        <string>{_xml_escape(value)}</string>" for value in _module_command(config))
    env = "\n".join(f"        <key>{_xml_escape(k)}</key><string>{_xml_escape(v)}</string>" for k, v in config.environment().items())
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key><string>{_launchd_label(config)}</string>
            <key>ProgramArguments</key><array>
{args}
            </array>
            <key>WorkingDirectory</key><string>{_xml_escape(str(Path.home() / '.auren' / 'current'))}</string>
            <key>EnvironmentVariables</key><dict>
{env}
            </dict>
            <key>RunAtLoad</key><true/>
            <key>KeepAlive</key><true/>
            <key>ProcessType</key><string>Background</string>
            <key>ThrottleInterval</key><integer>30</integer>
            <key>StandardOutPath</key><string>{_xml_escape(str(_service_home() / 'stdout.log'))}</string>
            <key>StandardErrorPath</key><string>{_xml_escape(str(_service_home() / 'stderr.log'))}</string>
        </dict>
        </plist>
        """)


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")


def _launchd_definition_path(config: MaintenanceServiceConfig) -> Path:
    if config.scope == "system":
        return Path("/Library/LaunchDaemons") / f"{_launchd_label(config)}.plist"
    return Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label(config)}.plist"


def _mac_control(config: MaintenanceServiceConfig, action: str) -> int:
    if not shutil.which("launchctl"):
        raise SystemExit("launchctl is not available on this macOS system")
    domain = f"gui/{os.getuid()}" if config.scope == "user" else "system"
    label = _launchd_label(config)
    command = {
        "start": ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
        "stop": ["launchctl", "kill", "SIGTERM", f"{domain}/{label}"],
        "status": ["launchctl", "print", f"{domain}/{label}"],
    }[action]
    result = _run(command)
    print(result.stdout or result.stderr)
    return result.returncode


def _install_macos(config: MaintenanceServiceConfig) -> int:
    if not shutil.which("launchctl"):
        raise SystemExit("launchctl is not available on this macOS system")
    path = _launchd_definition_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_launchd_plist(config), encoding="utf-8")
    domain = f"gui/{os.getuid()}" if config.scope == "user" else "system"
    _run(["launchctl", "bootout", domain, str(path)])
    _run(["launchctl", "bootstrap", domain, str(path)], check=True)
    _run(["launchctl", "enable", f"{domain}/{_launchd_label(config)}"])
    _write_state(config, path)
    print(f"Installed {config.service_name}: {path}")
    return 0


def _uninstall_macos(config: MaintenanceServiceConfig) -> int:
    if not shutil.which("launchctl"):
        return 0
    path = _launchd_definition_path(config)
    domain = f"gui/{os.getuid()}" if config.scope == "user" else "system"
    if path.exists():
        _run(["launchctl", "bootout", domain, str(path)])
        path.unlink(missing_ok=True)
    _service_state_path(config).unlink(missing_ok=True)
    return 0


try:
    import win32service
    import win32serviceutil
except ImportError:  # pragma: no cover - platform/dependency specific
    win32service = win32serviceutil = None


if win32serviceutil is not None:  # pragma: no cover - Windows integration
    class _WindowsMaintenanceService(win32serviceutil.ServiceFramework):
        _svc_name_ = DEFAULT_SERVICE_NAME
        _svc_display_name_ = DEFAULT_DISPLAY_NAME
        _svc_description_ = "AUREN durable adaptive-learning maintenance lane"

        def __init__(self, args):
            super().__init__(args)
            values = MaintenanceServiceConfig.from_env()
            if args:
                try:
                    parsed = argparse.ArgumentParser(add_help=False)
                    parsed.add_argument("--project-root", type=Path)
                    parsed.add_argument("--time", default=values.at_time)
                    parsed.add_argument("--timezone", default=values.timezone)
                    parsed.add_argument("--poll-seconds", type=int, default=values.poll_seconds)
                    parsed.add_argument("--budget", type=int, default=values.maintenance_budget)
                    parsed.add_argument("--scope", choices=("user", "system"), default=values.scope)
                    supplied = parsed.parse_args(args)
                    values = MaintenanceServiceConfig(
                        project_root=(supplied.project_root or values.project_root).expanduser().resolve(),
                        at_time=supplied.time,
                        timezone=supplied.timezone,
                        poll_seconds=supplied.poll_seconds,
                        maintenance_budget=supplied.budget,
                        enabled=values.enabled,
                        service_name=self._svc_name_,
                        display_name=self._svc_display_name_,
                        scope=supplied.scope,
                    )
                except SystemExit:
                    pass
            self.worker = MaintenanceService(values)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.worker.stop()

        def SvcDoRun(self):
            self.worker.loop()
else:
    _WindowsMaintenanceService = None


def _install_windows(config: MaintenanceServiceConfig) -> int:
    if os.name != "nt":
        raise SystemExit("Windows service installation is only supported on Windows")
    if win32serviceutil is None:
        raise SystemExit("pywin32 is required for Windows Service mode: python -m pip install pywin32")
    _WindowsMaintenanceService._svc_name_ = config.service_name
    _WindowsMaintenanceService._svc_display_name_ = config.display_name
    exe_args = subprocess.list2cmdline(_module_command(config)[3:-1])
    win32serviceutil.InstallService(
        _WindowsMaintenanceService,
        config.service_name,
        config.display_name,
        startType=win32service.SERVICE_AUTO_START,
        description=_WindowsMaintenanceService._svc_description_,
        exeArgs=exe_args,
    )
    _write_state(config, Path("Windows Service Control Manager"))
    _run(["sc.exe", "start", config.service_name])
    return 0


def _uninstall_windows(config: MaintenanceServiceConfig) -> int:
    if os.name != "nt" or win32serviceutil is None:
        return 0
    _run(["sc.exe", "stop", config.service_name])
    win32serviceutil.RemoveService(config.service_name)
    _service_state_path(config).unlink(missing_ok=True)
    return 0


def _windows_control(config: MaintenanceServiceConfig, action: str) -> int:
    if os.name != "nt":
        raise SystemExit("Windows service control is only supported on Windows")
    result = _run(["sc.exe", action, config.service_name])
    print(result.stdout or result.stderr)
    return result.returncode


def install(config: MaintenanceServiceConfig) -> int:
    if sys.platform.startswith("win"):
        return _install_windows(config)
    if sys.platform == "darwin":
        return _install_macos(config)
    if sys.platform.startswith("linux"):
        return _install_linux(config)
    raise SystemExit(f"unsupported service platform: {sys.platform}")


def uninstall(config: MaintenanceServiceConfig) -> int:
    if sys.platform.startswith("win"):
        return _uninstall_windows(config)
    if sys.platform == "darwin":
        return _uninstall_macos(config)
    if sys.platform.startswith("linux"):
        return _uninstall_linux(config)
    raise SystemExit(f"unsupported service platform: {sys.platform}")


def control(config: MaintenanceServiceConfig, action: str) -> int:
    if sys.platform.startswith("win"):
        return _windows_control(config, action)
    if sys.platform == "darwin":
        return _mac_control(config, action)
    if sys.platform.startswith("linux"):
        return _linux_control(config, action)
    raise SystemExit(f"unsupported service platform: {sys.platform}")


def status(config: MaintenanceServiceConfig) -> int:
    return control(config, "status")


def run_foreground(config: MaintenanceServiceConfig) -> int:
    return MaintenanceService(config).loop()


def run_once(config: MaintenanceServiceConfig) -> int:
    receipt = MaintenanceService(config).run_once()
    if receipt is not None:
        print(json.dumps(receipt if isinstance(receipt, dict) else receipt.__dict__, indent=2, sort_keys=True))
    else:
        print("AUREN maintenance: not due or disabled")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AUREN adaptive-learning maintenance service")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--time", default=DEFAULT_RUN_TIME)
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--budget", type=int, default=DEFAULT_MAINTENANCE_BUDGET)
    parser.add_argument("--scope", choices=("user", "system"), default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "uninstall", "start", "stop", "status", "run", "run-once"):
        sub.add_parser(name)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    scope = args.scope or os.environ.get("AUREN_SERVICE_SCOPE", DEFAULT_SCOPE)
    config = MaintenanceServiceConfig(
        project_root=(args.project_root or Path(os.environ.get("AUREN_PROJECT_ROOT", Path.cwd()))).expanduser().resolve(),
        at_time=args.time,
        timezone=args.timezone,
        poll_seconds=args.poll_seconds,
        maintenance_budget=args.budget,
        scope=scope,
    )
    AutomationScheduler.last_day_datetime(at_time=config.at_time, timezone_name=config.timezone)
    if args.command == "install":
        return install(config)
    if args.command == "uninstall":
        return uninstall(config)
    if args.command == "status":
        return status(config)
    if args.command == "start":
        return control(config, "start")
    if args.command == "stop":
        return control(config, "stop")
    if args.command == "run":
        return run_foreground(config)
    return run_once(config)


if __name__ == "__main__":
    raise SystemExit(main())
