# Local Mission Workbench

The Local Mission Workbench is an additive layer above the existing AUREN execution path. It applies the useful parts of the referenced agent-team pattern without introducing a second orchestrator.

## Why it exists

AUREN already has durable missions, graph execution, repository intelligence, learning, verification, and an end-user console. The missing piece was a small local dispatch surface that can hand independent work to local workers while keeping mutations controlled.

The model is:

~~~text
Mission
  |
  v
Existing Cognitive Plan / TaskPlan
  |
  v
StateGraph / GraphAgentTeam
  |
  +---- Local Mission Workbench
  |        |
  |        +--> read-only workers (bounded parallelism)
  |        +--> local command workers (argv, timeout, workspace)
  |        +--> serialized mutation lane
  |
  v
Existing verification / review / evidence
~~~

## Design principles carried from the reference

1. Mission before worker role: work is represented by an explicit mission and work packet.
2. Coordinator-friendly handoff: packets have IDs, dependencies, priorities, timeouts, and effects.
3. Small worker team: concurrency is bounded by local capacity rather than creating an unbounded swarm.
4. Reusable work: a packet is explicit enough to become a repeatable skill later.
5. Progressive trust: read-only work can run concurrently; mutations are serialized; promotion remains outside the workbench.
6. Proof over "done": each packet returns a structured receipt with stdout/stderr, exit status, timing, and digestable identity.
7. Local-first: no network call is required by the workbench itself.
8. Resource-aware: worker count is bounded and commands are timeout-bounded.

## Usage

Programmatic:

~~~python
from portable.local_workbench import LocalWorkbench, packet

work = [
    packet("inspect tests", ["python", "-m", "pytest", "tests/test_learning.py"]),
    packet("inspect package", ["python", "-m", "unittest", "discover", "-s", "tests"]),
]
receipt = LocalWorkbench(".").run(work)
~~~

CLI:

~~~bash
python -m portable.local_workbench --project-root . --workers 4 -- python -m unittest discover -s tests
~~~

The CLI is deliberately one packet at a time. Multi-packet orchestration belongs to the existing TaskPlan/StateGraph layer; the workbench is the local execution resource.

## Safety boundary

- read_only packets may run in parallel.
- mutating packets are serialized.
- commands use shell=False.
- working directories must remain inside the selected project root.
- every command has a timeout.
- credentials are not injected by the workbench.
- the workbench cannot push, merge, publish, or change policy by itself.
- verification and acceptance remain owned by the existing AUREN control plane.

## Relationship to existing AUREN

| Existing concern | Remains owner |
|---|---|
| Mission/dependencies | TaskPlan |
| Graph routing | StateGraph |
| Agent-team orchestration | GraphAgentTeam |
| Execution policy | .ai-harness/EXECUTION_POLICY.md |
| Verification/review | existing verification/review gates |
| Durable memory | existing memory owners |
| Learning | existing learning/maintenance lane |
| Local resources | LocalWorkbench |

This keeps the repository's core identity intact while adding a practical local execution lane.
