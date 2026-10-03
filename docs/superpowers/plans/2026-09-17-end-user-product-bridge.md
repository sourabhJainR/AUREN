# End-User Product Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single, read-only AUREN control surface that makes installation health, runtime state, provider readiness, activity, and repository intelligence discoverable without changing the existing orchestration engine.

**Architecture:** Add `portable/user_experience.py` as a read-only facade over existing AUREN state and repository-intelligence APIs. Route only `doctor`, `status`, `demo`, and the zero-argument invocation through that facade; all existing package-management commands continue through `portable.auren_runtime` unchanged.

**Tech Stack:** Python 3.11, standard library, existing `portable.repository_intelligence`, existing SQLite scheduler state, existing AUREN installation metadata, unittest.

**Spec:** `docs/superpowers/specs/2026-09-17-end-user-product-bridge-design.md`

## Global Constraints

- No new persistent state store.
- No target-repository mutation from the new UX commands.
- No network calls from `doctor`, `status`, or `demo`.
- Existing build/verify/install/update/check-update/rollback behavior remains unchanged.
- Optional providers and extensions remain optional.
- Python CI baseline is 3.11.
- Human output is concise; `--json` output is deterministic.
- Demo is read-only and does not claim to execute engineering work.

---

### Task 1: Add failing user-experience contract tests

**Files:**
- Create: `tests/test_user_experience.py`

**Interfaces:**
- Consumes: intended public functions from `portable.user_experience`: `build_status`, `run_doctor`, `run_demo`, `render_json`.
- Produces: executable tests defining the public contract before implementation.

- [ ] **Step 1: Write the failing tests**

```python
import json
import os
import tempfile
import unittest
from pathlib import Path

from portable.user_experience import build_status, render_json, run_demo, run_doctor


class UserExperienceTests(unittest.TestCase):
    def test_doctor_reports_uninstalled_auren_as_warning_not_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            checks, exit_code = run_doctor(project, auren_home=Path(temp) / ".auren")
            self.assertEqual(exit_code, 0)
            self.assertTrue(any(c.name == "installation" and c.status == "WARN" for c in checks))

    def test_status_is_read_only_when_auren_home_does_not_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            auren_home = Path(temp) / ".auren"
            snapshot = build_status(auren_home=auren_home, project_root=Path(temp))
            self.assertFalse(auren_home.exists())
            self.assertEqual(snapshot["installation"]["installed"], False)

    def test_json_render_is_deterministic_and_structured(self):
        payload = {"z": 1, "a": {"b": True}}
        self.assertEqual(render_json(payload), '{\n  "a": {\n    "b": true\n  },\n  "z": 1\n}')

    def test_demo_returns_repository_evidence_without_mutating_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            (project / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
            before = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
            result = run_demo(project, token_budget=300)
            after = sorted(p.relative_to(project).as_posix() for p in project.rglob("*"))
            self.assertEqual(before, after)
            self.assertTrue(result["snapshot"])
            self.assertIn("metrics", result)
            self.assertIn("files", result)

    def test_status_exposes_provider_and_skill_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot = build_status(auren_home=Path(temp) / ".auren", project_root=Path(temp))
            self.assertIn("providers", snapshot)
            self.assertIn("skills", snapshot)
            self.assertEqual(set(snapshot["providers"]), {"claude", "codex", "gemini"})
            self.assertEqual(set(snapshot["skills"]), {"agents", "claude", "gemini"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test to verify it fails for the expected reason**

Run: `python -m unittest tests.test_user_experience -v`

Expected: collection failure because `portable.user_experience` does not exist yet.

- [ ] **Step 3: Commit the red test**

```bash
git add tests/test_user_experience.py
git commit -m "test: define end-user control surface contract"
```

---

### Task 2: Implement the read-only user-experience facade

**Files:**
- Create: `portable/user_experience.py`
- Modify: `portable/__init__.py` only if an explicit public export is required by existing package conventions.

**Interfaces:**
- Consumes: `portable.repository_intelligence.RepositoryIntelligence`, existing `~/.auren` installation files, existing scheduler SQLite schema.
- Produces:
  - `CheckResult(name: str, status: str, message: str, remedy: str | None = None)`
  - `run_doctor(project_root: Path | str | None = None, *, auren_home: Path | str | None = None) -> tuple[list[CheckResult], int]`
  - `build_status(*, auren_home: Path | str | None = None, project_root: Path | str | None = None) -> dict[str, object]`
  - `run_demo(project_root: Path | str, *, token_budget: int = 1200) -> dict[str, object]`
  - `render_json(value: object) -> str`
  - `render_doctor(checks: list[CheckResult], exit_code: int, *, json_output: bool = False) -> str`
  - `render_status(snapshot: dict[str, object], *, json_output: bool = False) -> str`

- [ ] **Step 1: Implement only the structures required by the tests**

Use only the standard library and existing repository APIs. Keep all filesystem and SQLite reads guarded so a missing optional state source becomes an informational warning rather than a traceback.

```python
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .repository_intelligence import RepositoryIntelligence


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    message: str
    remedy: str | None = None


def _path(value: Path | str | None, default: Path) -> Path:
    return (Path(value).expanduser().resolve() if value is not None else default.expanduser().resolve())


