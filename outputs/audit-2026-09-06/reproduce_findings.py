"""Reproduce audit findings in disposable fixtures, without models or providers.

These probes describe defects; a true `*_accepted` or `*_bypassed` result is
evidence of an unresolved issue, not a successful safety check.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from ghost_agent import cli
from ghost_agent.approvals import ApprovalStore
from ghost_agent.extensions import ExtensionManager
from ghost_agent.kernel import GoalStore
from ghost_agent.orchestrator import Orchestrator, Task
from ghost_agent.promotion import PromotionService
from ghost_agent.runtime import EmergencyStop, ScheduledJob, Scheduler
from ghost_agent.schema import ActionResponse
from ghost_agent.self_repair import SelfRepairManager
from ghost_agent.tools.cmd_tools import run_cmd
from ghost_agent.tools.web_tools import source_credibility
from ghost_agent.utils.logging import JsonlLogger


def run_probes(root: Path) -> dict:
    observed = {}

    standard = root / "standard-promotion"
    sandbox = standard / ".ghost" / "outcomes" / "job" / "run" / "sandbox"
    sandbox.mkdir(parents=True)
    (sandbox / "app.py").write_text("value = 1\n", encoding="utf-8")
    service = PromotionService(standard)
    observed["standard_sandbox_preview"] = service.promotions.preview(sandbox)
    try:
        service.request("fixture_goal", sandbox)
    except ValueError as exc:
        observed["standard_sandbox_request_error"] = str(exc)

    project = root / "changed-approval"
    sandbox = project / "sandbox"
    sandbox.mkdir(parents=True)
    (sandbox / "app.py").write_text("value = 1\n", encoding="utf-8")
    service = PromotionService(project)
    tx_id, approval_id, _ = service.request("fixture_goal", sandbox)
    ApprovalStore(project).decide(approval_id, True, "Synthetic fixture approval")
    (sandbox / "app.py").write_text("value = 2\n", encoding="utf-8")
    service.commit(tx_id, approval_id)
    observed["changed_after_approval_promoted"] = (project / "app.py").read_text(encoding="utf-8").strip()
    observed["promotion_state_without_verifier"] = service.transactions.get(tx_id).state

    project = root / "extensions"
    extensions = ExtensionManager(project)
    extensions.scaffold("fixture")
    observed["extension_test_command_only_sets_status"] = extensions.mark_tested("fixture").status
    approval_id = extensions.request_enable("fixture")
    ApprovalStore(project).decide(approval_id, True, "Synthetic fixture approval")
    manifest = extensions.load("fixture")
    manifest.permissions["network"] = True
    extensions._save(manifest)
    manifest = extensions.enable("fixture", approval_id)
    observed["changed_extension_permissions_accepted"] = manifest.status == "enabled" and manifest.permissions["network"]

    repairs = SelfRepairManager(root / "repair")
    proposal = repairs.propose("fixture diagnosis", sandbox)
    observed["empty_self_repair_evidence_status"] = repairs.record_verification(proposal.proposal_id, [], []).status

    project = root / "plan"
    project.mkdir()
    agent = Orchestrator(project, model="scripted-no-model-calls")
    artifacts = project / ".ghost" / "outcomes" / "job" / "run"
    response = ActionResponse.model_validate({"actions": [{
        "tool": "write_file", "path": "plan.md", "content": "Implement then verify."
    }]})
    checkpoints = []
    original_write_state = agent._write_state

    def record_state(path, state):
        checkpoints.append(state.get("last_result"))
        original_write_state(path, state)

    with patch.object(agent, "_model_turn", return_value=(response, None)), patch.object(agent, "_write_state", side_effect=record_state):
        agent.run_task_with_roots(Task("fixture", "Plan", "Implement feature", {"iteration_limit": 1}, {}), project, artifacts)
    observed["valid_plan_reported_missing"] = "plan_missing" in checkpoints
    observed["plan_exists_in_sandbox"] = (artifacts / "sandbox" / "plan.md").is_file()
    observed["plan_exists_at_checked_artifact_path"] = (artifacts / "plan.md").is_file()

    EmergencyStop(project).engage("Synthetic fixture stop")
    with patch("ghost_agent.orchestrator.cmd_tools.run_cmd", return_value={"returncode": 0, "output": "mock only"}) as command:
        agent._run_verification_command("python -V", artifacts / "sandbox", JsonlLogger(artifacts / "verify.log"))
    observed["verification_reached_command_despite_stop"] = command.called

    queue_project = root / "queue"
    goals = GoalStore(queue_project)
    older_goal = goals.create("Older fixture job")
    scheduler = Scheduler(queue_project)
    scheduler.enqueue(ScheduledJob("older_fixture_job", older_goal.goal_id, priority=100))
    args = argparse.Namespace(goal=["New fixture job"], model="fixture-coder", research_model="fixture-research",
                              iterations=4, skip_research=True, dry_run=False, queue=False)
    with patch.object(Path, "cwd", return_value=queue_project), contextlib.redirect_stdout(io.StringIO()):
        cli._cmd_accomplish(args)
    observed["older_job_marked_running_without_execution"] = next(job["status"] for job in scheduler._load() if job["job_id"] == "older_fixture_job")

    observed["spoofed_publisher_score"] = source_credibility("https://nature.com.untrusted.invalid/article")["score"]
    if os.name == "nt":
        result = run_cmd("python -V & echo GHOST_AUDIT_CHAIN", ".", root)
        observed["shell_chain_executed"] = "GHOST_AUDIT_CHAIN" in result["output"]
    return observed


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="ghost-review-fixtures-") as scratch:
        result = run_probes(Path(scratch))
    print(json.dumps(result, indent=2))
