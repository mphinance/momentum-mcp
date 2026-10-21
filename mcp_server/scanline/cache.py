"""Tiny in-memory TTL cache keyed by a hash of the request payload.

Thread-safe enough for uvicorn's default worker. No time or random calls
happen at import time, only inside methods.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


def make_key(payload: Any) -> str:
    """Stable hash key for any JSON-serializable payload."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class TTLCache:
    """A small dict cache where entries expire after ttl_seconds and the
    number of entries is capped (oldest evicted first)."""

    def __init__(self, ttl_seconds: int = 20, max_entries: int = 256) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        """Return the cached value, or None if missing or expired."""
        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value with the configured TTL, sweeping expired entries and
        evicting the oldest when the cap would be exceeded."""
        now = time.time()
        expires_at = now + self.ttl
        with self._lock:
            for k in [k for k, (exp, _v) in self._store.items() if now >= exp]:
                del self._store[k]
            self._store.pop(key, None)
            while len(self._store) >= self.max_entries:
                self._store.pop(next(iter(self._store)))
            self._store[key] = (expires_at, value)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Drop all entries."""
        with self._lock:
            self._store.clear()
