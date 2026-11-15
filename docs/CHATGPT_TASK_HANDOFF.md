# ChatGPT task handoff and guarded proposal lifecycle

AUREN treats ChatGPT as an untrusted proposal author. Repository scope, approval, Guard policy, execution and evidence remain under AUREN control.

## Export a focused package

Run from an AUREN checkout:

```powershell
python auren_cli.py handoff export --project-root C:\Work\MyProject --output .\task-package.json --task "Implement the requested change" --accept "Required tests pass" --accept "No unrelated files change" --file src\feature.py --file tests\test_feature.py
```

The package contains the exact Git HEAD, repository origin, root instruction files, explicitly selected files and acceptance criteria. Common credential paths are excluded; common secret-like strings are redacted. Review the package before sharing it. Filtering is defense in depth, not a guarantee that all private data has been removed.

## Return and validate a proposal

Use UTF-8 JSON with this shape:

```json
{
  "schema_version": 1,
  "kind": "auren.chatgpt_proposal",
  "base_commit": "<40-character SHA from task-package.json>",
  "changes": [{"path": "src/feature.py", "content": "complete proposed file contents"}]
}
```

AUREN rejects stale bases, duplicate paths, absolute paths, traversal, reserved state directories, symlinks, NUL/binary content, oversized files and common secret-like values. Proposal content is data. It is never evaluated, executed or treated as trusted instructions.

```powershell
python auren_cli.py handoff validate --project-root C:\Work\MyProject --proposal .\proposal.json
```

## Approval, Guard and isolated application

The Python API apply_approved_proposal requires two injected callbacks bound by the trusted AUREN host: a durable approval verifier and the live Guard verifier. Both must approve the exact proposal digest and return evidence references. Guard must also return the active policy digest. Boolean flags, caller-supplied JSON and ChatGPT statements are not approval receipts.

The code creates a task-specific Git worktree at the exact base SHA, asks Guard to evaluate that worktree and proposal, then writes only approved files there. The source checkout remains unchanged. Failed worktrees remain available for explicit review and recovery.

The callbacks must be wired to AUREN's live operator identity, durable approval store, current Guard policy and evidence provider. Until that host adapter is connected, import validation is available but execution remains blocked. Do not use test callbacks in production.

## GitHub delivery

deliver_with_gh uses the laptop's local gh authentication, commits and pushes the isolated branch, opens a pull request and verifies that the PR head SHA equals the local commit SHA. It reports checks for that PR; it does not merge. Required checks must pass on the exact head before merge.



```powershell
python auren_cli.py handoff deliver --worktree C:\Work\MyProject\.auren-worktrees\<proposal-id> --title "Implement approved change" --body "Summary and test evidence" --file src\feature.py --file tests\test_feature.py
```

Authenticate locally with gh auth login and verify with gh auth status. Credentials are never copied into the task package.

## Independent projects

```powershell
python auren_cli.py handoff new-project --path C:\Work\NewProject --name NewProject
```

This creates a separate Git repository and AUREN project profile. Do not copy .auren credentials, task databases or approvals between projects. Existing GitHub checkouts continue to use AUREN's managed GitHub workspace provisioner. Start each project as an explicit project-bound session; active tasks cannot silently migrate.

## Acceptance boundary

Unit tests cover package export, redaction, proposal path and secret checks, stale revision rejection, approval/Guard callback requirements, isolated worktree application and independent project initialization. Windows acceptance still requires an on-device run covering export/import, switching projects, restart/recovery, local MCP, browser console, desktop shell, GitHub CLI delivery and exact-head CI. CI alone does not prove laptop acceptance.
