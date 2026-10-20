"""Run a deterministic module-preserving shard of AUREN's unittest suite.

All tests are discovered on every shard so discovery/import errors fail each shard.
Modules stay intact to preserve their module-level fixtures and test ordering.
"""
from __future__ import annotations

import argparse
import sys
import unittest
from collections import defaultdict
from pathlib import Path

# Running a script places scripts/ at sys.path[0]; tests import portable from
# the repository root, so make that root explicit just like `python -m unittest`.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def _module_name(test: unittest.TestCase) -> str:
    test_id = test.id()
    # unittest IDs are module.Class.method or Class.method depending on
    # discovery's top-level import convention.
    parts = test_id.split(".")
    return ".".join(parts[:-2]) if len(parts) >= 3 else test_id


def _partition(tests: list[unittest.TestCase], total: int) -> list[list[unittest.TestCase]]:
    by_module: dict[str, list[unittest.TestCase]] = defaultdict(list)
    for test in tests:
        by_module[_module_name(test)].append(test)

    shards: list[list[unittest.TestCase]] = [[] for _ in range(total)]
    # Deterministic greedy bin packing balances module-sized chunks without
    # splitting module-level setup/teardown or the order within a module.
    modules = sorted(by_module.items(), key=lambda item: (-len(item[1]), item[0]))
    for _, module_tests in modules:
        target = min(range(total), key=lambda index: (len(shards[index]), index))
        shards[target].extend(module_tests)
    return shards


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, required=True)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--start-dir", default="tests")
    args = parser.parse_args()
    if args.total < 1 or not 0 <= args.index < args.total:
        parser.error("require total >= 1 and 0 <= index < total")

    loader = unittest.TestLoader()
    discovered = list(_flatten(loader.discover(args.start_dir)))
    if not discovered:
        print(f"No tests discovered under {args.start_dir}", file=sys.stderr)
        return 2
    shards = _partition(discovered, args.total)
    selected = shards[args.index]
    if not selected:
        print(
            f"Shard {args.index}/{args.total} is empty "
            f"(discovered {len(discovered)} tests across {len(set(_module_name(t) for t in discovered))} modules)",
            file=sys.stderr,
        )
        return 2

    print(
        f"Running shard {args.index + 1}/{args.total}: "
        f"{len(selected)} of {len(discovered)} tests across "
        f"{len(set(_module_name(t) for t in selected))} whole modules"
    )
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
