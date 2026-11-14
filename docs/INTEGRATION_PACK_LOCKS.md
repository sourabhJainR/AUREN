# AUREN integration pack locks

AUREN now has a pinned, signed, data-only integration pack lock manager. It closes part of the gap between discovering an extension and safely preparing it for a trusted host.

This is a lock and approval record, **not a package installer or executable plugin loader**. It does not download archives, unpack files, import Python modules, run scripts, start MCP servers, or activate runtime adapters.

## Manifest contract

The machine-readable contract is [integration-pack-manifest.v1.schema.json](../schemas/integration-pack-manifest.v1.schema.json). A pack manifest records:

- Lowercase pack name and semantic version.
- HTTPS source URL and immutable Git commit revision.
- SHA-256 digest of the packaged artifact.
- Capabilities and exact requested permissions.
- Signer key ID and 64-byte Ed25519 signature.
- Minimum and optional maximum compatible AUREN versions.
- Exact pinned dependencies in `name@version` form.

Unknown fields, wildcard permissions, mutable branch names, unpinned artifacts, invalid signatures and incompatible AUREN versions are rejected. The signature covers the canonical JSON manifest excluding only the signature field. The manifest digest covers the complete manifest, including its signature.

## Trust boundary

Signature verification is delegated to a host-owned trust root:

```python
def verify_ed25519(key_id: str, payload: bytes, signature_hex: str) -> bool:
    # Resolve key_id only from the operator-managed trust store.
    # Verify Ed25519 over payload using the trusted public key.
    return trusted_keyring.verify(key_id, payload, bytes.fromhex(signature_hex))
```

The example describes the required adapter contract; AUREN does not treat a manifest's signer name or `trust` label as proof. The host must own the trusted public keys, revocation state and cryptographic verifier. If no verifier is supplied, verification fails closed.

The artifact SHA-256 check is separate from signature verification. Verify the exact downloaded bytes before any future installer or sandbox receives them:

```python
IntegrationPackLock.verify_artifact(archive_bytes, manifest.artifact_sha256)
```

The lock manager itself performs no network access.

## Review and stage a pack

```python
import json
from pathlib import Path

from portable.integration_pack_lock import (
    IntegrationPackLock,
    IntegrationPackManifest,
)

manifest = IntegrationPackManifest.from_mapping(
    json.loads(Path("pack.manifest.json").read_text(encoding="utf-8"))
)
packs = IntegrationPackLock(
    Path(".auren/integration-packs.lock.json"),
    auren_version="1.4.0",
    signature_verifier=verify_ed25519,
)

print(packs.preview(manifest))
staged = packs.stage(manifest)
print(packs.snapshot())
```

Preview reports the source revision, manifest and artifact digests, AUREN compatibility, signature status, and capability/permission changes. Updating an existing pack requires its current manifest digest as a compare-and-swap value:

```python
updated = packs.stage(
    next_manifest,
    expected_current_digest=staged["manifest_digest"],
)
```

Staging writes only the lockfile. New or updated packs are not approved for activation. The manager validates the complete transitive dependency graph, requires exact versions, and rejects dependency cycles. The lockfile uses an exclusive writer lock, bounded JSON parsing, duplicate-key rejection, symlink checks, atomic replacement, and a bounded change history. A stale writer lock is not removed automatically; inspect the owning process before clearing it.

## Record explicit activation approval

A trusted host may record an approval after its own caller, project, policy and permission checks have succeeded:

```python
approved = packs.approve_activation(
    manifest.name,
    expected_manifest_digest=staged["manifest_digest"],
    granted_permissions=["repository.read"],
    approval_granted=True,
)
```

Only permissions declared by the manifest can be granted. This records an approval state in the lockfile; it does **not** register or execute the pack. Runtime activation must still use a reviewed AUREN adapter, the canonical controller, Guard, host-owned tool registry and the host's live approval/evidence path. A lockfile entry is not a substitute for an authenticated approval receipt.

Revoke the recorded approval with `revoke_activation(name, expected_manifest_digest=...)`. Revocation clears granted permissions. A pack's dependencies must be staged at the exact pinned versions, signature-verified and separately approved before the dependent pack can receive approval.

## Rollback

Use the state digest returned by the manager for compare-and-swap rollback:

```python
restored = packs.rollback(
    manifest.name,
    expected_current_state_digest=approved["state_digest"],
)
```

Rollback restores the previous pinned manifest only after its signature, compatibility and full dependency graph are rechecked. It always clears permission grants and activation approval. The restored version must be reviewed and approved again. Stale state digests, missing history, missing or mismatched dependencies and invalid prior signatures fail closed.

## Current limits

- The manager stores metadata and approval state only; it is not a package downloader, installer, sandbox or code loader.
- The host must provide a real Ed25519 verifier backed by operator-managed public keys and revocation policy.
- A signed manifest proves integrity and signer possession only relative to that trust root; it does not prove that the pack is safe.
- Runtime activation, isolation, filesystem/network permissions, host acceptance and independent security review remain separate gates.
- Cost, quality and security claims in pack metadata are descriptive and must be independently measured.

Run the tests:

```bash
python -m unittest tests.test_integration_pack_lock tests.test_extension_contract -v
```

See [Extension Contract](EXTENSION_CONTRACT.md), [Plug-and-Play Integrations](PLUG_AND_PLAY_INTEGRATIONS.md) and [Provider Role Profiles](PROVIDER_ROLE_PROFILES.md).
