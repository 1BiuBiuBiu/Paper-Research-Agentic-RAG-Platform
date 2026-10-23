"""The process cache reuses a loaded index and releases it on scope change, idleness or pressure."""

import numpy as np

from app.retrieval.cache import IndexCache, LoadedIndex


def loaded(document_id: str, fingerprint: str = "f", rows: int = 2, size: int = 100):
    return LoadedIndex(
        document_id=document_id,
        fingerprint=fingerprint,
        vectors=np.zeros((rows, 4), dtype="float32"),
        abstract_count=0,
        terms={"size_bytes": size},
        size_bytes=size,
    )


def test_second_turn_reuses_the_loaded_index():
    cache = IndexCache()
    calls = []

    def loader():
        calls.append(1)
        return loaded("a")

    first = cache.get("a", "f", loader)
    second = cache.get("a", "f", loader)
    assert first is second and len(calls) == 1


def test_a_changed_fingerprint_is_reloaded():
    cache = IndexCache()
    calls = []

    def make(fingerprint):
        def loader():
            calls.append(fingerprint)
            return loaded("a", fingerprint)

        return loader

    cache.get("a", "old", make("old"))
    cache.get("a", "new", make("new"))
    assert calls == ["old", "new"]


def test_scope_change_releases_indexes_outside_the_set():
    cache = IndexCache()
    cache.get("a", "f", lambda: loaded("a"))
    cache.get("b", "f", lambda: loaded("b"))
    cache.retain({"b"})
    assert cache.stats()["entries"] == 1 and cache.stats()["bytes"] == 100


def test_entries_expire_after_the_idle_timeout():
    now = [0.0]
    cache = IndexCache(ttl=10, clock=lambda: now[0])
    calls = []

    def loader():
        calls.append(1)
        return loaded("a")

    cache.get("a", "f", loader)
    now[0] = 5
    cache.get("a", "f", loader)
    now[0] = 20
    cache.get("a", "f", loader)
    assert len(calls) == 2


def test_entry_count_and_byte_budget_evict_the_least_recently_used():
    cache = IndexCache(max_entries=2)
    cache.get("a", "f", lambda: loaded("a"))
    cache.get("b", "f", lambda: loaded("b"))
    cache.get("a", "f", lambda: loaded("a"))  # refresh a
    cache.get("c", "f", lambda: loaded("c"))
    cache.get("a", "f", lambda: loaded("a"))
    assert cache.stats()["entries"] == 2

    budget = IndexCache(max_bytes=250)
    budget.get("a", "f", lambda: loaded("a", size=100))
    budget.get("b", "f", lambda: loaded("b", size=100))
    budget.get("c", "f", lambda: loaded("c", size=200))
    assert budget.stats()["entries"] == 1 and budget.stats()["bytes"] == 200


def test_discard_forces_a_rebuild_after_reindexing():
    cache = IndexCache()
    calls = []
    cache.get("a", "f", lambda: (calls.append(1), loaded("a"))[1])
    cache.discard("a")
    cache.get("a", "f", lambda: (calls.append(1), loaded("a"))[1])
    assert len(calls) == 2


def test_a_failed_loader_is_not_cached():
    cache = IndexCache()
    attempts = []

    def broken():
        attempts.append(1)
        raise RuntimeError("disk error")

    for _ in range(2):
        try:
            cache.get("a", "f", broken)
        except RuntimeError:
            pass
    assert len(attempts) == 2 and cache.stats()["entries"] == 0
