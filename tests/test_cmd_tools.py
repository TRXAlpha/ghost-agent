from pathlib import Path
import os
import subprocess
import sys

import pytest

from ghost_agent.tools.cmd_tools import CmdError, run_cmd


def test_run_cmd_blocks_unlisted_exe(tmp_path: Path) -> None:
    with pytest.raises(CmdError):
        run_cmd("echo hello", cwd=".", root=tmp_path)


@pytest.mark.parametrize("command", [
    "python -V & echo GHOST_CHAIN", "python -V && echo GHOST_CHAIN",
    "python -V | echo GHOST_CHAIN", "python -V > redirected.txt",
    "python -V\necho GHOST_CHAIN", "pip install anything", "python -m pip install anything", "",
])
def test_shell_and_install_commands_are_not_dispatched(tmp_path, monkeypatch, command):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected command reached subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(CmdError):
        run_cmd(command, ".", tmp_path)


def test_empty_allowlist_denies_every_command(tmp_path):
    with pytest.raises(CmdError):
        run_cmd("python -V", ".", tmp_path, allowlist=[])


def test_workspace_executable_cannot_impersonate_python(tmp_path):
    fake = tmp_path / "python.exe"
    fake.write_text("not an executable", encoding="utf-8")
    with pytest.raises(CmdError):
        run_cmd(f'"{fake}" -V', ".", tmp_path)


def test_python_uses_the_harness_interpreter_without_shell(tmp_path, monkeypatch):
    captured = {}

    def record(command, **kwargs):
        captured.update(command=command, **kwargs)
        return {"returncode": 0, "output": "ok"}

    monkeypatch.setattr("ghost_agent.tools.cmd_tools.run_bounded", record)
    assert run_cmd("python -V", ".", tmp_path)["returncode"] == 0
    assert captured["command"] == [sys.executable, "-V"]
    assert captured["max_output"] == 4000


def test_python_arguments_keep_spaces_quotes_and_backslashes(tmp_path):
    # Use each platform's argument convention, just as the model prompt requires.
    import shlex
    args = ["python", "-c", "import sys; print(repr(sys.argv[1:]))", 'space and "quote"', r"C:\a b\c"]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    result = run_cmd(command, ".", tmp_path)
    assert result["returncode"] == 0
    assert result["output"].strip() == repr(args[3:])


def test_cwd_escape_is_rejected(tmp_path):
    with pytest.raises(CmdError):
        run_cmd("python -V", "..", tmp_path)


def test_commands_do_not_inherit_arbitrary_credentials(tmp_path, monkeypatch):
    from ghost_agent.tools.cmd_tools import join_command

    monkeypatch.setenv("GHOST_TEST_PRIVATE_VALUE", "synthetic-private-value")
    command = join_command(["python", "-c", "import os; print(os.getenv('GHOST_TEST_PRIVATE_VALUE', 'absent'))"])
    result = run_cmd(command, ".", tmp_path)
    assert result["returncode"] == 0
    assert result["output"].strip() == "absent"
