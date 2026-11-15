"""Windows ACL helpers for AUREN local credentials and private configuration."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path


def _current_user_sid() -> str:
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if result.returncode != 0:
        raise PermissionError("could not determine the current Windows user SID")
    try:
        rows = list(csv.reader(result.stdout.splitlines()))
        sid = rows[0][1].strip() if rows and len(rows[0]) > 1 else ""
    except (csv.Error, IndexError):
        sid = ""
    if not sid.startswith("S-1-"):
        raise PermissionError("Windows returned an invalid current-user SID")
    return sid


def enforce_private_acl(path: str | Path, *, directory: bool) -> None:
    """Remove inherited ACLs and grant access only to this user and SYSTEM."""
    target = Path(path).expanduser()
    if target.is_symlink() or not target.exists():
        raise PermissionError("private path must exist and must not be a symlink")
    if directory != target.is_dir() or (not directory and not target.is_file()):
        raise PermissionError("private path has an unexpected filesystem type")
    if os.name != "nt":
        return
    sid = _current_user_sid()
    # Use SIDs rather than localized account names. SYSTEM retains recovery access.
    grant = f"*{sid}:(F) *S-1-5-18:(F)"
    result = subprocess.run(
        ["icacls", str(target), "/inheritance:r", "/grant:r", grant],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if result.returncode != 0:
        raise PermissionError("could not enforce owner-only Windows ACL: " + (result.stderr or result.stdout)[-1000:])
    verify = subprocess.run(
        ["icacls", str(target)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    output = (verify.stdout or "") + (verify.stderr or "")
    if verify.returncode != 0 or sid not in output or "S-1-5-18" not in output:
        raise PermissionError("Windows ACL verification failed for private path")
    forbidden = ("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
    if any(entry in output for entry in forbidden):
        raise PermissionError("Windows ACL still grants access to a broad user group")
