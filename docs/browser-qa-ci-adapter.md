# Browser QA CI evidence adapter

AUREN observes browser tests run by CI without claiming to own a local interactive browser.

The bounded, read-only GitHub Actions poller requires a repository slug, run ID, full expected commit SHA and token. It returns passed only for a completed successful run on the exact SHA. Failures, cancellation, blocked conclusions, timeout and SHA mismatch remain distinct non-success states.

The canonical execution controller exposes `record_browser_qa_ci_receipt`. It checks repository and exact revision identity, then writes revision, run and receipt nodes to the durable evidence graph and verifies read-back. The receipt is evidence only; it does not grant execution, approval, promotion or Guard authority. Durable graph records survive controller restart.

TradingDYOR PR #40 is the reference browser QA testbed. Its Playwright workflow uploads a report and SHA manifest. Inspecting that artifact is a separate evidence-review step; run metadata alone is not artifact attestation.
