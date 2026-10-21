"""Strategy genome: Ghost's coding-loop strategy as versioned, validated data.

The orchestrator's prompts and phase policy used to be hard-coded. They now
live here as *genes*, so a strategy can be evolved (``ghost evolve``) without
ever changing code. The envelope is safe by construction: genes are bounded
text and enumerated flags; they cannot touch the evaluator, hidden tests,
policy, approvals or any authority code.

``DEFAULT_GENES`` reproduces the pre-genome behaviour exactly; the existing
test suite pins that equivalence.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils.storage import atomic_json, exclusive_lock

DEFAULT_GENES: Dict[str, Any] = {
    # Extra rules appended to the system prompt (empty = none).
    "system_rules_extra": "",
    # Phase policy.
    "plan_enabled": True,
    "plan_prompt": (
        "Create the plan now. Output exactly ONE JSON object containing exactly "
        "ONE write_file action: path must be plan.md and content must list concise "
        "implementation steps plus a verification strategy. Do not read files or "
        "perform any other action in this turn."
    ),
    "implement_prompt": "Implement the task. Use tools to read/write files as needed.",
    # "impl_first": implementation first (historical). "tests_first": the first
    # IMPLEMENT turn asks for the test file before the implementation.
    "test_policy": "impl_first",
    "tests_first_prompt": (
        "Start with the tests. Write the pytest test file for the requested behaviour now "
        "(one file, real assertions taken from the goal), then implement in the next turn."
    ),
    # Repair templates. {feedback} is the last verification/tool feedback.
    "repair_tests_failed": (
        "Previous step failed with errors:\n{feedback}\n\n"
        "Fix the issues listed above. Use tools to update files, "
        "then let VERIFY re-run the tests."
    ),
    "repair_generic": (
        "Fix the issues from the last step:\n{feedback}\n\n"
        "Use tools to update files."
    ),
    # {test_path}, {kind}, {sources} are filled by the orchestrator.
    "repair_tests_missing": (
        "The goal requires tests and no test file has been written yet. Your next "
        "response must write exactly ONE file: {test_path}, containing {kind}. "
        "Do NOT rewrite {sources} again; it already exists."
    ),
    # Feedback shaping.
    "read_feedback_limit": 8000,
    # 0 = full feedback; N > 0 = keep only the last N characters of long
    # verification output (failing tests usually print the cause at the end).
    "feedback_tail_chars": 0,
    # Skill library (advisory skills from past successful work).
    "use_skills": False,
    "skill_k": 2,
}

_TEXT_GENES = {
    "system_rules_extra": (0, 2000),
    "plan_prompt": (20, 2000),
    "implement_prompt": (10, 2000),
    "tests_first_prompt": (10, 2000),
    "repair_tests_failed": (10, 2000),
    "repair_generic": (10, 2000),
    "repair_tests_missing": (10, 2000),
}
_REQUIRED_PLACEHOLDERS = {
    "repair_tests_failed": ("{feedback}",),
    "repair_generic": ("{feedback}",),
    "repair_tests_missing": ("{test_path}", "{kind}", "{sources}"),
}
_ENUMS = {"test_policy": ("impl_first", "tests_first")}
_BOOLS = ("plan_enabled", "use_skills")
_INTS = {"read_feedback_limit": (1000, 20000), "feedback_tail_chars": (0, 20000), "skill_k": (0, 5)}


class StrategyError(ValueError):
    """Invalid strategy genes or strategy store state."""


def validate_genes(genes: Dict[str, Any]) -> Dict[str, Any]:
    """Return a complete, validated gene dict (missing keys take defaults).

    Unknown keys, wrong types, out-of-range values, oversized text and
    templates missing their placeholders are refused.
    """
    if not isinstance(genes, dict):
        raise StrategyError("genes must be a dict")
    unknown = set(genes) - set(DEFAULT_GENES)
    if unknown:
        raise StrategyError(f"unknown genes: {sorted(unknown)}")
    merged = {**DEFAULT_GENES, **genes}
    for key, (low, high) in _TEXT_GENES.items():
        value = merged[key]
        if not isinstance(value, str) or not low <= len(value) <= high:
            raise StrategyError(f"{key} must be text of {low}..{high} characters")
        for placeholder in _REQUIRED_PLACEHOLDERS.get(key, ()):
            if placeholder not in value:
                raise StrategyError(f"{key} must contain {placeholder}")
        try:
            # Templates must format cleanly with exactly their placeholders.
            if key in _REQUIRED_PLACEHOLDERS:
                value.format(feedback="x", test_path="x", kind="x", sources="x")
        except (KeyError, IndexError, ValueError) as exc:
            raise StrategyError(f"{key} is not a valid template: {exc}") from exc
    for key, allowed in _ENUMS.items():
        if merged[key] not in allowed:
            raise StrategyError(f"{key} must be one of {allowed}")
    for key in _BOOLS:
        if type(merged[key]) is not bool:
            raise StrategyError(f"{key} must be a boolean")
    for key, (low, high) in _INTS.items():
        value = merged[key]
        if type(value) is not int or not low <= value <= high:
            raise StrategyError(f"{key} must be an integer {low}..{high}")
    return merged


def genes_digest(genes: Dict[str, Any]) -> str:
    """Stable identity of a gene set (validated, sorted JSON)."""
    canonical = json.dumps(validate_genes(genes), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class Strategy:
    strategy_id: str
    genes: Dict[str, Any]
    parent_ids: List[str] = field(default_factory=list)
    description: str = ""
    origin: str = "manual"            # default | manual | mutation | merge
    scores: Dict[str, Any] = field(default_factory=dict)   # filled by ghost evolve
    created_at: float = field(default_factory=time.time)

    @staticmethod
    def default() -> "Strategy":
        return Strategy(strategy_id="default-" + genes_digest(DEFAULT_GENES),
                        genes=dict(DEFAULT_GENES), description="Built-in strategy",
                        origin="default")


class StrategyStore:
    """Archive of strategies plus the owner-controlled active pointer."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.dir = self.root / ".ghost" / "strategy"
        self.archive = self.dir / "archive"
        self.active_path = self.dir / "active.json"

    def _path(self, strategy_id: str) -> Path:
        if not strategy_id or any(ch in strategy_id for ch in "/\\:") or len(strategy_id) > 80:
            raise StrategyError("invalid strategy id")
        return self.archive / f"{strategy_id}.json"

    def save(self, strategy: Strategy) -> Strategy:
        strategy.genes = validate_genes(strategy.genes)
        self.archive.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.dir / "strategy.lock"):
            atomic_json(self._path(strategy.strategy_id), asdict(strategy))
        return strategy

    def get(self, strategy_id: str) -> Optional[Strategy]:
        if strategy_id.startswith("default-"):
            default = Strategy.default()
            if strategy_id == default.strategy_id:
                return default
        path = self._path(strategy_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["genes"] = validate_genes(data.get("genes", {}))
            return Strategy(**data)
        except (OSError, ValueError, TypeError) as exc:
            raise StrategyError(f"strategy {strategy_id} is corrupt; preserved for review") from exc

    def list(self) -> List[Strategy]:
        if not self.archive.exists():
            return []
        result = []
        for path in sorted(self.archive.glob("*.json")):
            try:
                strategy = self.get(path.stem)
            except StrategyError:
                continue
            if strategy is not None:
                result.append(strategy)
        return result

    def active(self) -> Strategy:
        """The owner-activated strategy, or the built-in default.

        A corrupt pointer or archive entry falls back to the default (the
        historical behaviour), never to an arbitrary evolved strategy.
        """
        try:
            data = json.loads(self.active_path.read_text(encoding="utf-8"))
            strategy = self.get(str(data.get("strategy_id", "")))
            if strategy is not None:
                return strategy
        except (OSError, ValueError, StrategyError):
            pass
        return Strategy.default()

    def set_active(self, strategy_id: str, *, reason: str) -> Strategy:
        strategy = self.get(strategy_id)
        if strategy is None:
            raise StrategyError(f"unknown strategy {strategy_id}")
        self.dir.mkdir(parents=True, exist_ok=True)
        previous = self.active().strategy_id
        with exclusive_lock(self.dir / "strategy.lock"):
            atomic_json(self.active_path, {"strategy_id": strategy_id, "previous": previous,
                                           "reason": reason[:500], "activated_at": time.time()})
        return strategy

    def rollback(self) -> Strategy:
        """Return to the previously active strategy (or the default)."""
        try:
            data = json.loads(self.active_path.read_text(encoding="utf-8"))
            previous = str(data.get("previous") or "")
        except (OSError, ValueError):
            previous = ""
        target = self.get(previous) if previous else None
        if target is None:
            target = Strategy.default()
            if self.active_path.exists():
                self.active_path.unlink()
            return target
        return self.set_active(target.strategy_id, reason="rollback")


def resolve_genes(constraints: Dict[str, Any], project_root: Path) -> Dict[str, Any]:
    """Genes for one run: an explicit task contract wins, else the active strategy."""
    explicit = constraints.get("strategy_genes")
    if explicit is not None:
        return validate_genes(explicit)
    return StrategyStore(project_root).active().genes
