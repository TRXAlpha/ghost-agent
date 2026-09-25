"""Selected inference path shared by coding, research, and protocol benchmarks."""
from __future__ import annotations

import os

from .ollama import ollama_chat as legacy_chat


def route_identity() -> dict:
    """Persist routing intent without credentials; workers cannot silently switch it."""
    backend = os.environ.get("GHOST_INFERENCE_BACKEND", "legacy")
    if backend == "legacy":
        return {"backend": "legacy"}
    if backend != "openjarvis":
        raise ValueError(f"Unknown GHOST_INFERENCE_BACKEND: {backend}")
    from ..openjarvis_bridge.runtime import EngineSettings
    settings = EngineSettings.from_env()
    return {"backend": backend, "engine": settings.engine, "endpoint": settings.endpoint,
            "allow_remote": settings.allow_remote}


def chat(*args, **kwargs):
    backend = os.environ.get("GHOST_INFERENCE_BACKEND", "legacy")
    if backend == "legacy":
        return legacy_chat(*args, **kwargs)
    if backend == "openjarvis":
        from ..openjarvis_bridge.runtime import chat as upstream_chat
        return upstream_chat(*args, **kwargs)
    raise ValueError(f"Unknown GHOST_INFERENCE_BACKEND: {backend}")
