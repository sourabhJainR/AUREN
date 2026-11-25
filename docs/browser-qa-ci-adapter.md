# Browser QA CI evidence adapter

AUREN can observe a browser test run executed by a CI runner, without pretending to possess a local interactive browser.

`portable.browser_qa_ci_adapter.poll_browser_qa` is a bounded, read-only GitHub Actions poller. It requires a repository slug, run ID, expected full commit SHA and token. It returns explicit states: passed, failed, cancelled, blocked, timeout or sha_mismatch. Only exact-SHA completed success returns passed.

Persist receipts with `persist_receipt` to atomically write a JSON evidence record. Integrate it as a bounded task/verification step in the canonical controller, and feed the receipt to the existing evidence gate. Polling is not a bypass for Guard, promotion, approval, repository preflight or review. Failure, mismatch, unavailable API, timeout and malformed responses must never be treated as success.

TradingDYOR is the reference testbed: PR #40's Playwright workflow runs isolated Chromium checks and uploads SHA-bound report artifacts. Artifact inspection remains a distinct evidence-review step; this adapter only observes run metadata and does not download or attest artifacts.
