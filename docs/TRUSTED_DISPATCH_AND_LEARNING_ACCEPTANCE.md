# Trusted dispatch and reproducible learning acceptance

Verified AUREN main revision: 80e6ce1907f32d4304659eff8a0a805cfa6aeb15.

## Merged implementation

- PR #1182 connects reproducible experiment manifests and outcomes to the canonical PersistentExperimentLearningStore and PersistentEvidenceGraph. The canonical store remains authoritative for evidence verification, candidate/negative/unscored disposition, promotion and retraction. Unknown outcomes cannot become positive lessons.
- PR #1183 adds TrustedPlanDispatchRoot and wires it into AdaptiveRuntime and the SDK. It resolves the live supervisor RuntimeExecutionGuard and immutable admission checkpoint, binds the exact plan/run/task/source revision, validates the durable approval audit chain and current context, checks expiry and separation of duties, and fences direct dispatch/resume bypasses. It clamps caller deadlines to the supervisor budget and rechecks the guard after approval resolution.
- Worker preflight revalidates the full trusted binding. Fresh-process resume fails closed until the host configures the trusted root. Existing task rows remain intact; older approvals without immutable expiry/request-event evidence must be reissued rather than inferred as trusted.

## Exact-head AUREN validation

PR #1183 merged only after all nine exact-head workflows passed: Runtime Resilience Matrix (Ubuntu and Windows, Python 3.11/3.12), AI Harness Check, Production AI Harness Hardening, Architecture Integrity, Portable Bundle, Agent Capability Runtime, Repository Construct Traceability, cleanup contract and PR Integration Conflict Guard.

## Cross-repository acceptance

Both external test beds pin the exact AUREN SHA above.

- TradingDYOR PR #32 merged with passing exact-head CI. It runs trusted dispatch, durable approvals, supervisor-to-graph/crew cancellation, worker lifecycle, state migration, CEO/CTO mandatory-gate tests, Docker Arena adapter integration and receiver-acknowledged HTTP delivery contracts.
- firstTestproj PR #1 merged with passing exact-head CI. It runs deterministic software-team and shared-vs-isolated persona experiments plus AUREN reproducible learning, canonical graph, persistent memory, evaluator and sealed-corpus regression suites.

## Remaining production acceptance blockers

| Gate | Status | Required evidence |
|---|---|---|
| Supervisor-to-worker trusted dispatch | Merged and CI-verified | Production bootstrap must configure the real canonical budget and approval resolvers for every deployment; deployment evidence must show the live supervisor ledger is the source |
| Graph/crew auto-mode collaboration | Repository-verified across AUREN, TradingDYOR and firstTestproj | Real provider/model runs with measured cost, latency, reliability and recovery; operator-visible final acknowledgements |
| Reproducible learning and canonical memory | Merged and CI-verified | Real experiment campaigns with independently verified evidence, transfer/holdout results and operator review |
| OS/process/filesystem/network/secret isolation | Docker adapter contract tested in CI | Independent audit against the actual production host, daemon configuration, image provenance, mount policy, secrets and threat model |
| Independent evaluator and sealed corpus custody | Internal gateway/custody contracts tested | Separately administered signing keys and holdout corpus, fresh sealed campaign, signed result and custody audit |
| #534 state migration and rollback | Blocked pending operator rehearsal | Staging dry-run/apply, interrupted-upgrade, fresh-process resume, SQLite/evidence integrity, rollback, post-cutover write reconciliation and before/after manifests |

## Readiness decision

The trusted dispatch, canonical learning bridge and cross-repository regression coverage are merged. They improve implementation confidence but do not establish production deployment readiness. Keep #696 and #534 open. Do not claim independent evaluator custody, production OS isolation or operational rollback until separately administered evidence is attached and verified.
