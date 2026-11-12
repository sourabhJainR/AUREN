from portable.requirement_contract import (
    Requirement, RequirementConflict, RequirementContract, RequirementContractEngine,
)


def sample_conflict(conflict_id="C1", requirement_ids=("R1", "R2")):
    return RequirementConflict(
        conflict_id=conflict_id,
        requirement_ids=requirement_ids,
        reason="The requirements specify incompatible data freshness.",
        impact="The selected provider changes cost and data quality.",
        options=("live-paid", "delayed-free"),
        recommended_option="live-paid",
    )


def test_build_and_validate():
    engine = RequirementContractEngine()
    contract = engine.build(
        "build app",
        [Requirement("R1", "login", ("valid credentials work",))],
    )
    assert contract.complete
    assert engine.validate(contract) == ()


def test_duplicate_and_ambiguous_requirements_fail_closed():
    engine = RequirementContractEngine()
    try:
        engine.build(
            "x",
            [Requirement("R1", "a", ("ok",)), Requirement("R1", "b", ("ok",))],
        )
    except ValueError:
        pass
    else:
        assert False
    contract = engine.build(
        "x", [Requirement("R1", "a", ("ok",))],
        ambiguities=("auth provider",),
    )
    assert engine.validate(contract) == ("unresolved ambiguities remain",)


def test_blocking_conflict_prevents_contract_completion_until_decided():
    engine = RequirementContractEngine()
    contract = engine.build(
        "build market data app",
        (
            Requirement("R1", "quotes are live", ("quote age is under one second",)),
            Requirement("R2", "provider is free", ("no paid subscription",)),
        ),
        conflicts=(sample_conflict(),),
    )
    assert not contract.complete
    assert contract.unresolved_conflicts == (contract.conflicts[0],)
    assert engine.validate(contract) == (
        "requirement contract is incomplete",
        "blocking requirement conflicts remain: C1",
    )


def test_decision_is_revision_bound_immutable_and_round_trips():
    engine = RequirementContractEngine()
    contract = engine.build(
        "build market data app",
        (
            Requirement("R1", "quotes are live", ("quote age is under one second",)),
            Requirement("R2", "provider is free", ("no paid subscription",)),
        ),
        conflicts=(sample_conflict(),),
    )
    decided = engine.decide(
        contract, "C1", "delayed-free",
        rationale="Use delayed quotes for the MVP and expose freshness clearly.",
        actor_id="local-operator",
        evidence_ref="decision://C1",
    )
    assert contract.revision == 1
    assert decided.revision == 2
    assert decided.complete
    assert decided.decisions[0].contract_revision == 2
    assert decided.decisions[0].evidence_ref == "decision://C1"
    assert decided.digest != contract.digest
    restored = RequirementContract.from_dict(decided.to_dict())
    assert restored == decided
    assert restored.digest == decided.digest
    with __import__("pytest").raises(ValueError, match="already has a decision"):
        engine.decide(
            decided, "C1", "live-paid",
            rationale="change decision", actor_id="local-operator",
        )


def test_requirement_revision_invalidates_only_dependent_conflict_decisions():
    engine = RequirementContractEngine()
    requirements = (
        Requirement("R1", "quotes are live", ("fresh quotes",)),
        Requirement("R2", "provider is free", ("free provider",)),
        Requirement("R3", "news is sourced", ("source links",)),
        Requirement("R4", "sentiment is explained", ("methodology is shown",)),
    )
    contract = engine.build(
        "build research app", requirements,
        conflicts=(
            sample_conflict("C1", ("R1", "R2")),
            sample_conflict("C2", ("R3", "R4")),
        ),
    )
    contract = engine.decide(
        contract, "C1", "delayed-free",
        rationale="MVP trade-off", actor_id="operator", evidence_ref="approval://C1",
    )
    contract = engine.decide(
        contract, "C2", "live-paid",
        rationale="Licensed news feed", actor_id="operator", evidence_ref="approval://C2",
    )
    revised = engine.revise_requirement(
        contract, "R1",
        Requirement("R1", "quotes may be delayed", ("quote age is disclosed",)),
    )
    assert revised.revision == contract.revision + 1
    assert [item.conflict_id for item in revised.decisions] == ["C2"]
    assert [item.conflict_id for item in revised.unresolved_conflicts] == ["C1"]
    assert not revised.complete


def test_invalid_conflict_and_decision_bindings_are_rejected():
    engine = RequirementContractEngine()
    requirements = (
        Requirement("R1", "quotes are live", ("fresh quotes",)),
        Requirement("R2", "provider is free", ("free provider",)),
    )
    with __import__("pytest").raises(ValueError, match="known requirements"):
        engine.build(
            "x", requirements,
            conflicts=(sample_conflict(requirement_ids=("R1", "missing")),),
        )
    contract = engine.build("x", requirements, conflicts=(sample_conflict(),))
    with __import__("pytest").raises(ValueError, match="not declared"):
        engine.decide(
            contract, "C1", "unlisted",
            rationale="not an option", actor_id="operator",
        )
    with __import__("pytest").raises(ValueError, match="actor"):
        engine.decide(
            contract, "C1", "live-paid",
            rationale="no actor", actor_id="", evidence_ref="approval://test",
        )
