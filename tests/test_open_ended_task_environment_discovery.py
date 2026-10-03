from portable.open_ended_task_environment_discovery import (
    DiscoverySignal, OpenEndedTaskEnvironmentDiscovery
)


def test_discovers_novel_targets_from_failures():
    signals = (
        DiscoverySignal("e1", "science", "long-horizon", .8),
        DiscoverySignal("e2", "planning", "unfamiliar-tool", .7, True),
    )
    targets = OpenEndedTaskEnvironmentDiscovery().discover(signals)
    assert len(targets) == 2
    assert targets[0].trustworthy
    assert targets[0].environment_dimension == "long-horizon"
    assert targets[1].unfamiliar_tools


def test_deduplicates_and_budgets():
    signals = tuple(
        DiscoverySignal(f"e{i}", "science", "adversarial", .5 + i / 100)
        for i in range(5)
    )
    targets = OpenEndedTaskEnvironmentDiscovery().discover(signals, max_targets=1)
    assert len(targets) == 1
    assert targets[0].task_family == "novel-science-adversarial"


def test_rejects_invalid_signal():
    try:
        DiscoverySignal("", "science", "constraint", .5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid evidence rejection")
