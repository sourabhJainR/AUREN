# Azure Web App and Long-Running Functions Orchestration

This deployment path is intentionally small: build each application into a ZIP package, then run one manually approved GitHub Actions workflow to deploy either target or both. It does not add an Azure SDK dependency to AUREN's runtime.

## Components

- **Web App:** Azure App Service, deployed with `azure/webapps-deploy`.
- **Long-running work:** Azure Functions, deployed separately with `Azure/functions-action`.
- **Orchestration:** `.github/workflows/azure-deploy.yml`, triggered manually with `workflow_dispatch`.
- **Authentication:** GitHub Actions secret `AZURE_CREDENTIALS`; use a least-privilege service principal scoped to the target resource group. Prefer workload identity federation/OIDC in a follow-up hardening pass rather than storing a long-lived client secret.
- **Approval:** configure the GitHub environment named `azure-production` with required reviewers and any environment-specific protection rules before production use.

## Run it

1. Build and test the Web App and Functions independently. Publish ZIP packages at `artifacts/webapp.zip` and `artifacts/functions.zip`, or supply alternate package paths in the workflow inputs.
2. In GitHub, open **Actions → Azure Web App and Functions Deployment → Run workflow**.
3. Select the target(s), enter the matching Azure app names, and run against a reviewed branch.
4. Inspect both job results and verify application health in Azure before promoting traffic.

The workflow deploys prebuilt packages; it does not guess the framework, build command, runtime version, infrastructure names, or environment settings. It deliberately fails if a selected package or app name is missing. Configure the `azure-production` environment before using this against production.

## Long-running function design

Deployment alone cannot make a function safe for arbitrarily long execution. Select the hosting plan and execution model deliberately:

- Use **Durable Functions** for multi-step, checkpointed, retryable work that may span minutes, hours, or days. Keep orchestration functions deterministic and put I/O in activity functions.
- For a single long-running invocation, choose a supported **Premium or Dedicated/App Service plan** and configure `functionTimeout` only within the limits supported by that plan and runtime. Do not assume Consumption supports unlimited execution.
- Make activities idempotent; persist progress outside process memory; configure retries, cancellation, poison/dead-letter handling where applicable, and application insights/alerts.
- Keep HTTP-triggered requests short. Return an accepted operation ID and let durable/background processing continue.

## Rollback and safety

- Keep the previous known-good package and release SHA available before deployment.
- For the Web App, prefer a staging slot and swap only after smoke tests; this workflow accepts an optional `slot_name` for deployment but does not perform a slot swap or automatic rollback.
- For Functions, use a separate staging Function App for pre-production validation when a slot is unavailable or unsuitable. Revert to the previous package if post-deployment checks fail.
- Do not put connection strings or credentials in package files. Use App Settings, Key Vault references, and managed identity.
- GitHub Actions green means the package deployment action succeeded; it does not prove the app is healthy, the function can run longer than its configured limit, or rollback was rehearsed.

## Acceptance checklist

- [ ] ZIP package build and tests pass for each selected target.
- [ ] GitHub `azure-production` environment has reviewers/protection rules.
- [ ] Azure identity is least-privilege and scoped to the target resource group.
- [ ] App settings, runtime stack, storage account, and monitoring are configured outside source control.
- [ ] Web App health endpoint passes after deployment.
- [ ] Long-running Functions complete a representative job with restart/retry behavior verified.
- [ ] Previous release package is retained and rollback is rehearsed.
