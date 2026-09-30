"""NEXUS: Central AI-OS kernel orchestrating all subsystems.

NEXUS is the primary entry point for the Ghost AI-OS. It coordinates:
- Proactive opportunity generation
- Learning and learner progress tracking
- Memory and context management
- Execution and resource management
- Health monitoring and system state

This module owns orchestration logic only. Actual implementations live in
their respective subsystems (learner, context_manager, resource_manager, etc.).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from .utils.storage import atomic_json, exclusive_lock


# ============================================================================
# Health and System State
# ============================================================================

class HealthStatus:
    """Possible health states for NEXUS and subsystems."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass
class SubsystemHealth:
    """Health snapshot for a single subsystem."""
    name: str
    status: str = HealthStatus.HEALTHY
    message: str = ""
    last_check: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NexusHealth:
    """Overall system health snapshot."""
    status: str = HealthStatus.HEALTHY
    subsystems: dict[str, dict[str, Any]] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# System State
# ============================================================================

class NexusState:
    """Mutable in-memory system state with atomic persistence."""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass
class NexusConfig:
    """Configuration for NEXUS orchestration."""
    health_check_interval: float = 60.0  # seconds
    max_degraded_subsystems: int = 2
    pause_on_degraded: bool = True
    auto_recover: bool = True
    state_dir: Optional[Path] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# Subsystem Protocol
# ============================================================================

class Subsystem(Protocol):
    """Protocol for NEXUS-managed subsystems."""
    _is_runtime_protocol = True

    def health_check(self) -> SubsystemHealth:
        """Return current health of this subsystem."""
        ...
    
    def initialize(self) -> None:
        """Initialize the subsystem."""
        ...
    
    def shutdown(self) -> None:
        """Graceful shutdown of the subsystem."""
        ...


# ============================================================================
# NEXUS: Central Orchestrator
# ============================================================================

class NexusError(ValueError):
    """NEXUS initialization, state, or coordination error."""


