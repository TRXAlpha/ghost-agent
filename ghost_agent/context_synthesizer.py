"""CONTEXT SYNTHESIZER: Combines context from multiple sources with relevance weighting.

The Context Synthesizer:
- Combines context from multiple sources (memory, active conversation, documents, etc.)
- Maintains relevance weighting that adapts based on usage
- Handles conflicting information gracefully
- Prunes stale context to manage context window

This module provides intelligent context management for AI-OS operations,
extending the existing context.py and context_manager.py.
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
# Context Sources and Items
# ============================================================================

class ContextSource:
    """Available context source types."""
    MEMORY = "memory"
    CONVERSATION = "conversation"
    DOCUMENT = "document"
    USER_INPUT = "user_input"
    SYSTEM = "system"
    DEVICE = "device"
    LEARNER = "learner"


@dataclass
class ContextItem:
    """A single item of context with metadata."""
    item_id: str
    content: str
    source: str
    relevance: float = 1.0  # 0-1, weighted by usage
    staleness: float = 0.0  # 0-1, 1 = very stale
    created_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    access_count: int = 0
    provenance: str = ""  # Source document, message ID, etc.
    sensitivity: str = "normal"  # normal, private, sensitive
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def effective_weight(self) -> float:
        """Combined relevance considering staleness."""
        age_days = (time.time() - self.created_at) / 86400
        staleness_factor = min(age_days / 7, 1.0)  # Max decay at 7 days
        return self.relevance * (1 - staleness_factor * 0.5)


@dataclass
class ContextConflict:
    """Identified conflict between context items."""
    item_ids: list[str]
    topic: str
    summary: str
    resolution: str = "unresolved"  # unresolved, prefer_newer, prefer_relevant, merge
    resolved_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SynthesizedContext:
    """Final synthesized context ready for use."""
    items: list[ContextItem]
    conflicts: list[ContextConflict]
    total_tokens: int
    pruned_items: list[ContextItem]
    synthesis_time_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self, max_tokens: int = 4000) -> str:
        """Render synthesized context as text."""
        lines = []
        remaining = max_tokens
        
        for item in self.items:
            prefix = f"[{item.source}]"
            if item.tags:
                prefix += f" ({', '.join(item.tags)})"
            line = f"{prefix}: {item.content}"
            
            # Rough token estimate (1 token ≈ 4 chars)
            item_tokens = len(line) // 4
            if remaining - item_tokens < 0:
                break
            lines.append(line)
            remaining -= item_tokens
        
        return "\n\n".join(lines)


# ============================================================================
# Context Synthesizer
# ============================================================================

class ContextSynthesizerError(ValueError):
    """Invalid synthesizer configuration or operation."""


class ContextSynthesizer:
    """Synthesizes context from multiple sources with intelligent weighting.

    The synthesizer:
    - Aggregates context from configured sources
    - Weights by relevance, recency, and usage patterns
    - Detects and resolves conflicts
    - Prunes stale or low-relevance items

    Example:
        synth = ContextSynthesizer(state_dir=Path(".ghost/context"))
        synth.register_source("memory", memory_store)
        synth.register_source("learner", learner_store)
        result = synth.synthesize(max_tokens=4000)
        print(result.to_text())
    """

    # Token estimates
    CHARS_PER_TOKEN = 4
    HEADER_OVERHEAD = 50  # chars

    def __init__(self, state_dir: Path,
                 max_context_tokens: int = 4000,
                 stale_threshold_days: float = 7.0) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_context_tokens = max_context_tokens
        self.stale_threshold_days = stale_threshold_days
        
        self._sources: dict[str, Any] = {}
        self._context_index_file = self.state_dir / "context_index.json"
        self._weights_file = self.state_dir / "source_weights.json"
        self._conflicts_file = self.state_dir / "conflicts.json"
        self._context_cache: Optional[SynthesizedContext] = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 60.0  # seconds

    def initialize(self) -> None:
        """Initialize the synthesizer."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self._context_index_file.exists():
            atomic_json(self._context_index_file, [])
        if not self._weights_file.exists():
            atomic_json(self._weights_file, {})
        if not self._conflicts_file.exists():
            atomic_json(self._conflicts_file, [])

    def shutdown(self) -> None:
        """Cleanup on shutdown."""
        self._context_cache = None

    # -- Source registration -----------------------------------------------

    def register_source(self, name: str, source: Any) -> None:
        """Register a context source.

        The source should implement:
        - get_context(limit) -> list[dict] with 'content', 'source', 'created_at'
        
        Or for LearnerStore:
        - concepts() -> list[Concept]
        - progress() -> dict
        """
        self._sources[name] = source

    def unregister_source(self, name: str) -> None:
        """Remove a context source."""
        self._sources.pop(name, None)

    def get_source_weights(self) -> dict[str, float]:
        """Get current source weights."""
        if self._weights_file.exists():
            return json.loads(self._weights_file.read_text(encoding="utf-8"))
        return {}

    def set_source_weight(self, source: str, weight: float) -> None:
        """Set weight for a context source."""
        if not 0 <= weight <= 2.0:
            raise ContextSynthesizerError("Weight must be between 0 and 2.0")
        weights = self.get_source_weights()
        weights[source] = weight
        atomic_json(self._weights_file, weights)

    # -- Context retrieval from sources -----------------------------------

    def _fetch_from_source(self, name: str, limit: int = 100) -> list[ContextItem]:
        """Fetch context from a registered source."""
        source = self._sources.get(name)
        if source is None:
            return []

        items: list[ContextItem] = []
        weights = self.get_source_weights()
        base_weight = weights.get(name, 1.0)

        try:
            # Try learner store interface
            if hasattr(source, 'concepts'):
                for concept in source.concepts()[:limit]:
                    items.append(ContextItem(
                        item_id=f"{name}:{concept.concept_id}",
                        content=f"{concept.name}: mastery={concept.mastery:.2f}",
                        source=name,
                        relevance=concept.mastery * base_weight,
                        tags=["learner", "concept"],
                    ))
            
            # Try progress interface
            elif hasattr(source, 'progress'):
                progress = source.progress()
                items.append(ContextItem(
                    item_id=f"{name}:progress",
                    content=f"Learning progress: {progress.get('overall_mastery', 0):.2%} overall",
                    source=name,
                    relevance=0.8 * base_weight,
                    tags=["learner", "progress"],
                ))

            # Try generic get_context interface
            elif hasattr(source, 'get_context'):
                for i, ctx in enumerate(source.get_context(limit)[:limit]):
                    items.append(ContextItem(
                        item_id=f"{name}:{i}",
                        content=ctx.get("content", ""),
                        source=name,
                        relevance=ctx.get("relevance", 1.0) * base_weight,
                        created_at=ctx.get("created_at", time.time()),
                        provenance=ctx.get("provenance", ""),
                        tags=ctx.get("tags", []),
                    ))

        except Exception:
            pass  # Best effort

        return items

    def fetch_all_sources(self, limit_per_source: int = 50) -> list[ContextItem]:
        """Fetch and combine context from all registered sources."""
        all_items: list[ContextItem] = []
        seen_ids: set[str] = set()

        for name in self._sources:
            for item in self._fetch_from_source(name, limit_per_source):
                if item.item_id not in seen_ids:
                    all_items.append(item)
                    seen_ids.add(item.item_id)

        return all_items

    # -- Conflict detection and resolution --------------------------------

    def detect_conflicts(self, items: list[ContextItem]) -> list[ContextConflict]:
        """Detect conflicting information in context items.

        Simple conflict detection based on:
        - Same topic from different sources with different claims
        - Contradicting numeric values
        """
        conflicts: list[ContextConflict] = []
        
        # Group by topic (simplified: use first significant word)
        topic_items: dict[str, list[ContextItem]] = {}
        for item in items:
            words = item.content.lower().split()
            if words:
                # Use first 3 words as topic key
                topic = " ".join(words[:3])
                if topic not in topic_items:
                    topic_items[topic] = []
                topic_items[topic].append(item)

        # Detect conflicts in multi-item topics
        for topic, topic_items_list in topic_items.items():
            if len(topic_items_list) < 2:
                continue
            
            # Look for numeric conflicts
            numeric_values: dict[str, list[str]] = {}
            for item in topic_items_list:
                import re
                numbers = re.findall(r'\d+\.?\d*%?', item.content)
                if numbers:
                    key = " ".join([w for w in topic.split() if not w[0].isdigit()])
                    numeric_values.setdefault(key, []).extend(numbers)

            for key, values in numeric_values.items():
                if len(set(values)) > 1:
                    conflicts.append(ContextConflict(
                        item_ids=[i.item_id for i in topic_items_list],
                        topic=topic,
                        summary=f"Conflicting values for '{key}': {values}",
                        resolution="unresolved",
                    ))

        return conflicts

    def resolve_conflict(self, conflict: ContextConflict,
                        resolution: str,
                        resolved_content: str = "") -> ContextConflict:
        """Apply a resolution strategy to a conflict."""
        conflict.resolution = resolution
        conflict.resolved_content = resolved_content
        
        conflicts = self._load_conflicts()
        for i, c in enumerate(conflicts):
            if c.get("item_ids") == conflict.item_ids:
                conflicts[i] = conflict.to_dict()
                break
        else:
            conflicts.append(conflict.to_dict())
        
        atomic_json(self._conflicts_file, conflicts)
        return conflict

    def _load_conflicts(self) -> list[dict[str, Any]]:
        """Load stored conflicts."""
        if self._conflicts_file.exists():
            return json.loads(self._conflicts_file.read_text(encoding="utf-8"))
        return []

    # -- Context pruning --------------------------------------------------

    def _calculate_staleness(self, item: ContextItem) -> float:
        """Calculate staleness score for an item."""
        age_days = (time.time() - item.last_used_at) / 86400
        threshold = self.stale_threshold_days
        return min(age_days / threshold, 1.0)

    def prune_context(self, items: list[ContextItem],
                     max_tokens: int) -> tuple[list[ContextItem], list[ContextItem]]:
        """Prune context to fit within token budget.

        Returns:
            Tuple of (kept_items, pruned_items)
        """
        # Calculate staleness for all items
        for item in items:
            item.staleness = self._calculate_staleness(item)

        # Sort by effective weight (relevance adjusted for staleness)
        sorted_items = sorted(items, key=lambda i: -i.effective_weight)

        kept: list[ContextItem] = []
        pruned: list[ContextItem] = []
        current_tokens = self.HEADER_OVERHEAD // self.CHARS_PER_TOKEN

        for item in sorted_items:
            item_tokens = len(item.content) // self.CHARS_PER_TOKEN
            
            if current_tokens + item_tokens <= max_tokens:
                kept.append(item)
                current_tokens += item_tokens
                item.last_used_at = time.time()
            else:
                pruned.append(item)

        return kept, pruned

    # -- Main synthesis ----------------------------------------------------

    def synthesize(self, max_tokens: Optional[int] = None,
                   include_stale: bool = False,
                   force_refresh: bool = False) -> SynthesizedContext:
        """Synthesize context from all sources.

        Args:
            max_tokens: Maximum tokens to include (default: self.max_context_tokens)
            include_stale: Include items beyond staleness threshold
            force_refresh: Bypass cache

        Returns:
            SynthesizedContext with items, conflicts, and metadata
        """
        start = time.time()
        max_tokens = max_tokens or self.max_context_tokens

        # Check cache
        if not force_refresh and self._context_cache:
            if time.time() - self._cache_time < self._cache_ttl:
                return self._context_cache

        # Fetch from all sources
        all_items = self.fetch_all_sources(limit_per_source=100)

        if not include_stale:
            # Filter out very stale items
            all_items = [i for i in all_items 
                        if self._calculate_staleness(i) < 0.9]

        # Detect conflicts
        conflicts = self.detect_conflicts(all_items)

        # Prune to fit budget
        kept_items, pruned_items = self.prune_context(all_items, max_tokens)

        # Sort kept items by weight
        kept_items.sort(key=lambda i: -i.effective_weight)

        # Build result
        result = SynthesizedContext(
            items=kept_items,
            conflicts=conflicts,
            total_tokens=sum(len(i.content) for i in kept_items) // self.CHARS_PER_TOKEN,
            pruned_items=pruned_items,
            synthesis_time_ms=(time.time() - start) * 1000,
        )

        self._context_cache = result
        self._cache_time = time.time()
        
        return result

    # -- Usage tracking ----------------------------------------------------

    def record_usage(self, item_id: str) -> None:
        """Record that a context item was used."""
        # This would update the index to track usage
        pass

    # -- Query interface --------------------------------------------------

    def query(self, query: str, max_results: int = 10) -> list[ContextItem]:
        """Search for context items matching a query."""
        items = self.fetch_all_sources(limit_per_source=100)
        
        query_lower = query.lower()
        scored: list[tuple[float, ContextItem]] = []
        
        for item in items:
            if query_lower in item.content.lower():
                # Score by relevance and recency
                score = item.effective_weight
                if query_lower in item.content.lower().split()[0:3]:
                    score *= 1.5  # Boost if in first few words
                scored.append((score, item))
        
        scored.sort(key=lambda x: -x[0])
        return [item for _, item in scored[:max_results]]

    def get_context_summary(self) -> dict[str, Any]:
        """Get a summary of current context state."""
        return {
            "registered_sources": list(self._sources.keys()),
            "source_weights": self.get_source_weights(),
            "max_context_tokens": self.max_context_tokens,
            "stale_threshold_days": self.stale_threshold_days,
            "cache_valid": (self._context_cache is not None and 
                          time.time() - self._cache_time < self._cache_ttl),
        }
