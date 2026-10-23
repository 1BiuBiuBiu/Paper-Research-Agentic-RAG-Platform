"""Process-wide, bounded cache for the indexes of the papers in scope.

Rebuilding vectors or BM25 statistics on every turn was the expensive part of a query.
The cache keeps the loaded index of each paper keyed by its fingerprint, so a conversation
that keeps the same scope reuses it across turns. Memory is bounded by a byte budget, an
entry count and an idle TTL, and a change of scope releases everything outside the new set.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.retrieval.bm25 import PaperTerms

DEFAULT_MAX_BYTES = 300 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 8
DEFAULT_TTL = 30 * 60.0


@dataclass(frozen=True)
class LoadedIndex:
    document_id: str
    fingerprint: str
    vectors: np.ndarray
    abstract_count: int
    terms: PaperTerms
    size_bytes: int


@dataclass
class _Entry:
    index: LoadedIndex
    loaded_at: float


class IndexCache:
    """Least-recently-used store with a byte budget and an idle timeout."""

    def __init__(
        self,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        ttl: float = DEFAULT_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}

    def get(
        self, document_id: str, fingerprint: str, loader: Callable[[], LoadedIndex]
    ) -> LoadedIndex:
        cached = self.peek(document_id, fingerprint)
        if cached is not None:
            return cached
        # Loading happens outside the lock so a slow disk read cannot block other papers.
        index = loader()
        with self._lock:
            self._purge_expired()
            self._entries[(document_id, fingerprint)] = _Entry(index=index, loaded_at=self._clock())
            self._evict()
        return index

    def peek(self, document_id: str, fingerprint: str) -> LoadedIndex | None:
        """Return a cached index without touching the disk; used to skip re-validation."""
        key = (document_id, fingerprint)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                return None
            if self._expired(entry):
                return None
            self._entries[key] = entry
            return entry.index

    def discard(self, document_id: str) -> None:
        with self._lock:
            for key in [key for key in self._entries if key[0] == document_id]:
                del self._entries[key]

    def retain(self, document_ids: set[str]) -> None:
        """Release every index whose paper left the current scope."""
        with self._lock:
            for key in [key for key in self._entries if key[0] not in document_ids]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            self._purge_expired()
            return {
                "entries": len(self._entries),
                "bytes": sum(entry.index.size_bytes for entry in self._entries.values()),
                "max_bytes": self.max_bytes,
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl,
            }

    def _expired(self, entry: _Entry) -> bool:
        return self._clock() - entry.loaded_at > self.ttl

    def _purge_expired(self) -> None:
        for key in [key for key, entry in self._entries.items() if self._expired(entry)]:
            del self._entries[key]

    def _evict(self) -> None:
        total = sum(entry.index.size_bytes for entry in self._entries.values())
        while self._entries and (len(self._entries) > self.max_entries or total > self.max_bytes):
            key, entry = next(iter(self._entries.items()))
            del self._entries[key]
            total -= entry.index.size_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def index_size(vectors: np.ndarray, terms: PaperTerms) -> int:
    return int(vectors.nbytes) + int(terms.get("size_bytes", 0))
