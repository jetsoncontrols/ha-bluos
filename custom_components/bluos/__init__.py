"""The BluOS integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BluOsClient
from .const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NODES,
    DEFAULT_PORT,
    DOMAIN,
    MANUFACTURER_FALLBACK,
    ZONE_PORT_STEP,
)
from .coordinator import (
    BluOsConfigEntry,
    BluOsCoordinator,
    BluOsRuntimeData,
    chassis_identifier,
    normalize_mac,
)
from .lsdp import LsdpDiscovery, LsdpUnit

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.UPDATE,
]

# This integration is configured only via config entries / discovery, never
# YAML, so reject any `bluos:` YAML config (also silences the hassfest warning).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class BluOsDomainData:
    """Integration-wide state shared across config entries."""

    coordinators_by_mac: dict[str, BluOsCoordinator] = field(default_factory=dict)
    coordinators_by_addr: dict[tuple[str, int], BluOsCoordinator] = field(
        default_factory=dict
    )
    lsdp: LsdpDiscovery | None = None
    lsdp_seen: set[str] = field(default_factory=set)


def _domain_data(hass: HomeAssistant) -> BluOsDomainData:
    return hass.data.setdefault(DOMAIN, BluOsDomainData())


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Start integration-wide LSDP discovery."""
    data = _domain_data(hass)
    if data.lsdp is not None:
        return True

    async def _on_unit(unit: LsdpUnit) -> None:
        base_mac = normalize_mac(unit.node_id)
        if base_mac in data.lsdp_seen:
            return
        data.lsdp_seen.add(base_mac)
        from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY

        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_HOST: unit.host,
                CONF_MAC: base_mac,
                CONF_NODES: [{"port": n.port, "name": n.name} for n in unit.nodes],
            },
        )

    lsdp = LsdpDiscovery(_on_unit)
    if await lsdp.async_start():
        data.lsdp = lsdp

        @callback
        def _stop(_: Event) -> None:
            hass.async_create_task(lsdp.async_stop())

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BluOsConfigEntry) -> bool:
    """Set up a BluOS unit from a config entry."""
    data = _domain_data(hass)
    session = async_get_clientsession(hass)
    host: str = entry.data[CONF_HOST]
    nodes: list[dict] = entry.data[CONF_NODES]
    base_mac: str = entry.data[CONF_MAC]
    is_multi = len(nodes) > 1

    coordinators: list[BluOsCoordinator] = []
    for node in nodes:
        client = BluOsClient(session, host, int(node["port"]))
        coordinator = BluOsCoordinator(
            hass, entry, client, normalize_mac(node["mac"]) if node.get("mac") else ""
        )
        coordinators.append(coordinator)

    # Refresh all nodes in parallel; tolerate offline secondaries but require
    # the primary (first node) to be reachable.
    await asyncio.gather(*(c.async_refresh() for c in coordinators))
    primary = coordinators[0]
    if not primary.last_update_success or primary.data is None:
        raise ConfigEntryNotReady(f"BluOS unit {host} is not reachable")

    live = [c for c in coordinators if c.last_update_success and c.data is not None]
    for coordinator in live:
        # Fill in the node MAC from the live response if it was unknown.
        if not coordinator.mac and coordinator.data.sync.mac:
            coordinator.mac = normalize_mac(coordinator.data.sync.mac)
        data.coordinators_by_mac[coordinator.mac] = coordinator
        data.coordinators_by_addr[(coordinator.host, coordinator.port)] = coordinator

    brand = primary.data.sync.brand or MANUFACTURER_FALLBACK
    # `modelName` ("CI580") is the friendly name; `model` ("CI580v2") is the
    # precise model used for the device model field.
    display_model = primary.data.sync.model_name or primary.data.sync.model or "Player"
    model = primary.data.sync.model or display_model
    chassis_name = f"{brand} {display_model}"

    dev_reg = dr.async_get(hass)
    if is_multi:
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={chassis_identifier(base_mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, base_mac)},
            name=chassis_name,
            manufacturer=brand,
            model=model,
            configuration_url=f"http://{host}",
        )

    # Create each node's device up front (before forwarding platforms) so every
    # entity domain attaches to a fully-named device regardless of platform
    # setup order. The per-node model carries the physical output on multi-zone
    # units; sw_version carries the firmware.
    for coordinator in live:
        sync = coordinator.data.sync
        node_model = sync.model or sync.model_name
        extra: dict = {}
        if is_multi:
            output = (coordinator.port - DEFAULT_PORT) // ZONE_PORT_STEP + 1
            node_model = (
                f"{node_model} (Output {output})" if node_model else f"Output {output}"
            )
            extra["via_device"] = chassis_identifier(base_mac)
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, coordinator.mac)},
            name=sync.name,
            manufacturer=sync.brand or MANUFACTURER_FALLBACK,
            model=node_model,
            sw_version=sync.version,
            configuration_url=f"http://{coordinator.host}",
            **extra,
        )

    entry.runtime_data = BluOsRuntimeData(
        coordinators=live,
        chassis_mac=base_mac,
        chassis_name=chassis_name,
        is_multi=is_multi,
    )

    # Firmware/reindex/doorbell live on the primary node only (unit-wide).
    await primary.async_refresh_firmware()
    await primary.async_check_doorbell()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    for coordinator in live:
        coordinator.async_start_loops()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BluOsConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = _domain_data(hass)
        unit = getattr(entry, "runtime_data", None)
        for coordinator in unit.coordinators if unit else ():
            data.coordinators_by_mac.pop(coordinator.mac, None)
            data.coordinators_by_addr.pop((coordinator.host, coordinator.port), None)
        data.lsdp_seen.discard(entry.data[CONF_MAC])
    return unloaded
