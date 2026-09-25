from pathlib import Path

from ghost_agent.security import SecurityMonitor
from ghost_agent.provenance import ProvenanceLedger
from ghost_agent.utils.logging import JsonlLogger


def test_security_monitor_detects_secret_patterns_and_snapshots_in_root(tmp_path: Path) -> None:
    monitor = SecurityMonitor(tmp_path)
    source = tmp_path / "settings.py"
    source.write_text('TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"\n', encoding="utf-8")

    assert "telegram_token" in monitor.scan_text(source.read_text(encoding="utf-8"))
    assert "settings.py" in monitor.snapshot([source])


def test_security_monitor_detects_sandbox_escape(tmp_path: Path) -> None:
    monitor = SecurityMonitor(tmp_path)
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    assert not monitor.sandbox_escape(sandbox / "ok.txt", sandbox)
    assert monitor.sandbox_escape(tmp_path / "outside.txt", sandbox)


def test_security_monitor_records_minimal_audit_events(tmp_path: Path) -> None:
    monitor = SecurityMonitor(tmp_path)
    monitor.record_event("network.search", {"query": "test", "large": "x" * 1000})

    assert monitor.recent_events()[0]["kind"] == "network.search"
    assert len(monitor.recent_events()[0]["detail"]["large"]) == 500


def test_secret_patterns_are_redacted_at_persistence_boundaries(tmp_path):
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcde"
    private_key = "-----BEGIN PRIVATE KEY-----\nsynthetic-sensitive-body\n-----END PRIVATE KEY-----"
    payload = {"input": {"content": token}, "result": [private_key]}
    logger = JsonlLogger(tmp_path / "actions.log")
    ledger = ProvenanceLedger(tmp_path)
    monitor = SecurityMonitor(tmp_path)
    logger.log(payload)
    ledger.record("tool.action", payload)
    monitor.record_event("network.search", {"query": "x" * 485 + token})
    for path in (logger.path, ledger.path, monitor.events_path):
        content = path.read_text(encoding="utf-8")
        assert token not in content
        assert "123456789:" not in content  # Redact before truncation.
        assert "synthetic-sensitive-body" not in content
    assert payload["input"]["content"] == token  # No mutation of caller data.
