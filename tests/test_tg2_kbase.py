"""TG2: knowledge base document lifecycle (versions, supersede, delete)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghost_agent.context_manager import MemoryRecord, ValidatedMemoryStore
from ghost_agent.knowledge_base import OwnerKnowledgeBase


def test_reingest_changed_source_supersedes_old_chunks(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("alpha beta gamma about kubernetes deployments\n" * 10,
                   encoding="utf-8")
    kb.ingest(src)
    old = list(kb.chunks)
    assert all(not c.superseded for c in old)
    src.write_text("alpha beta gamma about kubernetes helm charts and pods\n" * 10,
                   encoding="utf-8")
    kb.ingest(src)
    old_chunks = [c for c in kb.chunks if c.version == 1]
    new_chunks = [c for c in kb.chunks if c.version == 2]
    assert old_chunks and all(c.superseded for c in old_chunks)
    assert new_chunks and all(not c.superseded for c in new_chunks)
    assert all(c.version == 2 for c in new_chunks)
    # Retrieve returns only the new content.
    hits = kb.retrieve("kubernetes")
    assert hits and all(not h.superseded for h in hits)
    assert any("helm" in h.content for h in hits)
    assert not any("deployments" in h.content for h in hits)


def test_reingest_unchanged_source_is_noop(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("unchanged content here about routers\n", encoding="utf-8")
    kb.ingest(src)
    first = list(kb.chunks)
    kb.ingest(src)
    assert list(kb.chunks) == first
    assert all(not c.superseded for c in kb.chunks)
    assert all(c.version == 1 for c in kb.chunks)


def test_delete_source_removes_chunks_and_invalidates_memory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("facts about postgres indexes\n", encoding="utf-8")
    kb.ingest(src)
    label = str(src.relative_to(tmp_path.resolve()))

    store = ValidatedMemoryStore(tmp_path)
    store.remember(MemoryRecord(
        memory_id="m-dep", kind="semantic", content="pg uses btree",
        source="kbase", confidence=0.9, validation="validated",
        supporting_artifacts=[label]))
    store.remember(MemoryRecord(
        memory_id="m-free", kind="semantic", content="unrelated",
        source="elsewhere", confidence=0.9, validation="validated"))

    from ghost_agent.learner import LearnerStore
    learner = LearnerStore(tmp_path)
    learner.register_concept("postgres", "postgres")
    learner.record_attempt("postgres", True, lesson_id=label)

    removed = kb.delete_source(label)
    assert removed > 0
    assert not [c for c in kb.chunks if c.source == label]
    kb._load_index()  # deletion persisted
    assert not [c for c in kb.chunks if c.source == label]
    invalidated = {r.memory_id: r for r in store.active()}
    assert "m-dep" not in invalidated and "m-free" in invalidated

    event = None
    for line in (tmp_path / ".ghost" / "events.jsonl").read_text().splitlines():
        parsed = json.loads(line)
        if parsed["kind"] == "kbase.source_deleted":
            event = parsed
    assert event is not None
    assert event["payload"]["source"] == label
    assert event["payload"]["chunks"] == removed
    assert event["payload"]["affected_lessons"] == [label]


def test_markdown_section_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ghost_agent.knowledge_base as kb_module
    monkeypatch.setattr(kb_module, "CHUNK_WORDS", 4)  # force multiple chunks
    kb = OwnerKnowledgeBase(tmp_path)
    src = tmp_path / "guide.md"
    src.write_text(
        "# Install\nfirst words about installing things\n\n"
        "# Usage\nsecond words about running things\n",
        encoding="utf-8")
    kb.ingest(src)
    seen = {(c.section, "first words" in c.content, "second words" in c.content)
            for c in kb.chunks}
    assert ("Install", True, False) in seen
    assert ("Usage", False, True) in seen
    assert all(c.section in ("Install", "Usage") for c in kb.chunks)


def test_non_markdown_source_has_empty_section(tmp_path: Path) -> None:
    kb = OwnerKnowledgeBase(tmp_path)
    src = tmp_path / "notes.txt"
    src.write_text("# not a heading\nplain text notes about docker\n",
                   encoding="utf-8")
    kb.ingest(src)
    assert kb.chunks and all(c.section == "" for c in kb.chunks)
