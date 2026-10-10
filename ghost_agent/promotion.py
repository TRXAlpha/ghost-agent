"""Reviewable sandbox-to-live promotion with per-file backups for undo."""
from __future__ import annotations

import hashlib
import json
import shutil
import re
import time
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore
from .kernel import ActionProposal, TransactionJournal
from .runtime import EmergencyStop
from .utils.storage import atomic_write, atomic_json, exclusive_lock
from .provenance import ProvenanceLedger

# S08/R13: owner-controlled update policy and observation window.
UPDATE_POLICY_DEFAULTS: dict[str, Any] = {
    "version": 1,
    "auto_activate": False,
    "observation_window_seconds": 86400,
    "protected_paths": [
        "ghost_agent/policy.py", "ghost_agent/approvals.py",
        "ghost_agent/self_repair.py", "ghost_agent/promotion.py",
        "ghost_agent/evaluation.py", "ghost_agent/security.py",
        "ghost_agent/kernel.py",
    ],
}


def load_update_policy(root: Path) -> dict[str, Any]:
    """Read the owner's update policy; code defaults apply when it is missing."""
    path = Path(root) / ".ghost" / "update_policy.json"
    if not path.exists():
        return json.loads(json.dumps(UPDATE_POLICY_DEFAULTS))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("update_policy.json must contain an object")
    except (OSError, ValueError) as exc:
        raise ValueError(f"update_policy.json is unreadable or malformed: {exc}") from exc
    policy = dict(UPDATE_POLICY_DEFAULTS)
    for key in ("auto_activate", "observation_window_seconds", "protected_paths"):
        if key in data:
            policy[key] = data[key]
    return policy


