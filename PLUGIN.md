# Adaptive AI Coding Orchestrator

Adaptive, repository-aware AI software engineering orchestration for Claude Code and compatible Agent Skills environments.

## Install from this repository

From Claude Code:

```text
/plugin marketplace add sourabhJainR/AUREN
/plugin install adaptive-ai-coding-orchestrator@adaptive-ai-engineering
```

## Local development

```text
/plugin marketplace add .
/plugin install adaptive-ai-coding-orchestrator@adaptive-ai-engineering
```

## What it does

The plugin provides a single adaptive engineering run by default. It classifies the task, profiles the repository, discovers available optional extensions, retrieves precise code context, implements using local conventions, verifies the result, performs review, repairs failures when justified, and records evidence-backed learning.

## Optional extensions

The orchestrator can use integrations when they are already installed and enabled. They are not dependencies:

- Graphify: AST and code relationship graph.
- code-mem / codebase-memory-mcp: persistent codebase memory, structural and semantic retrieval, impact analysis.
- Superpowers: planning, TDD, systematic debugging, and execution skills.
- Ponytail: YAGNI and minimal-change discipline.
- Caveman: context/output compression.
- Other Agent Skills and MCP servers: discovered and used only when they materially improve the task.

The orchestrator never installs, enables, grants permissions to, or silently modifies optional extensions.

## Runtime contract

Normal execution is one adaptive run. Recursive loops are disabled by default and are available only when explicitly requested by the user.

Repository instructions, security boundaries, acceptance criteria, existing architecture, and verification requirements take precedence over optional extension guidance.

## Repository independence

The core skill is language-neutral and repository-first. It does not assume a framework, package manager, test runner, logging library, telemetry system, or code-intelligence provider. When a repository has an established convention, that convention wins.
