"""Local security observations: integrity, secret-pattern warnings, and action anomalies."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable


SECRET_PATTERNS = {
    "telegram_token": re.compile(r"(?<!\d)\d{7,12}:[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "generic_private_key": re.compile(
        r"-----BEGIN (?P<kind>[A-Z ]*PRIVATE KEY)-----[\s\S]*?(?:-----END (?P=kind)-----|$)"
    ),
}


def redact_secrets(value: Any) -> Any:
    """Best-effort pattern redaction for persisted data, without mutating it."""
    if isinstance(value, str):
        for name, pattern in SECRET_PATTERNS.items():
            value = pattern.sub(f"[REDACTED:{name}]", value)
        return value
    if isinstance(value, dict):
        return {redact_secrets(key): redact_secrets(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    return value


class SecurityMonitor:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.path = self.root / ".ghost" / "security.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / ".ghost" / "security-events.jsonl"

    def scan_text(self, text: str) -> list[str]:
        return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]

    def snapshot(self, paths: Iterable[Path]) -> Dict[str, str]:
        hashes: Dict[str, str] = {}
        for path in paths:
            candidate = Path(path).resolve()
            if not candidate.is_file() or not candidate.is_relative_to(self.root):
                continue
            hashes[str(candidate.relative_to(self.root))] = hashlib.sha256(candidate.read_bytes()).hexdigest()
        self.path.write_text(json.dumps({"at": time.time(), "hashes": hashes}, indent=2), encoding="utf-8")
        return hashes

    def sandbox_escape(self, target: Path, sandbox_root: Path) -> bool:
        try:
            return not Path(target).resolve().is_relative_to(Path(sandbox_root).resolve())
        except OSError:
            return True

    def record_event(self, kind: str, detail: Dict[str, str]) -> None:
        """Append a minimal audit event without retaining secrets or tool output."""
        safe_detail = {redact_secrets(str(key)): redact_secrets(str(value))[:500]
                       for key, value in detail.items()}
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, "detail": safe_detail, "at": time.time()}) + "\n")

    def recent_events(self, limit: int = 50) -> list[dict]:
        if not self.events_path.exists():
            return []
        events: list[dict] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events[-limit:]
