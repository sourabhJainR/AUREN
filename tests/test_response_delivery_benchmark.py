from benchmarks.response_delivery_comparison import run_comparison


def test_paired_delivery_benchmark_records_integrity_recovery_and_cost_limits():
    report = run_comparison(iterations=1)
    assert report["baseline"]["integrity_ok_all"] is True
    assert report["candidate"]["integrity_ok_all"] is True
    assert report["candidate"]["complete_all"] is True
    assert report["recovery"]["recovery_complete"] is True
    assert report["recovery"]["automatic_replay_of_acknowledged_chunks"] is False
    assert report["cost"]["provider_cost_usd"] is None
    assert report["cost"]["transported_bytes_proxy"] > 0
