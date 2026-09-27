"""Regression: generic-token retrieval must not inject unrelated past goals.

Root cause (MVP smoke test, 2026-09-27): _keyword_retrieve scored lessons by
raw token overlap, so lessons whose only overlap was generic metadata
("task", "test", "iteration", ...) matched every coding query. The retrieved
lesson carried the FULL goal text of an unrelated past task (LRU cache,
fizzbuzz), and the small local model implemented that wrong goal instead of
the requested one, burning the iteration budget. IDF weighting plus a
stop/common-token blocklist and a distinctiveness threshold now returns
nothing when a query shares no distinctive token with a lesson.
"""
from __future__ import annotations

from pathlib import Path

from ghost_agent.memory.store import MemoryStore


def _make_store(tmp_path: Path) -> MemoryStore:
    store = MemoryStore(tmp_path / "memories")
    store.write_structured_lesson(
        task_id="lru_cache_eval_001",
        goal=("Create lru_cache.py implementing an LRUCache class with "
              "__init__(capacity), get(key), put(key, value), and __len__. "
              "Add focused pytest tests for eviction behavior."),
        result="iteration_limit",
        strategy="plan_first",
        what_worked=[],
        what_failed=["Task failed with result: iteration_limit"],
        files_touched=["lru_cache.py", "tests/lru_cache_test.py"],
        iterations=10,
        key_insight="Strategy 'plan_first' failed after 10 iterations",
    )
    return store


def test_generic_query_does_not_retrieve_unrelated_lesson(tmp_path):
    """A new-task query sharing only generic tokens returns no lesson."""
    store = _make_store(tmp_path)
    query = ("Create a file named hello.py containing a function add(a, b) "
             "that returns a + b, and a test file test_add.py that asserts "
             "add(2, 3) == 5. Keep it minimal.")
    assert store.retrieve(query, use_code_context=False, use_knowledge=False) == []


def test_distinctive_query_still_retrieves_relevant_lesson(tmp_path):
    """A query sharing distinctive tokens (lru, cache) still finds the lesson."""
    store = _make_store(tmp_path)
    query = ("Fix the LRUCache eviction bug in lru_cache.py; the cache "
             "evicts the wrong key when capacity is exceeded.")
    results = store.retrieve(query, use_code_context=False, use_knowledge=False)
    assert results, "distinctive overlap must still retrieve"
    assert "lru_cache" in results[0]


def test_empty_index_returns_nothing(tmp_path):
    store = MemoryStore(tmp_path / "memories")
    assert store._keyword_retrieve("anything at all") == []
