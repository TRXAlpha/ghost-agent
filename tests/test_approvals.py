from pathlib import Path

import pytest

from ghost_agent.approvals import ApprovalStore


def test_approval_request_decision_is_persistent_and_one_way(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    request = store.request("telegram.send", "Send weekly summary", {"recipient": "approved"},
                            "high", "Message cannot be unsent")

    assert store.pending()[0].approval_id == request.approval_id
    decided = store.decide(request.approval_id, True, "approved for this account")

    assert decided.status == "approved"
    assert not store.pending()
    with pytest.raises(ValueError):
        store.decide(request.approval_id, False)


@pytest.mark.parametrize("data", ["{broken", "{}", "[null]", "[{}]"])
def test_corrupt_approval_state_is_preserved(tmp_path, data):
    store = ApprovalStore(tmp_path)
    store.path.write_text(data, encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        store.request("filesystem.promote", "test", {}, "high", "undo")
    assert store.path.read_text(encoding="utf-8") == data
