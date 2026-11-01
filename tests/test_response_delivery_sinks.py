import io
import tempfile
import unittest
from pathlib import Path

from portable.response_delivery_sinks import (
    OutputDeliveryError, OutputOutcomeUnknown, deliver_file, deliver_stdout,
)


class PartialWriter:
    def __init__(self, count):
        self.count = count
        self.data = b""

    def write(self, payload):
        self.data += payload[:self.count]
        return self.count

    def flush(self):
        return None


class ResponseDeliverySinkTests(unittest.TestCase):
    def test_stdout_is_prevalidated_and_never_truncates_oversized_payload(self):
        stream = io.BytesIO()
        with self.assertRaisesRegex(OutputDeliveryError, "no output was written"):
            deliver_stdout(b"12345", stream=stream, max_bytes=4)
        self.assertEqual(b"", stream.getvalue())

    def test_stdout_partial_write_is_unknown_and_not_reported_as_complete(self):
        stream = PartialWriter(2)
        with self.assertRaisesRegex(OutputOutcomeUnknown, "prefix"):
            deliver_stdout(b"abcdef", stream=stream)
        self.assertEqual(b"ab", stream.data)

    def test_stdout_complete_receipt_binds_exact_bytes(self):
        stream = io.BytesIO()
        receipt = deliver_stdout("東京".encode(), stream=stream)
        self.assertTrue(receipt["complete"])
        self.assertEqual(len("東京".encode()), receipt["bytes"])
        self.assertEqual("東京".encode(), stream.getvalue())

    def test_file_output_is_atomic_and_refuses_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.bin"
            receipt = deliver_file(b"new-result", target)
            self.assertEqual(b"new-result", target.read_bytes())
            self.assertTrue(receipt["complete"])
            with self.assertRaises(FileExistsError):
                deliver_file(b"replacement", target)
            self.assertEqual(b"new-result", target.read_bytes())

    def test_file_output_overwrite_replaces_complete_content(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "result.bin"
            target.write_bytes(b"old")
            receipt = deliver_file(b"replacement", target, overwrite=True)
            self.assertEqual(b"replacement", target.read_bytes())
            self.assertEqual(len(b"replacement"), receipt["bytes"])

    def test_file_output_rejects_symlink_and_cleans_temp_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real.bin"
            real.write_bytes(b"safe")
            link = root / "link.bin"
            link.symlink_to(real)
            with self.assertRaisesRegex(OutputDeliveryError, "symlink"):
                deliver_file(b"unsafe", link, overwrite=True)
            self.assertEqual(b"safe", real.read_bytes())
            self.assertEqual([], list(root.glob(".auren-delivery-*")))

    def test_cli_file_delivery_and_oversized_input(self):
        import auren_cli
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.txt"
            target = root / "output.txt"
            source.write_bytes(b"complete")
            self.assertEqual(0, auren_cli._run_delivery(["--input", str(source), "--output", str(target)]))
            self.assertEqual(b"complete", target.read_bytes())
            source.write_bytes(b"too-long")
            blocked = root / "blocked.txt"
            self.assertEqual(2, auren_cli._run_delivery([
                "--input", str(source), "--output", str(blocked), "--max-bytes", "2",
            ]))
            self.assertFalse(blocked.exists())


if __name__ == "__main__":
    unittest.main()
