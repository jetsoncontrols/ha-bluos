"""Tone-controls and fixed-output switches for each BluOS node."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import BluOsAudioEntity, resolve_setting_id
from .coordinator import BluOsConfigEntry

# (spec key, translation_key, prefix-match?) — eq-switch is numbered per zone
SWITCHES: tuple[tuple[str, str, bool], ...] = (
    ("eq-switch-", "tone_controls", True),
    ("fixedVolume", "fixed_output", False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a switch per supported audio boolean-setting on each node."""
    entities: list[BluOsAudioSwitch] = []
    for coordinator in entry.runtime_data.coordinators:
        settings = coordinator.audio_settings
        if settings is None:
            continue
        for key, translation_key, prefix in SWITCHES:
            setting_id = resolve_setting_id(settings, key, prefix=prefix)
            if setting_id is not None:
                entities.append(
                    BluOsAudioSwitch(coordinator, setting_id, translation_key)
                )
    async_add_entities(entities)


class BluOsAudioSwitch(BluOsAudioEntity, SwitchEntity):
    """A BluOS boolean audio setting (tone controls, fixed output level)."""

    def __init__(self, coordinator, setting_id: str, translation_key: str) -> None:
        super().__init__(coordinator, setting_id, translation_key=translation_key)

    @property
    def is_on(self) -> bool | None:
        setting = self._setting
        if setting is None or setting.value is None:
            return None
        return setting.value == "ON"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write("ON")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write("OFF")