def render_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
```

Build the remaining helpers around the canonical paths already used by `auren_runtime.py`: `~/.auren/active.json`, `~/.auren/history.jsonl`, `~/.auren/versions`, `~/.auren/automation/automation.db`, `~/.auren/observability/traces.jsonl`, and the three Agent Skills destinations.

- [ ] **Step 2: Implement installation and readiness discovery**

The installation check must classify three cases distinctly:

1. no installation: `WARN`, actionable message;
2. valid installation: `PASS`;
3. unreadable or internally inconsistent installation: `FAIL`.

Provider detection must use `shutil.which` for `claude`, `codex`, and `gemini`. Skill detection must inspect only the known user directories and never create them.

- [ ] **Step 3: Implement read-only status discovery**

Read active metadata, immutable version directory count, skill state, provider state, scheduler summaries, observability state, and report count. For scheduler tasks, never expose raw task payloads; classify each schedule by `schedule`, `kind`, or `custom` and include only enabled state, next-run time, attempt count, and recent run status. Query SQLite without writes.

- [ ] **Step 4: Implement deterministic demo**

```python
def run_demo(project_root: Path | str, *, token_budget: int = 1200) -> dict[str, object]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"project root does not exist: {root}")
    if token_budget < 100:
        raise ValueError("token_budget must be at least 100")
    repo = RepositoryIntelligence.build(root)
    answer = repo.answer(
        "repository structure dependencies and tests",
        mode="search",
        token_budget=token_budget,
        max_files=8,
        context_lines=12,
        graph_depth=1,
    )
    return {
        "project_detected": True,
        "snapshot": answer.snapshot,
        "files": list(answer.files),
        "metrics": dict(answer.metrics),
        "token_estimate": answer.token_estimate,
        "unknowns": list(answer.unknowns),
        "next_stages": ["plan", "change", "verification", "review", "evidence"],
    }
```

- [ ] **Step 5: Implement concise human renderers**

Doctor output should use one line per check plus a single next-action line. Status output should show installation, hosts, schedules, activity, and the most useful next command. JSON output must use the same underlying snapshot data.

- [ ] **Step 6: Run the focused tests to verify they pass**

Run: `python -m unittest tests.test_user_experience -v`

Expected: all user-experience tests pass.

- [ ] **Step 7: Commit the implementation**

```bash
git add portable/user_experience.py
git commit -m "feat: add read-only AUREN user experience facade"
```

---

### Task 3: Wire the facade into the stable CLI

**Files:**
- Modify: `auren_cli.py`
- Modify: `README.md`
- Modify: `docs/USAGE_AND_PLATFORM_INTEGRATION.md`

**Interfaces:**
- Consumes: `portable.user_experience` public functions.
- Produces: user-facing `python auren_cli.py`, `doctor`, `status`, and `demo` commands while retaining existing distribution commands.

- [ ] **Step 1: Add CLI dispatch before runtime loading**

The command router must handle zero arguments and the three UX commands before `_load_runtime()` so they remain useful even when the user is diagnosing an incomplete installation.

Supported forms:

```text
python auren_cli.py
python auren_cli.py doctor [--json] [--project-root PATH] [--auren-home PATH]
python auren_cli.py status [--json] [--project-root PATH] [--auren-home PATH]
python auren_cli.py demo [PROJECT_ROOT] [--json] [--token-budget N]
```

Unknown commands continue into the existing runtime parser so existing errors and package-management behavior remain unchanged.

- [ ] **Step 2: Add the zero-argument status/welcome renderer**

Use `build_status()` and `render_status()` to make a no-argument invocation immediately explain what AUREN is installed as and which next command is useful. Do not initialize state or perform network requests.

- [ ] **Step 3: Document the new golden path**

Add this sequence near the top of the README and platform usage guide:

```text
python auren_cli.py doctor
python auren_cli.py status
python auren_cli.py demo .
```

Explain that these are read-only and work without third-party providers.

- [ ] **Step 4: Add CLI dispatch regression tests**

Extend `tests/test_user_experience.py` with an import-level test that calls `auren_cli.main(["status", "--json", ...])` using a temporary AUREN home and captures stdout. Also test `auren_cli.main([])` returns `0`.

- [ ] **Step 5: Run the full deterministic suite locally where available**

Run:

```bash
python -m unittest tests.test_user_experience -v
python -m unittest discover -s tests -v
python -m py_compile auren_cli.py portable/user_experience.py
```

Expected: no failures, no syntax errors.

- [ ] **Step 6: Commit the CLI/doc changes**

```bash
git add auren_cli.py README.md docs/USAGE_AND_PLATFORM_INTEGRATION.md tests/test_user_experience.py
git commit -m "feat: expose AUREN doctor status and demo commands"
```

---

### Task 4: CI, review, and integration verification

**Files:**
- No new production files.

**Interfaces:**
- Consumes: the branch commits above.
- Produces: a reviewed pull request with green repository checks and evidence that existing distribution behavior is preserved.

- [ ] **Step 1: Open the pull request against `main`**

Title: `feat: bridge AUREN end-user experience gap`

Body must summarize:
- read-only control surface;
- no new state stores;
- preserved existing package-management path;
- doctor/status/demo usage;
- tests added.

- [ ] **Step 2: Wait for all repository checks and inspect failures**

Run/status to inspect every check, not just a single headline workflow. Any failure gets a targeted fix on the same branch.

- [ ] **Step 3: Perform the third-party PR review**

Review the final diff for:
- no target-repository writes;
- no hidden network access in UX commands;
- no duplicate state ownership;
- stable JSON contract;
- privacy-safe status output;
- Windows/macOS/Linux path handling;
- no breakage to existing CLI distribution commands.

- [ ] **Step 4: Fix review findings and wait for CI again**

Repeat until all actionable findings are addressed and all required checks are green.

- [ ] **Step 5: Merge only after green verification**

Use squash merge into `main` and record the merge commit SHA in the final task report.
