"""Model profiles: recorded limits, modalities and cost with explicit unknowns.

Unknown values are None, never guessed. In particular an unknown cost is not
free: ``ModelProfile.estimated_cost`` returns None when a needed price is None.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..utils.storage import atomic_json

def _post(url: str, payload: dict, timeout: float):
    import requests

    return requests.post(url, json=payload, timeout=timeout)


@dataclass
class ModelProfile:
    model: str
    provider: str = "ollama"
    context_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_tools: Optional[bool] = None
    modalities: list[str] = field(default_factory=lambda: ["text"])
    cost_per_1k_input: Optional[float] = None   # None = unknown, NOT free
    cost_per_1k_output: Optional[float] = None
    source: str = "owner"                        # owner | probe
    updated_at: float = field(default_factory=time.time)

    def estimated_cost(
        self, input_tokens: int, output_tokens: int
    ) -> Optional[float]:
        """Cost in currency units, or None if any needed price is unknown."""
        if self.cost_per_1k_input is None or self.cost_per_1k_output is None:
            return None
        return (
            input_tokens / 1000.0 * self.cost_per_1k_input
            + output_tokens / 1000.0 * self.cost_per_1k_output
        )


class ModelProfileStore:
    """Durable model profiles in ``.ghost/model_profiles.json``."""

    def __init__(self, project_root: Path) -> None:
        self.path = Path(project_root) / ".ghost" / "model_profiles.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        import json

        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data.get("profiles", {})

    def _save(self, profiles: dict[str, dict[str, Any]]) -> None:
        atomic_json(self.path, {"profiles": profiles})

    def get(self, model: str) -> ModelProfile:
        """Unknown model -> a profile with all unknown (None) values."""
        record = self._load().get(model)
        if record is None:
            return ModelProfile(model=model)
        return ModelProfile(**{k: v for k, v in record.items()
                               if k in ModelProfile.__dataclass_fields__})

    def set(self, profile: ModelProfile) -> None:
        profiles = self._load()
        profiles[profile.model] = asdict(profile)
        self._save(profiles)

    def probe_ollama(self, model: str, base_url: str) -> ModelProfile:
        """Ask Ollama /api/show for context length; never guesses missing values."""
        url = base_url.rstrip("/") + "/api/show"
        response = _post(url, {"model": model}, 10)
        response.raise_for_status()
        info = response.json().get("model_info", {})
        context_tokens: Optional[int] = None
        for key, value in info.items():
            if key.endswith("context_length"):
                context_tokens = int(value)
                break
        profile = ModelProfile(
            model=model,
            context_tokens=context_tokens,
            source="probe",
        )
        self.set(profile)
        return profile
