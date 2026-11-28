# Browser QA CI evidence adapter

The adapter polls GitHub Actions run metadata with a bounded read-only HTTPS
request. A passed receipt requires the configured repository, exact full SHA,
terminal success, and the expected workflow file path, normalizing GitHub's `@ref` suffix and preserving the ref in the receipt. Redirects are rejected.
Requests are capped by the remaining poll deadline; timeout, transport errors,
workflow mismatch, SHA mismatch and non-success conclusions remain non-success.

The controller must configure the expected repository from trusted host
configuration or the local repository identity. Missing repository identity
blocks receipt acceptance. Receipts are persisted in the durable evidence graph
and recovery ledger. They are evidence only and do not grant execution,
approval, promotion or Guard authority.

TradingDYOR PR #40 is the reference browser QA testbed. Its Playwright
workflow uploads a report and SHA manifest. Artifact review is a separate
evidence step; run metadata alone does not attest the artifact contents.
