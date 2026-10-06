"""LEARNING ENGINE: Personalized learning paths, mastery tracking, and adaptation.

The Learning Engine tracks skill mastery, identifies knowledge gaps, generates
personalized learning paths, and adapts difficulty based on performance.

It extends the existing learner.py (OJ12) with:
- Learning path generation based on concept prerequisites
- Difficulty adaptation based on performance metrics
- Learning effectiveness measurement
- Gap analysis and targeted practice recommendations
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .utils.storage import atomic_json


# ============================================================================
# Learning Path and Progress
# ============================================================================

@dataclass
class LearningPath:
    """A sequence of concepts forming a learning journey."""
    path_id: str
    name: str
    description: str = ""
    concept_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    mastery_at_completion: float = 0.0

    @property
    def is_complete(self) -> bool:
        return self.completed_at is not None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LearningProgress:
    """Aggregated progress across all concepts and paths."""
    total_concepts: int = 0
    mastered_concepts: int = 0
    in_progress_concepts: int = 0
    not_started_concepts: int = 0
    overall_mastery: float = 0.0
    strong_areas: list[str] = field(default_factory=list)  # concept_ids
    weak_areas: list[str] = field(default_factory=list)    # concept_ids
    recommended_focus: list[str] = field(default_factory=list)  # concept_ids

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PrerequisiteEdge:
    """A prerequisite relationship between concepts."""
    concept_id: str
    depends_on: str  # Must master this first
    strength: float = 1.0  # How strong is the dependency (0-1)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# Learning Effectiveness Metrics
# ============================================================================

@dataclass
class EffectivenessMetrics:
    """Measures how effective learning activities are."""
    concept_id: str
    attempts: int = 0
    correct_on_first: int = 0
    avg_attempts_to_master: float = 0.0
    time_to_master_days: float = 0.0
    error_patterns: list[str] = field(default_factory=list)
    last_struggled_at: float = 0.0
    improvement_trend: float = 0.0  # positive = improving

    @property
    def first_try_success_rate(self) -> float:
        if self.attempts == 0:
            return 0.0
        return self.correct_on_first / self.attempts

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================================
# Learning Engine Store
# ============================================================================

class LearningEngineError(ValueError):
    """Invalid learning path, prerequisite, or engine state."""


class LearningEngine:
    """Engine for generating personalized learning paths and tracking mastery.

    The Learning Engine analyzes concept relationships, performance history,
    and learning effectiveness to create tailored learning experiences.

    Example:
        engine = LearningEngine(state_dir=Path(".ghost/learner"))
        engine.add_prerequisite("functions", "variables")
        path = engine.generate_path(target_concepts=["recursion", "closures"])
        engine.update_progress()
        metrics = engine.get_effectiveness("closures")
    """

    def __init__(self, state_dir: Path, learner_store: Optional[Any] = None) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.learner_store = learner_store
        self._prerequisites_file = self.state_dir / "prerequisites.json"
        self._paths_file = self.state_dir / "learning_paths.json"
        self._effectiveness_file = self.state_dir / "effectiveness.json"
        self._gaps_file = self.state_dir / "knowledge_gaps.json"
        self._progress_cache: Optional[LearningProgress] = None

    def initialize(self) -> None:
        """Initialize the learning engine."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        for path in [self._prerequisites_file, self._paths_file,
                     self._effectiveness_file, self._gaps_file]:
            if not path.exists():
                atomic_json(path, [])

    def shutdown(self) -> None:
        """Cleanup on shutdown."""
        self._progress_cache = None

    # -- Persistence helpers -----------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _save_json(path: Path, data: list[dict[str, Any]]) -> None:
        atomic_json(path, data)

    # -- Prerequisites management -----------------------------------------

    def add_prerequisite(self, concept_id: str, depends_on: str,
                         strength: float = 1.0) -> PrerequisiteEdge:
        """Add a prerequisite relationship.

        Args:
            concept_id: The concept requiring the prerequisite
            depends_on: The prerequisite concept
            strength: How critical is this prerequisite (0-1)

        Returns:
            Created PrerequisiteEdge
        """
        if concept_id == depends_on:
            raise LearningEngineError("Concept cannot be its own prerequisite")
        if not 0 <= strength <= 1:
            raise LearningEngineError("Strength must be between 0 and 1")

        edges = self._load_json(self._prerequisites_file)
        edge_id = f"{concept_id}|{depends_on}"
        
        # Check for duplicates
        for edge in edges:
            if edge.get("concept_id") == concept_id and edge.get("depends_on") == depends_on:
                raise LearningEngineError("Prerequisite already exists")

        edge = PrerequisiteEdge(
            concept_id=concept_id,
            depends_on=depends_on,
            strength=strength,
        )
        edges.append(edge.to_dict())
        self._save_json(self._prerequisites_file, edges)
        return edge

    def remove_prerequisite(self, concept_id: str, depends_on: str) -> bool:
        """Remove a prerequisite relationship."""
        edges = self._load_json(self._prerequisites_file)
        original_len = len(edges)
        edges = [e for e in edges if not (e.get("concept_id") == concept_id 
                                          and e.get("depends_on") == depends_on)]
        if len(edges) == original_len:
            return False
        self._save_json(self._prerequisites_file, edges)
        return True

    def get_prerequisites(self, concept_id: str) -> list[str]:
        """Get all prerequisite concept IDs for a given concept."""
        edges = self._load_json(self._prerequisites_file)
        return [e["depends_on"] for e in edges if e.get("concept_id") == concept_id]

    def get_dependents(self, concept_id: str) -> list[str]:
        """Get all concepts that depend on this one."""
        edges = self._load_json(self._prerequisites_file)
        return [e["concept_id"] for e in edges if e.get("depends_on") == concept_id]

    def get_learning_order(self, concept_ids: list[str]) -> list[str]:
        """Return concepts in topological order respecting prerequisites."""
        edges = self._load_json(self._prerequisites_file)
        prereq_map: dict[str, set[str]] = {c: set() for c in concept_ids}
        
        for e in edges:
            cid, dep = e.get("concept_id"), e.get("depends_on")
            if cid in prereq_map:
                prereq_map[cid].add(dep)
        
        # Topological sort (Kahn's algorithm)
        result: list[str] = []
        remaining = set(concept_ids)
        completed: set[str] = set()
        
        while remaining:
            # Find concepts whose prerequisites are all satisfied
            ready = [c for c in remaining if prereq_map[c] <= completed]
            if not ready:
                # Circular dependency or missing prerequisite - add remaining
                result.extend(sorted(remaining))
                break
            for c in sorted(ready):
                result.append(c)
                remaining.remove(c)
                completed.add(c)
        
        return result

    # -- Learning Path Generation -----------------------------------------

    def generate_path(self, target_concepts: list[str],
                     name: str = "", description: str = "") -> LearningPath:
        """Generate a learning path for target concepts.

        The path respects prerequisites and prioritizes concepts
        based on dependencies.

        Args:
            target_concepts: Concepts to learn
            name: Optional name for the path
            description: Optional description

        Returns:
            LearningPath with ordered concepts
        """
        if not target_concepts:
            raise LearningEngineError("Must specify at least one target concept")

        # Get all prerequisites recursively
        all_concepts = set(target_concepts)
        to_visit = list(target_concepts)
        while to_visit:
            concept = to_visit.pop()
            prereqs = self.get_prerequisites(concept)
            for p in prereqs:
                if p not in all_concepts:
                    all_concepts.add(p)
                    to_visit.append(p)

        # Order by prerequisites
        ordered = self.get_learning_order(list(all_concepts))
        
        # Filter to only include concepts relevant to targets
        relevant = [c for c in ordered if c in all_concepts]

        path_id = "lp_" + hashlib.sha1(
            f"{name}|{target_concepts}|{time.time()}".encode()).hexdigest()[:12]
        
        path = LearningPath(
            path_id=path_id,
            name=name or f"Path to: {', '.join(target_concepts[:3])}",
            description=description,
            concept_ids=relevant,
        )
        
        # Save the path
        paths = self._load_json(self._paths_file)
        paths.append(path.to_dict())
        self._save_json(self._paths_file, paths)
        
        return path

    def get_path(self, path_id: str) -> Optional[LearningPath]:
        """Retrieve a learning path by ID."""
        paths = self._load_json(self._paths_file)
        for p in paths:
            if p.get("path_id") == path_id:
                return LearningPath(**p)
        return None

    def complete_path(self, path_id: str) -> LearningPath:
        """Mark a learning path as complete."""
        paths = self._load_json(self._paths_file)
        for p in paths:
            if p.get("path_id") == path_id:
                p["completed_at"] = time.time()
                self._save_json(self._paths_file, paths)
                return LearningPath(**p)
        raise LearningEngineError("Path not found")

    def list_paths(self, include_completed: bool = False) -> list[LearningPath]:
        """List all learning paths."""
        paths = self._load_json(self._paths_file)
        if not include_completed:
            paths = [p for p in paths if not p.get("completed_at")]
        return [LearningPath(**p) for p in paths]

    # -- Progress tracking ------------------------------------------------

    def update_progress(self) -> LearningProgress:
        """Update cached progress from learner store."""
        # This would integrate with learner.py's LearnerStore
        # For now, return a basic progress object
        progress = LearningProgress()
        self._progress_cache = progress
        return progress

    def get_progress(self) -> LearningProgress:
        """Get current learning progress."""
        if self._progress_cache is None:
            return self.update_progress()
        return self._progress_cache

    # -- Knowledge gaps and recommendations --------------------------------

    def analyze_gaps(self, concept_ids: list[str]) -> list[str]:
        """Identify knowledge gaps based on prerequisites and performance.

        Returns concept IDs that are prerequisites but not yet mastered.
        """
        gaps: list[str] = []
        for cid in concept_ids:
            prereqs = self.get_prerequisites(cid)
            for prereq in prereqs:
                if prereq not in concept_ids:
                    gaps.append(prereq)
        return list(set(gaps))

    def recommend_focus(self, count: int = 5) -> list[str]:
        """Recommend concepts to focus on based on gaps and dependencies."""
        # This would use effectiveness metrics and mastery data
        # Simple implementation: return concepts with most dependents
        edges = self._load_json(self._prerequisites_file)
        
        dep_count: dict[str, int] = {}
        for e in edges:
            dep = e.get("depends_on", "")
            dep_count[dep] = dep_count.get(dep, 0) + 1
        
        sorted_concepts = sorted(dep_count.items(), key=lambda x: -x[1])
        return [c for c, _ in sorted_concepts[:count]]

    # -- Effectiveness metrics ---------------------------------------------

    def record_attempt_result(self, concept_id: str, correct: bool,
                             attempts_for_concept: int) -> EffectivenessMetrics:
        """Record attempt result for effectiveness tracking."""
        metrics_list = self._load_json(self._effectiveness_file)
        
        # Find or create metrics for this concept
        metrics = None
        for m in metrics_list:
            if m.get("concept_id") == concept_id:
                metrics = EffectivenessMetrics(**m)
                break
        
        if metrics is None:
            metrics = EffectivenessMetrics(concept_id=concept_id)
            metrics_list.append(metrics.to_dict())

        # Update metrics
        metrics.attempts += 1
        if attempts_for_concept == 1:
            metrics.correct_on_first += 1
        
        # Calculate trend (simplified)
        if metrics.attempts >= 2:
            recent_correct = 1 if correct else 0
            metrics.improvement_trend = (recent_correct - metrics.first_try_success_rate) * 0.1

        if not correct:
            metrics.last_struggled_at = time.time()

        # Update file
        for m in metrics_list:
            if m.get("concept_id") == concept_id:
                m.update(metrics.to_dict())
        self._save_json(self._effectiveness_file, metrics_list)
        
        return metrics

    def get_effectiveness(self, concept_id: str) -> Optional[EffectivenessMetrics]:
        """Get effectiveness metrics for a concept."""
        metrics_list = self._load_json(self._effectiveness_file)
        for m in metrics_list:
            if m.get("concept_id") == concept_id:
                return EffectivenessMetrics(**m)
        return None

    def get_all_effectiveness(self) -> list[EffectivenessMetrics]:
        """Get effectiveness metrics for all concepts."""
        return [EffectivenessMetrics(**m) for m in self._load_json(self._effectiveness_file)]

    # -- Difficulty adaptation ---------------------------------------------

    def suggest_difficulty(self, concept_id: str) -> str:
        """Suggest appropriate difficulty level based on performance.

        Returns: "beginner", "intermediate", "advanced"
        """
        metrics = self.get_effectiveness(concept_id)
        if metrics is None:
            return "beginner"
        
        success_rate = metrics.first_try_success_rate
        if success_rate >= 0.8:
            return "advanced"
        elif success_rate >= 0.5:
            return "intermediate"
        else:
            return "beginner"

    def get_adaptive_recommendations(self, concept_id: str) -> dict[str, Any]:
        """Get adaptive learning recommendations for a concept."""
        metrics = self.get_effectiveness(concept_id)
        difficulty = self.suggest_difficulty(concept_id)
        
        recommendations: list[str] = []
        if metrics:
            if metrics.first_try_success_rate < 0.5:
                recommendations.append("Review prerequisite concepts")
                recommendations.append("Try simpler practice problems")
            elif metrics.first_try_success_rate < 0.8:
                recommendations.append("Practice with more examples")
            else:
                recommendations.append("Try advanced challenges")
        
        return {
            "concept_id": concept_id,
            "suggested_difficulty": difficulty,
            "recommendations": recommendations,
            "metrics": metrics.to_dict() if metrics else None,
        }
