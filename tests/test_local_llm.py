import os,unittest
from unittest.mock import patch
from portable.local_llm import LocalLLMConfig, fallback_allowed, generate, coding_review_prompt

class LocalLLMTests(unittest.TestCase):
    def test_fallback_is_read_only(self):
        self.assertTrue(fallback_allowed("Read-only: True"))
        self.assertTrue(fallback_allowed("patch_allowed: false"))
        self.assertFalse(fallback_allowed("Read-only: False"))

    def test_config_has_small_default_budget(self):
        cfg=LocalLLMConfig()
        self.assertEqual(cfg.num_ctx,4096)
        self.assertEqual(cfg.num_predict,768)
        self.assertEqual(cfg.reasoning_effort,"medium")
        self.assertTrue(cfg.prompt_matrix)
        self.assertTrue(cfg.coding_mode)
        self.assertEqual(cfg.model, "qwen2.5-coder:3b")
        self.assertEqual(cfg.model_path, "")
        self.assertEqual(cfg.backend, "auto")
        self.assertEqual(cfg.seed, 17)

    def test_coding_contract_is_selective(self):
        from portable.local_llm import _matrix_prompt
        cfg=LocalLLMConfig()
        self.assertIn("CODING CONTRACT", _matrix_prompt("Fix this Python bug", cfg))
        self.assertNotIn("CODING CONTRACT", _matrix_prompt("Summarize this document", cfg))

    def test_coding_review_prompt_requires_evidence(self):
        prompt = coding_review_prompt("Review retry handling", "portable/retry.py: existing retry helper")
        self.assertIn("FINDINGS", prompt)
        self.assertIn("VERIFICATION", prompt)
        self.assertIn("every finding must cite supplied evidence", prompt)
        self.assertIn("portable/retry.py", prompt)

    def test_embedded_readiness_requires_a_real_model_file(self):
        from portable import local_llm
        cfg = LocalLLMConfig(backend="embedded", model_path="/definitely/missing/model.gguf")
        with patch("portable.local_llm.llama_cpp", create=True):
            self.assertFalse(local_llm.embedded_available(cfg))

    def test_embedded_backend_routes_without_ollama(self):
        from portable import local_llm
        cfg = LocalLLMConfig(backend="embedded", model_path="/models/test.gguf")
        with patch("portable.local_llm._generate_embedded", return_value="embedded-ok") as embedded:
            self.assertEqual(generate("hello", config=cfg), "embedded-ok")
            embedded.assert_called_once()

    def test_generate_uses_ollama_payload(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self): return b'{"response":"ok"}'
        with patch("urllib.request.urlopen", return_value=Response()) as opened:
            self.assertEqual(generate("Fix this Python bug"),"ok")
            body=opened.call_args.args[0].data.decode()
            self.assertIn('"stream": false',body)
            self.assertIn('"num_ctx": 4096',body)
            self.assertIn('"top_p": 0.9',body)
            self.assertIn('"seed": 17',body)
            self.assertIn("LOCAL REASONING CONTRACT", body)
            self.assertIn("CODING CONTRACT", body)

if __name__=="__main__":
    unittest.main()
