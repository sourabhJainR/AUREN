# Engineering outcome scorecard

Use this template before and after an AUREN pilot. Store one row per task in the customer-controlled project; do not publish source code, prompts, credentials, or customer-identifying data without permission.

## Per-task record

| Field | Definition |
|---|---|
| Task ID | Stable, non-sensitive identifier |
| Task family / difficulty | Agreed category and difficulty band |
| Baseline or AUREN-assisted | Which workflow was used |
| Starting revision | Exact repository commit SHA |
| Final revision | Exact resulting commit SHA, or blank if no change |
| Task lead time | Elapsed time from accepted task to reviewed outcome |
| Human review time | Active review effort, not calendar wait |
| Acceptance criteria | Count satisfied / count agreed |
| Changed-path verification | Agreed checks run and changed paths covered |
| Regression outcome | None observed / found and fixed / escaped / unknown |
| Scope or policy violation | Count and description in restricted customer record |
| Recovery outcome | Recovered / not recovered / not applicable |
| Intervention count | Human unblock, plan change or manual repair count |
| Cost estimate | Compute/provider/tool costs and estimation method |
| Evidence completeness | Task, revision, checks, review and outcome linked |
| Notes | Confounders, missing data, limitations |

## Summary metrics

Report numerator, denominator, units, and missingness for every metric. Use medians for skewed duration data and show distributions where sample size allows.

- Median task lead time and median human review time.
- Acceptance pass rate.
- Regression count per task and any escaped regressions.
- Changed-path verification coverage.
- Evidence-completeness rate.
- Policy violations (target zero) and failed containment attempts.
- Recovery success rate for trials where recovery was expected.
- Median and total cost per completed task.
- Human intervention count per task.

## Comparison rules

- Freeze task selection, acceptance criteria and metric definitions before running the pilot.
- Record task difficulty and workflow differences; do not compare unrelated tasks as if randomized.
- Keep all failures and incomplete tasks in the results.
- Report sample size and uncertainty; avoid percentage claims from a tiny denominator.
- Do not claim causality unless the comparison design supports it.
- Never infer independent evaluation from this scorecard. Independent evaluation requires a separately controlled evaluator/corpus and retained attestation.
