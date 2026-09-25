import json

from ghost_agent.mcp_addons import MCPAddonManager


def test_mcp_addon_scaffold_declares_nonexecutable_tools(tmp_path) -> None:
    directory = MCPAddonManager(tmp_path).scaffold("Logs", "Read service logs", ["logs.read"])
    descriptor = json.loads((directory / "mcp.json").read_text(encoding="utf-8"))

    assert descriptor["status"] == "scaffolded_not_executable"
    assert descriptor["tools"][0]["requires_approval"] is True
