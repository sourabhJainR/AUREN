# Approved Crew Tool Observability

## Scope

`ApprovedToolRegistry.invoke()` emits a tool span when it runs inside an active AUREN trace context. This covers the proposal-phase read-only tools registered through `ApprovedToolCrewAdapter`. It is one concrete lifecycle integration, not a claim that every provider, MCP server, subprocess, test runner, evaluator, or external tool is instrumented.

## Event contract

Each invocation creates a child span named `tool.<registered-name>` under the currently bound supervisor/graph span. The span records:

- phase `tool` and kind `tool`;
- registered tool name and bounded argument-key list;
- SHA-256 digest of canonical JSON arguments when they are JSON-serializable;
- terminal status `ok` or `error`;
- on success, canonical result digest, result type, and encoded byte count;
- on failure, a generic terminal reason and no exception message.

Raw argument values, tool output, and exception messages are not copied into the span. This avoids placing private repository content or provider diagnostics in the observability store. The tool still returns its original result to its caller and preserves its original exception behavior.

## Failure and compatibility behavior

- When no trace context is bound, tool behavior is unchanged and no span is created.
- A missing, terminal, or otherwise unusable observation context does not grant or deny tool authority and does not prevent an already-approved tool call.
- Span persistence failures are contained by the observability exporter; canonical tool outcome remains authoritative.
- Tool allowlists and the existing side-effect prohibition remain unchanged. This instrumentation does not authorize tools, widen crew scope, or replace operation-level Guard checks.

## Regression coverage

`tests/test_crew_tool_observability.py` covers parent-span propagation, successful and failed terminal states, privacy of arguments/results/exception messages, and compatibility without a trace context.

## Remaining observability work

The integration above does not close the broader lifecycle contract in [issue #913](https://github.com/sourabhJainR/AUREN/issues/913). Continue wiring and testing the other concrete execution boundaries independently, especially provider calls outside StateGraph trace context, MCP and other tool adapters, test/evaluator transitions, evidence verification, learning persistence, and release/rollback events. Do not infer missing spans as successful work, and do not treat trace presence as evaluation or execution authority.
