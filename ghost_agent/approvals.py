"""Persistent, explicit approvals for consequential capability proposals."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .utils.storage import atomic_json, exclusive_lock


@dataclass
class ApprovalRequest:
    approval_id: str
    capability: str
    reason: str
    scope: Dict[str, Any]
    risk: str
    rollback: str
    status: str = "pending"
    decision_note: str = ""
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None


class ApprovalStore:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "approvals.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict[str, Any]]:
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("Approval state must be a list of records")
            seen = set()
            for item in items:
                request = ApprovalRequest(**item)
                if (not isinstance(request.approval_id, str) or request.approval_id in seen
                        or not isinstance(request.scope, dict)
                        or request.status not in {"pending", "approved", "rejected"}):
                    raise ValueError("Invalid approval record")
                seen.add(request.approval_id)
            return items
        except FileNotFoundError:
            return []
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Approval state is unreadable or corrupt; refusing to overwrite it") from exc

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.path, items)

    def request(self, capability: str, reason: str, scope: Dict[str, Any], risk: str,
                rollback: str) -> ApprovalRequest:
        digest = hashlib.sha1(f"{capability}|{reason}|{time.time_ns()}".encode()).hexdigest()[:10]
        request = ApprovalRequest(f"approval_{digest}", capability, reason, scope, risk, rollback)
        with exclusive_lock(self.path.with_suffix(".lock")):
            data = self._load()
            data.append(asdict(request))
            self._save(data)
        return request

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        return next((ApprovalRequest(**item) for item in self._load() if item.get("approval_id") == approval_id), None)

    def pending(self) -> list[ApprovalRequest]:
        return [ApprovalRequest(**item) for item in self._load() if item.get("status") == "pending"]

    def decide(self, approval_id: str, approved: bool, note: str = "") -> ApprovalRequest:
        with exclusive_lock(self.path.with_suffix(".lock")):
            return self._decide_locked(approval_id, approved, note)

    def _decide_locked(self, approval_id: str, approved: bool, note: str) -> ApprovalRequest:
        data = self._load()
        for item in data:
            if item.get("approval_id") != approval_id:
                continue
            if item.get("status") != "pending":
                raise ValueError("Approval has already been decided")
            item["status"] = "approved" if approved else "rejected"
            item["decision_note"] = note
            item["decided_at"] = time.time()
            self._save(data)
            return ApprovalRequest(**item)
        raise ValueError("Approval not found")
