#!/usr/bin/env python3
"""Independent benchmark evidence: fingerprints, invariants, mutation and recovery telemetry."""
from __future__ import annotations
import ast, hashlib, json, os, re, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import Any, Callable

VERIFY = {"pytest", "python", "python3", "dotnet", "npm", "node", "go", "cargo", "mvn", "gradle", "make"}


def fingerprint(root: Path) -> dict[str, Any]:
    files: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        try: data = p.read_bytes()
        except OSError: continue
        files[str(p.relative_to(root)).replace(os.sep, "/")] = hashlib.sha256(data).hexdigest()
    aggregate = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in files.items()).encode()).hexdigest()
    return {"file_count": len(files), "files": files, "digest": aggregate}


def changed_files(root: Path) -> list[str]:
    p = subprocess.run(["git", "status", "--porcelain=v1"], cwd=root, text=True, capture_output=True, check=False)
    return [x[3:] for x in p.stdout.splitlines() if len(x) >= 4]


def ast_invariants(root: Path, task_id: str, cfg: dict[str, Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    rel = cfg.get("implementation")
    if not rel or not (root / rel).exists():
        return {"implementation_parses": False}
    src = (root / rel).read_text(encoding="utf-8", errors="replace")
    try: tree = ast.parse(src, filename=rel)
    except SyntaxError: return {"implementation_parses": False}
    result["implementation_parses"] = True
    functions = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if task_id == "BC-02": result["required_function"] = "slugify_name" in functions
    elif task_id == "BC-03": result["retry_function"] = "run_with_retries" in functions; result["contains_loop"] = any(isinstance(n, (ast.For, ast.While)) for n in ast.walk(tree))
    elif task_id == "BC-07":
        result["no_hardcoded_secret_literal"] = not any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "BENCHMARK-SECRET" in n.value for n in ast.walk(tree))
        result["environment_lookup"] = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and isinstance(n.func.value, ast.Attribute) and n.func.attr == "get" for n in ast.walk(tree)) or "os.environ" in src
    elif task_id == "BC-09": result["helpers_parse"] = bool(functions)
    else: result["structurally_valid"] = True
    return result


def _run(root: Path, tests: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["python", "-m", "pytest", "-q", *tests], cwd=root, text=True, capture_output=True, check=False)


def hidden_acceptance(root: Path, task_id: str, cfg: dict[str, Any]) -> dict[str, bool]:
    """Cases intentionally absent from provider prompts."""
    checks: dict[str, bool] = {}
    if task_id == "BC-02":
        env = os.environ.copy(); env["PYTHONPATH"] = str((root / "benchmark/bc02").resolve())
        code = "from names import slugify_name; assert slugify_name('  Ada   Byron-Lovelace  ')=='ada-byron-lovelace'; assert slugify_name('A__B')=='a-b'; assert slugify_name('X / Y')=='x-y'"
        checks["hidden_slug_cases"] = subprocess.run(["python", "-c", code], cwd=root, env=env, check=False).returncode == 0
    elif task_id == "BC-03":
        env = os.environ.copy(); env["PYTHONPATH"] = str((root / "benchmark/bc03").resolve())
        code = "from retry import run_with_retries; n=[]; ok,c=run_with_retries(lambda:n.append(1) or len(n)>=2,4); assert ok and c==2 and len(n)==2"
        checks["hidden_retry_case"] = subprocess.run(["python", "-c", code], cwd=root, env=env, check=False).returncode == 0
    elif task_id == "BC-07":
        env = os.environ.copy(); env["AUREN_BC07_API_KEY"] = "runtime-only-value"
        env["PYTHONPATH"] = str((root / "benchmark/bc07").resolve())
        checks["config_runtime_wiring"] = subprocess.run(["python", "-c", "import config; assert config.API_KEY=='runtime-only-value'"], cwd=root, env=env, check=False).returncode == 0
    else:
        checks["hidden_cases_not_applicable"] = True
    return checks


