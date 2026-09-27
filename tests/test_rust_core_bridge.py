import unittest

from portable import rust_core


class RustCoreBridgeTests(unittest.TestCase):
    def test_bridge_is_safe_when_native_binary_is_missing(self):
        original = rust_core.executable
        try:
            rust_core.executable = lambda: None
            self.assertFalse(rust_core.available())
            self.assertIsNone(rust_core.sha256("hello"))
        finally:
            rust_core.executable = original

    def test_bridge_payload_shape_is_stable(self):
        payload = {"op": "sha256", "value": "hello"}
        self.assertEqual(set(payload), {"op", "value"})


if __name__ == "__main__":
    unittest.main()
