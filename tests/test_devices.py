from pathlib import Path

from ghost_agent.devices import DeviceRegistry


def test_device_registry_updates_world_and_routes_capabilities(tmp_path: Path) -> None:
    registry = DeviceRegistry(tmp_path)
    registry.register("desktop", "Precision", "computer", ["inference.run", "filesystem.read"],
                      {"gpu": True})
    registry.register("pi", "Pi", "computer", ["sensor.read"], {"always_on": True})

    assert registry.eligible("inference.run", {"gpu": True})[0].device_id == "desktop"
    assert registry.world.get("device.desktop").state["name"] == "Precision"
