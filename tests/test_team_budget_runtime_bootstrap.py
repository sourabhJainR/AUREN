from __future__ import annotations

import pytest

from portable.adaptive_runtime import AdaptiveRuntime
from portable.orchestration import Graph, Node, NodeKind


def _graph():
    return Graph([Node("bootstrap", NodeKind.DETERMINISTIC, lambda _state: {"ok": True})])


def test_adaptive_runtime_passes_host_budget_verifiers_to_controller(tmp_path):
    settlement_verifier = lambda _receipt: False
    release_verifier = lambda _receipt: False
    runtime = AdaptiveRuntime(
        _graph(),
        state_root=tmp_path,
        team_budget_settlement_verifier=settlement_verifier,
        team_budget_release_verifier=release_verifier,
    )
    assert runtime.execution_controller._team_budget_settlement_verifier is settlement_verifier
    assert runtime.execution_controller._team_budget_release_verifier is release_verifier


def test_adaptive_runtime_installs_enforcer_inside_composition_root(tmp_path):
    class Enforcer:
        def execute(self, *_args, **_kwargs):
            return None

    enforcer = Enforcer()
    factory_calls = []

    def factory(controller):
        factory_calls.append(controller)
        return enforcer

    runtime = AdaptiveRuntime(
        _graph(),
        state_root=tmp_path,
        team_budget_enforcer_factory=factory,
    )
    assert factory_calls == [runtime.execution_controller]
    assert runtime.execution_controller._team_budget_enforcer is enforcer
    assert not hasattr(runtime.execution_controller, "configure_team_budget_enforcer")


def test_adaptive_runtime_freezes_persona_dispatch_after_trusted_root(tmp_path):
    class Enforcer:
        def execute(self, *_args, **_kwargs):
            return None

    runtime = AdaptiveRuntime(
        _graph(),
        state_root=tmp_path,
        team_budget_enforcer_factory=lambda _controller: Enforcer(),
    )
    callbacks = {
        "dispatch_member": lambda _envelope: {},
        "verify_assignment_current": lambda _receipt: True,
        "verify_workspace_current": lambda _receipt: True,
        "verify_provider_readiness": lambda _profile, _assignment: True,
        "verify_execution_receipt": lambda _receipt: True,
    }
    with pytest.raises(RuntimeError, match="trusted plan dispatch root"):
        runtime.configure_team_persona_dispatch(**callbacks)
    with pytest.raises(RuntimeError, match="trusted plan dispatch root"):
        runtime.register_team_template("task-1", "run-1", "feature_delivery", revision=1)

    runtime.trusted_plan_dispatch_root = object()
    runtime.configure_team_persona_dispatch(**callbacks)
    with pytest.raises(RuntimeError, match="already configured"):
        runtime.configure_team_persona_dispatch(**callbacks)


def test_adaptive_runtime_rejects_invalid_enforcer_factory_result(tmp_path):
    with pytest.raises(TypeError, match="must return an adapter enforcer"):
        AdaptiveRuntime(
            _graph(),
            state_root=tmp_path,
            team_budget_enforcer_factory=lambda _controller: object(),
        )
