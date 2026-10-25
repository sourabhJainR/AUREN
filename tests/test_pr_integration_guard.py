from unittest.mock import patch

from scripts.pr_integration_guard import api_list, blocking_overlaps, commits_behind, overlapping_paths, require_revision


def test_api_list_reads_all_pages_without_silently_truncating_results():
    pages = [
        [{"number": index} for index in range(100)],
        [{"number": index} for index in range(100, 102)],
    ]
    with patch("scripts.pr_integration_guard.api_json", side_effect=pages) as fetch:
        result = api_list("https://api.github.com/repos/o/r/pulls?state=open&per_page=1", "token")
    assert len(result) == 102
    assert fetch.call_count == 2
    assert "per_page=100" in fetch.call_args_list[0].args[0]
    assert "page=2" in fetch.call_args_list[1].args[0]


def test_overlapping_paths_only_reports_shared_files():
    prs = [
        {"number": 12, "state": "open", "title": "same file", "files": ["a.py", "b.py"]},
        {"number": 13, "state": "closed", "title": "closed", "files": ["a.py"]},
        {"number": 14, "state": "open", "title": "different file", "files": ["c.py"]},
    ]
    assert overlapping_paths({"a.py", "c.py"}, prs) == [
        {"number": 12, "title": "same file", "paths": ["a.py"]},
        {"number": 14, "title": "different file", "paths": ["c.py"]},
    ]


def test_overlapping_paths_empty_when_no_shared_files():
    assert overlapping_paths({"a.py"}, [
        {"number": 12, "state": "open", "files": ["b.py"]},
    ]) == []



def test_require_revision_accepts_only_the_exact_event_sha():
    with patch("scripts.pr_integration_guard.git_output", return_value="a" * 40):
        assert require_revision("origin/main", "a" * 40, "base") == "a" * 40


def test_require_revision_fails_closed_when_base_or_head_moved():
    with patch("scripts.pr_integration_guard.git_output", return_value="b" * 40):
        try:
            require_revision("origin/main", "a" * 40, "base")
        except ValueError as exc:
            assert "base ref changed" in str(exc)
        else:
            raise AssertionError("moved base ref must fail closed")


def test_oldest_overlapping_pr_keeps_integration_slot():
    current = {"number": 20, "created_at": "2026-10-25T10:00:00Z"}
    overlaps = [
        {"number": 19, "created_at": "2026-10-25T09:00:00Z", "paths": ["portable/a.py"]},
        {"number": 21, "created_at": "2026-10-25T11:00:00Z", "paths": ["portable/a.py"]},
    ]
    assert blocking_overlaps(current, overlaps) == [overlaps[0]]


def test_older_overlapping_pr_is_not_blocked_by_newer_pr():
    current = {"number": 19, "created_at": "2026-10-25T09:00:00Z"}
    overlaps = [
        {"number": 20, "created_at": "2026-10-25T10:00:00Z", "paths": ["portable/a.py"]},
    ]
    assert blocking_overlaps(current, overlaps) == []


def test_overlap_ordering_missing_evidence_fails_closed():
    import pytest

    current = {"number": 20, "created_at": "2026-10-25T10:00:00Z"}
    with pytest.raises(ValueError, match="creation order"):
        blocking_overlaps(current, [{"number": 19, "paths": ["portable/a.py"]}])

def test_api_json_uses_pinned_github_host_scope_and_bounded_response():
    import json
    with patch(
        "scripts.pr_integration_guard.guarded_https_get",
        return_value=(200, b'{"ok": true}'),
    ) as request:
        from scripts.pr_integration_guard import api_json
        assert api_json("https://api.github.com/repos/o/r", "token") == {"ok": True}
    assert request.call_args.kwargs["allowed_hosts"] == ("api.github.com",)
    assert request.call_args.kwargs["max_bytes"] == 4 * 1024 * 1024


def test_api_json_rejects_http_failure_without_parsing_as_success():
    with patch("scripts.pr_integration_guard.guarded_https_get", return_value=(403, b'{"message":"denied"}')):
        from scripts.pr_integration_guard import api_json
        try:
            api_json("https://api.github.com/repos/o/r", "token")
        except RuntimeError as exc:
            assert "request failed" in str(exc)
        else:
            raise AssertionError("HTTP denial must not be accepted as API success")
