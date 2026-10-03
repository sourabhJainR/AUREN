"""Compatibility facade for the canonical repository-intelligence store.

The implementation lives in ``portable.repository_intelligence`` and uses the
existing ``CodebaseIndex``. This module exists only so older agent skills and
callers keep working; it must never create a second repository index.
"""
from __future__ import annotations

import argparse
import json

from .repository_intelligence import (
    RepositoryAnswer as RepoAnswer,
    RepositoryEvidence as Evidence,
    RepositoryIntelligence,
    RepositoryMap,
    PackedFile,
    RepositoryPack,
    render_compact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="AUREN canonical repository intelligence facade")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--for", dest="query", default="repository orientation")
    parser.add_argument("--mode", default="search", choices=("search", "callers", "callees", "impact", "tests", "situ", "pack-task"))
    parser.add_argument("--symbol", default="")
    parser.add_argument("--token-budget", type=int, default=4000)
    parser.add_argument("--max-files", type=int, default=12)
    parser.add_argument("--context-lines", type=int, default=24)
    parser.add_argument("--graph-depth", type=int, default=1)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    query = args.symbol or args.query
    repo = RepositoryIntelligence.build(args.root)
    answer = repo.answer(query, mode=args.mode, token_budget=args.token_budget,
                         max_files=args.max_files, context_lines=args.context_lines,
                         graph_depth=args.graph_depth, base=args.base)
    print(json.dumps(answer.as_dict(), sort_keys=True) if args.json else render_compact(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "RepositoryIntelligence", "RepositoryMap", "RepoAnswer", "Evidence",
    "PackedFile", "RepositoryPack", "render_compact", "main",
]
