from portable.autonomy_benchmark import AutonomyBenchmarkGate, DIMENSIONS
import pytest


def test_benchmark_requires_all_dimensions():
    with pytest.raises(ValueError):
        AutonomyBenchmarkGate().evaluate({name: 1.0 for name in DIMENSIONS[:-1]})


def test_benchmark_gate_requires_every_dimension():
    metrics = {name: .9 for name in DIMENSIONS}
    metrics["cross_task_transfer"] = .7
    result = AutonomyBenchmarkGate().evaluate(metrics)
    assert not result.gate_passed
    assert "cross_task_transfer" in result.failed_dimensions


def test_benchmark_passes_only_when_all_dimensions_and_overall_pass():
    result = AutonomyBenchmarkGate().evaluate({name: .9 for name in DIMENSIONS})
    assert result.gate_passed
    assert result.overall == .9
