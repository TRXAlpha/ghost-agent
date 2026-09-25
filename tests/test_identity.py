from pathlib import Path
import pytest

from ghost_agent.identity import IdentityStore, UserProfile


def test_identity_profile_persists_preferences_and_deduplicated_prohibitions(tmp_path: Path) -> None:
    store = IdentityStore(tmp_path)
    store.save(UserProfile(display_name="Chris", preferences={"quiet_hours": "23:00-08:00"}))
    store.add_prohibition("Do not modify live files without approval")
    profile = store.add_prohibition("Do not modify live files without approval")

    assert profile.display_name == "Chris"
    assert profile.prohibitions == ["Do not modify live files without approval"]


@pytest.mark.parametrize("content", ["{broken", "[]", '{"prohibitions": "do not run commands"}',
                                    '{"prohibitions": [null]}', '{"policies": {}}'])
def test_invalid_identity_is_preserved_and_never_becomes_empty_policy(tmp_path, content):
    store = IdentityStore(tmp_path)
    store.path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="Identity policy"):
        store.add_prohibition("new rule")
    assert store.path.read_text(encoding="utf-8") == content
