from unittest.mock import patch
import json
import tempfile
from pathlib import Path

from portable.browser_qa_ci_adapter import poll_browser_qa, persist_receipt

SHA = "a" * 40
PATH = ".github/workflows/browser-qa.yml"


def run_data(**updates):
    return {
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/owner/repo/actions/runs/1",
        "path": PATH,
        "workflow_id": 123,
        "name": "Browser QA",
        **updates,
    }


def test_pass_requires_exact_sha_and_workflow_identity():
    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        request=lambda u, t: run_data(),
    )
    assert receipt["state"] == "passed"
    assert receipt["observed_workflow_path"] == PATH
    assert receipt["workflow_id"] == 123


def test_workflow_path_mismatch_is_not_pass():
    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        request=lambda u, t: run_data(path=".github/workflows/test.yml"),
    )
    assert receipt["state"] == "workflow_mismatch"


def test_sha_mismatch_is_not_success():
    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        request=lambda u, t: run_data(head_sha="b" * 40),
    )
    assert receipt["state"] == "sha_mismatch"


def test_completed_failure_is_failed():
    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        request=lambda u, t: run_data(conclusion="failure"),
    )
    assert receipt["state"] == "failed"


def test_running_run_times_out_without_unbounded_poll():
    calls = [0]

    request_calls = [0]

    def clock():
        calls[0] += 1
        return 0 if calls[0] <= 2 else 2

    def request(url, token):
        request_calls[0] += 1
        return run_data(status="in_progress", conclusion=None)

    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        timeout_seconds=1, interval_seconds=1,
        request=request, clock=clock, sleep=lambda _: None,
    )
    assert request_calls[0] == 1
    assert receipt["state"] == "timeout"


def test_default_request_caps_timeout_to_remaining_budget():
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit): return json.dumps(run_data()).encode()

    class Opener:
        def open(self, request, timeout):
            observed["timeout"] = timeout
            return Response()

    observed = {}
    with patch("urllib.request.build_opener", return_value=Opener()):
        receipt = poll_browser_qa(
            "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
            timeout_seconds=1, clock=lambda: 0, sleep=lambda _: None,
        )
    assert observed["timeout"] == 1
    assert receipt["state"] == "passed"


def test_transport_error_is_blocked_or_timeout_not_success():
    receipt = poll_browser_qa(
        "owner/repo", 1, SHA, token="fake", expected_workflow_path=PATH,
        request=lambda u, t: (_ for _ in ()).throw(TimeoutError()),
        clock=lambda: 0, sleep=lambda _: None,
    )
    assert receipt["state"] == "blocked"


def test_receipt_atomically_persisted(tmp_path):
    path = persist_receipt({"state": "passed", "expected_sha": SHA}, tmp_path / "receipt.json")
    assert path.exists() and not tmp_path.joinpath("receipt.json.tmp").exists()
