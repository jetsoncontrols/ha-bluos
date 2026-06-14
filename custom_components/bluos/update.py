"""Firmware update entity (one per unit, on the primary node).

Uses Home Assistant's native update platform so the firmware shows up in
Settings -> Updates with an Install button. BluOS only reports *whether* an
update exists (via `/upgrade`), not the target version, so availability is
driven by that boolean rather than version-string comparison.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BluOsConfigEntry, BluOsCoordinator, chassis_identifier

# Shown as `latest_version` when an update exists but its version is unknown.
_UPDATE_AVAILABLE = "Update available"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the unit's firmware update entity (on the root/chassis device)."""
    unit = entry.runtime_data
    primary = unit.coordinators[0]
    root = (
        chassis_identifier(unit.chassis_mac) if unit.is_multi else (DOMAIN, primary.mac)
    )
    async_add_entities([BluOsFirmwareUpdate(primary, root)])


class BluOsFirmwareUpdate(CoordinatorEntity[BluOsCoordinator], UpdateEntity):
    """Native firmware update entity backed by `/upgrade`."""

    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(
        self, coordinator: BluOsCoordinator, device_id: tuple[str, str]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}-firmware"
        self._attr_device_info = DeviceInfo(identifiers={device_id})

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.data.sync.version

    @property
    def latest_version(self) -> str | None:
        if self.coordinator.firmware_update_available:
            return _UPDATE_AVAILABLE
        return self.installed_version

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # Target version is unknown; trust the /upgrade availability flag.
        return self.coordinator.firmware_update_available

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        await self.coordinator.client.install_firmware_update()
