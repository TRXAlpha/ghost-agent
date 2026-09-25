"""One enforced authority seam between Ghost grants and upstream tool dispatch.

Ghost owns grants, scope, and revocation; OpenJarvis only executes what this
module admits. The default is deny: a capability is never granted implicitly,
and the set of capabilities that CAN be provisioned is a fixed allowlist of
already-permitted read-only tools. We never mint a grant for a consequential
upstream tool (shell, browser, channel-send, scheduler, ...) even if some code
path requests it.

Corrupt authority state fails closed: it is never treated as an empty
permissive grant set.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Tuple

from ..utils.storage import atomic_json, exclusive_lock

# Capacities that may be provisioned as durable grants. Everything else is
# refused at the store boundary. This mirrors the bridge's read-only posture:
# the assistant can search the owner's explicitly imported project documents,
# and nothing consequential is ever enabled through a grant.
KNOWN_SAFE_CAPABILITIES: frozenset[str] = frozenset({"lesson_search"})

# Exact parameter shapes each provisioned capability accepts at dispatch.
_CAPABILITY_PARAMS: dict[str, frozenset[str]] = {
    "lesson_search": frozenset({"query"}),
}


class AuthorityStateError(RuntimeError):
    """Persisted authority state is corrupt or unsafe; never trust it."""


@dataclass
class Grant:
    grant_id: str
    principal: str
    capability: str
    scope: str  # absolute project root the grant is scoped to
    reason: str
    status: str = "active"  # active | revoked
    expires_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def _scope_for(root: Path) -> str:
    return str(Path(root).resolve())


class GrantStore:
    """Durable, project-scoped, revocable grants under the owner's state root.

    One write authority: the Ghost bridge. No competing approval database.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        state = self.root / ".ghost" / "openjarvis"
        self.path = state / "grants.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- persistence ------------------------------------------------------
    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AuthorityStateError("Grant state is unreadable; refusing to trust it.") from exc
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise AuthorityStateError("Grant state must be a list of records; refusing to trust it.")
        try:
            grants = [Grant(**{k: item[k] for k in ("grant_id", "principal", "capability",
                                                    "scope", "reason", "status")} | {
                        "expires_at": item.get("expires_at"),
                        "created_at": float(item["created_at"]),
                        "updated_at": float(item["updated_at"]),
                    })
                for item in items]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise AuthorityStateError("Grant state is malformed; refusing to trust it.") from exc
        seen: set[str] = set()
        for grant in grants:
            if not isinstance(grant.grant_id, str) or grant.grant_id in seen:
                raise AuthorityStateError("Grant state has duplicate or invalid ids.")
            if grant.status not in {"active", "revoked"}:
                raise AuthorityStateError("Grant state has an unknown status.")
            if grant.capability not in KNOWN_SAFE_CAPABILITIES:
                raise AuthorityStateError("Grant state references a capability that must never run.")
            if grant.scope != _scope_for(self.root):
                raise AuthorityStateError("Grant state is scoped to another project.")
            seen.add(grant.grant_id)
        return [asdict(g) for g in grants]

    def _save(self, items: list[dict[str, Any]]) -> None:
        atomic_json(self.path, items)

    # -- operations -------------------------------------------------------
    def ensure(self, principal: str, capability: str, scope: str, reason: str,
               ttl: Optional[float] = None) -> Grant:
        """Idempotently obtain an active grant for (principal, capability, scope).

        Raises if the capability is not in the fixed safe allowlist, so no
        code path can mint authority for a consequential upstream tool.
        """
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("Grant principal must be a nonempty owner identity.")
        if capability not in KNOWN_SAFE_CAPABILITIES:
            raise ValueError(f"Capability {capability!r} is not provisionable by Ghost.")
        scope = scope or _scope_for(self.root)
        if _scope_for(self.root) != scope:
            raise ValueError("Grant scope must match the active project root.")
        with exclusive_lock(self.path.with_suffix(".lock")):
            items = self._load()
            for item in items:
                if (item["status"] == "active"
                        and item["principal"] == principal
                        and item["capability"] == capability
                        and item["scope"] == scope):
                    if item.get("expires_at") is None or item["expires_at"] > time.time():
                        return Grant(**item)
            expiry = None if ttl is None else time.time() + ttl
            digest = hashlib.sha1(f"{principal}|{capability}|{scope}|{time.time_ns()}".encode()).hexdigest()[:12]
            grant = Grant(f"grant_{digest}", principal, capability, scope, reason,
                          expires_at=expiry)
            items.append(asdict(grant))
            self._save(items)
            return grant

    def get(self, grant_id: str) -> Optional[Grant]:
        for item in self._load():
            if item["grant_id"] == grant_id:
                return Grant(**item)
        return None

    def all(self) -> list[Grant]:
        return [Grant(**item) for item in self._load()]

    def active(self, principal: str, capability: str, scope: str,
               now: Optional[float] = None) -> Optional[Grant]:
        current = time.time() if now is None else now
        for item in self._load():
            if (item["status"] == "active" and item["principal"] == principal
                    and item["capability"] == capability and item["scope"] == scope
                    and (item["expires_at"] is None or item["expires_at"] > current)):
                return Grant(**item)
        return None

    def revoke(self, grant_id: str) -> Grant:
        with exclusive_lock(self.path.with_suffix(".lock")):
            items = self._load()
            for item in items:
                if item["grant_id"] == grant_id:
                    if item["status"] == "revoked":
                        raise ValueError("Grant is already revoked.")
                    item["status"] = "revoked"
                    item["updated_at"] = time.time()
                    self._save(items)
                    return Grant(**item)
            raise ValueError("Grant not found.")


def authorize_call(root: Path, principal: str, capability: str, params: dict,
                   now: Optional[float] = None) -> Tuple[bool, str]:
    """Deterministic authority for one upstream tool dispatch. Fail closed."""
    from ..runtime import EmergencyStop

    scope = _scope_for(root)
    try:
        store = GrantStore(root)
        grant = store.active(principal, capability, scope, now=now)
        if grant is None:
            return False, "No active grant for this tool and project scope."
    except AuthorityStateError as exc:
        return False, f"{exc} Tool denied."
    except (OSError, ValueError):
        return False, "Authority state unavailable; tool denied."
    if EmergencyStop(root).active():
        return False, "Tool denied: emergency stop is engaged."
    if not isinstance(params, dict):
        return False, "Tool denied: parameters are not an object."
    expected = _CAPABILITY_PARAMS.get(capability)
    if expected is None:
        return False, "Tool denied: capability has no defined parameter shape."
    if set(params) != set(expected):
        return False, "Tool denied: unexpected parameters for this capability."
    return True, "authorized"


def provision_lesson_search(root: Path, principal: str, reason: str,
                            *, now: Optional[float] = None) -> Grant:
    """Enable the read-only lesson search for this project under one grant."""
    store = GrantStore(root)
    scope = _scope_for(root)
    return store.ensure(principal, "lesson_search", scope, reason)
