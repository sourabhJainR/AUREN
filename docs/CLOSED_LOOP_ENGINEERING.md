# Closed-loop engineering

The closed-loop coordinator connects existing HWS components into a bounded lifecycle:

**observe -> predict -> decompose -> execute -> verify -> persist remediation -> learn -> promote/escalate**

`AutonomousEngineeringLoop` is an integration layer. It does not gain repository authority, bypass verification, or auto-approve promotion. Historical remediation risk can stop execution before changes are attempted. Successful verification feeds evidence back into the persistent backlog and optional learning/promotion callbacks.

The loop is bounded by `max_iterations` and defaults to fail-closed escalation when the pre-execution risk gate is exceeded.
