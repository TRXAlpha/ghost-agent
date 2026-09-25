import pytest

from ghost_agent.runtime import Doctor


@pytest.mark.parametrize("file,content,key", [
    ("scheduler.json", "{}", "scheduler"),
    ("scheduler.json", "[null]", "scheduler"),
    ("events.jsonl", '{"kind":"event","payload":[]}\n', "event_bus"),
    ("events.jsonl", '{"kind":', "event_bus"),
    ("extensions/demo/manifest.json", "{}", "extension_manifests"),
])
def test_existing_invalid_storage_is_not_healthy(tmp_path, file, content, key):
    path = tmp_path / ".ghost" / file
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert Doctor(tmp_path).check()[key] == "degraded"
    assert path.read_text() == content


def test_doctor_discloses_missing_isolation_without_initializing_project(tmp_path):
    result = Doctor(tmp_path).check()
    assert result["execution_isolation"] == "not_implemented_host_user_access"
    assert not (tmp_path / ".ghost").exists()
