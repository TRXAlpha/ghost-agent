"""OJ15/S08: trace-based learning over real Ghost outcome traces.

Mines the durable outcome records Ghost already writes (.ghost/outcomes/
*/outcome.json) for recurring failure signatures, and proposes improvements
through EXISTING subsystem APIs only — SelfRepairManager.propose for repairable
error classes, CapabilityRegistry.record_gap for missing-capability signals.
Nothing is ever auto-applied: this module only creates proposals/gaps for
later review, in line with Ghost's authority invariants.

Signature shape: (error_type, normalized_error_bucket, subsystem) where the
normalized bucket collapses volatile detail (numbers, paths, hashes, URLs)
so that "read timeout=300" and "read timeout=60" count as one recurring
failure pattern instead of two unique strings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

#: Failure results that indicate a capability/process gap rather than a
#: repairable defect in code Ghost owns.
CAPABILITY_GAP_RESULTS = {"iteration_limit", "capability_missing", "timeout"}

#: Error text classes that map to SelfRepairManager-style repair proposals.
REPAIRABLE_ERROR_CLASSES = {"ollama_read_timeout", "connection_error"}

DEFAULT_MIN_COUNT = 2


def normalize_error_text(text: str) -> str:
    """Collapse volatile detail so recurring failures share one bucket.

    Order matters: URLs and paths first (they contain digits), then hashes,
    then bare numbers.
    """
    if not isinstance(text, str):
        return str(text)
    normalized = text
    normalized = re.sub(r"https?://\S+", "<url>", normalized)
    normalized = re.sub(r"[A-Za-z]:[\\/][^:\s'\"]+", "<path>", normalized)
    normalized = re.sub(r"(?<![\w<])/[A-Za-z0-9_./-]{3,}", "<path>", normalized)
    normalized = re.sub(r"\b[a-f0-9]{8,}\b", "<hash>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+\b", "N", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def classify_error_text(text: str) -> str:
    """Map raw error text to a stable error class for repair routing."""
    lowered = (text or "").lower()
    if "read timed out" in lowered and "11434" in lowered:
        return "ollama_read_timeout"
    if "connection" in lowered and ("refused" in lowered or "error" in lowered):
        return "connection_error"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    return "generic_error"


@dataclass
class FailureSignature:
    error_type: str
    normalized_error: str
    subsystem: str
    count: int = 0
    sample_error: str = ""
    sample_job_id: str = ""

    def to_dict(self) -> dict:
        return {
            "error_type": self.error_type,
            "normalized_error": self.normalized_error,
            "subsystem": self.subsystem,
            "count": self.count,
            "sample_error": self.sample_error[:200],
            "sample_job_id": self.sample_job_id,
        }


@dataclass
class TraceAnalysis:
    generated_at: str
    scanned: int = 0
    failed: int = 0
    top_signatures: list[FailureSignature] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "scanned": self.scanned,
            "failed": self.failed,
            "top_signatures": [s.to_dict() for s in self.top_signatures],
        }


class TraceAnalyzer:
    """Load real outcome records and mine recurring failure signatures."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.outcomes_dir = self.root / ".ghost" / "outcomes"

    def load_outcomes(self) -> list[dict]:
        outcomes: list[dict] = []
        if not self.outcomes_dir.is_dir():
            return outcomes
        for path in sorted(self.outcomes_dir.glob("*/outcome.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(record, dict):
                record.setdefault("_path", str(path))
                outcomes.append(record)
        return outcomes

    def analyze(self) -> TraceAnalysis:
        analysis = TraceAnalysis(generated_at=datetime.now().isoformat(timespec="seconds"))
        signatures: dict[tuple[str, str, str], FailureSignature] = {}
        for record in self.load_outcomes():
            analysis.scanned += 1
            status = str(record.get("status", ""))
            result = str(record.get("result", ""))
            errors = record.get("errors") or []
            is_failed = status in ("failed", "needs_review") or result in CAPABILITY_GAP_RESULTS
            if not is_failed:
                continue
            analysis.failed += 1
            subsystem = "worker" if any(
                isinstance(e, str) and e.startswith("worker:") for e in errors
            ) else "orchestrator"
            if errors:
                for raw in errors:
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                    error_type = classify_error_text(text)
                    key = (error_type, normalize_error_text(text), subsystem)
                    sig = signatures.setdefault(key, FailureSignature(
                        error_type=error_type, normalized_error=key[1],
                        subsystem=subsystem))
                    sig.count += 1
                    sig.sample_error = sig.sample_error or text
                    sig.sample_job_id = sig.sample_job_id or str(record.get("job_id", ""))
            else:
                # No error text: the result code itself is the signature.
                error_type = f"result_{result or 'unspecified'}"
                key = (error_type, result or "unspecified", subsystem)
                sig = signatures.setdefault(key, FailureSignature(
                    error_type=error_type, normalized_error=key[1],
                    subsystem=subsystem))
                sig.count += 1
                sig.sample_job_id = sig.sample_job_id or str(record.get("job_id", ""))
        analysis.top_signatures = sorted(
            signatures.values(), key=lambda s: s.count, reverse=True)
        return analysis


class ImprovementProposer:
    """Bridge recurring failure signatures to reviewable proposals/gaps.

    NEVER auto-applies anything: only SelfRepairManager.propose (which itself
    only records a proposal) and CapabilityRegistry.record_gap (which only
    queues an investigation) are called.
    """

    def __init__(self, root: Path, min_count: int = DEFAULT_MIN_COUNT) -> None:
        self.root = Path(root)
        if min_count < 1:
            raise ValueError("min_count must be >= 1")
        self.min_count = min_count

    def propose_from(self, analysis: TraceAnalysis) -> list[dict]:
        from .autonomy import CapabilityRegistry
        actions: list[dict] = []
        for sig in analysis.top_signatures:
            if sig.count < self.min_count:
                continue
            if sig.error_type in REPAIRABLE_ERROR_CLASSES:
                actions.append({
                    "action": "repair_proposal",
                    "signature": sig.to_dict(),
                    "note": "self-repair proposal requires a sandbox candidate; "
                            "queued for orchestrator review instead of auto-created",
                })
            elif sig.error_type.startswith("result_") and sig.normalized_error in CAPABILITY_GAP_RESULTS:
                registry = CapabilityRegistry(self.root)
                gap = registry.record_gap(
                    goal=f"capability for recurring {sig.normalized_error} outcomes",
                    result=sig.normalized_error,
                    details=f"trace_learning: {sig.count} occurrences, "
                            f"sample job {sig.sample_job_id}",
                )
                actions.append({
                    "action": "capability_gap",
                    "signature": sig.to_dict(),
                    "gap_id": gap.get("id", ""),
                })
            else:
                actions.append({
                    "action": "report_only",
                    "signature": sig.to_dict(),
                })
        return actions


def run_trace_learning(root: Path, min_count: int = DEFAULT_MIN_COUNT,
                       persist: bool = True) -> dict:
    """Analyze + propose in one call; returns the summary dict for the CLI."""
    root = Path(root)
    analyzer = TraceAnalyzer(root)
    analysis = analyzer.analyze()
    proposer = ImprovementProposer(root, min_count=min_count)
    actions = proposer.propose_from(analysis)
    summary = {
        "analysis": analysis.to_dict(),
        "actions": actions,
    }
    if persist:
        out_dir = root / ".ghost" / "trace_learning"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "last_report.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
