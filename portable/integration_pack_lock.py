"""Pinned, signed, data-only integration pack lock management.

This module manages reviewed pack metadata and a local lockfile. It does not
download, unpack, import, execute, or activate third-party code. Signature
verification is delegated to a host-owned trust root.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlsplit


_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_PACK_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SIGNATURE = re.compile(r"^[0-9a-f]{128}$")
_MAX_LOCK_BYTES = 4 * 1024 * 1024
_MAX_HISTORY = 100
_MAX_DECLARED_ITEMS = 256
_MAX_DEPENDENCY_DEPTH = 64


class IntegrationPackError(ValueError):
    """Raised when pack metadata, trust, compatibility or lock state is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _tokens(field: str, values: Any, *, required: bool) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise IntegrationPackError(f"{field} must be a tuple of strings")
    if required and not values:
        raise IntegrationPackError(f"{field} must not be empty")
    if len(values) > _MAX_DECLARED_ITEMS:
        raise IntegrationPackError(f"{field} exceeds the maximum of {_MAX_DECLARED_ITEMS} entries")
    if any(not isinstance(item, str) or not _NAME.fullmatch(item) for item in values):
        raise IntegrationPackError(f"{field} contains an invalid identifier")
    if any(item in {"*", "all", "admin", "unrestricted"} for item in values):
        raise IntegrationPackError(f"{field} contains a forbidden wildcard")
    if len(values) != len(set(values)):
        raise IntegrationPackError(f"{field} values must be unique")
    return values


