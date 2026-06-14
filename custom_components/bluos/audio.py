"""Shared base for per-node audio-setting entities (switch/number/select).

Each entity is a thin view onto one `AudioSetting` in its node's cached
`/Settings?id=audio` menu. All of them bind to the SAME per-node coordinator
and the SAME device (`(DOMAIN, mac)`) as the media_player — no extra client,
coordinator or device is created.
"""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AudioSetting, AudioSettings
from .const import DOMAIN
from .coordinator import BluOsCoordinator


def resolve_setting_id(
    settings: AudioSettings, key: str, *, prefix: bool = False
) -> str | None:
    """Resolve a spec key to the node's actual setting id.

    The tone EQ controls are numbered per zone (`eq-switch-1`, `eq-switch-2`,
    …) so they are matched by prefix; everything else is an exact id.
    """
    if prefix:
        return next((sid for sid in settings.by_id if sid.startswith(key)), None)
    return key if settings.get(key) is not None else None


class BluOsAudioEntity(CoordinatorEntity[BluOsCoordinator]):
    """Base for an entity backed by one `/Settings?id=audio` control."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: BluOsCoordinator,
        setting_id: str,
        *,
        translation_key: str,
        icon: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._setting_id = setting_id
        self._attr_unique_id = f"{coordinator.mac}-{setting_id}"
        self._attr_translation_key = translation_key
        if icon:
            self._attr_icon = icon
        # Same identifier as the media_player -> attaches to the node's device.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.mac)})

    @property
    def _setting(self) -> AudioSetting | None:
        settings = self.coordinator.audio_settings
        return settings.get(self._setting_id) if settings else None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        setting = self._setting
        if setting is None:
            return False
        # Respect the device's own gating (e.g. Treble needs Tone Controls = ON,
        # Volume limits need fixed output = OFF).
        if setting.depends_on is not None:
            dep_id, dep_value = setting.depends_on
            other = self.coordinator.audio_settings.get(dep_id)
            if other is None or other.value != dep_value:
                return False
        return True

    async def _write(self, value: str) -> None:
        """Write a new value for this setting, then refresh the menu."""
        setting = self._setting
        if setting is None:
            return
        await self.coordinator.client.set_audio_setting(
            setting.id, value, url=setting.url
        )
        await self.coordinator.async_refresh_audio_settings()
