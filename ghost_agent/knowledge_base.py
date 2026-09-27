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

from .utils.storage import atomic_json, exclusive_lock

# File extensions we will index as text. Zim exports notes as .txt; we also
# accept common code/doc text types so a mixed reference tree works.
_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".rs", ".go",
    ".java", ".c", ".cpp", ".h", ".hpp", ".sh", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".html", ".css", ".sql", ".xml", ".ipynb",
}

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


@dataclass
class KnowledgeBaseStats:
    document_count: int
    chunk_count: int
    total_chars: int
    last_ingest_at: Optional[float]


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
            p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in _TEXT_EXTS
        )
        added = 0
        for file_path in files:
            added += self._ingest_file(file_path)
        self.last_ingest_at = time.time()
        self._save_index()
        return self.stats()

    def _ingest_file(self, file_path: Path) -> int:
        try:
            if file_path.stat().st_size > MAX_FILE_BYTES:
                return 0
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        if not text.strip():
            return 0
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Dedup by content hash: a chunk already indexed with identical source
        # bytes is skipped (idempotent re-ingest; renames are caught by content).
        if any(c.sha256 == digest for c in self.chunks):
            return 0
        # Relative-ish source label for provenance (never absolute in artifact?).
        label = self._relabel(file_path)
        added = 0
        for i, piece in enumerate(self._chunk(text)):
            chunk_id = f"{digest[:12]}:{i}"
            self.chunks.append(DocChunk(chunk_id=chunk_id, source=label,
                                        content=piece, sha256=digest))
            added += 1
        return added

    def _relabel(self, file_path: Path) -> str:
        try:
            return str(file_path.relative_to(self.root))
        except ValueError:
            return str(file_path)

    @staticmethod
    def _chunk(text: str) -> Iterable[str]:
        words = re.split(r"\s+", text.strip())
        for i in range(0, max(1, len(words)), CHUNK_WORDS):
            piece = " ".join(words[i:i + CHUNK_WORDS]).strip()
            if piece:
                yield piece

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

    def stats(self) -> KnowledgeBaseStats:
        return KnowledgeBaseStats(
            document_count=len({c.sha256 for c in self.chunks}),
            chunk_count=len(self.chunks),
            total_chars=sum(len(c.content) for c in self.chunks),
            last_ingest_at=self.last_ingest_at,
        )
