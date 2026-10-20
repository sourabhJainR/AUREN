"""Memory-bounded stdout/stderr draining for child processes."""
from __future__ import annotations
from collections import deque
import threading

class BoundedStreamCapture:
    """Drain a pipe without deadlock while retaining at most limit bytes."""
    def __init__(self, stream, limit: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        self.stream, self.limit = stream, limit
        self._chunks = deque()
        self._size = 0
        self._truncated = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._drain, name="auren-output-drain", daemon=True)
    def start(self):
        self._thread.start()
    def _drain(self):
        try:
            while True:
                chunk = self.stream.read(8192)
                if not chunk:
                    return
                with self._lock:
                    self._chunks.append(chunk)
                    self._size += len(chunk)
                    while self._size > self.limit:
                        excess = self._size - self.limit
                        first = self._chunks[0]
                        if len(first) <= excess:
                            self._chunks.popleft()
                            self._size -= len(first)
                        else:
                            self._chunks[0] = first[excess:]
                            self._size -= excess
                        self._truncated = True
        finally:
            try:
                self.stream.close()
            except OSError:
                pass
    def join(self, timeout: float) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()
    def result(self) -> tuple[str, bool]:
        with self._lock:
            raw = b"".join(self._chunks)
            truncated = self._truncated
        return raw.decode("utf-8", errors="replace"), truncated

__all__ = ["BoundedStreamCapture"]
