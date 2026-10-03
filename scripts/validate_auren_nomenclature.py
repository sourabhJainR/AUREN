#!/usr/bin/env python3
"""Reject legacy AUREN branding in the current AUREN source tree.

The old acronym is intentionally constructed below so this validator does not
become a permanent occurrence of the legacy branding it is enforcing away.
"""
from __future__ import annotations

import argparse
from pathlib import Path

LEGACY_UPPER = "AE" + "R"
LEGACY_LOWER = "ae" + "r"
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".gz", ".pdf", ".pyc", ".whl"}

def scan(root: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if LEGACY_UPPER in line or LEGACY_LOWER in line:
                findings.append((path.relative_to(root).as_posix(), line_no, line.strip()))
    return findings

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    findings = scan(Path(args.root).resolve())
    if findings:
        print("Legacy branding detected; use AUREN nomenclature:")
        for path, line_no, line in findings:
            print(f"{path}:{line_no}: {line}")
        return 1
    print("AUREN nomenclature check passed: no legacy branding remains.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
