from pathlib import Path

from ghost_agent.integrations import IntegrationPlanner
from ghost_agent.secrets import SecretStore


def test_telegram_plan_proposes_extension_without_storing_token(tmp_path: Path) -> None:
    planner = IntegrationPlanner(tmp_path)
    blueprint = planner.propose("telegram")

    assert blueprint.extension_name == "telegram-bridge"
    assert SecretStore(tmp_path).get("telegram.bot").environment_variable == "TELEGRAM_BOT_TOKEN"
    assert planner.extensions.load("telegram-bridge").status == "proposed"
