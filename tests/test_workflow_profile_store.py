from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from portable.workflow_profile import WorkflowProfile
from portable.workflow_profile_store import (
    WorkflowProfileStoreError,
    load_profile,
    save_profile,
)


def test_missing_profile_is_the_only_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "preferences" / "workflow.json"
    assert load_profile(path) is None
    expected = WorkflowProfile(delivery_mode="stage_only", execution_mode="autonomous")
    assert save_profile(path, expected) == expected
    assert load_profile(path) == expected


def test_profile_write_is_canonical_and_permissions_are_private_on_posix(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    save_profile(path, WorkflowProfile())
    first = path.read_bytes()
    save_profile(path, WorkflowProfile())
    assert path.read_bytes() == first
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0


def test_invalid_existing_profile_does_not_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_text('{"profile_version":"unknown"}', encoding="utf-8")
    with pytest.raises(WorkflowProfileStoreError):
        load_profile(path)


def test_duplicate_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_text('{"delivery_mode":"stage_only","delivery_mode":"working_tree"}', encoding="utf-8")
    with pytest.raises(WorkflowProfileStoreError, match="duplicate"):
        load_profile(path)


def test_oversized_profile_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    path.write_bytes(b" " * 16385)
    with pytest.raises(WorkflowProfileStoreError, match="size limit"):
        load_profile(path)


def test_symlink_profile_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    target.write_text(json.dumps(WorkflowProfile().as_dict()), encoding="utf-8")
    link = tmp_path / "workflow.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")
    with pytest.raises(WorkflowProfileStoreError, match="symlink"):
        load_profile(link)
    with pytest.raises(WorkflowProfileStoreError, match="symlink"):
        save_profile(link, WorkflowProfile())


def test_invalid_profile_is_not_written(tmp_path: Path) -> None:
    path = tmp_path / "workflow.json"
    with pytest.raises(WorkflowProfileStoreError):
        save_profile(path, {"profile_version": "unsupported"})
    assert not path.exists()
