# Stock research acceptance through the canonical runtime

This acceptance workflow exercises the existing TradingDYOR application through AUREN's canonical plan and task path. It is an integration proof for the reusable runtime, not a claim that AUREN can yet generate and release every application autonomously.

## Execution path

1. Check out the TradingDYOR source at a concrete Git commit and record that SHA.
2. Build a test image from that exact source revision. The image label binds the TradingDYOR SHA.
3. Install the application and its test dependencies in the image.
4. The test harness reviews the request with EngineeringDesignGuard, creates a versioned EngineeringPlan, starts its recovery checkpoint, and advances the acceptance task to ready/running.
5. AdaptiveRuntime configures TrustedPlanDispatchRoot with host-owned budget, approval and QA assignment resolvers.
6. QA-01 receives one bounded task through the canonical plan dispatch. The task handler can only run the host-pinned image; the image ID and source revision are not selected by the worker.
7. The host adapter starts a disposable Docker container with no network, no host mounts, a read-only root filesystem, dropped capabilities, no-new-privileges, an unprivileged user, bounded CPU/memory/processes, and temporary writable paths only.
8. The container runs the selected TradingDYOR fixture/contract tests. The handler returns test counts and content digests to ExecutionController.
9. The host acceptance test verifies the durable task result, transitions the plan task to passed only when the handler and controller both pass, and verifies the recovery checkpoint.
10. The workflow uploads `artifacts/stock-research-acceptance.json`, which links requirements, criteria, source revision, image ID, plan/run, assignment, dispatch digest and test evidence.

## Test coverage

- `tests/test_one_page_brief.py`: one-page API/UI response, recommendation, news and provenance, including partial-evidence behavior.
- `tests/test_news_sentiment.py`: source times, syndicated-story deduplication, sample/publisher coverage and provider failure.
- `tests/test_research_graph.py`: dependency ordering, partial synthesis and learning behavior.
- `tests/test_universe_screener.py`: market-universe parsing, filters and stale-cache fallback.
- `tests/test_point_in_time.py`: source availability versus retrieval time.
- `tests/test_market_intelligence.py`: insider, earnings, corporate-action and sector signals.

The test container has no network. These tests use deterministic fixtures and do not validate live quote freshness, provider credentials, licensing or redistribution rights.

## Security and rollback

- The image is built from the pinned public source SHA. `.dockerignore` excludes Git metadata, checkout credentials, local environment files and local state.
- The container receives no host mounts or inherited environment secrets. The Docker socket remains available only to the fixed host-side test adapter; it is not mounted into the test container.
- The adapter fixes the image ID, command, test-file list, resource limits and container security options. Task payloads cannot select arbitrary images, mounts, commands or environment variables.
- A failure leaves the application source unchanged. The disposable container is removed in `finally`; the failure report is retained as a workflow artifact.
- Rollback is to remove this workflow/test harness and revert its commits. It does not modify TradingDYOR.

## Explicit limitations

This slice does not prove production isolation, production deployment, live external data, independent evaluator custody, or physical multi-host behavior. It does not yet prove the complete requirements-to-release lifecycle, parallel frontend/backend implementation, conflict-aware partial replanning, versioned full-team profiles, independent security review, or evidence-gated learning promotion. Keep issue #1225 open until those dependencies and acceptance gates are separately demonstrated.
