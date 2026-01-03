from pathlib import Path

import pytest

from ghost_agent.tools.cmd_tools import CmdError, run_cmd


def test_run_cmd_blocks_unlisted_exe(tmp_path: Path) -> None:
    with pytest.raises(CmdError):
        run_cmd("echo hello", cwd=".", root=tmp_path)
