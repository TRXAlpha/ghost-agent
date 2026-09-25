from pathlib import Path

from ghost_agent.autonomy import CapabilityRegistry, OutcomeStore


def test_outcome_job_persists_research_and_handoff(tmp_path: Path) -> None:
    store = OutcomeStore(tmp_path)
    job = store.create("Build a small game")
    report = "# Research\nEvidence-backed context."
    research_path = store.write_research(job, report)
    payload = store.make_task_payload(job, report, 12)

    loaded = store.load(job.job_id)
    assert loaded is not None
    assert research_path.read_text(encoding="utf-8") == report
    assert payload["constraints"]["iteration_limit"] == 12
    assert "Build a small game" in payload["goal"]
    assert payload["context"]["research_dossier"] == report


def test_capability_registry_queues_one_deduplicated_workaround(tmp_path: Path) -> None:
    registry = CapabilityRegistry(tmp_path)
    first = registry.record_gap("Build a desktop app", "iteration_limit", "tests missing")
    second = registry.record_gap("Build a desktop app", "iteration_limit", "tests missing")

    assert first["id"] == second["id"]
    assert second["count"] == 2
    data = __import__("json").loads(registry.path.read_text(encoding="utf-8"))
    assert len(data["queue"]) == 1
    assert "sandboxed workaround" in data["queue"][0]["goal"]
