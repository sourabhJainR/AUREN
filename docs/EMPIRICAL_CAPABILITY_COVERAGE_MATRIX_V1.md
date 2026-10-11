# Empirical Capability Coverage Matrix v1.0

**Status:** roadmap and protocol specification; external tracks are **not yet measured** unless a signed, independently retained campaign report is linked below. This matrix is not a numerical superintelligence score and does not imply AGI/SI has been achieved.

- Program owner: AUREN Arena / issue [#454](https://github.com/sourabhJainR/AUREN/issues/454)
- Prerequisite sequence: [#399](https://github.com/sourabhJainR/AUREN/issues/399) kernel → [#400](https://github.com/sourabhJainR/AUREN/issues/400) sealed holdouts → [#401](https://github.com/sourabhJainR/AUREN/issues/401) transfer → [#402](https://github.com/sourabhJainR/AUREN/issues/402) evaluator trust → [#403](https://github.com/sourabhJainR/AUREN/issues/403) evidence-gated learning/promotion → [#404](https://github.com/sourabhJainR/AUREN/issues/404) independent campaign. Consequential promotion also depends on [#411](https://github.com/sourabhJainR/AUREN/issues/411).

## Measurement rules

1. Each campaign pins runtime/build, adapter, task generator, corpus, oracle, evaluator, configuration, environment image, and policy versions by digest.
2. Split provenance must be independently attested. The candidate cannot enumerate or read sealed cases, oracle labels, signing keys, or evaluator state.
3. Compare against at least a fixed baseline and a capability-specific ablation. Preserve source-domain regression results.
4. Report zero-shot, few-shot, and adapted results separately. Publish per-domain counts, uncertainty intervals, failures, abstentions, calibration, latency/resource cost, and recovery outcomes where relevant.
5. Include negative/adversarial cases and a fresh-holdout re-test. Keep failed/contaminated runs as evidence; never silently discard them.
6. Every promotion must bind the exact candidate revision to accepted evidence, an independent review, last-known-good version, rollback trigger/action, and verified rollback rehearsal.
7. Mark a track **independent** only when corpus custody, evaluator/oracle control, and signing authority are external to the evaluated AUREN trust domain. Internal CI fixtures are regression evidence only.
8. If a metric or task family is not operationally defined, report it as unmeasured rather than inventing an aggregate “SI score.”

## Capability-to-benchmark matrix

| Track / owner | Executable protocol and baseline | Held-out test design | Threat model / adversarial checks | Required metrics | Dependencies / promotion gate |
|---|---|---|---|---|---|
| **1. Multimodal perception and grounded action** — World Model + Arena | Versioned image, audio, video, spatial, and action suites. Baseline: modality-specific fixed model/policy and no-tool ablation. | New scenes, speakers, objects, camera/microphone conditions, spatial layouts, and action environments not used in tuning. | Hidden labels, prompt injection embedded in media, corrupted inputs, out-of-distribution scenes, unsafe or irreversible actions, simulator/real-world mismatch. | Task success by modality; grounding accuracy; action success; calibration/abstention; safety violations; latency, compute, and recovery. | External dataset/environment owner; safe action sandbox; #399–#402; no action promotion without safety + fresh-holdout gates. |
| **2. Open-ended environment learning** — World Model + Learning | Stateful environment tasks with novel goals, sparse rewards, exploration costs, reset/snapshot protocol. Baseline: fixed policy and non-learning agent. | New maps/rules/goals and environment seeds, with sealed snapshots retained by the operator. | Reward hacking, state leakage, simulator exploitation, unbounded exploration, reset contamination, resource exhaustion. | Goal completion; sample/interaction efficiency; regret; transfer to new seeds; recovery; resource use; safety incidents. | Independent environment operator; deterministic snapshot/restore; #399/#400/#404; bounded budgets and stop conditions. |
| **3. Long-horizon autonomy** — Engine + Memory | Multi-hour/day dependency graphs with interruptions, external waits, restart/resume, and bounded retry budgets. Baseline: one-shot agent and scripted workflow. | Hidden task variants and injected restarts/tool outages, unseen dependency combinations, held-out multi-day goals. | Infinite loops, stale state, duplicate side effects, lost checkpoints, privilege escalation, silent task abandonment. | End-to-end completion; deadline attainment; successful resume; duplicate side effects; escalation quality; cost; retries and time-to-recovery. | Durable state and recovery ledger; real restart/rollback rehearsal; #377/#411; independent run logs and fixed wall-clock/resource caps. |
| **4. Cross-domain transfer** — Arena + Learning | Common task/result adapter across engineering, data analysis, planning, scientific tasks, and other declared domains. Baseline: domain-specialist and no-adaptation variants. | Entire task families/projects/environments excluded from capability development; provenance and overlap checks by external curator. | Train/test overlap, contamination, task-template memorization, cherry-picked domains, adaptation data leakage. | Zero-shot, few-shot, adapted success separately; source regression; calibration; failure recovery; cost; confidence intervals. | #399–#402 and external provenance; promotion requires fresh independent target-domain retest (#401/#404). |
| **5. Causal reasoning and scientific discovery** — Learning + Arena | Preregistered hypotheses, intervention/control pairs, counterfactual predictions, replication, and negative-result retention. Baseline: correlational predictor and domain reference method. | New datasets/conditions and independently specified replication tasks; evaluator holds back outcomes until predictions are sealed. | P-hacking, leakage from labels, post-hoc hypothesis changes, confounding, selective reporting, fabricated citations/results. | Predictive accuracy; intervention effect error; calibration; replication rate; false discovery; negative-result coverage; resource cost. | Independent data/oracle custodian; preregistration and audit trail; causal attribution gate (#403); independent replication required. |
| **6. Memory and continual learning** — Memory + Learning + Guard | Longitudinal recall, retrieval, update, forgetting/interference, privacy and deletion, regression after learning. Baseline: frozen model and no-memory configuration. | New episodes/entities and adversarial distractors; fresh privacy probes and post-update regression corpus. | Poisoning, cross-user leakage, sensitive-data retention, catastrophic forgetting, stale facts, unapproved policy mutation. | Recall precision/recall; temporal accuracy; forgetting/interference; privacy leakage; regression rate; rollback correctness. | Consent/privacy policy; versioned memory snapshots; evidence-gated update and rollback; no promotion without #403/#411. |
| **7. Planning, tool use, and collaboration** — Engine + Guard | Multi-tool tasks with dependency constraints, role-separated specialist crews, independent QA, retries, and tool failures. Baseline: single-agent and fixed-plan baselines. | Unseen workflows, tool/API versions, permission configurations, and adversarial tool outputs. | Tool-output prompt injection, credential exfiltration, unauthorized side effects, self-review, crew collusion, duplicate actions. | Completion/quality; tool-call precision; permission violations; reviewer defect detection; coordination overhead; retry recovery; cost. | #411/#438; scoped adapters and trusted live context; independent reviewer; reversible side effects. |
| **8. Calibration, robustness, and adversarial resistance** — Guard + Arena | Calibration, abstention, distribution-shift, malformed-input, prompt/tool injection, and leakage test suites. Baseline: unguarded policy and calibrated reference. | Fresh adversarial generators and unseen shift categories retained by an external red team. | Adaptive attacks, evaluator probing, hidden-answer inference, output-channel leakage, unsafe requests, false confidence. | ECE/Brier or task-appropriate calibration; selective risk/coverage; attack success; leakage rate; false accept/deny; recovery. | Independent red team and sealed corpus; #400/#402; critical failures block promotion and trigger rollback. |
| **9. Independent evaluation and governance** — Arena + Guard | Signed campaign receipts, key lifecycle/revocation, durable nonce ledger, independent baseline comparison, exact-head CI, decision records and rollback drills. Baseline: current internal CI baseline, explicitly labeled non-independent. | Externally curated sealed holdout and fresh re-test under separately administered evaluator/operator. | Self-attestation, key reuse/revocation bypass, replay, stale evidence, operator/evaluator collision, unauthorized promotion. | Receipt verification rate; replay rejection; missing/stale evidence rejection; reproducibility; campaign coverage; rollback success; external attestation completeness. | #399–#404 and #411; signed raw receipts and operator-controlled infrastructure required. |

## Track lifecycle and evidence record

Use these states consistently:

- **Specified** — protocol, baseline, threat model, metrics, and dependencies documented.
- **Locally tested** — automated tests pass, but data/evaluator remain repository-controlled.
- **Externally run** — independent operator ran the versioned campaign; raw signed artifacts are retained.
- **Replicated** — a second independent run/operator reproduces the result or reports a documented discrepancy.
- **Promotion-eligible** — exact-head evidence is accepted, causal attribution passes where applicable, a fresh holdout passes, and rollback is rehearsed.
- **Blocked** — missing, stale, contaminated, contradictory, or untrusted evidence; no promotion.

Do not collapse these states into one green badge. The record for each campaign must include:

- Track and protocol version; candidate/runtime and adapter digests.
- Corpus, split manifest, environment, evaluator, oracle, and configuration digests.
- Independent operator/evaluator identities and key IDs (never private keys).
- Run nonce, timestamps, signed receipt digest, baseline/ablation definition, per-case outcome digest, metrics, confidence intervals, failures, and resource costs.
- Threat-model version, contamination/overlap review, deviations, limitations, decision-record ID, last-known-good version, rollback trigger/action, and rollback test evidence.
- A public aggregate report that does not reveal sealed prompts, answers, or sensitive environment state.

## Current evidence status (2026-10-11)

| Area | Status | Honest interpretation |
|---|---|---|
| Internal engineering/architecture regressions | Locally tested | Green CI demonstrates repository regression health only. |
| Signed sealed-corpus custody metadata, trusted receipt verification, key lifecycle, durable replay controls | Locally tested / implemented | Cryptographic and replay controls exist; they do not prove actual external custody or host isolation. |
| Cross-domain independent transfer | Not externally measured | Requires independent task-family provenance and external baseline. |
| Multimodal perception and grounded action | Not externally measured | No independent multimodal/action campaign is recorded here. |
| Open-ended learning and scientific discovery | Not externally measured | No independent environment/scientific replication artifacts are recorded here. |
| Long-horizon autonomy under independent observation | Partially implemented, not independently validated | Durable supervisor/recovery code needs representative operator-run restart and multi-hour/day evidence. |
| Full evidence-gated capability promotion | Partial | Evidence lineage is stronger, but the full default promotion path and external fresh-holdout proof remain open. |
| First independent generalization campaign | Not run | Issue #404 remains an operator-dependent blocker. |

## Next execution order

1. Finish current-main verification and the executable kernel acceptance in #399.
2. Finish sealed holdout leakage/replay/side-channel and custody acceptance in #400.
3. Run cross-domain transfer protocol #401 with externally verified no-overlap provenance.
4. Finalize independent evaluator identity, key rotation/revocation, and attestation acceptance in #402.
5. Complete evidence-to-learning and promotion/rollback integration in #403 and #411.
6. Have a separate operator execute and publish the first reproducible campaign in #404.
7. Only then use results to prioritize the nine tracks above, each with fresh holdouts and explicit limits.

**No superintelligence claim is authorized by this matrix.** AUREN must demonstrate broad, robust, independently replicated performance across these tracks before such a claim can even be evaluated; passing the governance or software gates is necessary evidence hygiene, not sufficient evidence of superintelligence.
