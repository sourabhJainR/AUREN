#!/usr/bin/env python3
"""Content-addressed context pages for provider-neutral coding-agent runs.

This is a harness-level analogue of KV-cache/paged-memory ideas. It does not
implement model attention or GPU KV caching. It keeps stable prompt material in
small immutable pages, reuses unchanged pages by digest, and lets callers select
only the pages needed for a phase.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ContextPage:
    page_id: str
    kind: str
    text: str
    digest: str
    stable: bool = False


class ContextPageCache:
    """Small deterministic cache with copy-on-write pages.

    The cache is deliberately process-local and dependency-free. A future
    provider adapter may map stable page digests to a real provider-side prompt
    cache without changing the context-selection contract.
    """

    def __init__(self, page_chars: int = 1800) -> None:
        self.page_chars = max(256, int(page_chars))
        self._pages: dict[str, ContextPage] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _digest(kind: str, text: str, stable: bool) -> str:
        payload = f"{kind}\0{int(stable)}\0{text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def put(self, kind: str, text: str, *, stable: bool = False) -> ContextPage:
        value = text.strip()
        digest = self._digest(kind, value, stable)
        existing = self._pages.get(digest)
        if existing is not None:
            self.hits += 1
            return existing
        self.misses += 1
        page = ContextPage(f"{kind}:{digest}", kind, value, digest, stable)
        self._pages[digest] = page
        return page

    def page(self, kind: str, text: str, *, stable: bool = False) -> ContextPage:
        return self.put(kind, text, stable=stable)

    def paginate(self, kind: str, text: str, *, stable: bool = False) -> list[ContextPage]:
        value = text.strip()
        if not value:
            return []
        chunks = [value[i : i + self.page_chars] for i in range(0, len(value), self.page_chars)]
        return [self.put(kind, chunk, stable=stable) for chunk in chunks]

    def search(self, kind: str, query: str, *, budget_chars: int = 8_000) -> list[ContextPage]:
        """Return matching existing pages without creating pages or contacting a provider."""
        if budget_chars < 1:
            raise ValueError("budget_chars must be positive")
        terms = {term.casefold() for term in query.split() if len(term) > 1}
        pages = [page for page in self._pages.values() if page.kind == kind]
        if terms:
            pages = [page for page in pages if terms.intersection(
                {term.casefold() for term in page.text.split()}
            )]
            pages.sort(key=lambda page: (
                len(terms.intersection({term.casefold() for term in page.text.split()})),
                page.stable,
            ), reverse=True)
        return self.select(pages, budget_chars)

    @staticmethod
    def select(pages: Iterable[ContextPage], budget_chars: int) -> list[ContextPage]:
        selected: list[ContextPage] = []
        used = 0
        for page in pages:
            size = len(page.text) + 1
            if used + size > max(0, int(budget_chars)):
                continue
            selected.append(page)
            used += size
        return selected

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "pages": len(self._pages),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "page_chars": self.page_chars,
        }
