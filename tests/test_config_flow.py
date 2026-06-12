"""Tests for the BluOS config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bluos.api import NodeInfo
from custom_components.bluos.const import CONF_HOST, CONF_NODES, DOMAIN


def _node(port: int, mac: str, name: str, model="CI580", brand="NAD") -> NodeInfo:
    return NodeInfo("192.168.1.60", port, mac, name, model, brand)


CI580_NODES = [
    _node(11000, "90:56:82:0A:23:7C", "BluOS Zone 1"),
    _node(11010, "90:56:82:0A:23:7C:11010", "Zone 2"),
    _node(11020, "90:56:82:0A:23:7C:11020", "Kitchen"),
    _node(11030, "90:56:82:0A:23:7C:11030", "Zone 4"),
]
STANDALONE = [
    NodeInfo("192.168.1.70", 11000, "AA:BB:CC:DD:EE:FF", "Den", "PULSE", "Bluesound")
]


def _patch_enumerate(nodes):
    return patch(
        "custom_components.bluos.config_flow.async_enumerate_nodes",
        return_value=nodes,
    )


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Avoid real (networked) entry setup after a flow creates an entry."""
    with patch("custom_components.bluos.async_setup_entry", return_value=True):
        yield


async def test_user_flow_multi_zone(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with _patch_enumerate(CI580_NODES):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.60"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "NAD CI580"  # model name for multi-zone racks
    assert len(result["data"][CONF_NODES]) == 4
    assert result["result"].unique_id == "90:56:82:0a:23:7c"


async def test_user_flow_standalone_uses_player_name(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_enumerate(STANDALONE):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.70"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Den"
    assert len(result["data"][CONF_NODES]) == 1


async def test_user_flow_cannot_connect(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_enumerate([]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.99"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_unit_aborts(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN, unique_id="90:56:82:0a:23:7c", data={CONF_HOST: "192.168.1.60"}
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with _patch_enumerate(CI580_NODES):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.60"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_lsdp_discovery_confirm(hass: HomeAssistant):
    with _patch_enumerate(CI580_NODES):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_INTEGRATION_DISCOVERY},
            data={
                CONF_HOST: "192.168.1.60",
                CONF_NODES: [{"port": 11000, "name": "BluOS Zone 1"}],
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "discovery_confirm"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "90:56:82:0a:23:7c"
