"""Setup and entity tests for the BluOS integration."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bluos.const import CONF_HOST, CONF_MAC, CONF_NODES, DOMAIN
from custom_components.bluos.media_player import chassis_identifier

from .helpers import FakeClient

BASE_MAC = "90:56:82:0a:23:7c"
ENTRY_DATA = {
    CONF_HOST: "192.168.1.60",
    CONF_MAC: BASE_MAC,
    CONF_NODES: [
        {"port": 11000, "mac": "90:56:82:0A:23:7C", "name": "BluOS Zone 1"},
        {"port": 11010, "mac": "90:56:82:0A:23:7C:11010", "name": "BluOS Zone 2"},
        {"port": 11020, "mac": "90:56:82:0A:23:7C:11020", "name": "Kitchen Speakers"},
        {"port": 11030, "mac": "90:56:82:0A:23:7C:11030", "name": "CI580-237C-4"},
    ],
}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=BASE_MAC, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with (
        patch("custom_components.bluos.BluOsClient", FakeClient),
        patch("custom_components.bluos.coordinator.BluOsCoordinator.async_start_loops"),
        patch("custom_components.bluos.LsdpDiscovery.async_start", return_value=False),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_creates_entity_per_zone(hass: HomeAssistant):
    await _setup(hass)
    ent_reg = er.async_get(hass)
    macs = [n["mac"].lower() for n in ENTRY_DATA[CONF_NODES]]
    entity_ids = [ent_reg.async_get_entity_id("media_player", DOMAIN, m) for m in macs]
    assert all(entity_ids)
    for entity_id in entity_ids:
        assert hass.states.get(entity_id).state == "idle"


async def test_volume_features_only_on_variable_zone(hass: HomeAssistant):
    await _setup(hass)
    ent_reg = er.async_get(hass)

    kitchen = ent_reg.async_get_entity_id(
        "media_player", DOMAIN, "90:56:82:0a:23:7c:11020"
    )
    zone1 = ent_reg.async_get_entity_id("media_player", DOMAIN, BASE_MAC)

    kitchen_feats = hass.states.get(kitchen).attributes["supported_features"]
    zone1_feats = hass.states.get(zone1).attributes["supported_features"]
    assert kitchen_feats & MediaPlayerEntityFeature.VOLUME_SET
    assert not (zone1_feats & MediaPlayerEntityFeature.VOLUME_SET)
    # Transport + grouping are present on every node.
    assert zone1_feats & MediaPlayerEntityFeature.PLAY
    assert zone1_feats & MediaPlayerEntityFeature.GROUPING


async def test_now_playing_metadata(hass: HomeAssistant):
    await _setup(hass)
    ent_reg = er.async_get(hass)
    # Zone 2 fixture carries a loaded track.
    zone2 = ent_reg.async_get_entity_id(
        "media_player", DOMAIN, "90:56:82:0a:23:7c:11010"
    )
    attrs = hass.states.get(zone2).attributes
    assert attrs["media_title"] == "13 Beaches"
    assert attrs["media_artist"] == "Lana Del Rey"
    assert attrs["media_album_name"] == "Lust for Life"


async def test_device_hierarchy_via_chassis(hass: HomeAssistant):
    await _setup(hass)
    dev_reg = dr.async_get(hass)

    chassis = dev_reg.async_get_device(identifiers={chassis_identifier(BASE_MAC)})
    assert chassis is not None
    assert chassis.name == "NAD CI580"
    assert chassis.configuration_url == "http://192.168.1.60"

    zone1 = dev_reg.async_get_device(identifiers={(DOMAIN, BASE_MAC)})
    assert zone1 is not None
    assert zone1.via_device_id == chassis.id
    assert zone1.configuration_url == "http://192.168.1.60"


async def test_transport_command_calls_client(hass: HomeAssistant):
    await _setup(hass)
    ent_reg = er.async_get(hass)
    zone1 = ent_reg.async_get_entity_id("media_player", DOMAIN, BASE_MAC)

    await hass.services.async_call(
        "media_player", "media_play", {"entity_id": zone1}, blocking=True
    )
    coordinator = hass.data[DOMAIN].coordinators_by_addr[("192.168.1.60", 11000)]
    assert ("play", ()) in coordinator.client.calls


async def test_join_calls_add_slave(hass: HomeAssistant):
    await _setup(hass)
    ent_reg = er.async_get(hass)
    zone1 = ent_reg.async_get_entity_id("media_player", DOMAIN, BASE_MAC)
    kitchen = ent_reg.async_get_entity_id(
        "media_player", DOMAIN, "90:56:82:0a:23:7c:11020"
    )
    await hass.services.async_call(
        "media_player",
        "join",
        {"entity_id": zone1, "group_members": [kitchen]},
        blocking=True,
    )
    data = hass.data[DOMAIN]
    primary = data.coordinators_by_addr[("192.168.1.60", 11000)]
    assert ("add_slave", ("192.168.1.60", 11020)) in primary.client.calls


async def test_unjoin_uses_primary_slave_list_when_secondary_is_stale(
    hass: HomeAssistant,
):
    """Unjoin must work even if the secondary's own master hasn't synced yet."""
    await _setup(hass)
    ent_reg = er.async_get(hass)
    kitchen = ent_reg.async_get_entity_id(
        "media_player", DOMAIN, "90:56:82:0a:23:7c:11020"
    )
    data = hass.data[DOMAIN]
    primary = data.coordinators_by_addr[("192.168.1.60", 11000)]
    secondary = data.coordinators_by_addr[("192.168.1.60", 11020)]

    # Group exists from the primary's view; the secondary hasn't caught up.
    primary.data.sync.slaves = [("192.168.1.60", 11020)]
    secondary.data.sync.master = None

    await hass.services.async_call(
        "media_player", "unjoin", {"entity_id": kitchen}, blocking=True
    )
    assert ("remove_slave", ("192.168.1.60", 11020)) in primary.client.calls


async def test_node_unavailable_when_uncontactable(hass: HomeAssistant):
    """A node whose long-polls keep failing must become unavailable, then
    recover when contact is re-established."""
    from custom_components.bluos.api import BluOsConnectionError
    from custom_components.bluos.const import MAX_FAILURES

    await _setup(hass)
    ent_reg = er.async_get(hass)
    zone1 = ent_reg.async_get_entity_id("media_player", DOMAIN, BASE_MAC)
    coordinator = hass.data[DOMAIN].coordinators_by_addr[("192.168.1.60", 11000)]

    assert hass.states.get(zone1).state != "unavailable"

    for _ in range(MAX_FAILURES):
        coordinator._register_failure("status", BluOsConnectionError("unreachable"))
    await hass.async_block_till_done()
    assert coordinator.last_update_success is False
    assert hass.states.get(zone1).state == "unavailable"

    # A later successful poll restores availability.
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert hass.states.get(zone1).state != "unavailable"


async def test_unload(hass: HomeAssistant):
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.data[DOMAIN].coordinators_by_mac
