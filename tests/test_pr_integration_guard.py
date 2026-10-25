from unittest.mock import patch

from scripts.pr_integration_guard import api_list, commits_behind, overlapping_paths


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
