"""Safe preview, additive import, export and revision-preserving rollback for agent profiles.

The registry remains the canonical source. Imports are additive and compare-and-swap
against its digest; rollback creates a new revision instead of rewriting history.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any, Iterator, Mapping

from .agent_profiles import (
    AgentProfileError,
    DEFAULT_ORGANIZATION_PATH,
    DEFAULT_PROFILE_PATH,
    PROFILE_REGISTRY_VERSION,
    _canonical,
    _load_json,
    _unique_pairs,
    load_agent_profile_registry,
    resolve_agent_profile,
)


def _decode_registry(candidate: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        try:
            payload = _canonical(dict(candidate))
        except (TypeError, ValueError) as exc:
            raise AgentProfileError("candidate registry cannot be encoded as canonical JSON") from exc
    elif isinstance(candidate, str):
        payload = candidate.encode("utf-8")
    elif isinstance(candidate, bytes):
        payload = candidate
    else:
        raise AgentProfileError("candidate registry must be JSON text, bytes, or a mapping")
    if not payload or len(payload) > 256 * 1024:
        raise AgentProfileError("candidate registry is empty or exceeds the registry size limit")
    try:
        raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentProfileError("candidate registry is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise AgentProfileError("candidate registry root must be an object")
    return raw


def _keyed(items: list[dict[str, Any]], *fields: str) -> dict[tuple[Any, ...], dict[str, Any]]:
    return {tuple(item[field] for field in fields): item for item in items}


def _diff_items(
    current: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    identity_fields: tuple[str, ...],
) -> dict[str, list[str]]:
    left = _keyed(current, *identity_fields)
    right = _keyed(candidate, *identity_fields)
    added = sorted("/".join(str(part) for part in key) for key in right.keys() - left.keys())
    removed = sorted("/".join(str(part) for part in key) for key in left.keys() - right.keys())
    modified = sorted(
        "/".join(str(part) for part in key)
        for key in left.keys() & right.keys()
        if left[key].get("digest") != right[key].get("digest")
    )
    return {"added": added, "removed": removed, "modified": modified}


class AgentProfileRegistryManager:
    """Manage immutable profile/template revisions in the canonical registry file."""

    def __init__(
        self,
        profile_path: str | Path = DEFAULT_PROFILE_PATH,
        *,
        organization_registry_path: str | Path = DEFAULT_ORGANIZATION_PATH,
    ) -> None:
        self.profile_path = Path(profile_path)
        self.organization_registry_path = Path(organization_registry_path)

    def _validate(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        with tempfile.TemporaryDirectory(prefix="auren-profile-preview-") as directory:
            candidate_path = Path(directory) / "candidate.json"
            candidate_path.write_bytes(_canonical(dict(raw)))
            return load_agent_profile_registry(
                candidate_path,
                organization_registry_path=self.organization_registry_path,
            )

    def _current(self) -> tuple[dict[str, Any], str, dict[str, Any]]:
        # Read the source file once so normalized data and its digest always
        # describe the same atomic-replacement snapshot.
        raw = _load_json(self.profile_path, label="agent profile registry")
        normalized, digest = self._validate(raw)
        return normalized, digest, raw

    def preview(self, candidate: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
        """Validate without writing and return a digest-bound change summary."""
        raw = _decode_registry(candidate)
        candidate_normalized, candidate_digest = self._validate(raw)
        current_normalized, current_digest, _ = self._current()
        return {
            "status": "valid",
            "current_digest": current_digest,
            "candidate_digest": candidate_digest,
            "profiles": _diff_items(
                current_normalized["profiles"], candidate_normalized["profiles"],
                ("profile_id", "revision"),
            ),
            "team_templates": _diff_items(
                current_normalized["team_templates"], candidate_normalized["team_templates"],
                ("template_id", "revision"),
            ),
            "candidate_profile_count": len(candidate_normalized["profiles"]),
            "candidate_template_count": len(candidate_normalized["team_templates"]),
        }

    def diff(self, candidate: str | bytes | Mapping[str, Any]) -> dict[str, Any]:
        """Return the validated profile/template diff without changing the registry."""
        preview = self.preview(candidate)
        return {
            "current_digest": preview["current_digest"],
            "candidate_digest": preview["candidate_digest"],
            "profiles": preview["profiles"],
            "team_templates": preview["team_templates"],
        }

    def export(self) -> dict[str, str]:
        """Export the canonical source registry, not a derived runtime projection."""
        raw = _load_json(self.profile_path, label="agent profile registry")
        _normalized, registry_digest = self._validate(raw)
        content = _canonical(raw).decode("utf-8")
        return {
            "content": content,
            "digest": registry_digest,
            "source_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.profile_path.with_name(self.profile_path.name + ".lock")
        if lock_path.is_symlink():
            raise AgentProfileError("profile registry lock path cannot be a symlink")
        with lock_path.open("a+b") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 10.0
            acquired = False
            while not acquired:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise AgentProfileError(
                            "profile registry is locked by another writer; retry with a fresh preview"
                        ) from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _check_expected(self, expected_current_digest: str, current_digest: str) -> None:
        if (
            not isinstance(expected_current_digest, str)
            or len(expected_current_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_current_digest)
        ):
            raise AgentProfileError("expected_current_digest must be a lowercase SHA-256 digest")
        if expected_current_digest != current_digest:
            raise AgentProfileError("profile registry changed since preview; refresh before writing")

    @staticmethod
    def _change_receipt(
        operation: str,
        previous_digest: str,
        registry_digest: str,
        affected_revisions: list[str],
    ) -> dict[str, Any]:
        body = {
            "schema_version": "auren-agent-profile-registry-change-v1",
            "operation": operation,
            "previous_registry_digest": previous_digest,
            "registry_digest": registry_digest,
            "affected_revisions": sorted(affected_revisions),
        }
        return {**body, "receipt_digest": hashlib.sha256(_canonical(body)).hexdigest()}

    def _atomic_write(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        if self.profile_path.is_symlink():
            raise AgentProfileError("profile registry path cannot be a symlink")
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _canonical(dict(raw)) + b"\n"
        if len(payload) > 256 * 1024:
            raise AgentProfileError("merged profile registry exceeds the configured size limit")
        old_mode = stat.S_IMODE(self.profile_path.stat().st_mode) if self.profile_path.exists() else 0o600
        fd, temp_name = tempfile.mkstemp(
            prefix="." + self.profile_path.name + ".",
            suffix=".tmp",
            dir=self.profile_path.parent,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, old_mode)
            # Validate the exact bytes that will replace the canonical registry.
            normalized, digest = load_agent_profile_registry(
                temp_path,
                organization_registry_path=self.organization_registry_path,
            )
            os.replace(temp_path, self.profile_path)
            if os.name != "nt":
                directory_fd = os.open(self.profile_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                except OSError as exc:
                    raise AgentProfileError(
                        "profile registry was replaced but directory durability could not be confirmed; reload before retrying"
                    ) from exc
                finally:
                    os.close(directory_fd)
            return normalized, digest
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def import_registry(
        self,
        candidate: str | bytes | Mapping[str, Any],
        *,
        expected_current_digest: str,
    ) -> dict[str, Any]:
        """Add new immutable revisions; identical imports are idempotent."""
        candidate_raw = _decode_registry(candidate)
        candidate_normalized, _candidate_digest = self._validate(candidate_raw)
        with self._exclusive_lock():
            current_normalized, current_digest, current_raw = self._current()
            self._check_expected(expected_current_digest, current_digest)
            if candidate_raw.get("schema_version") != PROFILE_REGISTRY_VERSION:
                raise AgentProfileError("candidate registry schema is unsupported")
            current_profiles = _keyed(current_normalized["profiles"], "profile_id", "revision")
            candidate_profiles = _keyed(candidate_normalized["profiles"], "profile_id", "revision")
            current_templates = _keyed(current_normalized["team_templates"], "template_id", "revision")
            candidate_templates = _keyed(candidate_normalized["team_templates"], "template_id", "revision")
            if current_profiles.keys() - candidate_profiles.keys() or current_templates.keys() - candidate_templates.keys():
                raise AgentProfileError("profile imports are additive; historical revisions cannot be removed")
            for key in current_profiles.keys() & candidate_profiles.keys():
                if current_profiles[key]["digest"] != candidate_profiles[key]["digest"]:
                    raise AgentProfileError("an existing profile revision cannot be overwritten")
            for key in current_templates.keys() & candidate_templates.keys():
                if current_templates[key]["digest"] != candidate_templates[key]["digest"]:
                    raise AgentProfileError("an existing team template revision cannot be overwritten")

            raw_profiles = {
                (item["profile_id"], item["revision"]): item
                for item in current_raw["profiles"]
            }
            raw_templates = {
                (item["template_id"], item["revision"]): item
                for item in current_raw["team_templates"]
            }
            for item in candidate_raw["profiles"]:
                raw_profiles.setdefault((item["profile_id"], item["revision"]), item)
            for item in candidate_raw["team_templates"]:
                raw_templates.setdefault((item["template_id"], item["revision"]), item)
            merged = {
                "schema_version": PROFILE_REGISTRY_VERSION,
                "profiles": [
                    raw_profiles[key] for key in sorted(raw_profiles)
                ],
                "team_templates": [
                    raw_templates[key] for key in sorted(raw_templates)
                ],
            }
            if len(raw_profiles) == len(current_profiles) and len(raw_templates) == len(current_templates):
                return {
                    "status": "unchanged",
                    "registry_digest": current_digest,
                    "added_profiles": [],
                    "added_templates": [],
                }
            normalized, digest = self._atomic_write(merged)
            return {
                "status": "imported",
                "registry_digest": digest,
                "added_profiles": sorted(
                    "/".join(map(str, key)) for key in raw_profiles.keys() - current_profiles.keys()
                ),
                "added_templates": sorted(
                    "/".join(map(str, key)) for key in raw_templates.keys() - current_templates.keys()
                ),
                "profile_count": len(normalized["profiles"]),
                "template_count": len(normalized["team_templates"]),
                "receipt": self._change_receipt(
                    "import",
                    current_digest,
                    digest,
                    [
                        *("/".join(map(str, key)) for key in raw_profiles.keys() - current_profiles.keys()),
                        *("/".join(map(str, key)) for key in raw_templates.keys() - current_templates.keys()),
                    ],
                ),
            }

    def rollback_profile(
        self,
        profile_id: str,
        *,
        to_revision: int,
        expected_current_digest: str,
    ) -> dict[str, Any]:
        """Create a new revision copied from a prior revision; never rewrite pinned tasks."""
        with self._exclusive_lock():
            current, current_digest, raw = self._current()
            self._check_expected(expected_current_digest, current_digest)
            matches = [
                item for item in current["profiles"]
                if item["profile_id"] == profile_id and item["revision"] == to_revision
            ]
            if len(matches) != 1:
                raise AgentProfileError(f"unknown profile revision: {profile_id}@{to_revision}")
            prior = matches[0]
            new_revision = max(
                item["revision"] for item in current["profiles"]
                if item["profile_id"] == profile_id
            ) + 1
            restored = {
                "profile_id": profile_id,
                "revision": new_revision,
                "role_id": prior["role_id"],
                "manager_role_id": prior["manager_role_id"],
                "action_classes": list(prior["action_classes"]),
                "required_capabilities": list(prior["required_capabilities"]),
                "provider_preferences": list(prior["provider_preferences"]),
                "tool_preferences": list(prior["tool_preferences"]),
                "workspace_policy": dict(prior["workspace_policy"]),
                "memory_namespace": f"agent-profile:{profile_id}:r{new_revision}",
                "budget_defaults": dict(prior["budget_defaults"]),
                "deadline_seconds": prior["deadline_seconds"],
                "approval_policy": prior["approval_policy"],
            }
            merged = dict(raw)
            merged["profiles"] = [*raw["profiles"], restored]
            normalized, digest = self._atomic_write(merged)
            profile = resolve_agent_profile(
                profile_id, revision=new_revision, profile_path=self.profile_path,
                organization_registry_path=self.organization_registry_path,
            )
            return {
                "status": "rolled_back_as_new_revision",
                "profile_id": profile_id,
                "restored_from_revision": to_revision,
                "new_revision": new_revision,
                "profile_digest": profile.digest,
                "registry_digest": digest,
                "historical_revision_count": sum(
                    item["profile_id"] == profile_id for item in normalized["profiles"]
                ),
                "receipt": self._change_receipt(
                    "rollback_profile",
                    current_digest,
                    digest,
                    [f"{profile_id}@{to_revision}", f"{profile_id}@{new_revision}"],
                ),
            }

    def rollback_team_template(
        self,
        template_id: str,
        *,
        to_revision: int,
        expected_current_digest: str,
    ) -> dict[str, Any]:
        """Create a new template revision from historical membership and dependencies."""
        with self._exclusive_lock():
            current, current_digest, raw = self._current()
            self._check_expected(expected_current_digest, current_digest)
            matches = [
                item for item in current["team_templates"]
                if item["template_id"] == template_id and item["revision"] == to_revision
            ]
            if len(matches) != 1:
                raise AgentProfileError(f"unknown team template revision: {template_id}@{to_revision}")
            prior = matches[0]
            new_revision = max(
                item["revision"] for item in current["team_templates"]
                if item["template_id"] == template_id
            ) + 1
            restored = {
                "template_id": template_id,
                "revision": new_revision,
                "description": prior["description"],
                "members": [
                    {
                        "member_id": member["member_id"],
                        "profile_id": member["profile_id"],
                        "profile_revision": member["profile_revision"],
                        "depends_on": list(member["depends_on"]),
                    }
                    for member in prior["members"]
                ],
            }
            merged = dict(raw)
            merged["team_templates"] = [*raw["team_templates"], restored]
            normalized, digest = self._atomic_write(merged)
            return {
                "status": "rolled_back_as_new_revision",
                "template_id": template_id,
                "restored_from_revision": to_revision,
                "new_revision": new_revision,
                "template_digest": next(
                    item["digest"] for item in normalized["team_templates"]
                    if item["template_id"] == template_id and item["revision"] == new_revision
                ),
                "registry_digest": digest,
                "historical_revision_count": sum(
                    item["template_id"] == template_id for item in normalized["team_templates"]
                ),
                "receipt": self._change_receipt(
                    "rollback_team_template",
                    current_digest,
                    digest,
                    [f"{template_id}@{to_revision}", f"{template_id}@{new_revision}"],
                ),
            }


__all__ = ["AgentProfileRegistryManager"]