def _version(value: str, field: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not _SEMVER.fullmatch(value):
        raise IntegrationPackError(f"{field} must be a stable major.minor.patch version")
    return tuple(int(part) for part in value.split("."))


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False))


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntegrationPackError("lockfile contains duplicate object keys")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class IntegrationPackManifest:
    name: str
    version: str
    source_url: str
    source_revision: str
    artifact_sha256: str
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    signer_key_id: str
    signature: str
    min_auren_version: str = "0.0.0"
    max_auren_version: str | None = None
    dependencies: tuple[str, ...] = ()
    contract_version: str = "auren-pack-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise IntegrationPackError("name must be a lowercase identifier")
        if not isinstance(self.version, str) or not _PACK_VERSION.fullmatch(self.version):
            raise IntegrationPackError("version must use semantic version syntax")
        if self.contract_version != "auren-pack-v1":
            raise IntegrationPackError("unsupported integration pack contract version")
        _version(self.min_auren_version, "min_auren_version")
        if self.max_auren_version is not None:
            minimum = _version(self.min_auren_version, "min_auren_version")
            maximum = _version(self.max_auren_version, "max_auren_version")
            if maximum < minimum:
                raise IntegrationPackError("max_auren_version must not be lower than min_auren_version")
        if not isinstance(self.source_url, str):
            raise IntegrationPackError("source_url must be an HTTPS URL")
        try:
            parsed = urlsplit(self.source_url)
            valid_source = (
                parsed.scheme == "https" and bool(parsed.hostname)
                and parsed.port in {None, 443}
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
            )
        except ValueError:
            valid_source = False
        if not valid_source:
            raise IntegrationPackError("source_url must be HTTPS without credentials, query or fragment")
        if not isinstance(self.source_revision, str) or not _REVISION.fullmatch(self.source_revision):
            raise IntegrationPackError("source_revision must be a pinned 40- or 64-character commit")
        if not isinstance(self.artifact_sha256, str) or not _SHA256.fullmatch(self.artifact_sha256):
            raise IntegrationPackError("artifact_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.signer_key_id, str) or not _NAME.fullmatch(self.signer_key_id):
            raise IntegrationPackError("signer_key_id must be a lowercase identifier")
        if not isinstance(self.signature, str) or not _SIGNATURE.fullmatch(self.signature):
            raise IntegrationPackError("signature must be a 64-byte hexadecimal Ed25519 signature")
        _tokens("capabilities", self.capabilities, required=True)
        _tokens("permissions", self.permissions, required=False)
        if not isinstance(self.dependencies, tuple):
            raise IntegrationPackError("dependencies must be a tuple of exact name@version pins")
        if len(self.dependencies) > _MAX_DECLARED_ITEMS:
            raise IntegrationPackError(f"dependencies exceed the maximum of {_MAX_DECLARED_ITEMS} entries")
        if any(not isinstance(dependency, str) for dependency in self.dependencies):
            raise IntegrationPackError("dependencies must use exact name@version pins")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise IntegrationPackError("dependencies must be unique")
        for dependency in self.dependencies:
            if "@" not in dependency:
                raise IntegrationPackError("dependencies must use exact name@version pins")
            dep_name, dep_version = dependency.rsplit("@", 1)
            if not _NAME.fullmatch(dep_name) or not _PACK_VERSION.fullmatch(dep_version):
                raise IntegrationPackError("dependencies must use exact name@version pins")
            if dep_name == self.name:
                raise IntegrationPackError("a pack cannot depend on itself")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IntegrationPackManifest":
        if not isinstance(value, Mapping):
            raise IntegrationPackError("manifest must be an object")
        allowed = {
            "name", "version", "source_url", "source_revision", "artifact_sha256",
            "capabilities", "permissions", "signer_key_id", "signature",
            "min_auren_version", "max_auren_version", "dependencies", "contract_version",
        }
        unknown = set(value) - allowed
        if unknown:
            raise IntegrationPackError("unknown manifest fields: " + ", ".join(sorted(str(x) for x in unknown)))
        required = {
            "name", "version", "source_url", "source_revision", "artifact_sha256",
            "capabilities", "permissions", "signer_key_id", "signature",
        }
        missing = required - set(value)
        if missing:
            raise IntegrationPackError("missing manifest fields: " + ", ".join(sorted(missing)))
        fields = dict(value)
        for key in ("capabilities", "permissions", "dependencies"):
            if key in fields and isinstance(fields[key], list):
                fields[key] = tuple(fields[key])
        return cls(**fields)

    def signed_payload(self) -> bytes:
        value = self.as_dict()
        value.pop("signature")
        return _canonical(value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "name": self.name,
            "version": self.version,
            "source_url": self.source_url,
            "source_revision": self.source_revision,
            "artifact_sha256": self.artifact_sha256,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "signer_key_id": self.signer_key_id,
            "signature": self.signature,
            "min_auren_version": self.min_auren_version,
            "max_auren_version": self.max_auren_version,
            "dependencies": list(self.dependencies),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.as_dict())).hexdigest()

    def verify_signature(self, verifier: Callable[[str, bytes, str], bool] | None) -> bool:
        """Verify with a host-owned trust root; a signer label alone is never trusted."""
        if not callable(verifier):
            return False
        try:
            return verifier(self.signer_key_id, self.signed_payload(), self.signature) is True
        except Exception:
            return False

    def supports_auren(self, version: str) -> bool:
        current = _version(version, "auren_version")
        minimum = _version(self.min_auren_version, "min_auren_version")
        maximum = _version(self.max_auren_version, "max_auren_version") if self.max_auren_version else None
        return current >= minimum and (maximum is None or current <= maximum)


