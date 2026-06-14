"""Tests for the BluOS config-entry diagnostics dump."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bluos.api import BluOsConnectionError
from custom_components.bluos.const import CONF_HOST, CONF_MAC, CONF_NODES, DOMAIN
from custom_components.bluos.diagnostics import async_get_config_entry_diagnostics

from .helpers import FakeClient

BASE_MAC = "aa:bb:cc:00:11:22"
ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_MAC: BASE_MAC,
    CONF_NODES: [
        {"port": 11000, "mac": "AA:BB:CC:00:11:22", "name": "BluOS Zone 1"},
        {"port": 11010, "mac": "AA:BB:CC:00:11:22:11010", "name": "BluOS Zone 2"},
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


async def test_diagnostics_includes_log(hass: HomeAssistant):
    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)

    # one log per unit, fetched via the primary node's host (not redacted),
    # split into a list of lines (tabs within a line preserved)
    assert diag["diagnostic_log"] == ["host\t192.0.2.10", "state\tok"]
    assert diag["is_multi"] is True
    assert len(diag["nodes"]) == 2
    # the entry block still redacts host/mac
    assert diag["entry"][CONF_HOST] != "192.0.2.10"


async def test_diagnostics_log_unavailable(hass: HomeAssistant):
    entry = await _setup(hass)
    with patch.object(
        FakeClient,
        "diagnostic_log",
        AsyncMock(side_effect=BluOsConnectionError("boom")),
    ):
        diag = await async_get_config_entry_diagnostics(hass, entry)

    # a failed fetch must not break the rest of the dump (still a list)
    assert diag["diagnostic_log"] == ["<unavailable: boom>"]
    assert len(diag["nodes"]) == 2
