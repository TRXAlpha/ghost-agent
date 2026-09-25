import argparse
import json
import pytest
from pathlib import Path

from ghost_agent.autonomy import CapabilityRegistry, OutcomeStore
from ghost_agent.cli import _cmd_accomplish, _execute_queued_outcome
from ghost_agent.kernel import GoalStore
from ghost_agent.runtime import ScheduledJob, Scheduler
from ghost_agent.runtime import EmergencyStop
from ghost_agent.worker import OutcomeWorker


def test_accomplish_queue_persists_worker_claimable_outcome(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        goal=["Build a queued calculator"], model="local-coder", research_model="local-research",
        iterations=4, skip_research=True, dry_run=False, queue=True,
    )

    _cmd_accomplish(args)

    job = OutcomeStore(tmp_path).list()[0]
    assert job.skip_research is True
    assert Scheduler(tmp_path).next().job_id == job.job_id
    assert "Queued outcome job" in capsys.readouterr().out


def test_worker_executes_queued_skip_research_outcome_in_sandbox(tmp_path: Path, monkeypatch) -> None:
    goal = GoalStore(tmp_path).create("Build worker result")
    store = OutcomeStore(tmp_path)
    job = store.create("Build worker result")
    job.goal_id, job.skip_research, job.coding_model = goal.goal_id, True, "local"
    store.save(job)
    Scheduler(tmp_path).enqueue(ScheduledJob(job.job_id, goal.goal_id))

    class FakeOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_task_with_roots(self, _task, _workspace_root, artifacts_root) -> None:
            Path(artifacts_root).mkdir(parents=True, exist_ok=True)
            (Path(artifacts_root) / "state.json").write_text('{"last_result": "tests_passed"}', encoding="utf-8")

    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FakeOrchestrator)
    result = OutcomeWorker(tmp_path).run_next(lambda scheduled: _execute_queued_outcome(tmp_path, scheduled))

    assert result and result.status == "completed"
    assert OutcomeStore(tmp_path).load(job.job_id).status == "completed"


def test_worker_failure_records_capability_gap(tmp_path: Path, monkeypatch) -> None:
    goal = GoalStore(tmp_path).create("Build failing worker result")
    store = OutcomeStore(tmp_path)
    job = store.create("Build failing worker result")
    job.goal_id, job.skip_research, job.coding_model = goal.goal_id, True, "local"
    store.save(job)
    Scheduler(tmp_path).enqueue(ScheduledJob(job.job_id, goal.goal_id))

    class BrokenOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run_task_with_roots(self, *_args) -> None:
            raise RuntimeError("sandbox failed")

    monkeypatch.setattr("ghost_agent.cli.Orchestrator", BrokenOrchestrator)
    result = OutcomeWorker(tmp_path).run_next(lambda scheduled: _execute_queued_outcome(tmp_path, scheduled))

    assert result and result.status == "failed"
    assert CapabilityRegistry(tmp_path)._load()["gaps"][0]["result"] == "worker_failed"


def test_worker_respects_emergency_stop_before_research(tmp_path: Path) -> None:
    goal = GoalStore(tmp_path).create("Stopped job")
    store = OutcomeStore(tmp_path)
    job = store.create("Stopped job")
    job.goal_id = goal.goal_id
    store.save(job)
    EmergencyStop(tmp_path).engage("test")

    result = _execute_queued_outcome(tmp_path, ScheduledJob(job.job_id, goal.goal_id))

    assert result.status == "cancelled"


def test_real_outcome_resumes_preserved_sandbox_after_interruption(tmp_path, monkeypatch):
    goal = GoalStore(tmp_path).create("Create calculator with tests")
    store = OutcomeStore(tmp_path)
    job = store.create(goal.title)
    job.goal_id, job.skip_research, job.coding_model, job.iterations = goal.goal_id, True, "scripted", 5
    store.save(job)
    worker = OutcomeWorker(tmp_path)
    worker.scheduler.enqueue(ScheduledJob(job.job_id, goal.goal_id))
    interrupted = False

    def model(_model, messages, **kwargs):
        nonlocal interrupted
        prompt = messages[-1]["content"]
        if "Create the plan now" in prompt:
            actions = [{"tool": "write_file", "path": "plan.md", "content": "Implement and test calculator."}]
        elif "Implement the task" in prompt:
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt("simulate interrupted worker")
            actions = [
                {"tool": "write_file", "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"},
                {"tool": "write_file", "path": "test_calculator.py", "content": "from calculator import add\ndef test_add():\n    assert add(2, 3) == 5\n"},
            ]
        else:
            raise AssertionError(prompt)
        return json.dumps({"actions": actions})

    monkeypatch.setattr("ghost_agent.orchestrator.ollama_chat", model)
    handler = lambda scheduled: _execute_queued_outcome(tmp_path, scheduled)
    with pytest.raises(KeyboardInterrupt):
        worker.run_next(handler)
    run = store.path_for(job) / "run"
    sentinel = run / "sandbox" / "preserved.txt"
    sentinel.write_text("keep this work", encoding="utf-8")
    assert worker.run_next(handler) is None
    worker.scheduler.resume(job.job_id)
    result = worker.run_next(handler)
    assert result.status == "completed"
    assert sentinel.read_text() == "keep this work"
    state = json.loads((run / "state.json").read_text())
    assert state["last_result"] == "tests_passed"
    assert state["prior_iterations"] == 1
    assert not (tmp_path / "calculator.py").exists()


def test_foreground_accomplish_uses_worker_and_does_not_claim_older_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_goal = GoalStore(tmp_path).create("Older queued work", priority=100)
    scheduler = Scheduler(tmp_path)
    scheduler.enqueue(ScheduledJob("older", old_goal.goal_id, priority=100))

    class FixtureOrchestrator:
        def __init__(self, *args, **kwargs):
            pass

        def run_task_with_roots(self, task, workspace, artifacts):
            artifacts.mkdir(parents=True)
            (artifacts / "state.json").write_text('{"last_result": "tests_passed"}', encoding="utf-8")

    monkeypatch.setattr("ghost_agent.cli.Orchestrator", FixtureOrchestrator)
    _cmd_accomplish(argparse.Namespace(goal=["New task"], model="scripted", research_model="scripted",
        iterations=4, skip_research=True, dry_run=False, queue=False))
    jobs = {job.job_id: job for job in scheduler.list()}
    new_job = OutcomeStore(tmp_path).list()[0]
    assert jobs["older"].status == "queued"
    assert jobs[new_job.job_id].status == "completed"
    assert jobs[new_job.job_id].owner.startswith("worker_")
