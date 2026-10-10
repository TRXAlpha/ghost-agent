"""Reviewed, sandboxed extension lifecycle; no extension is auto-enabled."""
from __future__ import annotations

import json
import re
import time
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .secrets import SecretStore
from .approvals import ApprovalStore
from .runtime import EmergencyStop
from .security import SecurityMonitor
from .utils.storage import atomic_json, exclusive_lock


@dataclass
class ExtensionManifest:
    extension_id: str
    name: str
    goal: str
    status: str = "proposed"
    permissions: Dict[str, Any] = field(default_factory=lambda: {
        "filesystem": [], "network": False, "secrets": [], "processes": [], "browser": False,
    })
    side_effects: list[str] = field(default_factory=list)
    rollback_supported: bool = True
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    enable_approval_id: str = ""


class ExtensionManager:
    STATES = {
        "proposed": {"scaffolded", "retired"},
        "scaffolded": {"tested", "retired"},
        "tested": {"approved", "retired"},
        "approved": {"enabled", "retired"},
        "enabled": {"disabled"},
        "disabled": {"enabled", "retired"},
        "retired": set(),
    }

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root).absolute() / ".ghost" / "extensions"
        if self.root.resolve() != self.root:
            raise ValueError("Extension storage must not follow links")
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        if not safe:
            raise ValueError("Extension name must contain letters or digits")
        return safe

    def directory(self, name: str) -> Path:
        directory = self.root / self._safe_name(name)
        if directory.resolve() != directory:
            raise ValueError("Extension directory must not follow links")
        return directory

    def propose(self, name: str, goal: str) -> ExtensionManifest:
        with exclusive_lock(self.root / ".lock"):
            return self._propose(name, goal)

    def _propose(self, name: str, goal: str) -> ExtensionManifest:
        safe = self._safe_name(name)
        existing = self.load(safe)
        if existing:
            return existing
        manifest = ExtensionManifest(f"ext_{safe}", safe, goal.strip())
        self._save(manifest)
        return manifest

    def load(self, name: str) -> Optional[ExtensionManifest]:
        path = self.directory(name) / "manifest.json"
        try:
            if path.resolve() != path:
                raise ValueError("Extension manifest must not follow links")
            manifest = ExtensionManifest(**json.loads(path.read_text(encoding="utf-8")))
            if manifest.name != self._safe_name(name) or manifest.extension_id != f"ext_{manifest.name}":
                raise ValueError("Extension identity does not match its directory")
            return manifest
        except FileNotFoundError:
            return None
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Extension manifest is corrupt; preserve it for review") from exc

    def scaffold(self, name: str, goal: str = "") -> ExtensionManifest:
        with exclusive_lock(self.root / ".lock"):
            return self._scaffold(name, goal)

    def _scaffold(self, name: str, goal: str) -> ExtensionManifest:
        manifest = self.load(name) or self._propose(name, goal)
        if manifest.status != "proposed":
            return manifest  # Never overwrite a user's implementation on retry.
        directory = self.directory(name)
        if any(path.name != "manifest.json" for path in directory.iterdir()):
            raise ValueError("Incomplete or user-created package contents require review; refusing to overwrite them")
        (directory / "adapter").mkdir(parents=True, exist_ok=True)
        (directory / "tests").mkdir(parents=True, exist_ok=True)
        (directory / "health").mkdir(parents=True, exist_ok=True)
        (directory / "docs").mkdir(parents=True, exist_ok=True)
        (directory / "rollback").mkdir(parents=True, exist_ok=True)
        (directory / "permissions.json").write_text(json.dumps(manifest.permissions, indent=2), encoding="utf-8")
        (directory / "manifest.yaml").write_text(
            f"id: {manifest.extension_id}\nname: {manifest.name}\nstatus: {manifest.status}\ngoal: {manifest.goal}\n",
            encoding="utf-8",
        )
        (directory / "permissions.yaml").write_text(
            "# Review and approve these scopes before enabling.\n"
            f"filesystem: {json.dumps(manifest.permissions['filesystem'])}\n"
            f"network: {str(manifest.permissions['network']).lower()}\n"
            f"secrets: {json.dumps(manifest.permissions['secrets'])}\n"
            f"processes: {json.dumps(manifest.permissions['processes'])}\n"
            f"browser: {str(manifest.permissions['browser']).lower()}\n",
            encoding="utf-8",
        )
        (directory / "signature").write_text(
            "unsigned: local scaffold; verify an approved package signature before distribution.\n",
            encoding="utf-8",
        )
        (directory / "adapter" / "__init__.py").write_text(
            '"""Sandboxed extension adapter. Implement capability calls here."""\n', encoding="utf-8"
        )
        (directory / "tests" / "test_adapter.py").write_text(
            "def test_extension_scaffold_exists():\n    assert True\n", encoding="utf-8"
        )
        (directory / "health" / "README.txt").write_text("Define deterministic health checks here.\n", encoding="utf-8")
        (directory / "rollback" / "README.txt").write_text("Define rollback steps before enabling.\n", encoding="utf-8")
        (directory / "docs" / "README.txt").write_text(
            "Document setup, permissions, secrets, side effects, and approval steps.\n", encoding="utf-8"
        )
        return self._transition(manifest, "scaffolded")

    def declare_secret(self, name: str, secret_name: str, environment_variable: str, purpose: str) -> ExtensionManifest:
        """Declare an extension secret reference without accepting its value."""
        with exclusive_lock(self.root / ".lock"):
            return self._declare_secret(name, secret_name, environment_variable, purpose)

    def _declare_secret(self, name: str, secret_name: str, environment_variable: str, purpose: str) -> ExtensionManifest:
        manifest = self.load(name)
        if not manifest:
            raise ValueError("Extension not found")
        if manifest.status not in {"proposed", "scaffolded", "tested", "disabled"}:
            raise PermissionError("Disable the extension before changing secret references")
        SecretStore(self.root.parents[1]).register(secret_name, environment_variable, purpose)
        if secret_name not in manifest.permissions["secrets"]:
            manifest.permissions["secrets"].append(secret_name)
            manifest.updated_at = time.time()
            self._save(manifest)
        return manifest

    def mark_tested(self, name: str) -> ExtensionManifest:
        with exclusive_lock(self.root / ".lock"):
            return self._mark_tested(name)

    def _mark_tested(self, name: str) -> ExtensionManifest:
        manifest = self.load(name)
        if not manifest:
            raise ValueError("Extension not found")
        if manifest.status == "tampered":
            raise ValueError("Extension is tampered; re-test and re-approve required")
        if manifest.status != "scaffolded":
            raise ValueError("Only scaffolded extensions can be marked tested")
        return self._transition(manifest, "tested")

    def request_enable(self, name: str) -> str:
        with exclusive_lock(self.root / ".lock"):
            return self._request_enable(name)

    def _request_enable(self, name: str) -> str:
        manifest = self.load(name)
        if not manifest or manifest.status == "tampered":
            raise ValueError("Extension is tampered; re-test and re-approve required"
                             if manifest and manifest.status == "tampered"
                             else "Only tested or disabled extensions can request enablement")
        approval = ApprovalStore(self.root.parents[1]).request(
            "extension.enable", f"Enable extension {manifest.name}",
            self._approval_scope(manifest), "high",
            "Disable the extension and follow its rollback instructions."
        )
        return approval.approval_id

    def enable(self, name: str, approval_id: str) -> ExtensionManifest:
        with exclusive_lock(self.root / ".lock"):
            return self._enable(name, approval_id)

    def _enable(self, name: str, approval_id: str) -> ExtensionManifest:
        manifest = self.load(name)
        approval = ApprovalStore(self.root.parents[1]).get(approval_id)
        if manifest and manifest.status == "tampered":
            raise ValueError("Extension is tampered; re-test and re-approve required")
        if not manifest or manifest.status not in {"tested", "disabled"}:
            raise ValueError("Only tested or disabled extensions can be enabled")
        if (not approval or approval.status != "approved" or approval.capability != "extension.enable"
                or approval.scope != self._approval_scope(manifest)):
            raise PermissionError("A matching approved extension.enable request is required")
        if EmergencyStop(self.root.parents[1]).active():
            raise PermissionError("Emergency stop is engaged")
        secrets = SecretStore(self.root.parents[1])
        missing_references = [name for name in manifest.permissions["secrets"] if not secrets.get(name)]
        if missing_references:
            raise PermissionError("Extension declares unregistered secret reference(s): " + ", ".join(missing_references))
        # Approval already exists in the approval store. Persist the final state
        # and its approval ID once, with no stranded intermediate "approved" state.
        manifest.status = "enabled"
        manifest.enable_approval_id = approval_id
        manifest.updated_at = time.time()
        self._save(manifest)
        return manifest

    def _approval_scope(self, manifest: ExtensionManifest) -> dict[str, Any]:
        directory = self.directory(manifest.name)
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.resolve() != path.absolute():
                raise ValueError("Extension package must not contain links")
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"extension": manifest.name, "permissions": manifest.permissions,
                "side_effects": manifest.side_effects, "files": files}

    def verify_integrity(self) -> list[dict[str, Any]]:
        """Recompute package hashes for approved/enabled extensions; disable tampered ones (R08)."""
        tampered: list[dict[str, Any]] = []
        approvals = ApprovalStore(self.root.parents[1])
        monitor = SecurityMonitor(self.root.parents[1])
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            manifest = self.load(directory.name)
            if manifest is None or manifest.status not in {"approved", "enabled"}:
                continue
            approval = approvals.get(manifest.enable_approval_id) if manifest.enable_approval_id else None
            current = self._approval_scope(manifest)
            # manifest.json is Ghost's own state and changes on enable; only the
            # extension payload is compared against the approval scope.
            stored_files = {k: v for k, v in (approval.scope.get("files", {}).items()
                                              if approval else []) if k != "manifest.json"}
            if stored_files != {k: v for k, v in current["files"].items() if k != "manifest.json"}:
                manifest.status = "tampered"  # Not in STATES; re-enable requires re-test and re-approval.
                manifest.updated_at = time.time()
                self._save(manifest)
                monitor.record_event("extension.tampered", {"extension": manifest.name,
                                                            "approval_id": manifest.enable_approval_id})
                tampered.append({"name": manifest.name, "status": "tampered"})
        return tampered

    def transition(self, manifest: ExtensionManifest, target: str) -> ExtensionManifest:
        if target in {"approved", "enabled"}:
            raise PermissionError("Use approval-gated enablement, not a direct state transition")
        with exclusive_lock(self.root / ".lock"):
            return self._transition(manifest, target)

    def _transition(self, manifest: ExtensionManifest, target: str) -> ExtensionManifest:
        persisted = self.load(manifest.name)
        if not persisted or asdict(persisted) != asdict(manifest):
            raise ValueError("Extension state changed; reload it before transitioning")
        if target not in self.STATES.get(manifest.status, set()):
            raise ValueError(f"Invalid extension transition: {manifest.status} -> {target}")
        manifest.status = target
        manifest.updated_at = time.time()
        self._save(manifest)
        return manifest

    def _save(self, manifest: ExtensionManifest) -> None:
        directory = self.directory(manifest.name)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "manifest.json", asdict(manifest))
