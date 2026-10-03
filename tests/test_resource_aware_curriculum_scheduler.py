from contextlib import contextmanager

@contextmanager
def raises(exc, match=None):
    try:
        yield
    except exc as error:
        if match is not None and match not in str(error):
            raise AssertionError(f"expected {match!r} in {error!s}")
    else:
        raise AssertionError(f"expected {exc.__name__} to be raised")
from portable.autonomous_curriculum_evolution import AutonomousCurriculumEvolution
from portable.open_ended_task_environment_discovery import DiscoverySignal, OpenEndedTaskEnvironmentDiscovery
from portable.resource_aware_curriculum_scheduler import ResourceAwareCurriculumScheduler
from portable.resource_calibration import ResourceCalibrator, ResourceObservation

def plan():
    ts = OpenEndedTaskEnvironmentDiscovery().discover((
        DiscoverySignal("a", "science", "constraint", .9),
        DiscoverySignal("b", "planning", "constraint", .8),
    ))
    return AutonomousCurriculumEvolution().build(ts, max_targets=2)

def test_builds_bounded_schedule():
    s = ResourceAwareCurriculumScheduler().build(plan())
    assert s.curriculum_plan_digest == plan().plan_digest
    assert len(s.targets) == 2
    assert s.schedule_digest

def test_uses_historical_resource_lane():
    c = ResourceCalibrator()
    for _ in range(3):
        c.record(ResourceObservation("local", "constraint", 1, 100, True))
    c.record(ResourceObservation("cloud", "constraint", 1, 100, False))
    s = ResourceAwareCurriculumScheduler(c).build(plan())
    assert all(x.lane == "local" for x in s.targets)

def test_rejects_budget_overrun():
    with raises(ValueError, match="duration budget"):
        ResourceAwareCurriculumScheduler().build(plan(), duration_budget=1)
