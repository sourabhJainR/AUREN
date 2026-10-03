#!/usr/bin/env python3
"""Validate AUREN interactive HTML documentation invariants."""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "html"
REQUIRED = {
    "index.html": ("Architecture", "svg", "script"),
    "architecture-current.html": ("Canonical authorities", "svg", "details"),
    "final-architecture-review.html": ("Residual risks", "svg", "details"),
    "self-improving-control-loop-design.html": ("Safety invariants", "svg", "details"),
    "self-improving-control-loop-plan.html": ("Regression gates", "svg", "details"),
}
EXTERNAL = re.compile(r"(?:src|href)\s*=\s*[\"'](?:https?:|//|data:)", re.IGNORECASE)


def validate() -> list[str]:
    errors: list[str] = []
    if not DOCS.is_dir():
        return [f"missing interactive docs directory: {DOCS}"]

    for name, required_markers in REQUIRED.items():
        path = DOCS / name
        if not path.is_file():
            errors.append(f"missing required interactive document: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        if "<!doctype html>" not in low:
            errors.append(f"{name}: missing doctype")
        if "<style>" not in low:
            errors.append(f"{name}: styles must be inline")
        for marker in required_markers:
            if marker.lower() not in low:
                errors.append(f"{name}: missing required interactive marker: {marker}")
        if EXTERNAL.search(text):
            errors.append(f"{name}: external resource dependency detected")
        if "fetch(" in low or "xmlhttprequest" in low:
            errors.append(f"{name}: runtime network access detected")

        for href in re.findall(r'href=[\"\']([^\"\']+)[\"\']', text, re.IGNORECASE):
            if href.startswith(("#", "mailto:", "javascript:")) or "://" in href:
                continue
            target = (path.parent / href.split("#", 1)[0]).resolve()
            if target.suffix == ".html" and not target.exists():
                errors.append(f"{name}: broken local HTML link: {href}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"validated {len(REQUIRED)} self-contained interactive documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
