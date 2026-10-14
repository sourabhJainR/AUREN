# Azure Web App and Long-Running Functions Orchestration

This is a small, manually triggered deployment path for an existing Web App and an existing Azure Functions app. It can deploy either target or both in parallel. It does not add an Azure SDK dependency to AUREN's runtime or provision Azure infrastructure.

## Components

- **Web App:** Azure App Service, deployed with `azure/webapps-deploy`.
- **Long-running work:** Azure Functions, deployed separately with `Azure/functions-action`.
- **Orchestration:** `.github/workflows/azure-deploy.yml`, triggered manually with `workflow_dispatch`.
- **Authentication:** GitHub Actions secret `AZURE_CREDENTIALS`; use a least-privilege identity scoped to the target resources. Prefer workload identity federation/OIDC as a hardening step.
- **Approval:** configure the GitHub environment named `azure-production` with required reviewers and protection rules before production use.

## Run it

1. Ensure the reviewed branch contains the deployable Web App and Functions packages/directories. Defaults are `webapp` and `functions`; override them in the workflow inputs if needed. Build and test both packages before deployment.
2. In GitHub, open **Actions → Azure Web App and Functions Deployment → Run workflow**.
3. Select one or both targets and enter the matching Azure app names.
4. Inspect the deployment job results, then verify application health and function execution in Azure.

The workflow deploys prepared packages; it does not guess the framework, build command, runtime version, infrastructure names, or environment settings. It fails if a selected package path or app name is missing. Configure the `azure-production` environment and `AZURE_CREDENTIALS` secret before use. Do not store generated deployment artifacts or secrets in source control; teams should adapt the package step to download a trusted CI artifact when their build pipeline already publishes one.

## Long-running function design

Deployment alone cannot make a function safe for arbitrarily long execution. Select the hosting plan and execution model deliberately:

- Use **Durable Functions** for multi-step, checkpointed, retryable work that may span minutes, hours, or days. Keep orchestrator functions deterministic and put I/O in activity functions.
- For a single long-running invocation, choose a supported **Premium or Dedicated/App Service plan** and configure `functionTimeout` only within limits supported by that plan and runtime. Do not assume the Consumption plan supports unlimited execution.
- Make activities idempotent; persist progress outside process memory; configure retries, cancellation, poison/dead-letter handling where applicable, and Application Insights/alerts.
- Keep HTTP-triggered requests short. Return an accepted operation ID and let durable/background processing continue.

## Rollback and safety

- Keep the previous known-good package and release SHA available before deployment.
- For the Web App, prefer a staging slot and swap only after smoke tests. This workflow accepts an optional `slot_name` for deployment but does not perform a slot swap or automatic rollback.
- For Functions, use a separate staging Function App for pre-production validation when appropriate. Revert to the previous package if post-deployment checks fail.
- Use App Settings, Key Vault references, and managed identity for secrets and service access.
- A green deployment job means the deployment action succeeded; it does not prove the app is healthy, that functions can run beyond configured limits, or that rollback was rehearsed.

## Acceptance checklist

- [ ] Both application packages build and tests pass.
- [ ] GitHub `azure-production` environment has required protection and reviewers.
- [ ] Azure identity is least-privilege.
- [ ] Runtime stack, app settings, storage account, and monitoring are configured outside source control.
- [ ] Web App health endpoint passes after deployment.
- [ ] A representative long-running function job completes and survives restart/retry as designed.
- [ ] Previous release package is retained and rollback is rehearsed.
