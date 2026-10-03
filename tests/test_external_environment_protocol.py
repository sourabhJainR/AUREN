from portable.external_environment_protocol import (
    EnvironmentContract,
    EpisodeTrace,
    ExternalEnvironmentEvaluator,
)


def contract(**overrides):
    values = dict(
        environment_id="world-1",
        version="1",
        observation_modalities=("text", "image"),
        action_types=("read", "tool_call"),
        max_steps=20,
        external_tools=("search",),
        unfamiliar_tools=("search",),
    )
    values.update(overrides)
    return EnvironmentContract(**values)


def trace(**overrides):
    values = dict(
        episode_id="e1",
        environment_digest=contract().contract_digest,
        steps=12,
        success=True,
        verified=True,
        evidence_ids=("evidence-1",),
        tool_calls=("search",),
    )
    values.update(overrides)
    return EpisodeTrace(**values)


def test_contract_and_trace_are_content_addressed():
    assert contract().contract_digest == contract().contract_digest
    assert trace().trace_digest == trace().trace_digest


def test_external_evaluator_exposes_transfer_dimensions():
    result = ExternalEnvironmentEvaluator().evaluate(contract(), (trace(),))
    assert result.long_horizon_success
    assert result.multimodal_success
    assert result.unfamiliar_tool_success
    assert result.verified_rate == 1.0


def test_trace_cannot_cross_environment_boundary():
    other = contract(environment_id="other")
    try:
        ExternalEnvironmentEvaluator().evaluate(other, (trace(),))
    except ValueError as exc:
        assert "environment contract" in str(exc)
    else:
        raise AssertionError("expected boundary failure")


def test_step_budget_is_enforced():
    try:
        ExternalEnvironmentEvaluator().evaluate(contract(max_steps=5), (trace(),))
    except ValueError as exc:
        assert "step budget" in str(exc)
    else:
        raise AssertionError("expected step-budget failure")
