# Persistent Evidence Graph

Phase 22 adds durable, content-addressed evidence lineage across campaigns,
failures, and learning artifacts.

Nodes are identified by kind plus digest. Edges are immutable and can only
connect registered nodes. Lineage queries are bounded and directional.

The graph is an evidence store, not an oracle and not a promotion authority.
It does not mutate capabilities or lifecycle state.
