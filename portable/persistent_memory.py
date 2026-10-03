"""Public facade for durable AUREN memory.

The facade also accepts the earlier compact API so existing integrations can
move to the unified memory implementation without carrying a second store.
"""
from pathlib import Path
from typing import Any

from .agent_capabilities import MemoryRecord, PersistentMemory as _PersistentMemory, redact, sanitize_untrusted


class PersistentMemory(_PersistentMemory):
    def __init__(self, path: Path | str, *, max_chars: int = 100_000,
                 require_approval: bool = True, require_write_approval: bool | None = None) -> None:
        if require_write_approval is not None:
            require_approval = require_write_approval
        super().__init__(path, max_chars=max_chars, require_approval=require_approval)

    def remember(self, project: str, category_or_text: str, text: str | None = None, **kwargs: Any) -> MemoryRecord | None:
        if text is None:
            return super().remember("default", project, category_or_text, **kwargs)
        return super().remember(project, category_or_text, text, **kwargs)

    def search(self, project_or_query: str, query: str | None = None, **kwargs: Any) -> list[MemoryRecord]:
        if query is None:
            return super().search("default", project_or_query, **kwargs)
        return super().search(project_or_query, query, **kwargs)

    def close(self) -> None:
        """Compatibility no-op; each operation owns its SQLite connection."""
        return None


__all__ = ["MemoryRecord", "PersistentMemory", "redact", "sanitize_untrusted"]
