"""Diagnostics support for BluOS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .api import BluOsError
from .const import CONF_HOST, CONF_MAC
from .coordinator import BluOsConfigEntry

TO_REDACT = {CONF_HOST, CONF_MAC, "mac", "node_id", "id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BluOsConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    unit = entry.runtime_data
    nodes = []
    for coordinator in unit.coordinators:
        data = coordinator.data
        nodes.append(
            {
                "port": coordinator.port,
                "available": coordinator.last_update_success,
                "model": data.sync.model_name if data else None,
                "state": data.status.state if data else None,
                "volume": data.status.volume if data else None,
                "volume_fixed": data.status.volume_fixed if data else None,
                "service": data.status.service if data else None,
                "grouped": bool(data and (data.sync.master or data.sync.slaves)),
            }
        )

    result: dict[str, Any] = {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "is_multi": unit.is_multi,
        "nodes": nodes,
    }

    # Device diagnostic log (per physical unit, fetched via any node's host).
    # Intentionally not redacted — this is an internal integration; the log's
    # MAC/IP/share details are part of what makes it useful for debugging.
    if unit.coordinators:
        client = unit.coordinators[0].client
        try:
            result["diagnostic_log"] = await client.diagnostic_log()
        except BluOsError as err:
            result["diagnostic_log"] = f"<unavailable: {err}>"

    return result
