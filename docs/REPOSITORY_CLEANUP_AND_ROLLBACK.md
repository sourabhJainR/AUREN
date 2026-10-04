# AUREN repository cleanup, regression and rollback contract

AUREN cleanup is a controlled engineering change, not a file-count exercise. A file or directory may be removed only after its runtime, CI, deployment, release, documentation, external-link, compatibility and operational dependencies have been evaluated.

This contract exists because cleanup can break AUREN's public presence without breaking ordinary unit tests. The GitHub Pages 404 regression is the reference failure mode: deployment entrypoints were removed before the active publishing configuration was validated.

## Mandatory cleanup gate

Every non-trivial cleanup must follow this sequence:

1. **Inventory** — map the candidate path to code imports, runtime discovery, CI workflows, release packaging, Pages, external links, generated artifacts, compatibility surfaces and operator instructions.
2. **Classify** — mark each candidate as active, externally referenced, compatibility-required, replaceable, historical, or obsolete. Only obsolete material is removable without a replacement decision.
3. **Baseline** — record the current `main` commit and the externally visible surfaces that must remain healthy.
4. **Isolate** — perform cleanup on a dedicated branch and PR. Do not mix unrelated implementation work into the cleanup.
5. **Regression evaluate** — run targeted checks plus the full relevant CI suite. For deployment or public presence, validate the deployed surface, not only repository tests.
6. **Rollback ready** — before merge, record the known-good base commit and the exact revert/restore action. If cleanup breaks a protected surface, stop forward cleanup and restore the known-good state first.
7. **Review** — inspect deleted paths, broken references, naming drift, packaging changes, workflow changes and documentation consistency.
8. **Merge only with evidence** — merge only when the cleanup preserves required behavior and the rollback path is known.
9. **Post-merge retest** — repeat the critical checks against the merged `main` state and verify externally visible behavior.
10. **Record the decision** — update this contract or its protected-artifact manifest when an artifact is intentionally retired or replaced.

## Protected AUREN presence

The machine-readable protected-artifact manifest is `docs/CLEANUP_PROTECTED_ARTIFACTS.yml`.

Protected artifacts are not automatically permanent. They are protected until their dependency is disproven **and** a replacement is validated. Removing a protected artifact requires updating the manifest in the same reviewed change and proving that the replacement preserves the required contract.

Current protected categories include:

- canonical product and installation instructions;
- GitHub Pages deployment workflow;
- canonical Pages entrypoints;
- compatibility Pages entrypoints;
- deployment contract documentation;
- release and CI surfaces that establish AUREN's installable/public presence.

## Rollback strategy

### Source rollback

For a cleanup PR that has not merged, close/revert the branch changes and return to the recorded base commit.

For a merged cleanup that introduces a regression, prefer a new revert commit/PR over rewriting `main` history. Restore the last known-good commit or the specific deleted artifacts, then re-run the regression suite.

### Deployment rollback

If public AUREN documentation or another externally visible surface regresses:

1. identify the last known-good `main` commit;
2. restore the deployment contract or revert the cleanup;
3. run the deployment workflow;
4. verify the public surface returns to the expected state;
5. only then resume cleanup analysis.

For GitHub Pages specifically, the current contract requires `site/index.html`, `site/404.html`, `docs/index.html`, and `docs/404.html` until the compatibility source is explicitly retired.

### Release/install rollback

Do not delete or overwrite an immutable installed release to recover from repository cleanup. AUREN's installer keeps versioned release directories and switches `~/.auren/current` atomically. Use the existing version rollback path, then investigate the repository regression separately.

## Regression evidence required for cleanup

The cleanup PR should capture, as applicable:

| Surface | Required evidence |
|---|---|
| Python/runtime | targeted tests and relevant integration tests |
| Architecture | architecture integrity checks |
| CI | all cleanup-affected workflows green |
| Packaging | portable bundle build/verification |
| Release | installer/release checks when packaging paths changed |
| GitHub Pages | build/deploy success and public URL validation |
| Documentation | links, paths, commands and product naming checked |
| External references | searched and confirmed, or intentionally redirected |
| Compatibility | old entrypoints/interfaces either preserved or retired with evidence |
| Rollback | known-good commit plus tested restoration/revert procedure |

A green unit-test suite alone is insufficient for cleanup that changes deployment, packaging, release, documentation or externally referenced artifacts.

## Cleanup decision rule

**Delete only when there is evidence that the artifact is obsolete, no required consumer remains, the replacement path is proven when applicable, regression checks pass, and rollback is immediately actionable.**

When uncertain, keep the artifact and investigate. A small amount of intentional compatibility surface is safer than an unverified deletion that damages AUREN's runtime or public presence.

## Change record

- 2026-10-05: established this contract after the GitHub Pages cleanup regression.
- The Pages entrypoint contract remains protected until the active publishing source and compatibility dependency are explicitly verified and retired.
