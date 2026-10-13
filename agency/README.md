# Agency layer

This directory defines AUREN's specialist-agent layer.

## Source

The upstream source is `msitarzewski/agency-agents`, pinned in `source-manifest.json`. AUREN does not copy upstream execution semantics into the control plane. The source supplies specialist personas and domain workflows; AUREN supplies routing, security, evidence, verification, review and promotion.

## Sync the full specialist library

```bash
python scripts/sync_agency_agents.py --ref ad9264e309bd5e5422c04784372d7841b1e5d604
python scripts/check_agency_agents.py
```

Use a commit SHA for reproducible builds. The sync command writes the complete specialist snapshot under `agency/agents/` and creates `agency/registry.json` with per-agent metadata and SHA-256 hashes.

## Use it

The AUREN orchestrator may use `portable.agency_registry.rank(task)` to obtain candidate specialists. Routing is advisory; the task contract, repository rules, security policy and verification gates remain authoritative.

## Design principle

Specialists should produce artifacts, not generic advice. The minimum quality bar is:

`clear objective -> evidence -> concrete deliverable -> verification -> independent review -> final quality gate`
