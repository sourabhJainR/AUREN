# AUREN Canary Rollout

A learned policy is first evaluated in shadow mode, then against a bounded canary set, before activation.

1. Shadow: no policy state mutation.
2. Replay: deterministic historical corpus.
3. Canary: candidate runs with explicit pass and verification thresholds.
4. Promotion: only after all gates pass.
5. Monitor: compare live acceptance and regression rate to baseline.
6. Rollback: restore the latest superseded policy when thresholds are breached.

Default canary thresholds are 100% success and 100% verification for the selected corpus. Learned strategy never overrides security, repository, permission, or approval controls.
