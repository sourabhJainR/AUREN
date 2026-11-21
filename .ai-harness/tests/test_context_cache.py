#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".ai-harness"))

from runtime.context_cache import ContextCacheScope, ContextPageCache


class ContextPageCacheTests(unittest.TestCase):
    def test_same_content_reuses_page(self):
        cache = ContextPageCache(page_chars=256)
        first = cache.put("repository", "stable instructions", stable=True)
        second = cache.put("repository", "stable instructions", stable=True)
        self.assertEqual(first.page_id, second.page_id)
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.misses, 1)

    def test_changed_content_gets_new_page(self):
        cache = ContextPageCache(page_chars=256)
        first = cache.put("repository", "version one", stable=True)
        second = cache.put("repository", "version two", stable=True)
        self.assertNotEqual(first.page_id, second.page_id)
        self.assertEqual(cache.misses, 2)

    def test_replace_pagination_evicts_stale_pages_but_reuses_unchanged_pages(self):
        cache = ContextPageCache(page_chars=256)
        original = cache.paginate(
            "repository",
            "unchanged " + "a" * 246 + "stale " + "b" * 250,
            stable=True,
        )
        refreshed = cache.paginate(
            "repository",
            "unchanged " + "a" * 246 + "fresh " + "c" * 250,
            stable=True,
            replace=True,
        )
        self.assertEqual(original[0].page_id, refreshed[0].page_id)
        self.assertNotIn(original[1].page_id, [page.page_id for page in refreshed])
        self.assertEqual([], cache.search("repository", "stale", budget_chars=600))
        self.assertEqual(2, cache.stats()["pages"])

    def test_invalidation_is_scoped_to_one_content_domain(self):
        cache = ContextPageCache(page_chars=256)
        repository_page = cache.put("repository", "repo old", stable=True)
        memory_page = cache.put("memory", "memory retained")
        removed = cache.invalidate("repository")
        self.assertEqual(1, removed)
        self.assertEqual([memory_page.page_id], [page.page_id for page in cache.search("memory", "memory")])
        self.assertNotIn(repository_page.page_id, [page.page_id for page in cache._pages.values()])

    def test_scoped_cache_isolated_by_tenant_and_privacy_scope(self):
        cache = ContextPageCache(page_chars=256)
        tenant_a = ContextCacheScope("tenant-a", "private", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        tenant_b = ContextCacheScope("tenant-b", "private", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        cache.put_scoped(tenant_a, "repository", "secret tenant A data")
        self.assertEqual([], cache.search_scoped(tenant_b, "repository", "secret"))
        self.assertEqual(["secret tenant A data"], [p.text for p in cache.search_scoped(tenant_a, "repository", "secret")])

    def test_scoped_cache_rejects_reuse_after_policy_or_rubric_revision(self):
        cache = ContextPageCache(page_chars=256)
        original = ContextCacheScope("local-anonymous", "local-only", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        changed_policy = ContextCacheScope("local-anonymous", "local-only", "ctx1", "model1", "tool1", "policy2", "repo1", "rubric1")
        changed_rubric = ContextCacheScope("local-anonymous", "local-only", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric2")
        cache.put_scoped(original, "instructions", "preserve constraints")
        self.assertEqual([], cache.search_scoped(changed_policy, "instructions", "constraints"))
        self.assertEqual([], cache.search_scoped(changed_rubric, "instructions", "constraints"))
        self.assertEqual(1, cache.invalidate_scoped(original))
        self.assertEqual([], cache.search_scoped(original, "instructions", "constraints"))

    def test_unscoped_reads_never_expose_scoped_pages(self):
        cache = ContextPageCache(page_chars=256)
        scope = ContextCacheScope("local-anonymous", "local-only", "ctx1", "model1", "tool1", "policy1", "repo1", "rubric1")
        cache.put_scoped(scope, "repository", "private scoped content")
        self.assertEqual([], cache.search("repository", "private"))

    def test_scoped_cache_requires_explicit_scope(self):
        cache = ContextPageCache()
        with self.assertRaises(TypeError):
            cache.put_scoped(None, "repository", "data")

    def test_pagination_is_bounded(self):
        cache = ContextPageCache(page_chars=256)
        pages = cache.paginate("history", "x" * 700)
        self.assertEqual([len(page.text) for page in pages], [256, 256, 188])
        selected = cache.select(pages, 520)
        self.assertEqual(len(selected), 2)
        self.assertLessEqual(sum(len(page.text) + 1 for page in selected), 520)


if __name__ == "__main__":
    unittest.main()
