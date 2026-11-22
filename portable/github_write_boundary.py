"""Fail-closed preflight for GitHub repository, branch, commit and PR writes.

This module does not perform GitHub writes. It binds a configured MCP write call
to the active canonical execution plan and produces metadata that the authority
resolver must approve before the MCP child process can launch.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_READ_ONLY_TOOLS = frozenset({
    "get_file_contents", "get_content", "get_directory_contents",
    "search_code", "search_repositories", "search_issues", "search_users",
    "search_commits", "search_pull_requests", "get_repository", "get_me",
    "get_commit", "list_commits", "list_branches", "get_branch",
    "get_pull_request", "list_pull_requests", "get_pull_request_files",
    "get_pull_request_status", "get_pull_request_reviews",
    "get_pull_request_comments", "get_issue", "list_issues",
    "get_issue_comments", "list_issue_comments", "list_tags",
    "list_releases", "get_latest_release", "get_release_by_tag",
    "list_workflows", "list_workflow_runs", "get_workflow_run",
    "get_workflow_job", "get_job_logs", "get_check_run", "list_check_runs",
    "get_commit_status", "get_combined_status",
})
_WRITE_TOOLS = {
    "create_branch": "create_branch",
    "push_files": "push_files",
    "create_or_update_file": "create_or_update_file",
    "create_file": "create_file",
    "update_file": "update_file",
    "delete_file": "delete_file",
    "create_commit": "create_commit",
    "update_ref": "update_ref",
    "update_branch": "update_branch",
    "create_pull_request": "create_pull_request",
    "update_pull_request": "update_pull_request",
    "merge_pull_request": "merge_pull_request",
    "delete_branch": "delete_branch",
}
_ACTIONS = {
    "create_branch": "repository.write",
    "push_files": "repository.write",
    "create_or_update_file": "repository.write",
    "create_file": "repository.write",
    "update_file": "repository.write",
    "delete_file": "delete_remote",
    "create_commit": "repository.write",
    "update_ref": "repository.write",
    "update_branch": "repository.write",
    "create_pull_request": "repository.write",
    "update_pull_request": "repository.write",
    "merge_pull_request": "merge",
    "delete_branch": "delete_remote",
}
_MUTATING_UNSUPPORTED = frozenset({
    "create_repository", "delete_repository", "create_issue", "update_issue",
    "add_issue_comment", "create_issue_comment", "create_pull_request_review",
    "create_pull_request_review_comment", "request_copilot_review",
    "assign_copilot_to_issue", "create_release", "update_release",
    "delete_release", "create_gist", "update_gist", "delete_gist",
    "create_workflow_dispatch", "cancel_workflow_run",
})


class GitHubWriteBoundaryError(PermissionError):
    """A GitHub mutation does not match the canonical repository write contract."""


def _tool_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if key.startswith("github_"):
        key = key[len("github_"):]
    return key


def is_github_server(argv: Sequence[str], tool_name: str) -> bool:
    if not isinstance(tool_name, str) or not tool_name.strip():
        return False
    if tool_name.casefold().startswith("github."):
        return True
    for token in argv:
        value = str(token).casefold()
        if "github" in value or Path(value).name in {"gh", "gh.exe"}:
            return True
    return False


def _sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value.casefold()):
        raise GitHubWriteBoundaryError(f"{field} must be a full 40-character commit SHA")
    return value.casefold()


def _branch(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GitHubWriteBoundaryError(f"{field} is required")
    result = value.strip()
    if result.startswith("refs/heads/"):
        result = result[len("refs/heads/"):]
    if ":" in result:
        result = result.rsplit(":", 1)[-1]
    if (
        len(result) > 240
        or result.startswith("-")
        or result.endswith(("/", "."))
        or "//" in result
        or ".." in result
        or "@{" in result
        or result.endswith(".lock")
        or any(ch in result for ch in " ~^:?*[\\")
        or any(part in {"", ".", ".."} for part in result.split("/"))
    ):
        raise GitHubWriteBoundaryError(f"{field} is not a safe Git branch name")
    return result


def _repository(arguments: Mapping[str, Any]) -> str:
    for key in ("repository", "repository_full_name", "repo_full_name", "repo_slug", "full_name"):
        value = arguments.get(key)
        if isinstance(value, str) and _SLUG.fullmatch(value):
            return value.casefold()
        if isinstance(value, Mapping):
            owner = value.get("owner")
            name = value.get("name") or value.get("repo")
            if isinstance(owner, str) and isinstance(name, str):
                candidate = f"{owner}/{name}"
                if _SLUG.fullmatch(candidate):
                    return candidate.casefold()
    owner, repo = arguments.get("owner"), arguments.get("repo")
    if isinstance(owner, str) and isinstance(repo, str):
        candidate = f"{owner}/{repo}"
        if _SLUG.fullmatch(candidate):
            return candidate.casefold()
    if isinstance(repo, str) and _SLUG.fullmatch(repo):
        return repo.casefold()
    raise GitHubWriteBoundaryError("GitHub write arguments must identify an exact owner/repo")


def _first(arguments: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        value = arguments.get(name)
        if value is not None:
            return value
    return None


def _expected_head_sha(arguments: Mapping[str, Any]) -> str:
    return _sha(_first(arguments, (
        "expected_head_sha", "expectedHeadSha", "expected_sha", "expectedSha",
    )), "expected_head_sha")


def _expected_base_sha(arguments: Mapping[str, Any]) -> str:
    return _sha(_first(arguments, (
        "expected_base_sha", "expectedBaseSha", "base_sha", "baseSha",
    )), "expected_base_sha")


def _paths(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("path", "file_path", "filePath", "path_in_repo", "repository_path"):
        value = arguments.get(key)
        if value is not None:
            values.append(value)
    for key in ("files", "file_changes", "fileChanges", "changes", "commit_files"):
        collection = arguments.get(key)
        if collection is None:
            continue
        if isinstance(collection, Mapping):
            values.extend(collection.keys())
        elif isinstance(collection, Sequence) and not isinstance(collection, (str, bytes)):
            for item in collection:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, Mapping):
                    path = _first(item, ("path", "file_path", "filePath", "filename"))
                    if path is not None:
                        values.append(path)
                    else:
                        raise GitHubWriteBoundaryError(f"{key} entries must identify a file path")
                else:
                    raise GitHubWriteBoundaryError(f"{key} must contain path strings or mappings")
        else:
            raise GitHubWriteBoundaryError(f"{key} must be a sequence or path mapping")
    if not values:
        raise GitHubWriteBoundaryError("GitHub file mutation must declare every written path")
    if any(not isinstance(path, str) or not path.strip() for path in values):
        raise GitHubWriteBoundaryError("GitHub file mutation contains an invalid path")
    normalized = tuple(dict.fromkeys(values))
    if len(normalized) != len(values):
        raise GitHubWriteBoundaryError("GitHub file mutation must not repeat a path")
    return normalized


def _plan_target(plan: Any) -> Any:
    target = getattr(plan, "target", None)
    if target is None or not callable(getattr(target, "authorize_path", None)):
        raise GitHubWriteBoundaryError("GitHub write requires an active canonical RepositoryTarget")
    if not isinstance(getattr(plan, "digest", None), str) or not re.fullmatch(r"[0-9a-f]{64}", plan.digest):
        raise GitHubWriteBoundaryError("GitHub write requires a digest-valid canonical execution plan")
    return target


@dataclass(frozen=True)
class GitHubWritePreflight:
    operation: str
    action: str
    repository: str
    branch: str
    base_sha: str
    expected_head_sha: str
    paths: tuple[str, ...]
    target: str
    plan_digest: str
    tool_name: str

    def payload(self) -> dict[str, Any]:
        value = {
            "schema_version": "auren-github-write-preflight-v1",
            "operation": self.operation,
            "action": self.action,
            "repository": self.repository,
            "branch": self.branch,
            "base_sha": self.base_sha,
            "expected_head_sha": self.expected_head_sha,
            "paths": list(self.paths),
            "target": self.target,
            "plan_digest": self.plan_digest,
            "tool_name": self.tool_name,
        }
        value["digest"] = _digest(value)
        return value


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_preflight_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GitHubWriteBoundaryError("GitHub write preflight metadata must be an object")
    required = {
        "schema_version", "operation", "action", "repository", "branch",
        "base_sha", "expected_head_sha", "paths", "target", "plan_digest",
        "tool_name", "digest",
    }
    if set(value) != required:
        raise GitHubWriteBoundaryError("GitHub write preflight metadata has missing or unknown fields")
    snapshot = dict(value)
    digest = snapshot.pop("digest")
    if snapshot.get("schema_version") != "auren-github-write-preflight-v1":
        raise GitHubWriteBoundaryError("unsupported GitHub write preflight schema")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or _digest(snapshot) != digest:
        raise GitHubWriteBoundaryError("GitHub write preflight digest is invalid")
    operation = snapshot.get("operation")
    if operation not in _WRITE_TOOLS.values() and operation not in _MUTATING_UNSUPPORTED:
        raise GitHubWriteBoundaryError("GitHub write operation is not registered")
    if snapshot.get("action") != _ACTIONS.get(operation):
        raise GitHubWriteBoundaryError("GitHub write action does not match its operation")
    for field in ("repository", "branch", "target", "tool_name"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise GitHubWriteBoundaryError(f"GitHub write {field} is required")
    _sha(snapshot.get("base_sha"), "base_sha")
    head_sha = snapshot.get("expected_head_sha")
    if head_sha:
        _sha(head_sha, "expected_head_sha")
    paths = snapshot.get("paths")
    if not isinstance(paths, list) or any(not isinstance(path, str) or not path for path in paths):
        raise GitHubWriteBoundaryError("GitHub write paths must be a list of non-empty strings")
    if not isinstance(snapshot.get("plan_digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", snapshot["plan_digest"]):
        raise GitHubWriteBoundaryError("GitHub write plan digest is invalid")
    snapshot["digest"] = digest
    return snapshot


def preflight_github_tool(
    *, argv: Sequence[str], tool_name: str, arguments: Mapping[str, Any],
    plan: Any | None, github_server: bool | None = None,
) -> dict[str, Any] | None:
    """Return host-created write metadata, allow audited reads, otherwise fail closed."""
    if not isinstance(arguments, Mapping):
        raise GitHubWriteBoundaryError("GitHub tool arguments must be an object")
    server = is_github_server(argv, tool_name) if github_server is None else github_server
    if type(server) is not bool:
        raise TypeError("github_server must be a boolean")
    key = _tool_key(tool_name)
    if key in _READ_ONLY_TOOLS:
        return None
    operation = _WRITE_TOOLS.get(key)
    if operation is None:
        if server:
            raise GitHubWriteBoundaryError(
                f"GitHub MCP tool {tool_name!r} is not in the audited read-only or write-operation registry"
            )
        return None
    if not server and not tool_name.casefold().startswith("github."):
        return None
    target = _plan_target(plan)
    repository = _repository(arguments)
    if repository != target.repository.casefold():
        raise GitHubWriteBoundaryError("GitHub write repository does not match the canonical plan target")

    expected_head_sha = ""
    paths: tuple[str, ...] = ()
    if operation == "create_branch":
        branch = _branch(_first(arguments, ("branch", "branch_name")), "branch")
        if branch != target.branch:
            raise GitHubWriteBoundaryError("GitHub branch creation does not match the canonical target branch")
        base_sha = _sha(_first(arguments, ("sha", "base_sha", "from_sha")), "branch base SHA")
        if base_sha != target.base_sha:
            raise GitHubWriteBoundaryError("GitHub branch creation uses a stale or unapproved base SHA")
    elif operation in {
        "push_files", "create_or_update_file", "create_file", "update_file",
        "delete_file", "create_commit", "update_ref", "update_branch",
    }:
        branch = _branch(_first(arguments, ("branch", "branch_name", "ref")), "branch")
        if branch != target.branch:
            raise GitHubWriteBoundaryError("GitHub write branch does not match the canonical target branch")
        expected_head_sha = _expected_head_sha(arguments)
        if expected_head_sha != target.base_sha:
            raise GitHubWriteBoundaryError("GitHub write expected head SHA is stale or differs from the approved base")
        base_sha = target.base_sha
        paths = tuple(target.authorize_path(path) for path in _paths(arguments)) if operation in {
            "push_files", "create_or_update_file", "create_file", "update_file", "delete_file", "create_commit",
        } else ()
        if operation == "create_commit":
            parents = arguments.get("parents")
            if not isinstance(parents, Sequence) or isinstance(parents, (str, bytes)) or tuple(parents) != (target.base_sha,):
                raise GitHubWriteBoundaryError("GitHub commit must have exactly the approved base SHA as its parent")
        if operation in {"update_ref", "update_branch"}:
            _sha(_first(arguments, ("new_sha", "newSha", "sha")), "new commit SHA")
    elif operation in {"create_pull_request", "update_pull_request", "merge_pull_request"}:
        head_branch = _branch(_first(arguments, ("head", "head_branch", "headBranch")), "pull request head branch")
        base_branch = _branch(_first(arguments, ("base", "base_branch", "baseBranch")), "pull request base branch")
        if head_branch != target.branch:
            raise GitHubWriteBoundaryError("pull request head branch does not match the canonical target branch")
        expected_base_branch = getattr(target, "pull_request_base_branch", "")
        if not expected_base_branch:
            raise GitHubWriteBoundaryError("canonical plan must declare pull_request_base_branch before PR writes")
        if base_branch != expected_base_branch:
            raise GitHubWriteBoundaryError("pull request base branch does not match the canonical plan")
        base_sha = _expected_base_sha(arguments)
        if base_sha != target.base_sha:
            raise GitHubWriteBoundaryError("pull request base SHA is stale or differs from the approved plan")
        expected_head_sha = _expected_head_sha(arguments)
        if operation != "create_pull_request":
            number = _first(arguments, ("pull_number", "pullNumber", "number"))
            if type(number) is not int or number < 1:
                raise GitHubWriteBoundaryError("pull request mutation requires a positive pull_number")
            target_name = f"{repository}#{number}@{expected_head_sha}:{head_branch}->{base_branch}@{base_sha}"
        else:
            target_name = f"{repository}#new@{expected_head_sha}:{head_branch}->{base_branch}@{base_sha}"
        return GitHubWritePreflight(
            operation=operation, action=_ACTIONS[operation], repository=repository,
            branch=head_branch, base_sha=base_sha, expected_head_sha=expected_head_sha,
            paths=(), target=target_name, plan_digest=plan.digest, tool_name=tool_name,
        ).payload()
    elif operation in {"delete_branch"}:
        branch = _branch(_first(arguments, ("branch", "branch_name", "ref")), "branch")
        if branch != target.branch:
            raise GitHubWriteBoundaryError("GitHub branch deletion does not match the canonical target branch")
        expected_head_sha = _expected_head_sha(arguments)
        if expected_head_sha != target.base_sha:
            raise GitHubWriteBoundaryError("GitHub branch deletion requires the exact approved branch SHA")
        base_sha = target.base_sha
    else:
        raise GitHubWriteBoundaryError(f"GitHub write operation {operation} is not supported by this boundary")

    return GitHubWritePreflight(
        operation=operation, action=_ACTIONS[operation], repository=repository,
        branch=branch, base_sha=base_sha, expected_head_sha=expected_head_sha,
        paths=paths, target=f"{repository}@{branch}", plan_digest=plan.digest,
        tool_name=tool_name,
    ).payload()


def validate_github_write_authorization(
    metadata: Mapping[str, Any], *, authorization: Any,
    request: Mapping[str, Any], call_binding: Any,
) -> None:
    """Require a canonical target, trusted approval, durable intent and exact evidence."""
    payload = validate_preflight_payload(metadata)
    context = getattr(authorization, "trusted_context", None)
    action_request = getattr(authorization, "action_request", None)
    gateway = getattr(authorization, "gateway", None)
    if context is None or action_request is None or gateway is None:
        raise GitHubWriteBoundaryError("GitHub write requires typed host authority")
    if payload["tool_name"] != getattr(call_binding, "tool_name", None):
        raise GitHubWriteBoundaryError("GitHub write metadata does not match the configured MCP tool")
    if payload["repository"] != str(getattr(context, "repository_slug", "")).casefold():
        raise GitHubWriteBoundaryError("GitHub write repository differs from the host-verified repository")
    if payload["branch"] != getattr(context, "repository_branch", ""):
        raise GitHubWriteBoundaryError("GitHub write branch differs from the host-verified implementation branch")
    if payload["base_sha"] != getattr(context, "repository_revision", ""):
        raise GitHubWriteBoundaryError("GitHub write base SHA differs from the host-verified workspace revision")
    if payload["plan_digest"] != getattr(context, "execution_plan_digest", ""):
        raise GitHubWriteBoundaryError("GitHub write is not bound to the host-verified canonical plan digest")
    if getattr(action_request, "capability", None) != "repository.write":
        raise GitHubWriteBoundaryError("GitHub write authority must use the repository.write capability")
    if getattr(action_request, "action", None) != payload["action"]:
        raise GitHubWriteBoundaryError("GitHub write action differs from the approved operation")
    if getattr(action_request, "target", None) != payload["target"]:
        raise GitHubWriteBoundaryError("GitHub write target differs from the approved repository/branch/PR")
    if getattr(action_request, "risk", None) != "external_effect":
        raise GitHubWriteBoundaryError("GitHub writes must be classified as external effects")
    if not getattr(action_request, "idempotency_key", ""):
        raise GitHubWriteBoundaryError("GitHub writes require a durable idempotency key")
    operation = request.get("provider_operation")
    if not isinstance(operation, Mapping) or operation.get("provider") != "github":
        raise GitHubWriteBoundaryError("GitHub writes require a durable GitHub provider-operation claim")
    required_evidence = {
        f"github-write:{payload['digest']}",
        f"canonical-plan:{payload['plan_digest']}",
    }
    if not required_evidence.issubset(set(getattr(action_request, "evidence_refs", ()))):
        raise GitHubWriteBoundaryError("GitHub write approval must bind the canonical plan and exact write target")
    if not callable(getattr(gateway, "requires_approval", None)) or not gateway.requires_approval(action_request):
        raise GitHubWriteBoundaryError("GitHub write policy must require trusted human approval")
    verdict = gateway.evaluate(action_request, approval=getattr(authorization, "approval", None))
    if not verdict.allowed or not verdict.approval_required:
        raise GitHubWriteBoundaryError("GitHub write approval was not accepted by the canonical governance gateway")