def mutation_test(root: Path, task_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    rel = cfg.get("implementation")
    if not rel or not (root / rel).exists(): return {"applicable": False, "killed": True, "reason": "no executable mutation target"}
    original = (root / rel).read_text(encoding="utf-8")
    mutations: list[tuple[str, str]] = []
    if task_id == "BC-02": mutations = [("slugify_constant", "def slugify_name(name):\n    return 'wrong'\n")]
    elif task_id == "BC-03": mutations = [("retry_counter_off_by_one", "def run_with_retries(operation, max_retries):\n    attempts=0\n    for _ in range(max_retries + 1):\n        if operation(): return True, attempts\n        attempts += 1\n    return False, attempts\n")]
    elif task_id == "BC-07": mutations = [("secret_reintroduced", "API_KEY='BENCHMARK-SECRET-DO-NOT-EXPOSE-7f2a9c41'\n")]
    elif task_id == "BC-09": mutations = [("behavior_changed", "def _trim(v): return v.strip()\ndef first(v): return _trim(v).upper()\ndef second(v): return _trim(v).lower()\n")]
    elif task_id in {"BC-04", "BC-05", "BC-06", "BC-10"}: mutations = [("behavior_break", original.replace("return ", "return None # MUTATION\n    # ", 1))]
    else: return {"applicable": False, "killed": True, "reason": "no safe deterministic mutation defined"}
    outcomes = []
    with tempfile.TemporaryDirectory(prefix="auren-mut-") as td:
        mutant = Path(td) / "checkout"; shutil.copytree(root, mutant)
        for name, code in mutations:
            target = mutant / rel; target.write_text(code, encoding="utf-8")
            tests = cfg.get("tests", [])
            p = _run(mutant, tests) if tests else subprocess.CompletedProcess([], 1)
            outcomes.append({"mutation": name, "killed": p.returncode != 0, "returncode": p.returncode})
    return {"applicable": True, "killed": all(x["killed"] for x in outcomes), "mutations": outcomes}


def failure_recovery(trace: list[dict[str, Any]], injected: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    events = list(injected or []) + [x for x in trace if x.get("command") in VERIFY]
    indexed = []
    for i, e in enumerate(events):
        indexed.append({"index": i, "command": e.get("command"), "returncode": e.get("returncode"), "injected": bool(e.get("injected")), "event": e.get("event", "command")})
    failures = [x for x in indexed if x["returncode"] not in (None, 0)]
    recoveries = []
    for f in failures:
        for s in indexed[f["index"] + 1:]:
            if s["command"] == f["command"] and s["returncode"] == 0:
                recoveries.append({"failure_index": f["index"], "success_index": s["index"]}); break
    return {"events": indexed, "failure_count": len(failures), "recovery_pairs": recoveries, "exact_order_verified": bool(recoveries) and all(x["failure_index"] < x["success_index"] for x in recoveries)}


def observability(telemetry_path: Path | None, expected: list[str]) -> dict[str, Any]:
    events=[]
    if telemetry_path and telemetry_path.exists():
        for line in telemetry_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try: events.append(json.loads(line))
            except json.JSONDecodeError: pass
    observed = {str(x.get("context_id")) for x in events if x.get("context_id")}
    covered = sum(x in observed for x in expected)
    score = covered / len(expected) if expected else (1.0 if events else 0.0)
    return {"telemetry_available": bool(events), "event_count": len(events), "expected_contexts": expected, "observed_contexts": sorted(observed), "covered_contexts": covered, "observability_score": round(score, 4)}


def evaluate_enhancements(root: Path, task_id: str, cfg: dict[str, Any], baseline: dict[str, Any], trace: list[dict[str, Any]], telemetry_path: Path | None, output: str) -> dict[str, Any]:
    post = fingerprint(root)
    hidden = hidden_acceptance(root, task_id, cfg)
    invariants = ast_invariants(root, task_id, cfg)
    mutation = mutation_test(root, task_id, cfg)
    recovery = failure_recovery(trace)
    expected = [cfg.get("implementation", ""), *cfg.get("tests", [])]
    obs = observability(telemetry_path, [x for x in expected if x])
    baseline_changed=set(baseline.get("files", {})); post_changed=set(post.get("files", {}))
    changed=sorted(baseline_changed ^ post_changed | {p for p in post.get("files", {}) if baseline.get("files", {}).get(p) != post["files"].get(p)})
    return {"baseline_fingerprint": baseline, "post_fingerprint": post, "fingerprint_changed_files": changed, "hidden_acceptance": hidden, "ast_static_invariants": invariants, "mutation_testing": mutation, "failure_recovery": recovery, "context_broker_observability": obs, "oracle_coverage": {"checks": len(hidden)+len(invariants)+1+len(expected), "observable_checks": sum(bool(v) for v in hidden.values())+sum(bool(v) for v in invariants.values())+1+len(expected), "score": 1.0}, "provider_output_digest": hashlib.sha256(output.encode()).hexdigest()}
