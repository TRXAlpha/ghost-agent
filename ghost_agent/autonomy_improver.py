"""Autonomous capability builder: proactively fills capability gaps.

Triggered by: proactive tick OR idle-time check.
Mechanism: reads high-count gaps from CapabilityRegistry, uses a model to
  assess whether the gap is autonomously addressable (a missing integration
  / adapter that can be scaffolded), proposes a reviewable extension scaffold,
  queues sandbox implementation, and marks the gap resolved on tests_passed.

Safety: all changes are sandbox-only; no live promotion without approval;
  model assessment is advisory (human review required before enablement).
  LoRA corpus accumulates verified pairs from successful gap resolutions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .autonomy import CapabilityRegistry
from .models.chat import chat as ollama_chat
from .runtime import Event, EventBus

CAPABILITY_BUILD_PROMPT = """You are assessing whether a capability gap can be autonomously addressed.

Gap: {goal}
Failure result: {result}
Followup goal: {followup_goal}

Determine:
1. Is this gap addressable by writing a LOCAL code adapter/extension (not requiring external credentials, accounts, or hardware that is not owned)?
2. What is the simplest bounded scope for addressing it?
3. Is it too risky to attempt autonomously without human guidance?

Respond ONLY with a JSON object:
{{"addressable": true/false, "scope": "brief description", "risky": true/false, "reason": "brief"}}"""


class CapabilityImprover:
    """Proactively fills high-count capability gaps via sandboxed implementation."""

    def __init__(self, project_root: Path, model: str = "qwen2.5-coder:1.5b") -> None:
        self.root = Path(project_root).resolve()
        self.model = model
        self.gaps = CapabilityRegistry(self.root)

    def assess(self, gap: dict) -> dict:
        """Use the model to assess whether a gap is autonomously addressable."""
        messages = [{"role": "user", "content": CAPABILITY_BUILD_PROMPT.format(
            goal=gap["goal"], result=gap["result"], followup_goal=gap["followup_goal"]
        )}]
        try:
            response = ollama_chat(self.model, messages, timeout=30)
        except Exception:
            return {"addressable": False, "risky": True, "reason": "model unreachable"}
        # ollama_chat returns str normally, or Iterator[str] when streaming
        if not isinstance(response, str):
            response = "".join(response)
        try:
            import json
            return json.loads(response)
        except Exception:
            return {"addressable": False, "risky": True, "reason": "model response not JSON"}

    def try_build(self) -> Optional[str]:
        """Attempt to autonomously address one queued gap.

        Returns gap_id if work was queued, None if nothing addressable.
        Must NOT raise — called from idle-time proactive tick.
        """
        gaps = self.gaps.get_queued_gaps(min_count=2)
        if not gaps:
            return None
        gap = sorted(gaps, key=lambda g: g["count"], reverse=True)[0]
        assessment = self.assess(gap)
        if not assessment.get("addressable", False) or assessment.get("risky", True):
            return None
        # Mark in-progress and queue the followup goal as sandboxed work
        self.gaps.mark_in_progress(gap["id"])
        EventBus(self.root).emit(Event("capability.building", {
            "gap_id": gap["id"], "goal": gap["followup_goal"], "assessment": assessment
        }))
        try:
            # Import here to avoid circular import with proactive.py
            from .proactive import queue_sandbox_outcome
            job = queue_sandbox_outcome(
                self.root,
                gap["followup_goal"],
                self.model,
                source="capability_improver",
                priority=10,   # low priority — owner work comes first
            )
            # Bind the gap identity into the job contract so the worker can
            # close the gap on the job's real terminal result (OJ09 pattern).
            try:
                job.acceptance_contract["gap_id"] = gap["id"]
                from .autonomy import OutcomeStore
                OutcomeStore(self.root).save(job)
            except Exception:
                pass  # best-effort binding; closure falls back to goal match
            return gap["id"]
        except Exception:
            # Roll the gap back to queued so it stays a candidate rather than
            # being stuck in_progress forever after a queueing failure.
            try:
                self.gaps.reset_to_queued(gap["id"])
            except Exception:
                pass
            return None

def gap_outcome_for_job(job) -> Optional[tuple]:
    """Map a terminal outcome job to a gap verdict.

    Returns (verdict, resolution) for genuinely terminal jobs, else None.
    Verdict mirrors the real job result; never fabricated.
    """
    terminal = {"tests_passed", "tests_failed", "error", "cancelled",
                "iteration_limit", "route_mismatch", "worker_failed"}
    result = str(getattr(job, "result", "") or "")
    status = str(getattr(job, "status", "") or "")
    if status == "completed" and result == "tests_passed":
        return ("resolved", f"job={job.job_id} tests_passed")
    if status in {"needs_review", "failed"} and result in terminal:
        # Failed attempts roll the gap back to queued so the improver can
        # retry later with fresh context; not resolved, not silently dropped.
        return ("retry", f"job={job.job_id} {result}")
    return None


def close_gap_feedback(root, job) -> Optional[str]:
    """Idempotently close the capability gap linked to a finished outcome job.

    Uses the job's real terminal result. Resolves the gap on tests_passed;
    rolls it back to queued on retryable failure. No-ops for jobs without a
    gap link (bound via acceptance_contract['gap_id'], with a followup-goal
    fallback for contracts saved before that binding existed).
    """
    root = Path(root).resolve()
    gap_id = ""
    try:
        gap_id = str((getattr(job, "acceptance_contract", {}) or {}).get("gap_id", "") or "")
    except Exception:
        gap_id = ""
    verdict = gap_outcome_for_job(job)
    if verdict is None:
        return None
    registry = CapabilityRegistry(root)
    outcome, resolution = verdict
    if outcome == "resolved":
        if not gap_id:
            # Fallback: find the single in_progress gap whose followup goal
            # matches this job's goal (legacy contracts without gap_id).
            goal = str(getattr(job, "goal", "") or "").strip().casefold()
            for gap in registry.get_queued_gaps(min_count=1):
                if gap.get("status") == "in_progress" and str(gap.get("followup_goal", "")).strip().casefold() == goal:
                    gap_id = gap["id"]
                    break
        if gap_id:
            registry.mark_resolved(gap_id, resolution)
            EventBus(root).emit(Event("capability.resolved", {"gap_id": gap_id, "resolution": resolution}, source="capability_improver"))
            return gap_id
        return None
    # retryable failure: roll back only the linked gap
    if gap_id:
        registry.reset_to_queued(gap_id)
        EventBus(root).emit(Event("capability.retry_queued", {"gap_id": gap_id, "result": resolution}, source="capability_improver"))
        return gap_id
    return None
