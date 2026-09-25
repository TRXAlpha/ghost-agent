"""Reviewable integration blueprints; never create accounts or call providers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .extensions import ExtensionManager


@dataclass
class IntegrationBlueprint:
    name: str
    extension_name: str
    setup_steps: list[str]
    secret_references: list[dict[str, str]]
    permissions: dict[str, Any]
    rate_limit: str
    health_check: str
    revoke_steps: list[str]
    webhook_verification: str = "not applicable"


class IntegrationPlanner:
    """Produce safe setup instructions and a local extension proposal."""

    def __init__(self, project_root: Path) -> None:
        self.root = Path(project_root)
        self.extensions = ExtensionManager(self.root)

    def blueprint(self, name: str) -> IntegrationBlueprint:
        normalized = name.strip().lower().replace("_", "-")
        if normalized != "telegram":
            raise ValueError(f"No built-in integration blueprint for: {name}")
        return IntegrationBlueprint(
            name="telegram",
            extension_name="telegram-bridge",
            setup_steps=[
                "Create a bot with Telegram's BotFather using your own Telegram account.",
                "Store its token in your environment as TELEGRAM_BOT_TOKEN; do not paste it into Ghost chat or source files.",
                "Send the bot a test message, then record the approved recipient/chat identifiers in extension configuration.",
                "Review the extension permissions, run its tests, and approve enablement explicitly.",
            ],
            secret_references=[{
                "name": "telegram.bot", "environment_variable": "TELEGRAM_BOT_TOKEN",
                "purpose": "Authenticated Bot API requests",
            }],
            permissions={"network": True, "filesystem": [], "secrets": ["telegram.bot"],
                         "side_effects": ["sends_messages"]},
            rate_limit="10 messages/minute unless a stricter user policy is configured",
            health_check="Call getMe with the configured token and verify the approved chat configuration without sending a message.",
            revoke_steps=["Revoke the token through BotFather.", "Remove TELEGRAM_BOT_TOKEN from the environment.",
                          "Disable the extension in Ghost."],
        )

    def propose(self, name: str) -> IntegrationBlueprint:
        blueprint = self.blueprint(name)
        manifest = self.extensions.propose(blueprint.extension_name, f"Approved {blueprint.name} integration")
        manifest.permissions.update(blueprint.permissions)
        self.extensions._save(manifest)
        for secret in blueprint.secret_references:
            self.extensions.declare_secret(blueprint.extension_name, secret["name"],
                                           secret["environment_variable"], secret["purpose"])
        return blueprint

    @staticmethod
    def as_dict(blueprint: IntegrationBlueprint) -> dict[str, Any]:
        return asdict(blueprint)
