from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from portable.agent_capabilities import PersistentMemory


ROOT = Path(__file__).resolve().parents[1]


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False

    def close(self):
        self.was_closed = True
        return super().close()


class MemoryConnectionLifecycleTests(unittest.TestCase):
    def test_persistent_memory_connection_context_closes_handle(self):
        original_connect = sqlite3.connect
        connections = []

        def tracked_connect(*args, **kwargs):
            kwargs["factory"] = _TrackingConnection
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with tempfile.TemporaryDirectory() as directory, patch(
            "sqlite3.connect", side_effect=tracked_connect,
        ):
            memory = PersistentMemory(Path(directory) / "memory.db", require_approval=False)
            connections.clear()
            with memory._connection() as db:
                db.execute("SELECT 1").fetchone()
            self.assertEqual(1, len(connections))
            self.assertTrue(connections[0].was_closed)

    def test_memory_consumers_do_not_use_sqlite_connection_context_as_close(self):
        unsafe = re.compile(
            r"with\\s+(?:self\\.memory\\._lock\\s*,\\s*)?"
            r"self\\.memory\\._connect\\(\\)\\s+as\\s+\\w+"
        )
        violations = []
        for path in sorted((ROOT / "portable").glob("*.py")):
            if unsafe.search(path.read_text(encoding="utf-8")):
                violations.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], violations, "Use PersistentMemory._connection() so SQLite handles close on Windows")


if __name__ == "__main__":
    unittest.main()
