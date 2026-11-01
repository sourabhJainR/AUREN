import io
import json
import os
from pathlib import Path

import pytest

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


def test_stdout_is_prevalidated_and_never_truncates_oversized_payload():
    stream = io.BytesIO()
    with pytest.raises(OutputDeliveryError, match="no output was written"):
        deliver_stdout(b"12345", stream=stream, max_bytes=4)
    assert stream.getvalue() == b""


def test_stdout_partial_write_is_unknown_and_not_reported_as_complete():
    stream = PartialWriter(2)
    with pytest.raises(OutputOutcomeUnknown, match="prefix"):
        deliver_stdout(b"abcdef", stream=stream)
    assert stream.data == b"ab"


def test_stdout_complete_receipt_binds_exact_bytes():
    stream = io.BytesIO()
    receipt = deliver_stdout("東京".encode(), stream=stream)
    assert receipt["complete"] is True
    assert receipt["bytes"] == len("東京".encode())
    assert stream.getvalue() == "東京".encode()


def test_file_output_is_atomic_and_refuses_implicit_overwrite(tmp_path):
    target = tmp_path / "result.bin"
    receipt = deliver_file(b"new-result", target)
    assert target.read_bytes() == b"new-result"
    assert receipt["complete"] is True
    with pytest.raises(FileExistsError):
        deliver_file(b"replacement", target)
    assert target.read_bytes() == b"new-result"


def test_file_output_overwrite_replaces_complete_content(tmp_path):
    target = tmp_path / "result.bin"
    target.write_bytes(b"old")
    receipt = deliver_file(b"replacement", target, overwrite=True)
    assert target.read_bytes() == b"replacement"
    assert receipt["bytes"] == len(b"replacement")


def test_file_output_rejects_symlink_and_cleans_temp_files(tmp_path):
    real = tmp_path / "real.bin"
    real.write_bytes(b"safe")
    link = tmp_path / "link.bin"
    link.symlink_to(real)
    with pytest.raises(OutputDeliveryError, match="symlink"):
        deliver_file(b"unsafe", link, overwrite=True)
    assert real.read_bytes() == b"safe"
    assert not list(tmp_path.glob(".auren-delivery-*"))


def test_cli_file_delivery_and_oversized_input(tmp_path, capsys):
    import auren_cli
    source = tmp_path / "input.txt"
    target = tmp_path / "output.txt"
    source.write_bytes(b"complete")
    assert auren_cli._run_delivery(["--input", str(source), "--output", str(target)]) == 0
    assert target.read_bytes() == b"complete"
    source.write_bytes(b"too-long")
    assert auren_cli._run_delivery([
        "--input", str(source), "--output", str(tmp_path / "blocked.txt"), "--max-bytes", "2",
    ]) == 2
    assert not (tmp_path / "blocked.txt").exists()
