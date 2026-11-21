#!/usr/bin/env python3
"""Content-addressed context pages with optional strict cache isolation.

The legacy process-local API remains available. Security-sensitive reuse must
use the scoped API, which binds every read/write to tenant, privacy and source
revision identity. This cache is not a persistent or distributed cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ContextPage:
    page_id: str
    kind: str
    text: str
    digest: str
    stable: bool = False
    scope_digest: str = ""


@dataclass(frozen=True, slots=True)
class ContextCacheScope:
    """Complete identity boundary for safe context reuse.

    Use a non-authenticated local namespace for standalone local runs when no
    account identity exists. Never reuse that namespace for remote tenants.
    """

    tenant_id: str
    privacy_scope: str
    context_revision: str
    model_revision: str
    tool_revision: str
    policy_revision: str
    repository_revision: str
    rubric_revision: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required for scoped cache access")

    @property
    def digest(self) -> str:
        payload = json.dumps(
            {name: getattr(self, name) for name in self.__dataclass_fields__},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ContextPageCache:
    """Deterministic process-local cache with copy-on-write scoped pages."""

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

    def put_scoped(
        self, scope: ContextCacheScope, kind: str, text: str, *, stable: bool = False,
    ) -> ContextPage:
        """Write a page whose cache identity includes every scope revision."""
        if not isinstance(scope, ContextCacheScope):
            raise TypeError("scoped cache writes require ContextCacheScope")
        value = text.strip()
        content_digest = self._digest(kind, value, stable)
        key = hashlib.sha256(
            f"{scope.digest}\0{content_digest}".encode("utf-8")
        ).hexdigest()
        existing = self._pages.get(key)
        if existing is not None:
            if existing.scope_digest != scope.digest or existing.kind != kind:
                raise RuntimeError("scoped cache key collision; refusing reuse")
            self.hits += 1
            return existing
        self.misses += 1
        page = ContextPage(
            f"scope:{scope.digest[:16]}:{kind}:{content_digest}",
            kind, value, content_digest, stable, scope.digest,
        )
        self._pages[key] = page
        return page

    def search_scoped(
        self, scope: ContextCacheScope, kind: str, query: str, *,
        budget_chars: int = 8_000,
    ) -> list[ContextPage]:
        """Read only pages matching the exact tenant/privacy/revision boundary."""
        if not isinstance(scope, ContextCacheScope):
            raise TypeError("scoped cache reads require ContextCacheScope")
        if budget_chars < 1:
            raise ValueError("budget_chars must be positive")
        terms = {term.casefold() for term in query.split() if len(term) > 1}
        pages = [
            page for page in self._pages.values()
            if page.scope_digest == scope.digest and page.kind == kind
        ]
        if terms:
            pages = [page for page in pages if terms.intersection(
                {term.casefold() for term in page.text.split()}
            )]
            pages.sort(key=lambda page: (
                len(terms.intersection({term.casefold() for term in page.text.split()})),
                page.stable,
            ), reverse=True)
        selected = self.select(pages, budget_chars)
        self.hits += len(selected)
        if not selected:
            self.misses += 1
        return selected

    def invalidate_scoped(self, scope: ContextCacheScope, *, kind: str | None = None) -> int:
        """Evict only the exact scope, optionally limited to one content domain."""
        if not isinstance(scope, ContextCacheScope):
            raise TypeError("scoped invalidation requires ContextCacheScope")
        removed = 0
        for key, page in list(self._pages.items()):
            if page.scope_digest == scope.digest and (kind is None or page.kind == kind):
                del self._pages[key]
                removed += 1
        return removed

    def page(self, kind: str, text: str, *, stable: bool = False) -> ContextPage:
        return self.put(kind, text, stable=stable)

    def paginate(
        self, kind: str, text: str, *, stable: bool = False, replace: bool = False,
    ) -> list[ContextPage]:
        value = text.strip()
        if not value:
            if replace:
                self.invalidate(kind)
            return []
        chunks = [value[i : i + self.page_chars] for i in range(0, len(value), self.page_chars)]
        if replace:
            keep_digests = {self._digest(kind, chunk.strip(), stable) for chunk in chunks}
            for digest, page in list(self._pages.items()):
                if page.kind == kind and not page.scope_digest and digest not in keep_digests:
                    del self._pages[digest]
        return [self.put(kind, chunk, stable=stable) for chunk in chunks]

    def invalidate(self, kind: str, *, keep_page_ids: set[str] | None = None) -> int:
        """Remove legacy unscoped pages for one content domain."""
        keep = keep_page_ids or set()
        removed = 0
        for digest, page in list(self._pages.items()):
            if page.kind == kind and not page.scope_digest and page.page_id not in keep:
                del self._pages[digest]
                removed += 1
        return removed

    def search(self, kind: str, query: str, *, budget_chars: int = 8_000) -> list[ContextPage]:
        """Return legacy unscoped pages only; scoped pages are never visible here."""
        if budget_chars < 1:
            raise ValueError("budget_chars must be positive")
        terms = {term.casefold() for term in query.split() if len(term) > 1}
        pages = [
            page for page in self._pages.values()
            if page.kind == kind and not page.scope_digest
        ]
        if terms:
            pages = [page for page in pages if terms.intersection(
                {term.casefold() for term in page.text.split()}
            )]
            pages.sort(key=lambda page: (
                len(terms.intersection({term.casefold() for term in page.text.split()})),
                page.stable,
            ), reverse=True)
        selected = self.select(pages, budget_chars)
        self.hits += len(selected)
        if not selected:
            self.misses += 1
        return selected

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
            "scoped_pages": sum(bool(page.scope_digest) for page in self._pages.values()),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "page_chars": self.page_chars,
        }


__all__ = ["ContextCacheScope", "ContextPage", "ContextPageCache"]
