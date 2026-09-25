from pathlib import Path

from ghost_agent.provenance import ProvenanceLedger


def test_provenance_ledger_preserves_parent_trace(tmp_path: Path) -> None:
    ledger = ProvenanceLedger(tmp_path)
    outcome = ledger.record("outcome", {"goal": "Build a parser"})
    action = ledger.record("tool.action", {"tool": "write_file"}, outcome.record_id)

    assert [item.record_id for item in ledger.trace(action.record_id)] == [outcome.record_id, action.record_id]
