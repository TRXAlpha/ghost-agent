"""Project-scoped document retrieval using OpenJarvis chunking and Rust/FTS5.

Ghost owns consent/scope/provenance, not a second search index. Documents are
explicitly imported, atomically corrected by source, and removable by source.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..security import SECRET_PATTERNS
from ..utils.storage import exclusive_lock
from .runtime import IntegrationError, require_upstream, state_root


class ProjectMemory:
    def __init__(self, root: Path):
        require_upstream()
        from openjarvis.tools.storage.sqlite import SQLiteMemory
        from openjarvis.tools.storage._stubs import MemoryBackendUnavailable

        self.root = Path(root).resolve()
        self.state = state_root(self.root)
        self.state.mkdir(parents=True, exist_ok=True)
        try:
            self.backend = SQLiteMemory(self.state / "knowledge.db")
        except MemoryBackendUnavailable as exc:
            raise IntegrationError("Build OpenJarvis's native extension with scripts/bootstrap-openjarvis.ps1 (without -SkipNative) to use document memory.") from exc

    def _source(self, name: str) -> tuple[Path, str]:
        raw = Path(name)
        path = (self.root / raw).resolve()
        if not path.is_relative_to(self.root):
            raise IntegrationError("Document must be inside this project.")
        relative = path.relative_to(self.root)
        blocked = {".ghost", ".git", ".venv", "workspaces", "memories", "node_modules"}
        if any(part.lower() in blocked or part.startswith(".") for part in relative.parts):
            raise IntegrationError("Private/runtime/hidden directories cannot be indexed.")
        if path.suffix.lower() not in {".md", ".txt", ".rst", ".py", ".rs", ".json", ".csv"}:
            raise IntegrationError("Import a UTF-8 text lesson or source file (.md/.txt/.rst/.py/.rs/.json/.csv).")
        return path, relative.as_posix()

    def index(self, name: str) -> dict:
        from openjarvis.tools.storage.chunking import ChunkConfig, chunk_text

        path, source = self._source(name)
        if not path.is_file():
            raise IntegrationError("Document is not a regular file.")
        with path.open("rb") as handle:
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise IntegrationError("Document exceeds the 1 MiB import limit.")
        text = raw.decode("utf-8")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS.values()):
            raise IntegrationError("Document contains a recognized secret; redact it before import.")
        digest = hashlib.sha256(raw).hexdigest()
        chunks = chunk_text(text, source=source, config=ChunkConfig(chunk_size=300, chunk_overlap=40))
        documents = [(chunk.content, {"source_sha256": digest, "chunk": chunk.index,
                                      "trust": "user_supplied_data", "source": source}) for chunk in chunks]
        with exclusive_lock(self.state / "knowledge.lock"):
            ids = self.backend.replace_source(source, documents)
        return {"source": source, "sha256": digest, "chunks": len(ids), "document_ids": ids}

    def search(self, query: str, limit: int = 5) -> list[dict]:
        if not isinstance(query, str) or len(query) > 1000 or type(limit) is not int or not 1 <= limit <= 10:
            raise IntegrationError("Search needs a query up to 1,000 characters and limit 1–10.")
        with exclusive_lock(self.state / "knowledge.lock"):
            results = self.backend.retrieve(query, top_k=limit)
        return [{"content": item.content[:4000], "source": item.source,
                 "score": item.score, "metadata": item.metadata} for item in results]

    def forget(self, name: str) -> dict:
        _, source = self._source(name)
        with exclusive_lock(self.state / "knowledge.lock"):
            self.backend.replace_source(source, [])
        return {"forgotten_source": source, "original_file_deleted": False}

    def close(self):
        if self.backend is not None:
            self.backend.close()
            # At this pin upstream close() is a no-op; releasing the wrapper
            # also releases its native connection instead of retaining it for
            # the lifetime of a caller's closed ProjectMemory instance.
            self.backend = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