class IntegrationPackLock:
    """Atomic lockfile manager; it never installs or executes pack contents."""

    def __init__(
        self,
        path: str | Path,
        *,
        auren_version: str,
        signature_verifier: Callable[[str, bytes, str], bool] | None,
    ) -> None:
        self.path = Path(os.path.abspath(Path(path).expanduser()))
        self.auren_version = auren_version
        _version(auren_version, "auren_version")
        self.signature_verifier = signature_verifier

    @staticmethod
    def verify_artifact(data: bytes, expected_sha256: str) -> bool:
        if not isinstance(data, bytes) or not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise IntegrationPackError("artifact bytes and a valid SHA-256 digest are required")
        return hashlib.sha256(data).hexdigest() == expected_sha256

    def preview(self, manifest: IntegrationPackManifest | Mapping[str, Any]) -> dict[str, Any]:
        item = self._manifest(manifest)
        state = self._read_state()
        current = state["packs"].get(item.name)
        old_manifest = self._entry_manifest(current) if current else None
        old_permissions = set(old_manifest.permissions if old_manifest else ())
        new_permissions = set(item.permissions)
        old_capabilities = set(old_manifest.capabilities if old_manifest else ())
        new_capabilities = set(item.capabilities)
        return {
            "action": "unchanged" if current and current["manifest_digest"] == item.digest else ("update" if current else "stage"),
            "name": item.name,
            "version": item.version,
            "manifest_digest": item.digest,
            "signature_valid": item.verify_signature(self.signature_verifier),
            "compatible": item.supports_auren(self.auren_version),
            "source_revision": item.source_revision,
            "artifact_sha256": item.artifact_sha256,
            "capabilities_added": sorted(new_capabilities - old_capabilities),
            "capabilities_removed": sorted(old_capabilities - new_capabilities),
            "permissions_added": sorted(new_permissions - old_permissions),
            "permissions_removed": sorted(old_permissions - new_permissions),
            "currently_approved": bool(current and current.get("activation_approved") is True),
            "will_execute_code": False,
        }

    def stage(
        self,
        manifest: IntegrationPackManifest | Mapping[str, Any],
        *,
        expected_current_digest: str | None = None,
    ) -> dict[str, Any]:
        item = self._manifest(manifest)
        if not item.verify_signature(self.signature_verifier):
            raise IntegrationPackError("manifest signature is not verified by the configured trust root")
        if not item.supports_auren(self.auren_version):
            raise IntegrationPackError("pack is incompatible with this AUREN version")
        with self._exclusive_lock():
            state = self._read_state()
            self._validate_dependencies(item, state)
            current = state["packs"].get(item.name)
            if current and current["manifest_digest"] == item.digest:
                return self._public_entry(current)
            if current and self._approved_dependents(item.name, state):
                raise IntegrationPackError("revoke dependent pack approvals before updating this dependency")
            if current and expected_current_digest != current["manifest_digest"]:
                raise IntegrationPackError("pack update requires the current manifest digest")
            before = _copy(current) if current else None
            entry = {
                "manifest": item.as_dict(),
                "manifest_digest": item.digest,
                "activation_approved": False,
                "granted_permissions": [],
            }
            state["packs"][item.name] = entry
            self._record(state, item.name, "stage" if current is None else "update", before, entry)
            self._write_state(state)
            return self._public_entry(entry)

    def approve_activation(
        self,
        name: str,
        *,
        expected_manifest_digest: str,
        granted_permissions: tuple[str, ...] | list[str],
        approval_granted: bool,
    ) -> dict[str, Any]:
        if type(approval_granted) is not bool or not approval_granted:
            raise IntegrationPackError("explicit activation approval is required")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise IntegrationPackError("name must be a lowercase identifier")
        if not isinstance(granted_permissions, (tuple, list)):
            raise IntegrationPackError("granted_permissions must be a list of exact permissions")
        grants = _tokens("granted_permissions", tuple(granted_permissions), required=False)
        with self._exclusive_lock():
            state = self._read_state()
            current = state["packs"].get(name)
            if current is None:
                raise IntegrationPackError("pack is not staged")
            item = self._entry_manifest(current)
            if current["manifest_digest"] != expected_manifest_digest or item.digest != expected_manifest_digest:
                raise IntegrationPackError("manifest digest changed; review the new pack before activation")
            if not item.verify_signature(self.signature_verifier):
                raise IntegrationPackError("manifest signature is no longer verified")
            if not item.supports_auren(self.auren_version):
                raise IntegrationPackError("pack is incompatible with this AUREN version")
            if not set(grants).issubset(item.permissions):
                raise IntegrationPackError("granted permissions exceed the manifest declaration")
            self._validate_dependencies(item, state, require_approved=True)
            before = _copy(current)
            current["activation_approved"] = True
            current["granted_permissions"] = list(grants)
            self._record(state, name, "approve_activation", before, current)
            self._write_state(state)
            return self._public_entry(current)

    def revoke_activation(self, name: str, *, expected_manifest_digest: str) -> dict[str, Any]:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise IntegrationPackError("name must be a lowercase identifier")
        with self._exclusive_lock():
            state = self._read_state()
            current = state["packs"].get(name)
            if current is None:
                raise IntegrationPackError("pack is not staged")
            if current["manifest_digest"] != expected_manifest_digest:
                raise IntegrationPackError("manifest digest changed; refusing stale approval revocation")
            if current.get("activation_approved") is True and self._approved_dependents(name, state):
                raise IntegrationPackError("revoke dependent pack approvals before revoking this dependency")
            before = _copy(current)
            current["activation_approved"] = False
            current["granted_permissions"] = []
            self._record(state, name, "revoke_activation", before, current)
            self._write_state(state)
            return self._public_entry(current)

    def rollback(self, name: str, *, expected_current_state_digest: str) -> dict[str, Any] | None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise IntegrationPackError("name must be a lowercase identifier")
        with self._exclusive_lock():
            state = self._read_state()
            current = state["packs"].get(name)
            if current is None:
                raise IntegrationPackError("pack has no current state to roll back")
            if self._state_digest(current) != expected_current_state_digest:
                raise IntegrationPackError("pack state changed; refusing stale rollback")
            if self._approved_dependents(name, state):
                raise IntegrationPackError("revoke dependent pack approvals before rolling back this dependency")
            matching_event = next((
                event for event in reversed(state["history"])
                if event["pack"] == name and event["after"] is not None
                and self._state_digest(event["after"]) == expected_current_state_digest
            ), None)
            if matching_event is None:
                raise IntegrationPackError("no matching rollback record; refusing to guess a previous state")
            previous = _copy(matching_event["before"]) if matching_event["before"] is not None else None
            if previous is None:
                state["packs"].pop(name, None)
                self._record(state, name, "rollback_remove", _copy(current), None)
                self._write_state(state)
                return None
            restored = self._entry_manifest(previous)
            if not restored.verify_signature(self.signature_verifier):
                raise IntegrationPackError("previous manifest signature is no longer verified")
            if not restored.supports_auren(self.auren_version):
                raise IntegrationPackError("previous pack is incompatible with this AUREN version")
            self._validate_dependencies(restored, state)
            # Rollback restores the pinned version only. Previous permission grants
            # and activation approval are never carried forward; re-approval is required.
            target = {
                "manifest": restored.as_dict(),
                "manifest_digest": restored.digest,
                "activation_approved": False,
                "granted_permissions": [],
            }
            state["packs"][name] = target
            self._record(state, name, "rollback_restore", _copy(current), target)
            self._write_state(state)
            return self._public_entry(target)

    def snapshot(self) -> dict[str, Any]:
        state = self._read_state()
        return {
            "schema_version": state["schema_version"],
            "revision": state["revision"],
            "packs": {
                name: self._public_entry(entry)
                for name, entry in sorted(state["packs"].items())
            },
            "history_count": len(state["history"]),
            "will_execute_code": False,
        }

    def _manifest(self, value: IntegrationPackManifest | Mapping[str, Any]) -> IntegrationPackManifest:
        if isinstance(value, IntegrationPackManifest):
            return value
        return IntegrationPackManifest.from_mapping(value)

    def _entry_manifest(self, entry: Mapping[str, Any]) -> IntegrationPackManifest:
        if not isinstance(entry, Mapping) or set(entry) != {
            "manifest", "manifest_digest", "activation_approved", "granted_permissions",
        }:
            raise IntegrationPackError("lockfile pack entry schema is invalid")
        item = IntegrationPackManifest.from_mapping(entry["manifest"])
        if item.digest != entry.get("manifest_digest"):
            raise IntegrationPackError("lockfile manifest digest mismatch")
        if type(entry.get("activation_approved")) is not bool:
            raise IntegrationPackError("lockfile activation approval state is invalid")
        grants = entry.get("granted_permissions")
        if (
            not isinstance(grants, list)
            or any(not isinstance(x, str) or not _NAME.fullmatch(x) for x in grants)
            or len(grants) != len(set(grants))
        ):
            raise IntegrationPackError("lockfile permission grants are invalid")
        if not set(grants).issubset(item.permissions):
            raise IntegrationPackError("lockfile contains permissions outside the manifest")
        if entry["activation_approved"] is False and grants:
            raise IntegrationPackError("pack without activation approval must not retain permission grants")
        return item

    def _approved_dependents(self, name: str, state: Mapping[str, Any]) -> list[str]:
        dependents: list[str] = []
        for dependent_name, entry in state["packs"].items():
            if dependent_name == name or entry.get("activation_approved") is not True:
                continue
            manifest = self._entry_manifest(entry)
            if any(dependency.rsplit("@", 1)[0] == name for dependency in manifest.dependencies):
                dependents.append(dependent_name)
        return sorted(dependents)

    def _validate_dependencies(
        self, item: IntegrationPackManifest, state: Mapping[str, Any], *, require_approved: bool = False
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(
            manifest: IntegrationPackManifest,
            *,
            expected_version: str | None = None,
            depth: int = 0,
        ) -> None:
            if depth > _MAX_DEPENDENCY_DEPTH:
                raise IntegrationPackError("integration pack dependency graph exceeds the maximum depth")
            if expected_version is not None and manifest.version != expected_version:
                raise IntegrationPackError(
                    f"dependency version mismatch: {manifest.name}@{expected_version}"
                )
            if manifest.name in visiting:
                raise IntegrationPackError("integration pack dependency cycle detected")
            if manifest.name in visited:
                return
            if not manifest.verify_signature(self.signature_verifier):
                raise IntegrationPackError(f"dependency signature is not verified: {manifest.name}")
            if not manifest.supports_auren(self.auren_version):
                raise IntegrationPackError(f"dependency is incompatible with this AUREN version: {manifest.name}")
            if require_approved and manifest.name != item.name:
                entry = state["packs"].get(manifest.name)
                if entry is None or entry.get("activation_approved") is not True:
                    raise IntegrationPackError(
                        f"dependency activation is not approved: {manifest.name}"
                    )

            visiting.add(manifest.name)
            for dependency in manifest.dependencies:
                dep_name, dep_version = dependency.rsplit("@", 1)
                if dep_name == item.name:
                    dep_manifest = item
                else:
                    entry = state["packs"].get(dep_name)
                    if entry is None:
                        raise IntegrationPackError(f"missing pinned dependency: {dependency}")
                    dep_manifest = self._entry_manifest(entry)
                visit(dep_manifest, expected_version=dep_version, depth=depth + 1)
            visiting.remove(manifest.name)
            visited.add(manifest.name)

        visit(item)

    def _public_entry(self, entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if entry is None:
            return None
        item = self._entry_manifest(entry)
        return {
            "name": item.name,
            "version": item.version,
            "manifest_digest": item.digest,
            "state_digest": self._state_digest(entry),
            "artifact_sha256": item.artifact_sha256,
            "source_revision": item.source_revision,
            "activation_approved": entry["activation_approved"],
            "granted_permissions": list(entry["granted_permissions"]),
            "signature_valid": item.verify_signature(self.signature_verifier),
        }

    @staticmethod
    def _state_digest(entry: Mapping[str, Any]) -> str:
        return hashlib.sha256(_canonical(entry)).hexdigest()

    @staticmethod
    def _record(
        state: dict[str, Any], name: str, action: str,
        before: Mapping[str, Any] | None, after: Mapping[str, Any] | None,
    ) -> None:
        state["revision"] += 1
        state["history"].append({
            "revision": state["revision"],
            "pack": name,
            "action": action,
            "before": _copy(before) if before is not None else None,
            "after": _copy(after) if after is not None else None,
        })
        state["history"] = state["history"][-_MAX_HISTORY:]

    def _read_state(self) -> dict[str, Any]:
        if any(parent.is_symlink() for parent in self.path.parents):
            raise IntegrationPackError("refusing to read through a symlinked lockfile directory")
        if self.path.is_symlink():
            raise IntegrationPackError("refusing to read a symlinked lockfile")
        if not self.path.exists():
            return {"schema_version": 1, "revision": 0, "packs": {}, "history": []}
        if not self.path.is_file() or self.path.stat().st_size > _MAX_LOCK_BYTES:
            raise IntegrationPackError("lockfile is not a bounded regular file")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise IntegrationPackError("lockfile cannot be read as valid JSON") from exc
        if (
            not isinstance(data, dict) or set(data) != {"schema_version", "revision", "packs", "history"}
            or type(data.get("schema_version")) is not int or data.get("schema_version") != 1
            or type(data.get("revision")) is not int
            or data["revision"] < 0 or not isinstance(data.get("packs"), dict)
            or not isinstance(data.get("history"), list) or len(data["history"]) > _MAX_HISTORY
        ):
            raise IntegrationPackError("lockfile schema or revision is invalid")
        for name, entry in data["packs"].items():
            if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(entry, dict):
                raise IntegrationPackError("lockfile pack entry is invalid")
            item = self._entry_manifest(entry)
            if item.name != name:
                raise IntegrationPackError("lockfile pack name does not match its manifest")
        previous_revision = 0
        for event in data["history"]:
            if (
                not isinstance(event, dict)
                or set(event) != {"revision", "pack", "action", "before", "after"}
                or type(event.get("revision")) is not int
                or event["revision"] <= previous_revision or event["revision"] > data["revision"]
                or not isinstance(event.get("pack"), str) or not _NAME.fullmatch(event["pack"])
                or not isinstance(event.get("action"), str) or not event["action"]
            ):
                raise IntegrationPackError("lockfile history entry is invalid")
            previous_revision = event["revision"]
            for snapshot in (event["before"], event["after"]):
                if snapshot is not None:
                    if not isinstance(snapshot, dict):
                        raise IntegrationPackError("lockfile history snapshot is invalid")
                    snapshot_manifest = self._entry_manifest(snapshot)
                    if snapshot_manifest.name != event["pack"]:
                        raise IntegrationPackError("lockfile history pack name does not match its manifest")
        if (
            (data["history"] and data["history"][-1]["revision"] != data["revision"])
            or (not data["history"] and data["revision"] != 0)
        ):
            raise IntegrationPackError("lockfile revision does not match its latest history entry")
        return data

    def _write_state(self, state: Mapping[str, Any]) -> None:
        payload = _canonical(state) + b"\n"
        if len(payload) > _MAX_LOCK_BYTES:
            raise IntegrationPackError("lockfile exceeds the maximum size")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in self.path.parents) or self.path.is_symlink():
            raise IntegrationPackError("refusing to write through a symlinked lockfile path")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(parent.is_symlink() for parent in self.path.parents):
            raise IntegrationPackError("refusing to lock through a symlinked directory")
        lock_path = self.path.with_name(self.path.name + ".lock")
        if lock_path.is_symlink():
            raise IntegrationPackError("refusing a symlinked lock file")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise IntegrationPackError("another pack operation is active or a stale lock needs operator review") from exc
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.fsync(descriptor)
            os.close(descriptor)
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


__all__ = ["IntegrationPackError", "IntegrationPackManifest", "IntegrationPackLock"]
