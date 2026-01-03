from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List


class MemoryStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self._ensure_folders()

    def _ensure_folders(self) -> None:
        for folder in ("facts", "lessons", "snippets", "projects"):
            (self.base_dir / folder).mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.index_path.write_text("{}", encoding="utf-8")

    def retrieve(self, query: str, limit: int = 3) -> List[str]:
        index = self._load_index()
        tokens = self._tokenize(query)
        scores: Dict[str, int] = {}
        for token in tokens:
            for path in index.get(token, []):
                scores[path] = scores.get(path, 0) + 1
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        results = []
        for path, _score in ranked[:limit]:
            note_path = self.base_dir / path
            if note_path.exists():
                results.append(note_path.read_text(encoding="utf-8"))
        return results

    def write_lesson(self, task_id: str, content: str, metadata: Dict[str, object]) -> Path:
        filename = f"{task_id}.md"
        path = self.base_dir / "lessons" / filename
        note = self._format_note(metadata, content)
        path.write_text(note, encoding="utf-8")
        self._update_index(path, note)
        return path

    def _format_note(self, metadata: Dict[str, object], content: str) -> str:
        header = "---\n"
        for key, value in metadata.items():
            header += f"{key}: {json.dumps(value)}\n"
        header += "---\n\n"
        return header + content.strip() + "\n"

    def _load_index(self) -> Dict[str, List[str]]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _update_index(self, path: Path, content: str) -> None:
        index = self._load_index()
        tokens = self._tokenize(content)
        rel_path = str(path.relative_to(self.base_dir))
        for token in tokens:
            items = index.setdefault(token, [])
            if rel_path not in items:
                items.append(rel_path)
        self.index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    def _tokenize(self, text: str) -> Iterable[str]:
        return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token]
