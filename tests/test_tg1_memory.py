"""TG1: memory record fields, owner controls, and retention."""
import argparse
import json
import time

import pytest

from ghost_agent.context_manager import MemoryRecord, ValidatedMemoryStore


def make_store(tmp_path):
    return ValidatedMemoryStore(tmp_path)


def add(store, memory_id="m1", kind="working", content="c", **kwargs):
    record = MemoryRecord(memory_id=memory_id, kind=kind, content=content,
                          source="test", confidence=1.0, **kwargs)
    return store.remember(record)


def test_legacy_record_without_new_fields_loads(tmp_path):
    store = make_store(tmp_path)
    add(store)
    raw = json.loads((tmp_path / ".ghost" / "validated-memory.json").read_text())
    for key in ("subject", "sensitivity", "last_verified_at", "revisions"):
        raw[0].pop(key)
    (tmp_path / ".ghost" / "validated-memory.json").write_text(json.dumps(raw))
    reloaded = ValidatedMemoryStore(tmp_path)
    assert len(reloaded.active()) == 1
    record = reloaded.active()[0]
    assert record.subject == "" and record.sensitivity == "normal"
    assert record.last_verified_at is None and record.revisions == []


def test_new_kinds_accepted_and_sensitivity_validated(tmp_path):
    store = make_store(tmp_path)
    for kind in ("preference", "hypothesis", "feedback"):
        add(store, memory_id=kind, kind=kind)
    assert len(store.active()) == 3
    with pytest.raises(ValueError):
        add(make_store(tmp_path), memory_id="bad", sensitivity="topsecret")


def test_edit_content_keeps_revisions(tmp_path):
    store = make_store(tmp_path)
    add(store)
    for i in range(12):
        store.edit("m1", content=f"v{i}")
    record = store.edit("m1", content="final", subject="s", sensitivity="personal")
    assert record.content == "final" and record.subject == "s"
    assert record.sensitivity == "personal"
    assert len(record.revisions) == 10
    assert record.revisions[-1]["content"] == "v11"


def test_edit_confirm_verified_sets_timestamp(tmp_path):
    store = make_store(tmp_path)
    add(store)
    record = store.edit("m1", confirm_verified=True)
    assert record.last_verified_at is not None
    assert abs(record.last_verified_at - time.time()) < 5


def test_retracted_record_cannot_be_edited(tmp_path):
    store = make_store(tmp_path)
    add(store)
    store.retract("m1", "wrong")
    with pytest.raises(ValueError):
        store.edit("m1", content="nope")


def test_retention_hides_then_applies_through_purge(tmp_path):
    store = make_store(tmp_path)
    add(store, memory_id="old", created_at=time.time() - 30 * 86_400)
    add(store, memory_id="new")
    store.set_retention("working", 7)
    retention = json.loads(
        (tmp_path / ".ghost" / "memory_retention.json").read_text())
    assert retention == {"default_days": None, "by_kind": {"working": 7}}
    assert [r.memory_id for r in store.active()] == ["new"]
    # apply_retention goes through purge: a dependent is invalidated too.
    add(store, memory_id="dep", depends_on=["old"])
    purged = store.apply_retention()
    assert purged >= 1
    assert [r.memory_id for r in store.active()] == ["new"]
    remaining = json.loads(
        (tmp_path / ".ghost" / "validated-memory.json").read_text())
    assert not any(item["memory_id"] in ("old", "dep") for item in remaining)


def test_export_omits_secret_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    store = make_store(tmp_path)
    add(store, memory_id="open")
    add(store, memory_id="hush", sensitivity="secret")
    out = tmp_path / "export.json"
    monkeypatch.chdir(tmp_path)
    from ghost_agent import cli
    cli._cmd_memory(argparse.Namespace(memory_subcommand="export", out=str(out)))
    data = json.loads(out.read_text())
    assert [r["memory_id"] for r in data] == ["open"]
    cli._cmd_memory(argparse.Namespace(memory_subcommand="export", out=str(out)))
    assert "Refusing to overwrite" in capsys.readouterr().out


def test_list_hides_health_and_secret_without_flag(tmp_path, monkeypatch, capsys):
    store = make_store(tmp_path)
    add(store, memory_id="ok")
    add(store, memory_id="med", sensitivity="health")
    add(store, memory_id="hush", sensitivity="secret")
    monkeypatch.chdir(tmp_path)
    from ghost_agent import cli
    cli._cmd_memory(argparse.Namespace(memory_subcommand="list", kind=None,
                                       json=False, show_sensitive=False))
    listed = json.loads(capsys.readouterr().out)
    assert [r["memory_id"] for r in listed] == ["ok"]
    cli._cmd_memory(argparse.Namespace(memory_subcommand="list", kind=None,
                                       json=True, show_sensitive=True))
    listed = json.loads(capsys.readouterr().out)
    assert [r["memory_id"] for r in listed] == ["ok", "med", "hush"]
