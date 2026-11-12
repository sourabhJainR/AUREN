"""Run AUREN's authenticated remote MCP ASGI service."""
from __future__ import annotations

import argparse
import importlib
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from .remote_mcp import AurenMCPASGI, service_from_environment


def _load_runtime(factory_path: str, project_root: Path) -> Any:
    module_name, separator, attribute = factory_path.partition(":")
    if not separator or not module_name or not attribute or "." in attribute:
        raise ValueError("runtime factory must use module.path:function_name")
    factory = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(factory):
        raise TypeError("configured runtime factory is not callable")
    runtime = factory(project_root=project_root)
    if not callable(getattr(runtime, "run", None)):
        raise TypeError("runtime factory must return a configured AdaptiveRuntime-compatible host")
    if getattr(runtime, "execution_controller", None) is None:
        raise TypeError("runtime factory did not configure the AUREN execution controller")
    if getattr(runtime, "trigger_runtime", None) is None:
        raise TypeError("runtime factory did not configure the durable trigger runtime")
    graph = getattr(getattr(runtime, "orchestrator", None), "graph", None)
    order = getattr(graph, "order", None)
    if not callable(order) or not order():
        raise TypeError("runtime factory must return a configured runtime with a non-empty agent graph")
    return runtime


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run AUREN's authenticated remote MCP service")
    result.add_argument("--project-root", type=Path, default=Path(os.environ.get("AUREN_PROJECT_ROOT", Path.cwd())))
    result.add_argument("--runtime-factory", default=os.environ.get("AUREN_MCP_RUNTIME_FACTORY", ""))
    result.add_argument("--host", default=os.environ.get("AUREN_MCP_BIND_HOST", "127.0.0.1"))
    result.add_argument("--port", type=int, default=int(os.environ.get("AUREN_MCP_PORT", "8765")))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    project_root = args.project_root.expanduser().resolve()
    if not project_root.is_dir():
        raise SystemExit("AUREN project root must be an existing directory")
    if not 1 <= args.port <= 65535:
        raise SystemExit("AUREN_MCP_PORT must be between 1 and 65535")
    if args.host not in {"127.0.0.1", "::1", "0.0.0.0", "::"}:
        raise SystemExit("AUREN_MCP_BIND_HOST must be a literal loopback or wildcard bind address")
    if args.host in {"0.0.0.0", "::"} and os.environ.get("AUREN_MCP_ALLOW_PUBLIC_BIND") != "1":
        raise SystemExit("public bind requires AUREN_MCP_ALLOW_PUBLIC_BIND=1 and an externally enforced TLS/firewall boundary")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install the optional HTTP runtime with: pip install 'uvicorn[standard]'") from exc
    composition = None
    if args.runtime_factory:
        runtime = _load_runtime(args.runtime_factory, project_root)
    else:
        from .identity_compat import canonical_auren_home
        from .local_composition import create_local_composition
        project_key = hashlib.sha256(str(project_root).encode("utf-8")).hexdigest()[:24]
        composition = create_local_composition(
            project_root,
            state_root=canonical_auren_home() / "remote-mcp-runtime" / project_key,
        )
        runtime = composition.runtime
    try:
        service = service_from_environment(
            runtime, project_root=project_root, composition=composition,
        )
        app = AurenMCPASGI(service)
        uvicorn.run(
            app, host=args.host, port=args.port, proxy_headers=True,
            forwarded_allow_ips=os.environ.get("AUREN_MCP_FORWARDED_ALLOW_IPS", "127.0.0.1,::1"),
            lifespan="on", access_log=False,
        )
    finally:
        if composition is not None:
            composition.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
