# Container-backed Arena execution

Use `portable.container_sandboxed_arena.run_campaign_container_sandboxed` for evaluation of policy code that should not inherit the host process's normal filesystem and network access.

## Required setup

- Install Docker or Podman and ensure the caller is authorized to use its daemon.
- Build a policy image that reads one JSON task from stdin and writes exactly one JSON value to stdout.
- Pin the image by immutable `@sha256:<64 hex>` digest. Set `policy_digest` to the corresponding `sha256:<64 hex>` value.
- Keep the evaluator-owned manifest, oracle answers, nonce ledger, and receipt authority outside the image. Do not mount host paths into the policy container.

## Enforced container settings

Every case runs in a fresh container with networking disabled, a read-only root filesystem, all Linux capabilities dropped, no-new-privileges, an unprivileged numeric UID/GID, bounded PID count, memory/swap and CPU limits, and a small no-exec temporary filesystem. The runner applies a wall-clock timeout and requests forced container removal on timeout. Only `case_id`, `domain`, and `prompt` are sent to stdin; oracle labels and split assignments remain in the evaluator process.

The runner does not use a shell, does not mount host directories, does not inherit host environment variables into the container, and does not put stderr or exception text into the evaluation receipt. A non-zero exit, malformed output, or timeout becomes an explicit error outcome rather than a pass.

## Trust limits

This is stronger than a subprocess-only boundary, but it is not a proof of perfect isolation. It trusts the host kernel, container engine/daemon, image publisher, and host operator. Container escape vulnerabilities remain possible, and an image may contain unsafe or untrusted code. For genuinely independent evaluations, run the evaluator and the evaluated runtime under separately controlled principals, preferably on separately administered infrastructure. Keep signing keys and sealed corpus material out of the evaluated environment.

The legacy `run_campaign_isolated` remains available for trusted local development and unit tests. Do not treat its subprocess boundary as an OS sandbox or as independent-evaluation evidence.
