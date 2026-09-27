"""Regression contracts for capabilities inherited from superseded PRs.

These tests intentionally verify runtime behavior/contracts rather than PR history.
They prevent future cleanup from accidentally removing functionality that was
introduced by the Hermes, Agency, portable-release, or adaptive-trigger work.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from portable.agent_capabilities import (
    AutomationScheduler,
    CapabilityFabric,
    DelegationPool,
    OutputQualityGate,
    PersistentMemory,
    ProviderAdapter,
    ProviderAdapterRegistry,
    Skill,
)
from portable.adaptive_trigger import AdaptiveTrigger
from portable.ai_coding_agency_bridge import CodingTask, run_coding_task
from portable.hermes_runtime import HermesRuntime


def test_hermes_capability_fabric_contract():
    fabric = CapabilityFabric()
    discovered = fabric.discover()
    for name in (
        "memory", "session_search", "skills", "cronjob",
        "background_processes", "provider_fallback", "delegate_task",
    ):
        assert name in discovered
    assert fabric.plan(["terminal"], network_allowed=False)[0].name == "terminal"


def test_hermes_persistent_memory_and_skill_contract(tmp_path: Path):
    memory = PersistentMemory(tmp_path / "memory.db", require_approval=False)
    record = memory.remember(
        "project", "lesson", "verified engineering lesson",
        confidence=0.9, verified=True,
    )
    assert record is not None
    assert memory.search("project", "engineering lesson")[0].verified

    runtime = HermesRuntime()
    assert runtime is not None

    # Skill registry is part of the consolidated Hermes capability surface.
    from portable.agent_capabilities import SkillRegistry
    registry = SkillRegistry()
    registry.register(Skill("demo", "demo skill", "run safely"))
    assert registry.load("demo", []).name == "demo"


def test_hermes_provider_fallback_contract():
    registry = ProviderAdapterRegistry(
        [
            ProviderAdapter("primary", frozenset({"chat"}), priority=10),
            ProviderAdapter("fallback", frozenset({"chat"}), priority=1),
        ]
    )
    assert registry.resolve(["chat"]).name == "primary"
    assert registry.resolve(["chat"], preferred=["fallback"]).name == "fallback"


def test_agency_bridge_contract():
    bridge = AICodingAgencyBridge()
    assert bridge is not None
    # The bridge must expose a callable execution boundary without requiring
    # a concrete external provider during import/construction.
    assert any(callable(getattr(bridge, name, None)) for name in (
        "execute", "run", "plan", "dispatch", "invoke"
    ))


def test_background_delegation_and_scheduler_contract(tmp_path: Path):
    pool = DelegationPool(max_workers=1)
    receipt, future = pool.submit("task-1", lambda: "ok")
    assert receipt.status == "running"
    assert future.result(timeout=2) == "ok"
    pool.close()

    scheduler = AutomationScheduler(tmp_path / "scheduler.db")
    schedule = scheduler.add("safe task", 60)
    assert scheduler.due()
    claim = scheduler.claim(schedule.id)
    assert claim
    scheduler.finish(schedule.id, claim, "success")
    assert scheduler.recent_runs(schedule.id)[0]["status"] == "success"
    scheduler.close()


def test_output_quality_gate_contract():
    result = OutputQualityGate().evaluate(
        acceptance_met=True,
        verification_passed=True,
        evidence_count=1,
        diff_clean=True,
        scope_clean=True,
        unresolved=0,
    )
    assert result.status == "ready"
    assert result.score == 100


def test_adaptive_trigger_store_contract(tmp_path: Path):
    store = AdaptiveTriggerStore(tmp_path / "triggers.db")
    # Exercise construction and durable schema; dispatch is intentionally
    # tested separately because provider/runtime execution is external.
    assert store is not None
    assert sqlite3.connect(tmp_path / "triggers.db").execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()


def test_portable_release_and_lineage_contract():
    # Current release must remain newer than the superseded 22.0.0 PR.
    import json
    plugin = json.loads(Path(".claude-plugin/plugin.json").read_text())
    assert tuple(map(int, plugin["version"].split("."))) >= (22, 1, 0)
