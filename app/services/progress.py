"""Short-lived, in-memory progress for one agent turn; nothing is persisted or replayed."""

import threading
import time
from dataclasses import dataclass, field

STEP_LABELS = {
    "route_query": "确定范围",
    "translate_query": "生成英文检索式",
    "retrieve": "库级召回",
    "rerank": "Cross-Encoder 精排",
    "grade_documents": "判分：本轮文档能否完整回答",
    "targeted_retrieve": "定向补检（第 2 轮）",
    "web_search": "联网补充",
    "generate": "生成回答",
}


@dataclass
class Entry:
    events: list[dict] = field(default_factory=list)
    status: str = "running"
    updated: float = field(default_factory=time.monotonic)


class ProgressRegistry:
    """Thread-safe because the turn runs in a worker thread while the UI polls."""

    def __init__(self, ttl: float = 900) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}

    def start(self, request_id: str) -> None:
        with self._lock:
            self._purge()
            self._entries[request_id] = Entry()

    def publish(self, request_id: str, event: dict) -> None:
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return
            entry.events.append(event)
            entry.updated = time.monotonic()

    def finish(self, request_id: str, status: str = "done", message: str = "") -> None:
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                return
            if message:
                entry.events.append(
                    {"node": "error", "label": "失败", "detail": message, "query": ""}
                )
            entry.status = status
            entry.updated = time.monotonic()

    def snapshot(self, request_id: str) -> dict:
        with self._lock:
            self._purge()
            entry = self._entries.get(request_id)
            if entry is None:
                return {"status": "unknown", "events": []}
            return {"status": entry.status, "events": list(entry.events)}

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self._entries.items() if now - entry.updated > self._ttl]
        for key in expired:
            del self._entries[key]
