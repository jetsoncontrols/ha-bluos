"""Replay-gain and output-mode selects for each BluOS node."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .audio import BluOsAudioEntity, resolve_setting_id
from .coordinator import BluOsConfigEntry

# (spec key, translation_key, prefix-match?)
SELECTS: tuple[tuple[str, str, bool], ...] = (
    ("replayGainMode", "replay_gain", False),
    ("channelMode", "output_mode", False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a select per supported audio list-setting on each node."""
    entities: list[BluOsAudioSelect] = []
    for coordinator in entry.runtime_data.coordinators:
        settings = coordinator.audio_settings
        if settings is None:
            continue
        for key, translation_key, prefix in SELECTS:
            setting_id = resolve_setting_id(settings, key, prefix=prefix)
            if setting_id is not None:
                entities.append(
                    BluOsAudioSelect(coordinator, setting_id, translation_key)
                )
    async_add_entities(entities)


class BluOsAudioSelect(BluOsAudioEntity, SelectEntity):
    """A BluOS list-type audio setting (replay-gain, output mode)."""

    def __init__(self, coordinator, setting_id: str, translation_key: str) -> None:
        super().__init__(coordinator, setting_id, translation_key=translation_key)

    @property
    def options(self) -> list[str]:
        setting = self._setting
        return [display for _name, display in setting.options] if setting else []

    @property
    def current_option(self) -> str | None:
        setting = self._setting
        if setting is None:
            return None
        return next(
            (display for name, display in setting.options if name == setting.value),
            None,
        )

    async def async_select_option(self, option: str) -> None:
        setting = self._setting
        if setting is None:
            return
        name = next((n for n, display in setting.options if display == option), None)
        if name is not None:
            await self._write(name)
