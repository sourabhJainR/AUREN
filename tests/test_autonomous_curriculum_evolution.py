from portable.autonomous_curriculum_evolution import AutonomousCurriculumEvolution
from portable.open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery


def targets():
    return OpenEndedTaskEnvironmentDiscovery().discover((
        DiscoverySignal("e1", "science", "long-horizon", .9),
        DiscoverySignal("e2", "planning", "adversarial", .8, True),
        DiscoverySignal("e3", "coding", "constraint-shift", .6),
    ))


def test_builds_novel_curriculum():
    plan = AutonomousCurriculumEvolution().build(targets(), max_targets=2)
    assert plan.trustworthy
    assert len(plan.entries) == 2
    assert plan.entries[0].priority >= plan.entries[1].priority


def test_excludes_completed_targets():
    ts = targets()
    plan = AutonomousCurriculumEvolution().build(ts, completed_target_ids=(ts[0].target_id,))
    assert ts[0].target_id not in {e.target_id for e in plan.entries}


def test_budgets_targets():
    plan = AutonomousCurriculumEvolution().build(targets(), max_targets=1)
    assert len(plan.entries) == 1
    assert plan.plan_digest
