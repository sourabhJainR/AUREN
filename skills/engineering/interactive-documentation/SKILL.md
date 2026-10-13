---
name: interactive-documentation
description: Turn repository-backed architecture, workflow, sequence, data-flow, and lifecycle evidence into a self-contained interactive HTML document with search, focus, relationship tracing, theme switching, and export-friendly views.
disable-model-invocation: true
---

# Interactive Documentation

Create an evidence-backed, browser-native visual document from the canonical AUREN repository and context model. This is an authoring and presentation surface, not a second repository intelligence system.

## Authoring flow

1. Establish the question and audience: architecture, workflow, sequence, data flow, lifecycle, or a focused subsystem.
2. Start from `RepositoryMap` / `CodebaseIndex` and the current `ContextEvidence` envelope when the document describes real code.
3. Keep the topology bounded: prefer a clear primary path, explicit boundaries, stable node IDs, and a small number of meaningful relationships.
4. Emit a typed JSON document containing nodes, edges, groups, metadata, evidence IDs, snapshot digest, and optional named views.
5. Validate the document before rendering. Missing evidence, duplicate IDs, dangling edges, and unsupported relationship types are errors.
6. Render one self-contained HTML file. Do not load CDN JavaScript, remote fonts, telemetry, or a viewer runtime.
7. The generated document must remain useful when opened from `file://` with no server.

## Reader capabilities

The generated document should support:

- dark/light theme switching;
- node search and focus;
- keyboard navigation and accessible labels;
- upstream/downstream relationship tracing using authored edges;
- route inspection between connected nodes;
- a details panel containing role, relationships, source references, and evidence;
- curated views such as Overview, Request Path, Data Flow, and Verification;
- zoom and pan without changing the underlying topology;
- a compact legend and evidence/verification status;
- print-friendly and screenshot-friendly presentation.

Interaction must reveal authored facts only. Do not infer runtime reachability from visual proximity, and do not claim execution coverage from graph edges.

## Evidence contract

Every code-backed node or edge should carry at least one of:

- repository path/symbol;
- `ContextEvidence` evidence ID;
- repository snapshot digest;
- verification receipt ID.

Unknowns and omitted areas must be visible in the document rather than silently filled by the renderer.

## Architecture integration

Use the canonical ownership already present in AUREN:

- repository intelligence: `RepositoryMap` / `CodebaseIndex`;
- context: `ContextEvidence`;
- provenance: existing provenance ledger;
- verification: existing verification receipts;
- lifecycle: existing phase and rollout contracts.

Never introduce another repository graph, memory store, planner, evidence ledger, or capability registry just to support visualization.

## Artifact contract

The renderer accepts one JSON document and writes one standalone HTML artifact. The artifact is deterministic for the same JSON input apart from generated-at metadata supplied by the caller.

The HTML should contain the data and viewer logic inline so the file is portable and shareable. Static rendering is the default; animated trace behavior is optional and finite. Respect `prefers-reduced-motion`.

## Quality gate

Before presenting the artifact, verify:

- JSON schema/shape is valid;
- every edge references existing nodes;
- every node ID is unique and stable;
- evidence references are preserved;
- HTML contains no external script/style dependencies;
- keyboard focus and theme controls work without a server;
- the document exposes unknowns and verification status;
- the generated artifact does not mutate repository source unless explicitly requested.
