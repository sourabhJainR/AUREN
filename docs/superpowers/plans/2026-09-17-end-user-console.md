# End-User Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AUREN a single local, read-only console and CLI status surface that exposes existing execution, learning, observability, installation, and recovery state without creating a second runtime or state store.

**Architecture:** Add a small standard-library module, `portable.auren_console`, responsible only for collecting existing machine-scoped state and rendering a responsive HTML dashboard or JSON status document. Wire it into the existing `portable.auren_runtime` CLI as `status` and `console`; keep the HTTP server loopback-only and read-only. Reuse existing state files and parsers where available, and degrade individual sections to explicit `unknown`/`unavailable` states when data is absent or malformed.

**Tech Stack:** Python 3 standard library, `http.server`, `urllib.parse`, HTML/CSS/JS embedded in a single response, JSONL/SQLite read-only access, unittest.

**Spec:** `docs/superpowers/specs/2026-09-17-end-user-console-design.md`

## Global Constraints

- The console must not become an execution, learning, scheduling, or promotion owner.
- The server must bind to loopback only; no public-interface option.
- No repository source, credentials, full prompts, or full task payloads are served.
- No POST/PUT/DELETE endpoint is added in this slice.
- Existing CLI command behavior remains compatible.
- Missing/malformed state is exposed as an explicit unknown/unavailable condition.
- Use only Python standard-library dependencies in the portable runtime.

---

### Task 1: Build the state snapshot API

**Files:**
- Create: `portable/auren_console.py`
- Test: `tests/test_auren_console.py`

**Interfaces:**
- Consumes: `~/.auren/active.json`, `history.jsonl`, `observability/traces.jsonl`, `sessions/`, `automation/automation.db`, `memory/memory.db`.
- Produces: `ConsoleSnapshot` dataclass and `collect_snapshot(auren_home: Path) -> ConsoleSnapshot`.

- [ ] **Step 1: Write the failing tests**

```python
class TestConsoleSnapshot(unittest.TestCase):
    def test_empty_home_is_healthy_enough_to_render(self):
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.installation["status"], "not_installed")
        self.assertEqual(snapshot.runs["count"], 0)
        self.assertEqual(snapshot.learning["status"], "unknown")

    def test_secret_like_trace_metadata_is_redacted(self):
        (self.home / "observability").mkdir()
        (self.home / "observability" / "traces.jsonl").write_text(
            '{"trace_id":"t1","status":"ok","metadata":{"api_key":"secret"},"spans":[],"scores":[]}\n',
            encoding="utf-8",
        )
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.runs["recent"][0]["metadata"], "[REDACTED]")

    def test_malformed_sources_do_not_break_snapshot(self):
        (self.home / "active.json").write_text("{broken", encoding="utf-8")
        (self.home / "observability").mkdir()
        (self.home / "observability" / "traces.jsonl").write_text("not-json\n", encoding="utf-8")
        snapshot = collect_snapshot(self.home)
        self.assertEqual(snapshot.installation["status"], "unknown")
        self.assertEqual(snapshot.runs["status"], "unknown")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python -m unittest tests.test_auren_console -v
```

Expected: import/attribute failures because `portable.auren_console` does not yet exist.

- [ ] **Step 3: Implement the minimal snapshot model**

Create `ConsoleSnapshot` plus small private readers. The readers must:

```python
# public contract
@dataclass(frozen=True)
class ConsoleSnapshot:
    generated_at: str
    installation: Mapping[str, Any]
    runs: Mapping[str, Any]
    learning: Mapping[str, Any]
    recovery: Mapping[str, Any]
    health: Mapping[str, Any]
    capabilities: Mapping[str, Any]
```

Read at most 25 recent JSONL records, never expose full prompt/task bodies, and replace secret-like values in metadata using the same key-pattern concept as `agency_observability._safe`.

For SQLite, open with read-only URI mode where supported and query schema defensively. On any missing DB/table/query mismatch return `{"status":"unknown"}` for that section instead of failing the whole snapshot.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
python -m unittest tests.test_auren_console -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/auren_console.py tests/test_auren_console.py
git commit -m "feat: add end-user console state snapshot"
```

---

### Task 2: Add status rendering and console HTTP server

**Files:**
- Modify: `portable/auren_console.py`
- Test: `tests/test_auren_console.py`

**Interfaces:**
- Consumes: `ConsoleSnapshot` and `collect_snapshot` from Task 1.
- Produces: `render_status(snapshot, as_json=False) -> str`, `render_dashboard(snapshot) -> str`, and `serve_console(auren_home, host="127.0.0.1", port=0, open_browser=False) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
class TestConsoleRendering(unittest.TestCase):
    def test_json_status_has_stable_top_level_keys(self):
        payload = json.loads(render_status(collect_snapshot(self.home), as_json=True))
        self.assertEqual(
            set(payload),
            {"generated_at", "installation", "runs", "learning", "recovery", "health", "capabilities"},
        )

    def test_dashboard_contains_human_facing_sections(self):
        html = render_dashboard(collect_snapshot(self.home))
        self.assertIn("AUREN Console", html)
        self.assertIn("Overview", html)
        self.assertIn("Runs", html)
        self.assertIn("Learning", html)
        self.assertIn("Recovery", html)

    def test_http_handler_rejects_mutating_methods(self):
        self.assertEqual(allowed_methods(), ("GET", "HEAD"))
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python -m unittest tests.test_auren_console.TestConsoleRendering -v
```

Expected: missing rendering/HTTP helpers.

- [ ] **Step 3: Implement read-only HTTP serving**

Use `http.server.ThreadingHTTPServer`, but bind only to `127.0.0.1` regardless of user input. Route only:

```text
GET /                  -> HTML dashboard
GET /api/status        -> JSON status
GET /health            -> {"status":"ok"}
HEAD /...              -> same headers, no body
```

Return `405` for all other methods and `404` for unknown paths. Do not serve arbitrary files. Print the actual bound URL when port `0` is used. `--open` may call `webbrowser.open(url)` but a failure to open the browser must not fail the server startup.

The dashboard should use system fonts, compact cards, status chips, responsive layout, and plain-language labels. Keep the page dependency-free and under a few hundred KB.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
python -m unittest tests.test_auren_console -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/auren_console.py tests/test_auren_console.py
git commit -m "feat: add read-only local AUREN console"
```

