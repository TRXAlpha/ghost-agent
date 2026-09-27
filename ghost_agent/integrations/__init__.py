"""Ghost integration adapters: Telegram, Huawei, DeskSentinel.

All integrations are optional. Ghost runs without any of them.
Each adapter has a health_check() that returns its configuration status.
"""
from .telegram import health_check as telegram_health_check, is_configured as telegram_configured
from .huawei import health_check as huawei_health_check, is_configured as huawei_configured
from .desksentinel import health_check as sentinel_health_check, is_configured as sentinel_configured

# Compatibility: IntegrationPlanner was previously hosted in ghost_agent/integrations.py.
# It now lives here so existing consumers (`cli.py`, tests) keep importing it from
# `ghost_agent.integrations`.
from .planner import IntegrationBlueprint, IntegrationPlanner

__all__ = [
    "telegram_health_check", "telegram_configured",
    "huawei_health_check", "huawei_configured",
    "sentinel_health_check", "sentinel_configured",
    "IntegrationBlueprint", "IntegrationPlanner",
]
