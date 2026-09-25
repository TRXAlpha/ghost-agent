"""Scaffold reviewable local MCP add-ons without installing or executing them."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .extensions import ExtensionManager


class MCPAddonManager:
    """Create an extension-owned MCP skeleton with explicit tool declarations."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.extensions = ExtensionManager(self.root)

    @staticmethod
    def _name(name: str) -> str:
        safe = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        if not safe:
            raise ValueError("MCP add-on name must contain letters or digits")
        return safe

    def scaffold(self, name: str, goal: str, tools: list[str]) -> Path:
        safe = self._name(name)
        if not tools or any(not re.fullmatch(r"[a-z][a-z0-9_.-]*", tool) for tool in tools):
            raise ValueError("At least one lowercase dotted tool capability is required")
        extension_name = f"mcp-{safe}"
        manifest = self.extensions.scaffold(extension_name, goal)
        directory = self.extensions.directory(extension_name)
        (directory / "mcp.json").write_text(json.dumps({
            "name": safe,
            "transport": "stdio",
            "status": "scaffolded_not_executable",
            "extension": manifest.name,
            "tools": [{"name": tool, "requires_approval": True} for tool in sorted(set(tools))],
            "security": {"secrets_from_environment_only": True, "network": False,
                         "installation_requires_approval": True},
        }, indent=2), encoding="utf-8")
        (directory / "adapter" / "mcp_server.py").write_text(
            '"""Implement a stdio MCP server here; no tool is enabled by this scaffold."""\n', encoding="utf-8"
        )
        return directory