class PromotionManager:
    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.backups = self.root / ".ghost" / "promotions"
        if self.backups.resolve() != self.backups:
            raise ValueError("Promotion storage must not follow links")
        self.backups.mkdir(parents=True, exist_ok=True)

    def preview(self, sandbox: Path) -> list[str]:
        sandbox = Path(sandbox).resolve()
        changed: list[str] = []
        for source in sandbox.rglob("*"):
            relative = source.relative_to(sandbox)
            if any(part.lower() in {".ghost", ".git", "__pycache__", ".pytest_cache", ".venv"}
                   for part in relative.parts):
                continue
            if not source.is_file():
                continue
            self._file(sandbox, str(relative))
            self._file(self.root, str(relative))
            target = self.root / relative
            if not target.exists() or target.read_bytes() != source.read_bytes():
                changed.append(str(relative))
        return sorted(changed)

    @staticmethod
    def _file(root: Path, relative: str) -> Path:
        path = Path(relative)
        if (path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts)
                or ":" in relative or "\x00" in relative):
            raise ValueError("Invalid promotion file path")
        if any(part.lower() in {".ghost", ".git"} for part in path.parts):
            raise ValueError("Promotion cannot replace kernel or Git metadata")
        candidate = root / path
        if candidate.resolve() != candidate.absolute() or not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError("Promotion paths must not escape or follow links")
        if candidate.exists() and not candidate.is_file():
            raise ValueError("Promotion target must be a regular file")
        return candidate

    @staticmethod
    def _hash(path: Path) -> str | None:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def snapshot(self, sandbox: Path) -> dict[str, dict[str, str | None]]:
        sandbox = Path(sandbox).resolve()
        return {name: {"before": self._hash(self._file(self.root, name)),
                       "after": self._hash(self._file(sandbox, name))}
                for name in self.preview(sandbox)}

    def promote(self, sandbox: Path, files: list[str], approval_id: str) -> str:
        with exclusive_lock(self.backups / ".lock"):
            return self._promote_locked(sandbox, files, approval_id)

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.backups.glob("*/journal.json")):
            journal: dict[str, Any] = {}
            try:
                journal = json.loads(path.read_text(encoding="utf-8"))
                status = journal.get("status", "legacy_manual_review")
                if journal.get("version") != 2:
                    status = "legacy_manual_review"
                elif (status not in {"prepared", "rolling_back", "verified", "rolled_back"}
                      or not isinstance(journal.get("files"), list) or not journal.get("approval_id")):
                    status = "corrupt_manual_review"
            except (OSError, ValueError, AttributeError):
                status = "corrupt_manual_review"
            record = {"promotion_id": path.parent.name, "status": status}
            if status == "verified":
                # Computed on read (R13): observing until the window passes
                # with no rollback, then operationally_verified.
                observe_until = journal.get("observe_until")
                if isinstance(observe_until, (int, float)) and time.time() < observe_until:
                    record["observation"] = "observing"
                else:
                    record["observation"] = "operationally_verified"
            records.append(record)
        return records

    def _promote_locked(self, sandbox: Path, files: list[str], approval_id: str) -> str:
        if any(record["status"] not in {"verified", "rolled_back"} for record in self.list()):
            raise ValueError("An unfinished promotion requires recovery or manual review first; use ghost promote list")
        sandbox = Path(sandbox).resolve()
        approval = ApprovalStore(self.root).get(approval_id)
        if (not approval or approval.status != "approved" or approval.capability != "filesystem.promote"
                or approval.scope.get("sandbox") != str(sandbox)
                or approval.scope.get("files") != files):
            raise PermissionError("A matching persisted approval is required")
        snapshot = self.snapshot(sandbox)
        if not snapshot or files != list(snapshot) or snapshot != approval.scope.get("snapshot"):
            raise ValueError("Promotion content or destination changed since approval; request a new preview")
        if EmergencyStop(self.root).active():
            raise PermissionError("Emergency stop is engaged")
        if not re.fullmatch(r"approval_[A-Za-z0-9_]+", approval_id):
            raise ValueError("Invalid approval ID")
        stamp = f"promotion_{approval_id}"
        backup = self.backups / stamp
        backup.mkdir(parents=True, exist_ok=False)
        validated: list[tuple[str, Path, Path]] = []
        for relative in files:
            source = self._file(sandbox, relative)
            target = self._file(self.root, relative)
            if not source.is_file() or not source.is_relative_to(sandbox) or not target.is_relative_to(self.root):
                raise ValueError(f"Invalid promotion file: {relative}")
            validated.append((relative, source, target))
        applied: list[dict[str, Any]] = []
        candidates: dict[str, bytes] = {}
        for relative, source, target in validated:
            existed = target.exists()
            if existed:
                saved = backup / "files" / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
                if self._hash(saved) != snapshot[relative]["before"]:
                    raise ValueError("Live file changed while creating its backup")
            if self._hash(source) != snapshot[relative]["after"] or self._hash(target) != snapshot[relative]["before"]:
                raise ValueError("Promotion content changed during application")
            candidates[relative] = source.read_bytes()
            if hashlib.sha256(candidates[relative]).hexdigest() != snapshot[relative]["after"]:
                raise ValueError("Candidate changed during staging")
            applied.append({"file": relative, "existed": existed,
                            "sha256": snapshot[relative]["after"], "before_sha256": snapshot[relative]["before"]})
        journal = {"version": 2, "status": "prepared", "approval_id": approval_id, "files": applied}
        atomic_json(backup / "journal.json", journal)
        try:
            for item in applied:
                if EmergencyStop(self.root).active():
                    raise PermissionError("Emergency stop engaged during promotion")
                target = self._file(self.root, item["file"])
                if self._hash(target) != item["before_sha256"]:
                    raise ValueError("Live file changed during promotion")
                atomic_write(target, candidates[item["file"]])
                if self._hash(target) != item["sha256"]:
                    raise ValueError("Promoted file failed content verification")
            journal["status"] = "verified"
            journal["applied_at"] = time.time()
            journal["observe_until"] = journal["applied_at"] + load_update_policy(self.root)["observation_window_seconds"]
            atomic_json(backup / "journal.json", journal)
        except Exception:
            # Atomic target replacement leaves each file at a known before or
            # after hash. Process death instead leaves this journal for undo.
            self._rollback_locked(stamp)
            raise
        return stamp

    def rollback(self, promotion_id: str) -> None:
        with exclusive_lock(self.backups / ".lock"):
            self._rollback_locked(promotion_id)

    def _rollback_locked(self, promotion_id: str) -> None:
        if not re.fullmatch(r"promotion_[A-Za-z0-9_]+", promotion_id):
            raise ValueError("Invalid promotion ID")
        backup = self.backups / promotion_id
        if backup.resolve() != backup:
            raise ValueError("Rollback storage must not follow links")
        journal = json.loads((backup / "journal.json").read_text(encoding="utf-8"))
        if journal.get("version") != 2:
            raise ValueError("Legacy or untrusted journal requires manual review")
        approval = ApprovalStore(self.root).get(journal.get("approval_id", ""))
        expected = {item["file"]: {"before": item["before_sha256"], "after": item["sha256"]}
                    for item in journal["files"]}
        if (not approval or approval.capability != "filesystem.promote" or approval.status != "approved"
                or expected != approval.scope.get("snapshot")
                or len(expected) != len(journal["files"])
                or any(item["existed"] != (item["before_sha256"] is not None) for item in journal["files"])):
            raise PermissionError("Rollback journal does not match its approved content scope")
        # Validate every target and backup before changing any file.
        for item in journal["files"]:
            target = self._file(self.root, item["file"])
            saved = self._file(backup / "files", item["file"])
            if self._hash(target) not in {item["sha256"], item["before_sha256"]}:
                raise ValueError(f"Rollback conflict: {item['file']} has changed")
            if item["existed"] and self._hash(saved) != item["before_sha256"]:
                raise ValueError("Rollback backup integrity check failed")
        journal["status"] = "rolling_back"
        atomic_json(backup / "journal.json", journal)
        for item in journal["files"]:
            target = self._file(self.root, item["file"])
            saved = self._file(backup / "files", item["file"])
            if self._hash(target) == item["before_sha256"]:
                continue
            if self._hash(target) != item["sha256"]:
                raise ValueError("Rollback conflict: live file changed during recovery")
            if item["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                content = saved.read_bytes()
                if hashlib.sha256(content).hexdigest() != item["before_sha256"]:
                    raise ValueError("Rollback backup integrity check failed during recovery")
                atomic_write(target, content)
            else:
                target.unlink(missing_ok=True)
        journal["status"] = "rolled_back"
        atomic_json(backup / "journal.json", journal)


class PromotionService:
    """Bind live promotion to a matching approved transaction."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).resolve()
        self.promotions = PromotionManager(self.root)
        self.approvals = ApprovalStore(self.root)
        self.transactions = TransactionJournal(self.root)
        self.provenance = ProvenanceLedger(self.root)

    def request(self, goal_id: str, sandbox: Path, *,
                expected_snapshot: dict[str, dict[str, str | None]] | None = None) -> tuple[str, str, list[str]]:
        snapshot = self.promotions.snapshot(sandbox)
        if expected_snapshot is not None and snapshot != expected_snapshot:
            raise ValueError("Candidate or live files changed since verification; verify again")
        files = list(snapshot)
        if not files:
            raise ValueError("Sandbox has no promotable changes")
        proposal = ActionProposal("filesystem.promote", {"files": files}, target=str(self.root),
                                  reason="Review and promote these exact candidate bytes into the live project.")
        transaction = self.transactions.prepare(goal_id, proposal)
        approval = self.approvals.request(
            "filesystem.promote", proposal.reason, {"sandbox": str(Path(sandbox).resolve()), "files": files,
                                                     "snapshot": snapshot, "transaction_id": transaction.transaction_id},
            "high", "Use ghost promote rollback <promotion_id> to restore saved files."
        )
        self.transactions.transition(transaction, "previewed", {
            "files": files, "approval_id": approval.approval_id,
            "scope": approval.scope,
            "rollback": "Restore backups recorded by PromotionManager.",
        })
        self.provenance.record("promotion.requested", {
            "transaction_id": transaction.transaction_id, "approval_id": approval.approval_id,
            "snapshot": snapshot,
        })
        return transaction.transaction_id, approval.approval_id, files

    def commit(self, transaction_id: str, approval_id: str) -> str:
        with exclusive_lock(self.promotions.backups / ".lock"):
            return self._commit_locked(transaction_id, approval_id)

    def _commit_locked(self, transaction_id: str, approval_id: str) -> str:
        transaction = self.transactions.get(transaction_id)
        approval = self.approvals.get(approval_id)
        if not transaction or transaction.state != "previewed":
            raise ValueError("Promotion transaction must exist and be previewed")
        if not approval or approval.status != "approved" or approval.capability != "filesystem.promote":
            raise PermissionError("A matching approved filesystem.promote request is required")
        preview = transaction.preview
        if preview.get("approval_id") != approval_id:
            raise PermissionError("Approval does not match the promotion transaction")
        scope = approval.scope
        if scope != preview.get("scope"):
            raise PermissionError("Approved content scope does not match the transaction")
        files = list(scope.get("files", []))
        if files != list(preview.get("files", [])):
            raise PermissionError("Approved file scope does not match the preview")
        try:
            promotion_id = self.promotions._promote_locked(Path(scope["sandbox"]), files, approval_id)
        except Exception:
            if any(record == {"promotion_id": "promotion_" + approval_id, "status": "rolled_back"}
                   for record in self.promotions.list()):
                self.transactions.transition(transaction, "rolled_back", {"reason": "Application failed; backups restored"})
            raise
        self.transactions.transition(transaction, "committed", {"promotion_id": promotion_id})
        if any(self.promotions._hash(self.promotions._file(self.promotions.root, name)) != hashes["after"]
               for name, hashes in scope["snapshot"].items()):
            raise ValueError("Post-promotion content verification failed")
        self.transactions.transition(transaction, "verified", {
            "promotion_id": promotion_id, "check": "approved file SHA-256 hashes; not functional tests"})
        self.provenance.record("promotion.verified", {"transaction_id": transaction_id,
                                "promotion_id": promotion_id, "check": "approved file SHA-256 hashes"})
        return promotion_id

    def rollback(self, promotion_id: str) -> None:
        with exclusive_lock(self.promotions.backups / ".lock"):
            self.promotions._rollback_locked(promotion_id)
            journal = json.loads((self.promotions.backups / promotion_id / "journal.json").read_text(encoding="utf-8"))
            approval = self.approvals.get(journal["approval_id"])
            transaction = self.transactions.get(approval.scope["transaction_id"])
            if transaction and transaction.state != "rolled_back":
                self.transactions.transition(transaction, "rolled_back", {"promotion_id": promotion_id})
            self.provenance.record("promotion.rolled_back", {"promotion_id": promotion_id})
