import argparse

from ghost_agent.cli import _cmd_device


def test_cli_registers_declared_device(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    _cmd_device(argparse.Namespace(action="register", device_id="desktop", name="Precision",
                                   kind="computer", capability=["inference.run"]))

    assert '"device_id": "desktop"' in capsys.readouterr().out
