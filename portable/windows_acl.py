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
    """Replace the DACL with current-user and SYSTEM full-control entries only."""
    target = Path(path).expanduser()
    if target.is_symlink() or not target.exists():
        raise PermissionError("private path must exist and must not be a symlink")
    if directory != target.is_dir() or (not directory and not target.is_file()):
        raise PermissionError("private path has an unexpected filesystem type")
    if os.name != "nt":
        return

    sid = _current_user_sid()
    script = r"""
$ErrorActionPreference = 'Stop'
# Resolve the ACL cmdlets through PowerShell module auto-loading. Explicitly
# importing this module can fail when its type data is already registered.
Get-Command -Name Get-Acl -ErrorAction Stop | Out-Null
Get-Command -Name Set-Acl -ErrorAction Stop | Out-Null
$path = $env:AUREN_ACL_TARGET
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($existing in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($existing) }
$userSid = New-Object System.Security.Principal.SecurityIdentifier($env:AUREN_ACL_USER_SID)
$systemSid = New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18')
$inheritance = [System.Security.AccessControl.InheritanceFlags]::None
if ($env:AUREN_ACL_DIRECTORY -eq '1') {
  $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
}
$propagation = [System.Security.AccessControl.PropagationFlags]::None
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$full = [System.Security.AccessControl.FileSystemRights]::FullControl
$userRule = New-Object System.Security.AccessControl.FileSystemAccessRule($userSid, $full, $inheritance, $propagation, $allow)
$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, $full, $inheritance, $propagation, $allow)
$acl.AddAccessRule($userRule)
$acl.AddAccessRule($systemRule)
Set-Acl -LiteralPath $path -AclObject $acl
$verified = Get-Acl -LiteralPath $path
$rules = @($verified.Access)
$identities = @($rules | ForEach-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value })
if ($rules.Count -ne 2 -or $identities -notcontains $env:AUREN_ACL_USER_SID -or $identities -notcontains 'S-1-5-18') { exit 4 }
if (@($rules | Where-Object { $_.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or $_.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl }).Count -ne 0) { exit 5 }
"""
    environment = os.environ.copy()
    environment.update({
        "AUREN_ACL_TARGET": str(target.resolve()),
        "AUREN_ACL_USER_SID": sid,
        "AUREN_ACL_DIRECTORY": "1" if directory else "0",
    })
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15, check=False, env=environment,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout)[-1000:]
        raise PermissionError("could not enforce and verify private Windows ACL: " + detail)