class Nexus:
    """Central AI-OS kernel orchestrating all Ghost subsystems.

    NEXUS maintains:
    - System state (initializing, running, paused, stopping, stopped)
    - Registered subsystems and their health
    - Routing between components
    - Configuration management

    Example:
        nexus = Nexus(state_dir=Path(".ghost"))
        nexus.register("learner", learner_store)
        nexus.register("context", context_manager)
        nexus.start()
        # ...
        nexus.shutdown()
    """

    def __init__(self, config: Optional[NexusConfig] = None,
                 state_dir: Optional[Path] = None) -> None:
        self.config = config or NexusConfig(state_dir=state_dir)
        self._state_dir = self._resolve_state_dir(state_dir)
        self._state_file = self._state_dir / "nexus_state.json"
        self._subsystems: dict[str, Subsystem] = {}
        self._state: str = NexusState.INITIALIZING
        self._health_cache: Optional[NexusHealth] = None
        self._last_health_check: float = 0.0
        self._load_state()

    def _resolve_state_dir(self, state_dir: Optional[Path]) -> Path:
        if state_dir:
            return Path(state_dir).resolve()
        return Path(".ghost")

    def _load_state(self) -> None:
        """Load persisted state from disk."""
        if not self._state_file.exists():
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._state = data.get("state", NexusState.INITIALIZING)
        except (OSError, ValueError, TypeError):
            pass  # Use default state on error

    def _save_state(self) -> None:
        """Persist current state to disk atomically."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(self._state_file, {
            "state": self._state,
            "subsystems": list(self._subsystems.keys()),
            "timestamp": time.time(),
        })

    # -- Subsystem management -----------------------------------------------

    def register(self, name: str, subsystem: Subsystem) -> None:
        """Register a subsystem with NEXUS.

        Args:
            name: Unique identifier for the subsystem
            subsystem: Subsystem implementing the Subsystem protocol

        Raises:
            NexusError: If subsystem is already registered or invalid
        """
        if name in self._subsystems:
            raise NexusError(f"Subsystem '{name}' is already registered")
        if not isinstance(subsystem, Subsystem):
            raise NexusError(f"Object does not implement Subsystem protocol")
        self._subsystems[name] = subsystem

        # Initialize if NEXUS is already running
        if self._state == NexusState.RUNNING:
            try:
                subsystem.initialize()
            except Exception as exc:
                raise NexusError(f"Failed to initialize subsystem '{name}'") from exc

    def unregister(self, name: str) -> None:
        """Remove a subsystem from NEXUS.

        Args:
            name: Name of the subsystem to remove
        """
        if name not in self._subsystems:
            return
        subsystem = self._subsystems.pop(name)
        if hasattr(subsystem, "shutdown"):
            try:
                subsystem.shutdown()
            except Exception:
                pass  # Best effort shutdown

    def get_subsystem(self, name: str) -> Optional[Subsystem]:
        """Retrieve a registered subsystem by name."""
        return self._subsystems.get(name)

    def list_subsystems(self) -> list[str]:
        """Return names of all registered subsystems."""
        return list(self._subsystems.keys())

    # -- Lifecycle ----------------------------------------------------------

    def initialize(self) -> None:
        """Initialize all registered subsystems."""
        self._state = NexusState.INITIALIZING
        failed: list[str] = []
        for name, subsystem in self._subsystems.items():
            try:
                subsystem.initialize()
            except Exception as exc:
                failed.append(f"{name}: {exc}")
        if failed:
            raise NexusError(f"Failed to initialize subsystems: {failed}")
        self._state = NexusState.RUNNING
        self._save_state()

    def start(self) -> None:
        """Alias for initialize() for clarity."""
        self.initialize()

    def pause(self) -> None:
        """Pause NEXUS and all subsystems."""
        if self._state == NexusState.STOPPED:
            raise NexusError("Cannot pause a stopped NEXUS")
        self._state = NexusState.PAUSED
        self._save_state()

    def resume(self) -> None:
        """Resume NEXUS from paused state."""
        if self._state != NexusState.PAUSED:
            raise NexusError("NEXUS is not paused")
        self._state = NexusState.RUNNING
        self._save_state()

    def shutdown(self) -> None:
        """Gracefully shutdown all subsystems and NEXUS."""
        if self._state == NexusState.STOPPED:
            return
        self._state = NexusState.STOPPING
        self._save_state()
        for name, subsystem in self._subsystems.items():
            try:
                if hasattr(subsystem, "shutdown"):
                    subsystem.shutdown()
            except Exception:
                pass  # Best effort
        self._state = NexusState.STOPPED
        self._save_state()

    @property
    def state(self) -> str:
        """Current NEXUS state."""
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == NexusState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self._state == NexusState.PAUSED

    # -- Health monitoring -------------------------------------------------

    def check_health(self, force: bool = False) -> NexusHealth:
        """Check health of all subsystems and return aggregate health.

        Args:
            force: If True, bypass cache and check all subsystems now

        Returns:
            NexusHealth with overall status and per-subsystem details
        """
        now = time.time()
        if not force and self._health_cache \
                and (now - self._last_health_check) < self.config.health_check_interval:
            return self._health_cache

        subsystem_health: dict[str, dict[str, Any]] = {}
        degraded_count = 0
        unhealthy_count = 0
        messages: list[str] = []

        for name, subsystem in self._subsystems.items():
            try:
                health = subsystem.health_check()
                subsystem_health[name] = health.to_dict()
                if health.status == HealthStatus.DEGRADED:
                    degraded_count += 1
                    if health.message:
                        messages.append(f"{name}: {health.message}")
                elif health.status == HealthStatus.UNAVAILABLE:
                    unhealthy_count += 1
                    messages.append(f"{name} unavailable: {health.message}")
            except Exception as exc:
                subsystem_health[name] = {
                    "name": name,
                    "status": HealthStatus.UNAVAILABLE,
                    "message": str(exc),
                    "last_check": now,
                }
                unhealthy_count += 1
                messages.append(f"{name} error: {exc}")

        # Determine overall status
        if unhealthy_count > 0:
            overall = HealthStatus.UNAVAILABLE
        elif degraded_count > self.config.max_degraded_subsystems:
            overall = HealthStatus.DEGRADED
            messages.insert(0, f"{degraded_count} subsystems degraded")
        elif degraded_count > 0:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        health = NexusHealth(
            status=overall,
            subsystems=subsystem_health,
            timestamp=now,
        )
        self._health_cache = health
        self._last_health_check = now

        # Auto-pause if configured and heavily degraded
        if self.config.pause_on_degraded \
                and self._state == NexusState.RUNNING \
                and unhealthy_count > self.config.max_degraded_subsystems:
            self.pause()

        return health

    def get_health_summary(self) -> dict[str, Any]:
        """Quick health status without full subsystem checks."""
        return {
            "state": self._state,
            "subsystem_count": len(self._subsystems),
            "cached_health": self._health_cache.to_dict() if self._health_cache else None,
            "last_check": self._last_health_check,
        }

    # -- Routing -----------------------------------------------------------

    def route(self, target: str, method: str, *args: Any, **kwargs: Any) -> Any:
        """Route a call to a specific subsystem.

        Args:
            target: Subsystem name
            method: Method name on the subsystem
            *args: Positional arguments for the method
            **kwargs: Keyword arguments for the method

        Returns:
            Result of the method call

        Raises:
            NexusError: If subsystem not found or method fails
        """
        subsystem = self._subsystems.get(target)
        if subsystem is None:
            raise NexusError(f"Unknown subsystem: {target}")
        if not hasattr(subsystem, method):
            raise NexusError(f"Subsystem '{target}' has no method '{method}'")
        try:
            return getattr(subsystem, method)(*args, **kwargs)
        except Exception as exc:
            raise NexusError(f"Subsystem '{target}.{method}' failed") from exc

    def broadcast(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Call a method on all subsystems that implement it.

        Returns:
            Dict mapping subsystem names to their results (or errors)
        """
        results: dict[str, Any] = {}
        for name, subsystem in self._subsystems.items():
            if hasattr(subsystem, method):
                try:
                    results[name] = getattr(subsystem, method)(*args, **kwargs)
                except Exception as exc:
                    results[name] = {"error": str(exc)}
        return results

    # -- Configuration -----------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        """Return current configuration."""
        return self.config.to_dict()

    def update_config(self, **kwargs: Any) -> dict[str, Any]:
        """Update configuration values.

        Only valid config fields are accepted.
        """
        valid_fields = {"health_check_interval", "max_degraded_subsystems",
                        "pause_on_degraded", "auto_recover"}
        for key, value in kwargs.items():
            if key in valid_fields:
                setattr(self.config, key, value)
        return self.get_config()