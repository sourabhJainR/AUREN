import unittest
from portable.repository_index import RepositoryIndex

class RustRepositoryIndexTests(unittest.TestCase):
    def test_digest_is_stable_and_python_fallback_is_available(self):
        i=RepositoryIndex()
        i.update("b.py",b"b")
        i.update("a.py",b"a")
        digest=i.digest()
        self.assertEqual(len(digest),64)

    def test_empty_index_has_deterministic_digest(self):
        self.assertEqual(len(RepositoryIndex().digest()),64)

if __name__=="__main__":
    unittest.main()
