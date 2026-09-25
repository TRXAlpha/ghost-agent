"""Secret metadata and environment-backed resolution.

Ghost deliberately never persists secret values in `.ghost`. Integrations
declare an environment-variable reference, resolved only by an approved adapter.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SecretReference:
    name: str
    environment_variable: str
    purpose: str
    required: bool = True
    created_at: float = field(default_factory=time.time)


class SecretStore:
    """Persist only validated secret *references*, never values."""

    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "secrets.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, references: list[dict]) -> None:
        self.path.write_text(json.dumps(references, indent=2), encoding="utf-8")

    def register(self, name: str, environment_variable: str, purpose: str, required: bool = True) -> SecretReference:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", environment_variable):
            raise ValueError("Secret environment variable must be uppercase letters, digits, and underscores")
        if not name.strip() or not purpose.strip():
            raise ValueError("Secret name and purpose are required")
        reference = SecretReference(name.strip(), environment_variable, purpose.strip(), required)
        data = [item for item in self._load() if item.get("name") != reference.name]
        data.append(asdict(reference))
        self._save(data)
        return reference

    def get(self, name: str) -> Optional[SecretReference]:
        return next((SecretReference(**item) for item in self._load() if item.get("name") == name), None)

    def resolve(self, name: str) -> Optional[str]:
        reference = self.get(name)
        if not reference:
            raise ValueError(f"Secret reference is not registered: {name}")
        value = os.getenv(reference.environment_variable)
        if reference.required and not value:
            raise RuntimeError(f"Required secret is unavailable: {reference.environment_variable}")
        return value
