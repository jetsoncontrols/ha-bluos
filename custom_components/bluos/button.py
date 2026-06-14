"""Maintenance buttons: reboot (per node), reindex + install firmware (unit)."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BluOsConfigEntry, BluOsCoordinator, chassis_identifier


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Unit-wide maintenance buttons on the root device.

    Reboot and reindex are single buttons issued via the primary node and shown
    on the chassis/root device on multi-zone units (or the lone node device when
    standalone). The doorbell is only offered when the player actually exposes
    the feature (custom-install units like the CI580; standalone speakers omit
    it), to avoid a dead button.
    """
    unit = entry.runtime_data
    primary = unit.coordinators[0]
    root = (
        chassis_identifier(unit.chassis_mac) if unit.is_multi else (DOMAIN, primary.mac)
    )
    entities: list[_BluOsButton] = [
        BluOsRebootButton(primary, device_id=root),
        BluOsReindexButton(primary, device_id=root),
    ]
    if primary.doorbell_supported:
        entities.append(BluOsDoorbellButton(primary, device_id=root))
    async_add_entities(entities)


class _BluOsButton(CoordinatorEntity[BluOsCoordinator], ButtonEntity):
    """Base for a stateless maintenance button bound to a node's device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: BluOsCoordinator,
        translation_key: str,
        *,
        key: str,
        icon: str | None = None,
        device_id: tuple[str, str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}-{key}"
        self._attr_translation_key = translation_key
        if icon:
            self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={device_id or (DOMAIN, coordinator.mac)}
        )


class BluOsDoorbellButton(_BluOsButton):
    """Ring the unit's doorbell chime (unit-wide; a functional control)."""

    _attr_entity_category = None  # not configuration — a user-facing action

    def __init__(
        self, coordinator: BluOsCoordinator, *, device_id: tuple[str, str] | None = None
    ) -> None:
        super().__init__(
            coordinator,
            "doorbell",
            key="doorbell",
            icon="mdi:bell-ring",
            device_id=device_id,
        )

    async def async_press(self) -> None:
        await self.coordinator.client.doorbell()


class BluOsRebootButton(_BluOsButton):
    """Soft-reboot the unit."""

    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self, coordinator: BluOsCoordinator, *, device_id: tuple[str, str] | None = None
    ) -> None:
        super().__init__(coordinator, "reboot", key="reboot", device_id=device_id)

    async def async_press(self) -> None:
        await self.coordinator.client.reboot()


class BluOsReindexButton(_BluOsButton):
    """Rebuild the music-library index."""

    def __init__(
        self, coordinator: BluOsCoordinator, *, device_id: tuple[str, str] | None = None
    ) -> None:
        super().__init__(
            coordinator,
            "reindex",
            key="reindex",
            icon="mdi:database-refresh",
            device_id=device_id,
        )

    async def async_press(self) -> None:
        await self.coordinator.client.reindex()
