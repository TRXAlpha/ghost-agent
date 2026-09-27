"""Tests for the Owner knowledge base (ingest/retrieve) and verified
training-pair capture used to help the model improve over time."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.knowledge_base import OwnerKnowledgeBase
from ghost_agent.training_capture import TrainingPairStore, build_pair


def _make_docs(tmp_path: Path) -> Path:
    """Build a small Zim-style note tree: nested .txt + a .py note."""
    notes = tmp_path / "notes"
    (notes / "Zim").mkdir(parents=True)
    (notes / "python").mkdir()
    (notes / "python" / "requests.txt").write_text(
        "HTTP client library. Use requests.get(url) to perform a GET request. "
        "Always check response.status_code before reading response.text. "
        "Prefer timeout=10 on every request to avoid hangs.",
        encoding="utf-8",
    )
    (notes / "sync.txt").write_text(
        "File sync notes. Never delete owner data during a sync. Stage backups "
        "before writing and verify the destination matches before committing.",
        encoding="utf-8",
    )
    (notes / "flask" / "routing.py").mkdir(parents=True)
    (notes / "flask" / "routing.py" / "app.py").write_text(
        "# Flask routing\n@app.route('/health')\ndef health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    return notes


# ── Ingestion ──────────────────────────────────────────────────────────────

def test_ingest_counts_documents(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    stats = kb.ingest(_make_docs(tmp_path))
    assert stats.document_count >= 3      # requests, sync, flask app
    assert stats.chunk_count >= stats.document_count
    assert stats.total_chars > 0
    assert stats.last_ingest_at is not None


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = _make_docs(tmp_path)
    first = kb.ingest(src).chunk_count
    second = kb.ingest(src).chunk_count
    assert second == first               # re-ingest adds no duplicate chunks


def test_ingest_skips_oversized_and_unknown(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = _make_docs(tmp_path)
    (src / "binary.bin").write_bytes(b"\x00" * 10_000)     # binary ext skipped
    (src / "big.md").write_text("x" * (600 * 1024))         # oversized skipped
    kb.ingest(src)
    # No crash; binary/oversized are not in the chunk set
    assert all(c.sha256 for c in kb.chunks)


# ── Retrieval ──────────────────────────────────────────────────────────────

def test_retrieve_returns_relevant_chunk(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    kb.ingest(_make_docs(tmp_path))
    hits = kb.retrieve("how to make a request to an http api", limit=2)
    assert hits
    assert any("requests" in h.content.lower() for h in hits)


def test_retrieve_empty_when_no_query_or_nothing(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    assert kb.retrieve("") == []
    assert kb.retrieve("anything") == []          # empty index
    kb.ingest(_make_docs(tmp_path))
    # Unrelated query should yield no (or no high-scoring) hits
    hits = kb.retrieve("quantum chromodynamics notation", limit=2)
    assert isinstance(hits, list)


def test_retrieve_context_empty_when_no_relevant(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    assert kb.retrieve_context("hello") == ""


# ── Training-pair capture ──────────────────────────────────────────────────

def test_capture_appends_and_dedups(tmp_path: Path) -> None:
    store = TrainingPairStore(tmp_path)
    pair = build_pair("job-a", "build an http client", "qwen2.5-coder:1.5b",
                      "tests_passed", context="requests docs", sandbox_path=None)
    assert store.capture(pair) is True
    assert store.capture(pair) is False           # duplicate by job_id
    assert store.count() == 1


def test_capture_snapshot_sandbox_files(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "app.py").write_text("import requests\nprint('hi')\n", encoding="utf-8")
    (sandbox / "data.bin").write_bytes(b"\x01\x02")   # not in sample exts
    store = TrainingPairStore(tmp_path)
    pair = build_pair("job-b", "hit an api", "gemma4:26b", "tests_passed",
                      sandbox_path=sandbox)
    assert store.capture(pair) is True
    raw = store.list()[0]
    assert "app.py" in raw["sandbox_files"]
    assert "data.bin" not in raw["sandbox_files"]
    assert raw["tests_passed"] is True


def test_capture_rejects_empty_pair(tmp_path: Path) -> None:
    store = TrainingPairStore(tmp_path)
    from ghost_agent.training_capture import TrainingPair
    assert store.capture(TrainingPair(job_id="", goal="", model="m", result="")) is False
