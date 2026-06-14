"""Per-node audio-setting entities (switch / number / select)."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bluos.api import AudioSettings
from custom_components.bluos.audio import resolve_setting_id
from custom_components.bluos.const import DOMAIN

from .helpers import load_fixture
from .test_init import BASE_MAC, _setup

ZONE1 = BASE_MAC  # primary node's mac == unique-id stem


def _audio() -> AudioSettings:
    return AudioSettings.from_xml(load_fixture("settings_audio.xml"))


def test_resolve_setting_id_exact_and_prefix():
    settings = _audio()
    # exact match
    assert resolve_setting_id(settings, "replayGainMode") == "replayGainMode"
    assert resolve_setting_id(settings, "nope") is None
    # prefix match picks the per-zone-numbered id
    assert resolve_setting_id(settings, "eq-switch-", prefix=True) == "eq-switch-1"
    assert resolve_setting_id(settings, "eq-treble-", prefix=True) == "eq-treble-1"
    assert resolve_setting_id(settings, "zzz-", prefix=True) is None


async def test_audio_entities_created_per_node(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    # six controls expected on each of the four nodes.
    expect = [
        ("switch", "eq-switch-1"),
        ("switch", "fixedVolume"),
        ("select", "replayGainMode"),
        ("select", "channelMode"),
        ("number", "eq-treble-1"),
        ("number", "eq-bass-1"),
    ]
    for platform, setting_id in expect:
        entity_id = reg.async_get_entity_id(platform, DOMAIN, f"{ZONE1}-{setting_id}")
        assert entity_id
        # audio controls are configuration entities
        assert reg.async_get(entity_id).entity_category == EntityCategory.CONFIG


async def test_select_state_and_write_mapping(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    entity_id = reg.async_get_entity_id("select", DOMAIN, f"{ZONE1}-replayGainMode")

    # device value "none" is shown as its display name.
    assert hass.states.get(entity_id).state == "Disabled"

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id, "option": "Track gain"},
        blocking=True,
    )
    coordinator = hass.data[DOMAIN].coordinators_by_addr[("192.0.2.10", 11000)]
    # display "Track gain" -> machine "track"; replay-gain posts to /audiomodes.
    assert (
        "set_audio_setting",
        ("replayGainMode", "track", "/audiomodes"),
    ) in coordinator.client.calls


async def test_switch_write_uses_alsa_setting(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    entity_id = reg.async_get_entity_id("switch", DOMAIN, f"{ZONE1}-eq-switch-1")

    assert hass.states.get(entity_id).state == "off"  # fixture: OFF
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id}, blocking=True
    )
    coordinator = hass.data[DOMAIN].coordinators_by_addr[("192.0.2.10", 11000)]
    assert (
        "set_audio_setting",
        ("eq-switch-1", "ON", "/alsa_setting"),
    ) in coordinator.client.calls


async def test_treble_is_gated_on_tone_controls(hass: HomeAssistant):
    """Treble depends on eq-switch-1=ON; with Tone Controls off it is unavailable."""
    await _setup(hass)
    reg = er.async_get(hass)
    treble = reg.async_get_entity_id("number", DOMAIN, f"{ZONE1}-eq-treble-1")
    state = hass.states.get(treble)
    assert state.state == "unavailable"
    # bounds still come through from the device description.
    assert state.attributes["min"] == -6.0
    assert state.attributes["max"] == 6.0
    assert state.attributes["step"] == 0.5
