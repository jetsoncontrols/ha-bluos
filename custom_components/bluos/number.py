"""Treble and bass number entities for each BluOS node.

Only present/available when the node exposes them and Tone Controls is on
(the device gates Treble/Bass on `eq-switch-1`; the base entity enforces it).
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import BluOsAudioEntity, resolve_setting_id
from .coordinator import BluOsConfigEntry

# (spec key, translation_key, prefix-match?) — eq-treble/bass numbered per zone
NUMBERS: tuple[tuple[str, str, bool], ...] = (
    ("eq-treble-", "treble", True),
    ("eq-bass-", "bass", True),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a number per supported audio range-setting on each node."""
    entities: list[BluOsAudioNumber] = []
    for coordinator in entry.runtime_data.coordinators:
        settings = coordinator.audio_settings
        if settings is None:
            continue
        for key, translation_key, prefix in NUMBERS:
            setting_id = resolve_setting_id(settings, key, prefix=prefix)
            if setting_id is not None:
                entities.append(
                    BluOsAudioNumber(coordinator, setting_id, translation_key)
                )
    async_add_entities(entities)


class BluOsAudioNumber(BluOsAudioEntity, NumberEntity):
    """A BluOS range audio setting (treble, bass), in the device's own units."""

    def __init__(self, coordinator, setting_id: str, translation_key: str) -> None:
        super().__init__(coordinator, setting_id, translation_key=translation_key)
        setting = self._setting
        if setting is not None:
            if setting.minimum is not None:
                self._attr_native_min_value = setting.minimum
            if setting.maximum is not None:
                self._attr_native_max_value = setting.maximum
            if setting.step is not None:
                self._attr_native_step = setting.step
            if setting.units:
                self._attr_native_unit_of_measurement = setting.units

    @property
    def native_value(self) -> float | None:
        setting = self._setting
        if setting is None or setting.value is None:
            return None
        try:
            return float(setting.value)
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        # Format like the device: integers without a trailing ".0", halves as-is.
        await self._write(f"{value:g}")
