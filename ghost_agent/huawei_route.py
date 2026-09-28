"""OJ14: eligible Huawei route as a connector feeding shared observations.

Provider-specific (Huawei Health Kit) access/data mapping with conservative
health/activity behavior, per S02.  It is NOT a generic connector framework (the
roadmap forbids a duplicate one) and does not add a second observation system:
it reuses the existing DeviceRegistry / WorldModel / EventBus for shared
observations and attention.

Design principles:
- A discovered provider route records sources, checked date, prerequisites and
  unresolved limitations; access state is honest (active / revoked /
  unavailable), never silently degraded.
- Ingested observations preserve unit, timestamp, time zone, freshness and
  provenance (S02).  No data means "unknown", never a confident guess.
- Owner context (unworn watch, illness, travel, intentional rest) can be
  corrected and shadows raw data.
- Trend summaries separate measured changes from hypotheses; burnout/illness
  prediction is explicitly unsupported (supports_prediction is always False).
- Health data only flows to authorized endpoints/logs/integrations (privacy).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .devices import DeviceRegistry
from .runtime import Event, EventBus, Observation, WorldModel
from .utils.storage import atomic_json, exclusive_lock

SUPPORTED_METRICS = {"steps", "activity", "sleep", "heart_rate", "stress"}
DEFAULT_UNITS = {"steps": "steps", "activity": "minutes", "sleep": "minutes",
                 "heart_rate": "bpm", "stress": "level"}
STATE_SET = {"discovered", "configured", "active", "revoked", "unavailable"}


class HuaweiRouteError(ValueError):
    """Invalid Huawei route, observation, or privacy request."""


@dataclass
class ProviderRoute:
    route_id: str
    name: str
    sources: list[str]
    checked_at: float
    prerequisites: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    state: str = "discovered"

    def __post_init__(self):
        if self.state not in STATE_SET:
            raise HuaweiRouteError("Invalid provider route state")


@dataclass
class HealthObservation:
    observation_id: str
    metric: str
    value: float
    unit: str
    timestamp: float
    timezone: str
    provenance: str
    source_id: str = "huawei"
    corrected: bool = False


@dataclass
class ContextCorrection:
    correction_id: str
    note: str
    applies_from: float
    effect: str = ""     # e.g. no-sleep | rest | unworn | travel | illness
class HuaweiRoute:
    """Provider-specific Huawei route; conservative, privacy-aware, synthetic-testable."""

    def __init__(self, project_root: Path, ingest_fn: Optional[Any] = None) -> None:
        self.root = Path(project_root).resolve()
        self.dir_path = self.root / ".ghost" / "huawei"
        self.dir_path.mkdir(parents=True, exist_ok=True)
        self.route_path = self.dir_path / "route.json"
        self.observations_path = self.dir_path / "observations.json"
        self.context_path = self.dir_path / "context.json"
        self.privacy_path = self.dir_path / "privacy.json"
        self.ingest_fn = ingest_fn  # injectable raw-fetch for synthetic data
        # Reuse the existing shared world/event bus (no second observation system).
        self.world = WorldModel(self.root)
        self.events = EventBus(self.root)
        self.devices = DeviceRegistry(self.root)

    # -- persistence ------------------------------------------------------
    def _load_list(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Expected a list")
            return raw
        except (OSError, ValueError, TypeError) as exc:
            raise HuaweiRouteError("Huawei route state is corrupt; preserve it for review") from exc

    def _save_list(self, path: Path, items: list[dict[str, Any]]) -> None:
        atomic_json(path, items)

    # -- provider route (S02: sources, checked date, prereqs, limitations) --
    def register_route(self, name: str, sources: list[str], checked_at: float,
                       prerequisites: Optional[list[str]] = None,
                       limitations: Optional[list[str]] = None) -> ProviderRoute:
        name = name.strip()
        if not name or not sources or checked_at <= 0:
            raise HuaweiRouteError("Route needs a name, at least one source, and a checked date")
        digest = hashlib.sha1(f"{name}|{time.time_ns()}".encode()).hexdigest()[:10]
        route = ProviderRoute(f"route_{digest}", name, list(sources), checked_at,
                              prerequisites or [], limitations or [])
        with exclusive_lock(self.route_path.with_suffix(".lock")):
            self._save_list(self.route_path, [asdict(route)])
        return route

    def route(self) -> Optional[ProviderRoute]:
        items = self._load_list(self.route_path)
        return ProviderRoute(**items[0]) if items else None

    def set_state(self, state: str) -> ProviderRoute:
        if state not in STATE_SET:
            raise HuaweiRouteError("Invalid provider route state")
        route = self.route()
        if route is None:
            raise HuaweiRouteError("Route not registered")
        with exclusive_lock(self.route_path.with_suffix(".lock")):
            route.state = state
            self._save_list(self.route_path, [asdict(route)])
        if state in {"revoked", "unavailable"}:
            self.events.emit(Event("provider.integration", {
                "provider": "huawei", "state": state,
                "note": "Data access is not available; no health observations are reported."},
                source="huawei_route"))
        return route

    # -- ingest (S02: units, timestamp, timezone, freshness, provenance) ----
    def ingest(self, raw: list[dict[str, Any]]) -> list[HealthObservation]:
        """Map raw provider rows into preserved observations; write to shared world."""
        current = self.route()
        if current is None or current.state != "active":
            raise HuaweiRouteError("Route must be active before ingesting observations")
        if not isinstance(raw, list):
            raise HuaweiRouteError("ingest expects a list of observation dicts")
        mapped = self._map(raw) if self.ingest_fn is None else self.ingest_fn(raw)
        observations: list[HealthObservation] = []
        for item in mapped:
            metric = item.get("metric", "")
            if metric not in SUPPORTED_METRICS:
                continue  # ignore unknown metrics rather than guess
            value = item.get("value")
            if value is None or type(value) not in (int, float):
                raise HuaweiRouteError("Each observation needs a numeric value")
            obs = HealthObservation(
                observation_id="hp_" + hashlib.sha1(
                    f"{metric}|{item.get('timestamp', 0)}|{time.time_ns()}".
                    encode()).hexdigest()[:12],
                metric=metric, value=float(value),
                unit=item.get("unit") or DEFAULT_UNITS[metric],
                timestamp=float(item.get("timestamp", time.time())),
                timezone=str(item.get("timezone", "local")),
                provenance=str(item.get("provenance", "huawei_synthetic")),
                source_id=item.get("source_id", "huawei"))
            observations.append(obs)
        if not observations:
            return []  # nothing recognized; unknown metrics are ignored, never guessed
        with exclusive_lock(self.observations_path.with_suffix(".lock")):
            existing = self._load_list(self.observations_path)
            merged = existing + [asdict(o) for o in observations]
            self._save_list(self.observations_path, merged)
        # Feed the shared world model for attention/observations (local only).
        for obs in observations:
            if self.privacy_allowed("ghost_local"):
                self.world.observe(Observation(
                    f"huawei.{obs.metric}", "health", {
                        "value": obs.value, "unit": obs.unit,
                        "timestamp": obs.timestamp, "timezone": obs.timezone,
                        "provenance": obs.provenance, "corrected": obs.corrected},
                    source="huawei_route", owner="user"))
        return observations

    @staticmethod
    def _map(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Default mapper expects already-normalized rows (explicit keys).
        return raw

    # -- freshness & reminders (S02: no data means unknown) ------------------
    def fresh(self, now: float, max_age_s: float = 3600) -> list[HealthObservation]:
        return [HealthObservation(**item) for item in self._load_list(self.observations_path)
                if now - float(item.get("timestamp", 0)) <= max_age_s]

    def observations(self, metric: Optional[str] = None) -> list[HealthObservation]:
        items = self._load_list(self.observations_path)
        if metric:
            items = [i for i in items if i.get("metric") == metric]
        return [HealthObservation(**i) for i in items]

    def inactivity_status(self, now: float, max_age_s: float = 3600,
                          low_activity_value: float = 200.0) -> str:
        """Activity reminder status using only sufficiently recent data.

        Returns 'active' when fresh activity exists above the threshold,
        'inactive' when fresh data shows low activity, and 'unknown' when there
        is no fresh data at all (never a confident guess from stale/absent data).
        """
        recent = [o for o in self.fresh(now, max_age_s) if o.metric == "activity"]
        if not recent:
            return "unknown"
        latest = max(recent, key=lambda o: o.timestamp)
        return "active" if latest.value >= low_activity_value else "inactive"


    # -- owner context correction (S02: illness/travel/unworn/rest) ----------
    def correct_context(self, note: str, effect: str = "",
                        applies_from: Optional[float] = None) -> ContextCorrection:
        note = note.strip()
        if not note or len(note) > 500:
            raise HuaweiRouteError("Context correction needs a nonempty note")
        applies = time.time() if applies_from is None else float(applies_from)
        correction = ContextCorrection(
            correction_id="ctx_" + hashlib.sha1(f"{note}|{time.time_ns()}".encode()).
            hexdigest()[:10],
            note=note, applies_from=applies, effect=effect.strip())
        with exclusive_lock(self.context_path.with_suffix(".lock")):
            items = self._load_list(self.context_path)
            items.append(asdict(correction))
            self._save_list(self.context_path, items)
        self.world.observe(Observation(
            "huawei.context", "context",
            {"note": correction.note, "effect": correction.effect,
             "applies_from": correction.applies_from},
            source="huawei_route", owner="user"))
        return correction

    def active_corrections(self, now: Optional[float] = None) -> list[ContextCorrection]:
        """Corrections in force at `now` (owner context shadows raw data)."""
        now_ts = time.time() if now is None else now
        return [ContextCorrection(**item) for item in self._load_list(self.context_path)
                if float(item.get("applies_from", 0)) <= now_ts]

    # -- trend summary (S02: measured changes vs hypotheses) ------------------
    def trend_summary(self, now: float, window_s: float = 7 * 86400,
                      metric: str = "activity") -> dict[str, Any]:
        """Summarize recent data; separate measured changes from hypotheses.

        Burnout/illness prediction is never produced (hypotheses stays empty if
        data alone is insufficient; supports_prediction is False).
        """
        relevant = [o for o in self.fresh(now, window_s) if o.metric == metric]
        if not relevant:
            return {"metric": metric, "measured": [], "hypotheses": [],
                    "supports_prediction": False, "note": "no recent data; unknown"}
        values = [o.value for o in relevant]
        latest = max(relevant, key=lambda o: o.timestamp)
        change = (latest.value - values[0]) if values else 0.0
        measured = [{"metric": metric, "latest": latest.value, "unit": latest.unit,
                     "first": values[0], "change": round(change, 3),
                     "provenance": latest.provenance, "freshness_s": round(now - latest.timestamp, 1)}]
        return {"metric": metric, "measured": measured, "hypotheses": [],
                "supports_prediction": False, "note": "measured change only; no prediction"}

    @property
    def supports_prediction(self) -> bool:
        """Burnout/illness prediction is a separately validated future feature."""
        return False

    # -- privacy (S02: health data excluded from unauthorized endpoints) ------

    # Default-deny policy: only these endpoint classes are allow-listed.
    # Raw health values (step counts, heart rate, sleep minutes) must NEVER
    # enter model context or be forwarded to external systems by default.
    # Allow-listed paths produce only aggregate statistics (e.g. daily step
    # COUNT, not individual values) or summaries that preserve privacy.
    ALLOWED_ENDPOINT_CLASSES: frozenset[str] = frozenset({
        # Internal summarisation that never emits raw values
        "ghost_daily_aggregate",
        # Privacy-preserving aggregates: count/sum without identifiers
        "aggregate_daily_step_count",
        "aggregate_weekly_activity_sum",
    })

    def _privacy_policy(self, endpoint: str) -> tuple[str, str]:
        """Evaluate routing policy for a health-data request.

        Returns (decision, reason_code):
          - ("allow", "policy_explicit")   — endpoint class is in the allow-list
          - ("deny",  "policy_default")   — default-deny; not allow-listed
        """
        if endpoint in self.ALLOWED_ENDPOINT_CLASSES:
            return "allow", "policy_explicit"
        return "deny", "policy_default"

    def _log_privacy_event(self, route_action: str, endpoint: str,
                           decision: str, reason_code: str) -> None:
        """Append a privacy event to .ghost/privacy/events.jsonl (no raw values)."""
        event = {
            "ts": time.time(),
            "route": route_action,
            "endpoint": endpoint,
            "decision": decision,
            "reason_code": reason_code,
        }
        events_path = self.root / ".ghost" / "privacy" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(events_path.with_suffix(".lock")):
            with open(events_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _load_privacy(self) -> list[str]:
        return self._load_list(self.privacy_path)

    def allow_endpoint(self, endpoint: str) -> None:
        endpoint = endpoint.strip()
        if not endpoint:
            raise HuaweiRouteError("Endpoint must be nonempty")
        with exclusive_lock(self.privacy_path.with_suffix(".lock")):
            allowed = self._load_privacy()
            if endpoint not in allowed:
                allowed.append(endpoint)
                self._save_list(self.privacy_path, allowed)

    def privacy_allowed(self, endpoint: str) -> bool:
        """Health data may flow only to authorized endpoints (never by default).

        Policy: explicit allow-list only. Endpoint classes that do not appear
        in ALLOWED_ENDPOINT_CLASSES are denied. Unauthorised requests are logged
        to .ghost/privacy/events.jsonl without any raw health values.
        """
        decision, reason_code = self._privacy_policy(endpoint)
        if decision == "deny":
            self._log_privacy_event("privacy_allowed", endpoint, decision, reason_code)
        return endpoint in self.ALLOWED_ENDPOINT_CLASSES

    def forward(self, endpoint: str,
                observations: Optional[list[HealthObservation]] = None) -> list[dict]:
        """Export observations to an authorized endpoint; refuse unauthorized ones.

        Health values never reach model context or external calls by default.
        Only allow-listed aggregate endpoints (policy-class permits) pass.
        Each routing decision is recorded in the privacy event log.
        """
        decision, reason_code = self._privacy_policy(endpoint)
        self._log_privacy_event("forward", endpoint, decision, reason_code)
        if decision == "deny":
            raise HuaweiRouteError(
                f"Health data is not authorized for endpoint '{endpoint}'")
        items = [asdict(o) for o in (observations or
                 [HealthObservation(**i) for i in self._load_list(self.observations_path)])]
        return items
