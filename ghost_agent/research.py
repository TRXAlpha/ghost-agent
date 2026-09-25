"""Persistent provenance graph for source-grounded research runs."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ResearchProvenanceGraph:
    """Track how a research run moved from questions to final evidence."""

    def __init__(self, root: Path, topic: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        digest = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:8]
        self.run_id = f"research_{stamp}_{digest}"
        self.path = self.root / f"{self.run_id}.json"
        self.data: Dict[str, Any] = {
            "run_id": self.run_id,
            "topic": topic,
            "started_at": time.time(),
            "finished_at": None,
            "nodes": [],
            "edges": [],
            "gaps": [],
        }
        self._query_tokens: Dict[str, set[str]] = {}
        self._source_ids: Dict[str, str] = {}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", text.lower()))

    def _node(self, kind: str, label: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        node_id = f"{kind}_{len(self.data['nodes']) + 1:04d}"
        self.data["nodes"].append({
            "id": node_id,
            "kind": kind,
            "label": label,
            "metadata": metadata or {},
        })
        return node_id

    def _edge(self, source: str, target: str, relation: str) -> None:
        self.data["edges"].append({
            "source": source,
            "target": target,
            "relation": relation,
        })

    def is_novel_query(self, query: str, threshold: float = 0.82) -> bool:
        tokens = self._tokens(query)
        if not tokens:
            return False
        for existing in self._query_tokens.values():
            union = tokens | existing
            if union and len(tokens & existing) / len(union) >= threshold:
                return False
        return True

    def add_query(self, query: str, round_index: int) -> str:
        node_id = self._node("query", query, {"round": round_index})
        self._query_tokens[node_id] = self._tokens(query)
        return node_id

    def add_source(self, query_id: str, finding: Dict[str, Any]) -> str:
        url = str(finding.get("url", ""))
        source_id = self._source_ids.get(url)
        if source_id is None:
            source_id = self._node("source", str(finding.get("title", url)), {
                "url": url,
                "tier": finding.get("tier", "unknown"),
                "score": finding.get("score", 0.0),
                "source_kind": finding.get("source_kind", "full_text"),
                "retrieved_via": finding.get("retrieved_via"),
                "work_id": finding.get("work_id"),
                "doi": finding.get("doi"),
                "publisher_id": finding.get("publisher_id"),
                "venue_id": finding.get("venue_id"),
            })
            self._source_ids[url] = source_id
        self._edge(query_id, source_id, "returned")
        return source_id

    def add_claim(self, claim: Dict[str, Any], source_id: Optional[str]) -> str:
        claim_id = self._node("claim", str(claim.get("claim", "")), {
            "quote": claim.get("source_quote", ""),
            "source_url": claim.get("source_url", ""),
        })
        if source_id:
            self._edge(source_id, claim_id, "quoted_support")
        return claim_id

    def add_verdict(self, claim_id: str, verdict: str, metadata: Dict[str, Any]) -> str:
        verdict_id = self._node("verdict", verdict, metadata)
        self._edge(claim_id, verdict_id, "assessed_as")
        return verdict_id

    def add_gap(self, description: str) -> None:
        if description not in self.data["gaps"]:
            self.data["gaps"].append(description)

    def summary(self) -> str:
        queries = [
            node["label"] for node in self.data["nodes"] if node["kind"] == "query"
        ][-6:]
        gaps = self.data["gaps"][-3:]
        return (
            f"Trace: {len(queries)} recent queries; "
            f"{len(self._source_ids)} unique sources; "
            f"open gaps: {', '.join(gaps) if gaps else 'none'}; "
            f"recent queries: {' | '.join(queries)}"
        )

    def save(self) -> Path:
        self.data["finished_at"] = time.time()
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.path
