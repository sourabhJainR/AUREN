# Docker-isolated external evaluator

## Runtime contract

The Docker adapter is Linux-only and requires a reachable Docker daemon plus an evaluator image pinned by an immutable `sha256` digest. A tag such as `:latest` is not accepted. Readiness probes the daemon's OS type and confirms that the exact digest is present before a launch.

Each evaluator is launched without a network, as a non-root UID/GID, with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, PID/memory/CPU limits, and a bounded `noexec,nosuid,nodev` tmpfs. The sealed request is streamed over stdin; the adapter does not mount host corpus paths or pass the host PATH through to the container. Request and receipt sizes are bounded by the gateway and policy.

Timeout and output-overflow handling target the container ID, not only the attached Docker CLI. If the container ID or cleanup outcome cannot be verified, the gateway rejects the result. The container-ID file is retained when cleanup remains unresolved so an operator can reconcile the actual runtime state.

## Unsupported environments

- Windows and non-Linux hosts: unsupported; policy construction fails closed. Do not treat Windows subprocess tests as evidence of equivalent OS isolation.
- Missing Docker daemon, missing pinned image, or unavailable runtime probe: production readiness is false and evaluation is blocked.
- CI jobs that cannot execute the real container integration suite must report that gate as skipped-but-required. They must not substitute mocked CLI tests for isolation proof.

## Required release evidence still needed

Unit tests cover policy validation, launch flags, readiness, and fail-closed cleanup behavior. These are not sufficient to declare the production evaluator gate complete. Before enabling production evaluation, record:
- Real Linux container probes for network denial, non-root identity, read-only filesystem, capabilities, no-new-privileges, PID/memory/CPU ceilings, and tmpfs flags.
- A sealed-corpus integration run through this adapter, with signed receipt verification and replay rejection.
- Fault injection for timeout, output overflow, Docker daemon failure, lost client connection, cleanup failure, and unknown container outcome.
- Exact-head CI and independent code review.
- Operator-held signed deployment evidence and rollback rehearsal.

## Rollback

Revert the adapter and gateway integration commits together. Keep production evaluation disabled unless a verified isolation adapter is supplied; do not restore the host subprocess fallback as a production path. If cleanup outcome is unknown, retain the container ID evidence and reconcile the container state before retrying the same campaign nonce.
