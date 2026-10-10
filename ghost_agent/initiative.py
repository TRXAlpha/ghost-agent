"""Composite initiative score for proactive opportunities (TN1).

This is a transparent heuristic ranking, not a validated utility model: it
ranks proposed opportunities so the auto-queue rule has a defensible order.
It is tuned only by recorded outcomes (kind_success), never presented as
ground truth.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from .proactive import Opportunity, OpportunityStore

DEFAULT_WEIGHTS = {"goal_relevance": 0.25, "urgency": 0.2, "confidence": 0.2,
                   "benefit": 0.2, "effort": 0.1, "interruption": 0.05}
QUEUE_THRESHOLD = 0.55

_BENEFIT_VALUES = {"low": 0.2, "medium": 0.6, "high": 1.0}
_EFFORT_VALUES = {"small": 1.0, "medium": 0.6, "large": 0.3}  # cheaper is better


def load_weights(root: Path) -> dict[str, float]:
    """Default weights, overridden by .ghost/initiative_weights.json.

    An invalid (unreadable, non-JSON, non-dict, or wrong-typed) file falls
    back to DEFAULT_WEIGHTS and emits a warning; the loop never stops on it.
    A numeric "threshold" key in the file overrides QUEUE_THRESHOLD.
    """
    merged = dict(DEFAULT_WEIGHTS)
    path = Path(root) / ".ghost" / "initiative_weights.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return merged
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        _warn_invalid(path)
        return merged
    if not isinstance(raw, dict):
        _warn_invalid(path)
        return merged
    for key, value in raw.items():
        if key == "threshold":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                merged["threshold"] = float(value)
            else:
                _warn_invalid(path)
        elif key in DEFAULT_WEIGHTS and isinstance(value, (int, float)) \
                and not isinstance(value, bool):
            merged[key] = float(value)
        else:
            _warn_invalid(path)
    return merged


def _warn_invalid(path: Path) -> None:
    warnings.warn(f"initiative weights file invalid or has unusable entries; "
                  f"using defaults: {path}", stacklevel=3)


def kind_success_rate(store: OpportunityStore, kind: str) -> float | None:
    """Success rate for one opportunity kind, from recorded outcomes only.

    Counted items: opportunities of `kind` whose `result` is a decided
    outcome (tests_passed / tests_failed / error), plus rejected/dismissed
    records with less_like_this true (a "less of this" signal is a failure).
    Returns None with fewer than 3 decided items: no evidence, no tuning.
    """
    decided = 0
    successes = 0
    for item in store.list_all():
        if item.kind != kind:
            continue
        if item.result in {"tests_passed", "tests_failed", "error"}:
            decided += 1
            if item.result == "tests_passed":
                successes += 1
        elif item.status in {"rejected", "dismissed"} and item.less_like_this:
            decided += 1
    if decided < 3:
        return None
    return successes / decided


def score(opp: Opportunity, *, kind_success: float | None = None,
          weights: dict[str, float] | None = None) -> tuple[float, list[str]]:
    """Return (score, reasons) for one opportunity.

    Every factor value is in 0..1; the base is the weighted mean. When
    kind_success is recorded, the score is tuned by it. Reasons are short
    strings, one per factor, and never claim validated utility.
    """
    weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)
    used = {name: weights[name] for name in DEFAULT_WEIGHTS if name in weights}
    total_weight = sum(used.values()) or 1.0

    goal_value = 1.0 if opp.goal_ids else 0.3
    goal_label = "matched" if opp.goal_ids else "none"
    if opp.trigger == "deadline":
        urgency_value, urgency_label = 1.0, "deadline"
    elif opp.trigger == "stale_goal":
        urgency_value, urgency_label = 0.7, "stale_goal"
    else:
        urgency_value, urgency_label = 0.3, "tick"
    confidence_value = min(1.0, max(0.0, float(opp.confidence)))
    benefit_value = _BENEFIT_VALUES.get(opp.benefit, _BENEFIT_VALUES["medium"])
    effort_value = _EFFORT_VALUES.get(opp.cost_effort, _EFFORT_VALUES["medium"])
    interruption_value = 0.2 if opp.requires_user_input else 1.0
    interruption_label = "needs input" if opp.requires_user_input else "none"

    values = [
        ("goal_relevance", goal_value, f"{goal_label}"),
        ("urgency", urgency_value, urgency_label),
        ("confidence", confidence_value, f"{confidence_value:.2f}"),
        ("benefit", benefit_value, str(opp.benefit)),
        ("effort", effort_value, str(opp.cost_effort)),
        ("interruption", interruption_value, interruption_label),
    ]
    base = sum(used[name] * value for name, value, _ in values) / total_weight
    final = base
    reasons = [f"{name} {label} ({used[name] * value:+.2f})"
               for name, value, label in values]
    if kind_success is not None:
        tuned = base * (0.5 + 0.5 * float(kind_success))
        reasons.append(f"outcome tuning kind_success={float(kind_success):.2f} "
                       f"({base:.3f} -> {tuned:.3f})")
        final = tuned
    reasons.append("heuristic ranking; tuned only by recorded outcomes")
    return round(final, 3), reasons
