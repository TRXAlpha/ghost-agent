"""Append-only, machine-readable provenance for outcomes and consequential work."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .security import redact_secrets


@dataclass
class ProvenanceRecord:
    record_id: str
    kind: str
    payload: dict[str, Any]
    parent_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class ProvenanceLedger:
    """Store immutable event records and reconstruct a parent trace."""

    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "provenance.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, payload: dict[str, Any], parent_id: Optional[str] = None) -> ProvenanceRecord:
        payload = redact_secrets(payload)
        digest = hashlib.sha256(json.dumps([kind, payload, parent_id, time.time_ns()], sort_keys=True,
                                           default=str).encode("utf-8")).hexdigest()[:16]
        record = ProvenanceRecord(f"prov_{digest}", kind, payload, parent_id)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")
        return record

    def all(self) -> list[ProvenanceRecord]:
        if not self.path.exists():
            return []
        records: list[ProvenanceRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                records.append(ProvenanceRecord(**json.loads(line)))
            except (TypeError, json.JSONDecodeError):
                continue
        return records

    def trace(self, record_id: str) -> list[ProvenanceRecord]:
        by_id = {record.record_id: record for record in self.all()}
        trace: list[ProvenanceRecord] = []
        current = by_id.get(record_id)
        while current:
            trace.append(current)
            current = by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(trace))
