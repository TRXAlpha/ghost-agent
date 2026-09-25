"""Persistent user identity, preferences, and explicit behavioral constraints."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass
class UserProfile:
    user_id: str = "primary"
    display_name: str = ""
    preferences: Dict[str, Any] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)
    prohibitions: list[str] = field(default_factory=list)


class IdentityStore:
    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "identity.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> UserProfile:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            profile = UserProfile(**raw)
            for rules in (profile.policies, profile.prohibitions):
                if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
                    raise ValueError("Policy rules must be lists of strings")
            return profile
        except FileNotFoundError:
            return UserProfile()
        except (ValueError, OSError, TypeError) as exc:
            raise ValueError("Identity policy is unreadable or invalid; refusing to assume no restrictions") from exc

    def save(self, profile: UserProfile) -> UserProfile:
        self.path.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
        return profile

    def add_prohibition(self, rule: str) -> UserProfile:
        profile = self.load()
        clean = rule.strip()
        if clean and clean not in profile.prohibitions:
            profile.prohibitions.append(clean)
        return self.save(profile)
