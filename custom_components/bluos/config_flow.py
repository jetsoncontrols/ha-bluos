"""Config flow for the BluOS integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import NodeInfo, async_enumerate_nodes
from .const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NODES,
    DOMAIN,
    MANUFACTURER_FALLBACK,
)
from .coordinator import normalize_mac

if TYPE_CHECKING:
    # Imported only for typing; importing the zeroconf component at runtime is
    # unnecessary (Home Assistant loads it and passes us the discovery info).
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo


class BluOsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BluOS units."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._nodes: list[NodeInfo] = []
        self._base_mac: str | None = None
        self._title: str = "BluOS"

    # --- manual entry ----------------------------------------------------
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            nodes = await async_enumerate_nodes(
                async_get_clientsession(self.hass), host
            )
            if not nodes:
                errors["base"] = "cannot_connect"
            else:
                await self._async_set_unit(host, nodes)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self._create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    # --- zeroconf / mDNS -------------------------------------------------
    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        return await self._async_discovered(discovery_info.host, None)

    # --- LSDP ------------------------------------------------------------
    async def async_step_integration_discovery(
        self, discovery_info: dict[str, Any]
    ) -> ConfigFlowResult:
        ports = [int(n["port"]) for n in discovery_info.get(CONF_NODES, [])] or None
        return await self._async_discovered(discovery_info[CONF_HOST], ports)

    async def _async_discovered(
        self, host: str, ports: list[int] | None
    ) -> ConfigFlowResult:
        nodes = await async_enumerate_nodes(
            async_get_clientsession(self.hass), host, ports
        )
        if not nodes:
            return self.async_abort(reason="cannot_connect")
        await self._async_set_unit(host, nodes)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self.context["title_placeholders"] = {"name": self._title}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._create_entry()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={"name": self._title},
        )

    # --- helpers ---------------------------------------------------------
    async def _async_set_unit(self, host: str, nodes: list[NodeInfo]) -> None:
        self._host = host
        self._nodes = nodes
        self._base_mac = normalize_mac(nodes[0].mac)
        self._title = _unit_title(nodes)
        await self.async_set_unique_id(self._base_mac)

    @callback
    def _create_entry(self) -> ConfigFlowResult:
        assert self._host is not None and self._base_mac is not None
        return self.async_create_entry(
            title=self._title,
            data={
                CONF_HOST: self._host,
                CONF_MAC: self._base_mac,
                CONF_NODES: [
                    {"port": n.port, "mac": n.mac, "name": n.name} for n in self._nodes
                ],
            },
        )


def _unit_title(nodes: list[NodeInfo]) -> str:
    """A friendly title: model for multi-zone racks, player name otherwise."""
    primary = nodes[0]
    brand = primary.brand or MANUFACTURER_FALLBACK
    model = primary.model_name or "Player"
    if len(nodes) > 1:
        return f"{brand} {model}"
    return primary.name or f"{brand} {model}"
