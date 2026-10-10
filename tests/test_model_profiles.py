"""Tests for TH2: model profiles with explicit unknowns."""

from __future__ import annotations

import argparse
import json

from ghost_agent.cli import _cmd_model_profile
from ghost_agent.models.profiles import ModelProfile, ModelProfileStore


def test_unknown_model_gives_all_none_values(tmp_path) -> None:
    store = ModelProfileStore(tmp_path)
    profile = store.get("never-heard-of-it")
    assert profile.model == "never-heard-of-it"
    assert profile.context_tokens is None
    assert profile.max_output_tokens is None
    assert profile.supports_tools is None
    assert profile.cost_per_1k_input is None
    assert profile.cost_per_1k_output is None


def test_estimated_cost_is_none_with_unknown_price(tmp_path) -> None:
    store = ModelProfileStore(tmp_path)
    profile = store.get("some-model")
    assert profile.estimated_cost(1000, 1000) is None
    profile.cost_per_1k_input = 0.5
    assert profile.estimated_cost(1000, 1000) is None
    profile.cost_per_1k_output = 1.5
    assert profile.estimated_cost(1000, 1000) == 2.0


def test_probe_parses_fixture_show_response(tmp_path, monkeypatch) -> None:
    """A /api/show fixture response is parsed without guessing missing values."""
    import ghost_agent.models.profiles as profiles_module

    def fake_post(url, payload=None, timeout=None, **kwargs):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "model_info": {
                        "general.architecture": "llama",
                        "llama.context_length": 8192,
                    }
                }

        return FakeResponse()

    monkeypatch.setattr(profiles_module, "_post", fake_post)

    store = ModelProfileStore(tmp_path)
    profile = store.probe_ollama("fixture-model", "http://127.0.0.1:11434")
    assert profile.context_tokens == 8192
    assert profile.source == "probe"
    # Not probed -> stays unknown, not guessed.
    assert profile.cost_per_1k_input is None
    assert profile.max_output_tokens is None
    assert profile.supports_tools is None


def test_probe_does_not_guess_when_show_has_no_context_length(tmp_path, monkeypatch) -> None:
    import ghost_agent.models.profiles as profiles_module

    def fake_post(url, payload=None, timeout=None, **kwargs):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"model_info": {"general.architecture": "llama"}}

        return FakeResponse()

    monkeypatch.setattr(profiles_module, "_post", fake_post)
    store = ModelProfileStore(tmp_path)
    profile = store.probe_ollama("opaque-model", "http://127.0.0.1:11434")
    assert profile.context_tokens is None
    assert profile.source == "probe"


def test_set_show_round_trip(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    store_path = tmp_path / ".ghost" / "model_profiles.json"
    _cmd_model_profile(argparse.Namespace(
        model_profile_action="set", model="m1",
        context=4096, max_output=512, cost_in=0.25, cost_out=0.75))
    assert store_path.exists()
    _cmd_model_profile(argparse.Namespace(
        model_profile_action="show", model="m1",
        context=None, max_output=None, cost_in=None, cost_out=None))
    out = capsys.readouterr().out
    assert "context_tokens: 4096" in out
    assert "max_output_tokens: 512" in out
    assert "cost_per_1k_input: 0.25" in out
    assert "cost_per_1k_output: 0.75" in out

    # Round trip through the store itself.
    store = ModelProfileStore(tmp_path)
    assert store.get("m1").context_tokens == 4096
    assert store.get("m1").cost_per_1k_output == 0.75


def test_show_unknown_model_prints_unknown_costs(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _cmd_model_profile(argparse.Namespace(
        model_profile_action="show", model="ghost-in-the-machine",
        context=None, max_output=None, cost_in=None, cost_out=None))
    out = capsys.readouterr().out
    assert "cost_per_1k_input: unknown" in out
    assert "cost_per_1k_output: unknown" in out
    assert "context_tokens: None" in out
