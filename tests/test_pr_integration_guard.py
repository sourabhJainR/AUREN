from scripts.pr_integration_guard import commits_behind, overlapping_paths


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
