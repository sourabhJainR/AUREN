"""Public facade for durable AUREN memory.

The facade also accepts the earlier compact API so existing integrations can
move to the unified memory implementation without carrying a second store.
"""
from pathlib import Path
from typing import Any, Sequence

from .agent_capabilities import MemoryRecord, PersistentMemory as _PersistentMemory, redact, sanitize_untrusted
from .memory_efficiency import (
    CompactSnapshot,
    MemoryChunk,
    chunk_memory_context,
    compress_snapshot,
)


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

    def context_chunks(
        self,
        project: str,
        query: str,
        *,
        max_chars: int,
        limit: int = 20,
        intent_digest: str | None = None,
        branch: str | None = None,
        source_revision: str | None = None,
        protected_categories: Sequence[str] | None = None,
    ) -> list[MemoryChunk]:
        """Retrieve from canonical memory and emit bounded, evidence-linked chunks.

        The method does not create a second memory store and does not summarize or
        silently drop returned records. Callers should process every chunk or
        explicitly record which chunks were consumed.
        """
        records = super().search(
            project, query, limit=limit, intent_digest=intent_digest,
            branch=branch, source_revision=source_revision,
        )
        options: dict[str, Any] = {"max_chars": max_chars}
        if protected_categories is not None:
            options["protected_categories"] = protected_categories
        return chunk_memory_context(records, **options)

    @staticmethod
    def compact_snapshot(records: Sequence[MemoryRecord], *, level: int = 9) -> CompactSnapshot:
        """Return a reversible compressed snapshot; canonical SQLite memory is untouched."""
        return compress_snapshot(records, level=level)

    def close(self) -> None:
        """Compatibility no-op; each operation owns its SQLite connection."""
        return None


__all__ = [
    "CompactSnapshot", "MemoryChunk", "MemoryRecord", "PersistentMemory",
    "redact", "sanitize_untrusted",
]
