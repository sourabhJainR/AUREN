# AUREN Gemini entry point

Use `AGENTS.md` for the repository-wide engineering contract and `skills/ai-coding-orchestrator/SKILL.md` for the deployable AUREN workflow.

For non-trivial work, use AUREN to understand the task contract, profile the repository, progressively discover only relevant context/capabilities, execute bounded changes, verify with repository evidence, review meaningful changes, and learn only from evidence-backed outcomes.

Do not preload the full `.ai-harness` policy set. Retrieve policies, exact files/symbols/tests, history, memory, extensions, MCP tools and external research only when the current phase justifies them. Preserve `intent_digest`, boundaries, acceptance criteria, protected behavior and verification evidence.

Keep this file small and stable. Let Gemini's hierarchical and JIT context behavior provide narrower component guidance when files are touched; do not duplicate the full AUREN methodology here.

Extensions, MCP servers and A2A agents are optional capabilities. Detect and use them only when available, relevant and permitted. Never broaden permissions because another provider supports a capability.

A model response is not verification. Never claim completion, regression safety or correctness without repository-native evidence.

For security-sensitive, production, migration, irreversible or high-risk work, use AUREN's isolation, review/grill and approval gates. Never bypass repository/team rules or security boundaries.

When structured execution is needed, run `.ai-harness/run.py`; otherwise preserve the same AUREN contract even when working directly in Gemini CLI.