---

### Task 3: Wire CLI commands and exports

**Files:**
- Modify: `portable/auren_runtime.py`
- Modify: `portable/__init__.py`
- Test: `tests/test_auren_runtime.py`

**Interfaces:**
- Consumes: `collect_snapshot`, `render_status`, and `serve_console`.
- Produces: `status` and `console` CLI subcommands while preserving build/verify/install/update/check-update/rollback.

- [ ] **Step 1: Write failing CLI parsing tests**

```python
def test_status_command_parses_json_flag(self):
    args = parser().parse_args(["status", "--json"])
    self.assertEqual(args.command, "status")
    self.assertTrue(args.json)


def test_console_defaults_to_loopback_and_ephemeral_port(self):
    args = parser().parse_args(["console"])
    self.assertEqual(args.host, "127.0.0.1")
    self.assertEqual(args.port, 0)
    self.assertFalse(args.open_browser)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
python -m unittest tests.test_auren_runtime -v
```

Expected: parser rejects the new subcommands.

- [ ] **Step 3: Add parser wiring and dispatch**

In `portable.auren_runtime.parser()` add:

```python
status_parser = sub.add_parser("status")
status_parser.add_argument("--json", action="store_true")
status_parser.add_argument("--auren-home", type=Path, default=None)

console_parser = sub.add_parser("console")
console_parser.add_argument("--host", default="127.0.0.1")
console_parser.add_argument("--port", type=int, default=0)
console_parser.add_argument("--open", dest="open_browser", action="store_true")
console_parser.add_argument("--auren-home", type=Path, default=None)
```

Dispatch status through `render_status(collect_snapshot(...), args.json)`. Dispatch console through `serve_console(...)`.

Reject any console host other than `127.0.0.1`, `localhost`, or `::1`; normalize accepted loopback names to `127.0.0.1` before binding.

Export `ConsoleSnapshot`, `collect_snapshot`, `render_status`, `render_dashboard`, and `serve_console` from `portable.__init__`.

- [ ] **Step 4: Run targeted and existing CLI tests**

Run:

```bash
python -m unittest tests.test_auren_runtime tests.test_auren_console -v
python -m unittest discover -s tests -v
```

Expected: all relevant tests PASS.

- [ ] **Step 5: Commit**

```bash
git add portable/auren_runtime.py portable/__init__.py tests/test_auren_runtime.py
 git commit -m "feat: expose AUREN console and status commands"
```

---

### Task 4: Document the end-user path

**Files:**
- Modify: `README.md`
- Modify: `portable/README.md`
- Modify: `docs/USAGE_AND_PLATFORM_INTEGRATION.md`

**Interfaces:**
- Consumes: final CLI contract from Task 3.
- Produces: first-run, status, console, and troubleshooting instructions.

- [ ] **Step 1: Add a first-run section to the root README**

Add a short path:

```text
python auren_cli.py auren-portable.zip
python -m portable.auren_runtime status
python -m portable.auren_runtime console --open
```

Explain that the console is local/read-only and is the easiest place to inspect health, recent work, learning, and recovery state.

- [ ] **Step 2: Add portable CLI details**

Document the exact endpoints and the loopback-only guarantee. Include `status --json` for scripts and CI diagnostics.

- [ ] **Step 3: Update platform usage guidance**

Add the console to the normal golden path immediately after installation and before a first substantial task.

- [ ] **Step 4: Run documentation grep checks**

Run:

```bash
grep -R "auren_runtime.*console\|auren_runtime.*status" README.md portable/README.md docs/USAGE_AND_PLATFORM_INTEGRATION.md
```

Expected: each document contains the new commands.

- [ ] **Step 5: Commit**

```bash
git add README.md portable/README.md docs/USAGE_AND_PLATFORM_INTEGRATION.md
git commit -m "docs: document AUREN end-user console"
```

---

### Task 5: Full regression and release readiness

**Files:**
- Test: `tests/test_auren_console.py`
- Test: `tests/test_auren_runtime.py`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified, merge-ready feature branch with no changes to execution policy.

- [ ] **Step 1: Run portable and full test suites**

Run:

```bash
python -m unittest discover -s tests -v
python scripts/validate_plugin.py
python scripts/run_evals.py
```

Expected: PASS or an existing, explicitly identified baseline failure unrelated to the feature.

- [ ] **Step 2: Exercise the real CLI manually**

Run:

```bash
python -m portable.auren_runtime status
python -m portable.auren_runtime status --json
python -m portable.auren_runtime console --port 0
```

Expected: status prints without traceback; JSON parses; console prints a loopback URL and serves the dashboard.

- [ ] **Step 3: Inspect repository isolation**

Run from a temporary target repository and verify the console command does not create or modify target-repository files. The only intended state reads/writes are outside the target repository.

- [ ] **Step 4: Record verification evidence**

Capture command names and pass/fail outcomes in the PR body. Mention that the server is loopback-only and read-only.

- [ ] **Step 5: Commit any final test-only adjustments**

```bash
git add tests/test_auren_console.py tests/test_auren_runtime.py
 git commit -m "test: verify end-user console integration"
```
