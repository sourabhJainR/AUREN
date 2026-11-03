from __future__ import annotations

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind


def test_adaptive_runtime_passes_host_budget_verifiers_to_controller(tmp_path):
    settlement_verifier = lambda _receipt: False
    release_verifier = lambda _receipt: False
    runtime = AdaptiveRuntime(
        Graph([Node("bootstrap", NodeKind.DETERMINISTIC, lambda _state: {"ok": True})]),
        state_root=tmp_path,
        team_budget_settlement_verifier=settlement_verifier,
        team_budget_release_verifier=release_verifier,
    )
    assert runtime.execution_controller._team_budget_settlement_verifier is settlement_verifier
    assert runtime.execution_controller._team_budget_release_verifier is release_verifier


def test_controller_budget_enforcer_can_only_be_installed_once(tmp_path):
    controller = AdaptiveRuntime(
        Graph([Node("bootstrap", NodeKind.DETERMINISTIC, lambda _state: {"ok": True})]),
        state_root=tmp_path,
    ).execution_controller

    class Enforcer:
        def execute(self, *_args, **_kwargs):
            return None

    first = Enforcer()
    controller.configure_team_budget_enforcer(first)
    controller.configure_team_budget_enforcer(first)
    try:
        controller.configure_team_budget_enforcer(Enforcer())
    except RuntimeError as exc:
        assert "cannot be replaced" in str(exc)
    else:
        raise AssertionError("configured team-budget enforcer was replaceable")
