"""Persist the separation between user request, inferred intent, plan, and authorization."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class IntentRecord:
    intent_id: str
    user_request: str
    inferred_outcome: str
    goal_id: str = ""
    plan: list[str] = field(default_factory=list)
    authorized_actions: list[str] = field(default_factory=list)
    clarification_question: str = ""
    status: str = "captured"
    created_at: float = field(default_factory=time.time)


class IntentStore:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "intents.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, records: list[dict]) -> None:
        self.path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    def capture(self, user_request: str, inferred_outcome: Optional[str] = None,
                goal_id: str = "", clarification_question: str = "") -> IntentRecord:
        request = user_request.strip()
        if not request:
            raise ValueError("User request cannot be empty")
        digest = hashlib.sha1(f"{request}|{time.time_ns()}".encode()).hexdigest()[:10]
        record = IntentRecord(f"intent_{digest}", request, (inferred_outcome or request).strip(),
                              goal_id, clarification_question=clarification_question,
                              status="needs_clarification" if clarification_question else "captured")
        records = self._load()
        records.append(asdict(record))
        self._save(records)
        return record

    def set_plan(self, intent_id: str, steps: list[str]) -> IntentRecord:
        records = self._load()
        for record in records:
            if record.get("intent_id") == intent_id:
                record["plan"] = [step.strip() for step in steps if step.strip()]
                record["status"] = "planned"
                self._save(records)
                return IntentRecord(**record)
        raise ValueError("Intent not found")
