import hashlib
import io
import json
import subprocess
import sys
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

    def test_cli_stdout_subprocess_preserves_unicode_bytes_and_emits_digest_receipt(self):
        cli = Path(__file__).resolve().parents[1] / "auren_cli.py"
        payload = ("multi-source research evidence: ₹ € 東京\n" * 3000).encode("utf-8")
        result = subprocess.run(
            [sys.executable, str(cli), "deliver", "--input", "-", "--output", "-"],
            input=payload, capture_output=True, check=False, timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(payload, result.stdout)
        receipt = json.loads(result.stderr.decode("utf-8").strip().splitlines()[-1])
        self.assertTrue(receipt["complete"])
        self.assertEqual(len(payload), receipt["bytes"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), receipt["sha256"])

    def test_cli_subprocess_oversize_emits_no_partial_output(self):
        cli = Path(__file__).resolve().parents[1] / "auren_cli.py"
        payload = "oversized-東京".encode("utf-8")
        result = subprocess.run(
            [sys.executable, str(cli), "deliver", "--input", "-", "--output", "-", "--max-bytes", "4"],
            input=payload, capture_output=True, check=False, timeout=15,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertIn(b"no output was written", result.stderr)

    def test_cli_file_subprocess_never_overwrites_without_explicit_flag(self):
        cli = Path(__file__).resolve().parents[1] / "auren_cli.py"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.bin"
            target = root / "output.bin"
            source.write_bytes(b"first-complete-value")
            first = subprocess.run(
                [sys.executable, str(cli), "deliver", "--input", str(source), "--output", str(target)],
                capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(0, first.returncode, first.stderr.decode("utf-8", errors="replace"))
            self.assertEqual(b"first-complete-value", target.read_bytes())

            source.write_bytes(b"replacement-must-not-leak")
            second = subprocess.run(
                [sys.executable, str(cli), "deliver", "--input", str(source), "--output", str(target)],
                capture_output=True, check=False, timeout=15,
            )
            self.assertEqual(2, second.returncode)
            self.assertEqual(b"first-complete-value", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
