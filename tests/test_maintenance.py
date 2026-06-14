"""Maintenance entities: reboot/reindex buttons + firmware update entity."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.bluos.const import DOMAIN
from custom_components.bluos.coordinator import chassis_identifier

from .test_init import BASE_MAC, _setup

ZONE2_MAC = "aa:bb:cc:00:11:22:11010"


def _primary(hass: HomeAssistant):
    return hass.data[DOMAIN].coordinators_by_addr[("192.0.2.10", 11000)]


async def test_unit_wide_entities_live_on_root_device(hass: HomeAssistant):
    """Reboot/reindex/doorbell/firmware are single and on the chassis device."""
    await _setup(hass)
    reg = er.async_get(hass)
    chassis = dr.async_get(hass).async_get_device(
        identifiers={chassis_identifier(BASE_MAC)}
    )
    assert chassis is not None

    for domain, key in [
        ("button", "reboot"),
        ("button", "reindex"),
        ("button", "doorbell"),
        ("update", "firmware"),
    ]:
        entity_id = reg.async_get_entity_id(domain, DOMAIN, f"{BASE_MAC}-{key}")
        assert entity_id, f"{domain}.{key} missing"
        assert reg.async_get(entity_id).device_id == chassis.id, f"{key} not on root"

    # No per-node reboot anymore — it is a single unit reboot.
    assert reg.async_get_entity_id("button", DOMAIN, f"{ZONE2_MAC}-reboot") is None


async def test_doorbell_button(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    # The doorbell rings unit-wide, so it lives once on the primary (not per node)
    # and is a primary control (no entity_category).
    doorbell = reg.async_get_entity_id("button", DOMAIN, f"{BASE_MAC}-doorbell")
    assert doorbell
    assert reg.async_get(doorbell).entity_category is None
    assert reg.async_get_entity_id("button", DOMAIN, f"{ZONE2_MAC}-doorbell") is None

    await hass.services.async_call(
        "button", "press", {"entity_id": doorbell}, blocking=True
    )
    assert ("doorbell", ()) in _primary(hass).client.calls


async def test_reboot_and_reindex_call_client(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    reboot = reg.async_get_entity_id("button", DOMAIN, f"{BASE_MAC}-reboot")
    reindex = reg.async_get_entity_id("button", DOMAIN, f"{BASE_MAC}-reindex")

    await hass.services.async_call(
        "button", "press", {"entity_id": reboot}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": reindex}, blocking=True
    )
    calls = _primary(hass).client.calls
    assert ("reboot", ()) in calls
    assert ("reindex", ()) in calls


async def test_firmware_update_entity(hass: HomeAssistant):
    await _setup(hass)
    reg = er.async_get(hass)
    update = reg.async_get_entity_id("update", DOMAIN, f"{BASE_MAC}-firmware")

    # No pending update -> off, with the installed firmware version shown.
    state = hass.states.get(update)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "4.16.6"

    # Simulate a pending update and push.
    primary = _primary(hass)
    primary.firmware_update_available = True
    primary.async_set_updated_data(primary.data)
    await hass.async_block_till_done()
    assert hass.states.get(update).state == "on"

    # Install routes to the upgrade endpoint.
    await hass.services.async_call(
        "update", "install", {"entity_id": update}, blocking=True
    )
    assert ("install_firmware_update", ()) in primary.client.calls
