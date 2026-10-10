"""Owner knowledge base: ingest reference docs and retrieve relevant chunks.

What this is (and is not):

This is the *inference-time retrieval* half of \"help the model make fewer
mistakes.\" You point Ghost at a tree of reference documents (Zim wiki exports,
repo docs, .md/.txt/.rst/.py notes) and it indexes them so the coding loop can
inject the relevant snippets into the model's context window before it answers.
That lets a small local model stop re-inventing APIs, idioms, and facts it has
seen in your own docs.

This does NOT change the model's weights. It is not fine-tuning. Real
capability growth (approaching SOTA) requires fine-tuning a LoRA on a curated
corpus of verified input/target pairs; that corpus is captured separately by
``ghost_agent.training_capture`` from passing jobs. This module only makes
reference material available at inference time.

Complements the existing stores (does not replace them):
- ``MemoryStore``  -> lessons/facts/snippets learned from past tasks
- ``KnowledgeGraph`` -> file/import/task-strategy relationships
- ``CodeIndex``     -> the live repository's own code for RAG
This adds: *owner-supplied external reference documents*, chunked and
provenance-tracked, retrieved by IDF-weighted keyword scoring.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .extractors import ExtractionUnavailable, extract_any
from .utils.storage import atomic_json, exclusive_lock

# File extensions we will index as text. Zim exports notes as .txt; we also
# accept common code/doc text types so a mixed reference tree works.
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".sh", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".sql", ".xml", ".ipynb",
}

# Binary document types handled by the external extraction adapters (TP1).
_EXTRACT_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

MAX_FILE_BYTES = 512 * 1024      # skip oversized binaries/artifacts
CHUNK_WORDS = 220                 # rough chunk size for retrieval granularity
MAX_RETRIEVE_CHARS = 6000         # cap total injected text per query


@dataclass
class DocChunk:
    chunk_id: str
    source: str             # original file path (as given, relative-safe)
    content: str
    sha256: str             # of the whole source file at ingest time
    indexed_at: float = field(default_factory=time.time)
    version: int = 1        # bumped when the same source is re-ingested changed
    superseded: bool = False  # replaced by a newer version of the same source
    section: str = ""       # nearest preceding heading (.md only)
    page: Optional[int] = None  # reserved for future PDF support
    extraction_quality: str = "text"  # text | ocr | partial


@dataclass
class KnowledgeBaseStats:
    document_count: int
    chunk_count: int
    total_chars: int
    last_ingest_at: Optional[float]
    skipped: List[Dict[str, str]] = field(default_factory=list)  # [{path, reason}]


class OwnerKnowledgeBase:
    """Durable, deduplicated, provenance-tracked reference document store.

    Ingests a directory of text documents (Zim exports, doc trees), splits
    them into sharded chunks keyed by content hash, and retrieves the chunks
    most relevant to a query using IDF-weighted overlap scoring with a
    stop-word blocklist (mirrors the memory store's retrieval discipline).
    """

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.store_dir = self.root / ".ghost" / "knowledge-base"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.store_dir / "index.json"
        self._load_index()

    def _index_file(self) -> Path:
        return self.store_dir / "index.json"

    def _load_index(self) -> None:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("chunks"), list):
                raise ValueError("bad index shape")
            self.chunks: List[DocChunk] = [DocChunk(**c) for c in raw["chunks"]]
            self.last_ingest_at: Optional[float] = raw.get("last_ingest_at")
        except (FileNotFoundError, ValueError, OSError, TypeError):
            self.chunks = []
            self.last_ingest_at = None

    def _save_index(self) -> None:
        with exclusive_lock(self.index_path.with_suffix(".lock")):
            payload = {
                "last_ingest_at": self.last_ingest_at,
                "chunks": [asdict(c) for c in self.chunks],
            }
            atomic_json(self.index_path, payload)

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest(self, path: str | Path) -> KnowledgeBaseStats:
        """Index every supported text file under ``path`` (file or dir)."""
        target = Path(path).resolve()
        if not target.exists():
            raise FileNotFoundError(f"No such file or directory: {target}")
        files = [target] if target.is_file() else sorted(
            p for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in (_TEXT_EXTS | _EXTRACT_EXTS)
        )
        added = 0
        self.last_skipped: List[Dict[str, str]] = []
        for file_path in files:
            added += self._ingest_file(file_path)
        self.last_ingest_at = time.time()
        self._save_index()
        return self.stats()

    def _ingest_file(self, file_path: Path) -> int:
        if file_path.suffix.lower() in _EXTRACT_EXTS:
            return self._ingest_extracted(file_path)
        try:
            if file_path.stat().st_size > MAX_FILE_BYTES:
                return 0
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        if not text.strip():
            return 0
        return self._index_text(file_path, text)

    def _ingest_extracted(self, file_path: Path) -> int:
        # PDF/image: run the external extraction tool. When it is not
        # installed, record a skip rather than crashing the whole ingest.
        try:
            extraction = extract_any(file_path)
        except ExtractionUnavailable as exc:
            self.last_skipped.append(
                {"path": self._relabel(file_path), "reason": str(exc)})
            return 0
        except (RuntimeError, OSError, ValueError):
            return 0
        if not extraction.text.strip():
            return 0
        digest = hashlib.sha256(extraction.text.encode("utf-8")).hexdigest()
        label = self._relabel(file_path)
        existing = [c for c in self.chunks if c.source == label]
        if any(c.sha256 == digest for c in existing):
            return 0
        version = (max((c.version for c in existing), default=0) + 1) if existing else 1
        for c in existing:
            c.superseded = True
        added = 0
        for i, (page_no, page_text) in enumerate(extraction.pages):
            piece = page_text.strip()
            if not piece:
                continue  # blank/scanned page: no retrievable text
            chunk_id = f"{digest[:12]}:{i}"
            self.chunks.append(DocChunk(chunk_id=chunk_id, source=label,
                                        content=piece, sha256=digest,
                                        version=version, page=page_no,
                                        extraction_quality=extraction.quality))
            added += 1
        return added

    def _index_text(self, file_path: Path, text: str) -> int:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        label = self._relabel(file_path)
        # Same source, same bytes -> idempotent no-op. Same source, changed
        # bytes -> supersede the old chunks and store this as the next version.
        existing = [c for c in self.chunks if c.source == label]
        if any(c.sha256 == digest for c in existing):
            return 0
        version = (max((c.version for c in existing), default=0) + 1) if existing else 1
        for c in existing:
            c.superseded = True
        added = 0
        for i, (piece, section) in enumerate(self._chunk(text, label)):
            chunk_id = f"{digest[:12]}:{i}"
            self.chunks.append(DocChunk(chunk_id=chunk_id, source=label,
                                        content=piece, sha256=digest,
                                        version=version, section=section))
            added += 1
        return added

    def _relabel(self, file_path: Path) -> str:
        try:
            return str(file_path.relative_to(self.root))
        except ValueError:
            return str(file_path)

    @staticmethod
    def _chunk(text: str, source: str = "") -> Iterable[tuple[str, str]]:
        # Track the nearest preceding markdown heading so chunks keep their
        # section provenance; other file types carry no section label.
        is_markdown = source.lower().endswith((".md", ".markdown"))
        words = re.split(r"\s+", text.strip())
        # Map word index -> section heading at the time that word was written.
        sections: List[str] = []
        current = ""
        bare_hash_index: Optional[int] = None  # index of a bare '#' awaiting its heading text
        for word in words:
            if bare_hash_index is not None:
                # Word after a bare '#': it names the heading; backfill the
                # '#' position so the chunk starting there gets the heading.
                current = word
                sections[bare_hash_index] = current
                bare_hash_index = None
            elif is_markdown and word.startswith("#"):
                heading = word.lstrip("#").strip()
                if heading:
                    current = heading
                else:
                    bare_hash_index = len(sections)
                    sections.append(current)
                    continue
            sections.append(current)
        for i in range(0, max(1, len(words)), CHUNK_WORDS):
            piece = " ".join(words[i:i + CHUNK_WORDS]).strip()
            if piece:
                yield piece, sections[i] if i < len(sections) else ""

    # ── Retrieval ──────────────────────────────────────────────────────────

    @staticmethod
    def _tokens(text: str) -> List[str]:
        return [w for w in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(w) > 1]

    def retrieve(self, query: str, limit: int = 4) -> List[DocChunk]:
        """Return the most relevant chunks for ``query``, IDF-weighted.

        Returns [] when there is nothing indexed or nothing relevant, so
        callers can treat the knowledge base as fully optional.
        """
        if not self.chunks or not query.strip():
            return []
        q_tokens = self._tokens(query)
        if not q_tokens:
            return []
        df: Dict[str, int] = {}
        for c in self.chunks:
            for t in set(self._tokens(c.content)):
                df[t] = df.get(t, 0) + 1
        n = max(1, len(self.chunks))
        idf = {t: max(0.5, (n / (1 + df.get(t, 0)))) for t in set(q_tokens)}

        scored = []
        for c in self.chunks:
            if c.superseded:
                continue  # only the latest version of a source is retrievable
            c_tokens = set(self._tokens(c.content))
            score = 0.0
            for t in q_tokens:
                if t in c_tokens:
                    score += idf.get(t, 0.0)
                else:
                    # Prefix/substring match so 'request' hits 'requests' and
                    # 'http' hits 'https' — token morphology is common in docs.
                    for ct in c_tokens:
                        if t in ct or ct in t:
                            score += idf.get(t, 0.0) * 0.5
                            break
            if score > 0.5:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:limit]]

    def retrieve_context(self, query: str, limit: int = 4) -> str:
        """Human-readable knowledge context, or '' if nothing relevant."""
        chunks = self.retrieve(query, limit=limit)
        if not chunks:
            return ""
        parts = []
        total = 0
        for c in chunks:
            block = f"[{c.source}] {c.content}"
            total += len(block)
            if total > MAX_RETRIEVE_CHARS:
                break
            parts.append(block)
        if not parts:
            return ""
        return "Owner reference docs:\n" + "\n\n".join(parts)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def delete_source(self, source: str) -> int:
        """Remove every chunk of ``source`` and invalidate derived content.

        Emits ``kbase.source_deleted`` listing the removed chunk count and any
        learner lessons citing this source (learner history is not modified).
        ValidatedMemoryStore records whose supporting artifacts cite the source
        are invalidated (cascade, same as retraction).
        """
        from .context_manager import ValidatedMemoryStore
        from .runtime import EventBus, Event

        before = len(self.chunks)
        self.chunks = [c for c in self.chunks if c.source != source]
        removed = before - len(self.chunks)
        if removed:
            self._save_index()

        # Derived items built on this source must not outlive it.
        invalidated: List[str] = []
        try:
            memory_store = ValidatedMemoryStore(self.root)
            for record in memory_store.active():
                if source in record.supporting_artifacts:
                    memory_store.invalidate(
                        record.memory_id,
                        f"supporting source deleted from knowledge base: {source}")
                    invalidated.append(record.memory_id)
        except (OSError, ValueError):
            pass  # missing/corrupt memory store must not block chunk deletion

        affected_lessons: List[str] = []
        try:
            attempts_path = self.root / ".ghost" / "learner" / "attempts.json"
            raw = json.loads(attempts_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                affected_lessons = sorted({item.get("lesson_id", "")
                                           for item in raw
                                           if item.get("lesson_id") == source})
        except (OSError, ValueError):
            pass  # no learner history: nothing to report

        EventBus(self.root).emit(Event("kbase.source_deleted", {
            "source": source,
            "chunks": removed,
            "invalidated_memories": invalidated,
            "affected_lessons": affected_lessons,
        }, source="knowledge_base"))
        return removed

    def sources(self) -> List[Dict[str, Any]]:
        """Per-source summary: latest version and live (non-superseded) count."""
        by_source: Dict[str, List[DocChunk]] = {}
        for c in self.chunks:
            by_source.setdefault(c.source, []).append(c)
        return [
            {
                "source": source,
                "version": max(c.version for c in chunks),
                "chunks": sum(1 for c in chunks if not c.superseded),
                "sha256": max(chunks, key=lambda c: c.version).sha256,
            }
            for source, chunks in sorted(by_source.items())
        ]

    def stats(self) -> KnowledgeBaseStats:
        return KnowledgeBaseStats(
            document_count=len({c.sha256 for c in self.chunks}),
            chunk_count=len(self.chunks),
            total_chars=sum(len(c.content) for c in self.chunks),
            last_ingest_at=self.last_ingest_at,
            skipped=list(self.last_skipped),
        )
