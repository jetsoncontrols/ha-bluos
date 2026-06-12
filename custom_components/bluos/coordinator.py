"""Per-node data coordinator driven by BluOS long-polling.

Each player node gets its own coordinator running two background long-poll
loops (`/Status` and `/SyncStatus`). This gives near-instant push updates and
isolates failures: a stuck zone cannot block its siblings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import (
    BluOsClient,
    BluOsConnectionError,
    InputSource,
    PlayerStatus,
    Preset,
    SyncStatus,
)
from .const import (
    DOMAIN,
    ERROR_BACKOFF,
    LOGGER,
    MAX_FAILURES,
    MIN_REQUEST_INTERVAL,
    STATUS_TIMEOUT,
    SYNC_TIMEOUT,
)


def normalize_mac(mac: str) -> str:
    """Canonicalise a node id to a stable lowercase key.

    Real 12-hex MACs become lowercase colon form. The CI580 secondary nodes
    report a pseudo-MAC of ``<base>:<port>`` which `format_mac` leaves untouched
    (preserving case); lowercasing keeps every node id consistent.
    """
    return format_mac(mac).lower()


@dataclass(slots=True)
class BluOsData:
    """Combined snapshot pushed to entities."""

    status: PlayerStatus
    sync: SyncStatus


@dataclass(slots=True)
class BluOsRuntimeData:
    """Per-config-entry runtime state (one physical unit)."""

    coordinators: list[BluOsCoordinator]
    chassis_mac: str
    chassis_name: str
    is_multi: bool


type BluOsConfigEntry = ConfigEntry[BluOsRuntimeData]


class BluOsCoordinator(DataUpdateCoordinator[BluOsData]):
    """Coordinator for a single BluOS player node."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BluOsConfigEntry,
        client: BluOsClient,
        mac: str,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}:{client.port}",
            update_interval=None,  # push-only; driven by the long-poll loops
        )
        self.client = client
        self.mac = mac
        self.host = client.host
        self.port = client.port
        self.status_updated_at: datetime = dt_util.utcnow()
        # Source caches feeding source_list (fetched out-of-band from /Status).
        self.inputs: list[InputSource] = []
        self.presets: list[Preset] = []
        self._status: PlayerStatus | None = None
        self._sync: SyncStatus | None = None
        self._prid: str | None = None
        self._status_failures = 0
        self._sync_failures = 0
        self._last_status_req = 0.0
        self._last_sync_req = 0.0

    async def _async_update_data(self) -> BluOsData:
        """Initial blocking fetch used by async_config_entry_first_refresh."""
        self._status, self._sync = await asyncio.gather(
            self.client.status(), self.client.sync_status()
        )
        self.status_updated_at = dt_util.utcnow()
        self._prid = self._status.prid
        await self._refresh_inputs()
        await self._refresh_presets()
        return BluOsData(self._status, self._sync)

    async def _refresh_inputs(self) -> None:
        """Fetch the (rarely changing) physical input list; best-effort."""
        try:
            self.inputs = await self.client.inputs()
        except BluOsConnectionError as err:
            LOGGER.debug("%s inputs fetch failed: %s", self.name, err)

    async def _refresh_presets(self) -> None:
        """Fetch the preset list; best-effort."""
        try:
            self.presets = await self.client.presets()
        except BluOsConnectionError as err:
            LOGGER.debug("%s presets fetch failed: %s", self.name, err)

    async def _refresh_presets_and_push(self) -> None:
        await self._refresh_presets()
        self._push()

    @callback
    def async_start_loops(self) -> None:
        """Start the two long-poll background loops (after first refresh)."""
        assert self.config_entry is not None
        self.config_entry.async_create_background_task(
            self.hass, self._status_loop(), name=f"{self.name} status loop"
        )
        self.config_entry.async_create_background_task(
            self.hass, self._sync_loop(), name=f"{self.name} sync loop"
        )

    @callback
    def _push(self) -> None:
        if self._status is not None and self._sync is not None:
            self.async_set_updated_data(BluOsData(self._status, self._sync))

    async def _throttle(self, last_req: float) -> float:
        """Honour the spec's >1s-between-identical-requests rule."""
        now = self.hass.loop.time()
        wait = MIN_REQUEST_INTERVAL - (now - last_req)
        if wait > 0:
            await asyncio.sleep(wait)
        return self.hass.loop.time()

    async def _status_loop(self) -> None:
        while True:
            self._last_status_req = await self._throttle(self._last_status_req)
            try:
                etag = self._status.etag if self._status else None
                status = await self.client.status(etag=etag, timeout=STATUS_TIMEOUT)
            except BluOsConnectionError as err:
                self._register_failure("status", err)
                await asyncio.sleep(ERROR_BACKOFF)
                continue

            self._status_failures = 0
            self._status = status
            self.status_updated_at = dt_util.utcnow()
            self._push()
            # A changed syncStat signals grouping/identity changes -> refresh sync.
            if (
                self._sync
                and status.sync_stat
                and status.sync_stat != self._sync.sync_stat
            ):
                self.hass.async_create_task(self._refresh_sync_once())
            # A changed prid signals the preset list was edited -> refresh it.
            if status.prid != self._prid:
                self._prid = status.prid
                self.hass.async_create_task(self._refresh_presets_and_push())

    async def _sync_loop(self) -> None:
        while True:
            self._last_sync_req = await self._throttle(self._last_sync_req)
            try:
                etag = self._sync.etag if self._sync else None
                sync = await self.client.sync_status(etag=etag, timeout=SYNC_TIMEOUT)
            except BluOsConnectionError as err:
                self._register_failure("sync", err)
                await asyncio.sleep(ERROR_BACKOFF)
                continue

            self._sync_failures = 0
            self._sync = sync
            self._push()

    async def _refresh_sync_once(self) -> None:
        try:
            self._sync = await self.client.sync_status()
        except BluOsConnectionError:
            return
        self._push()

    @callback
    def _register_failure(self, which: str, err: Exception) -> None:
        """Count a failed long-poll; mark the node unavailable once it can no
        longer be contacted (MAX_FAILURES consecutive failures on a loop)."""
        if which == "status":
            self._status_failures += 1
            failures = self._status_failures
        else:
            self._sync_failures += 1
            failures = self._sync_failures
        LOGGER.debug("%s %s long-poll failed (%d): %s", self.name, which, failures, err)
        if failures >= MAX_FAILURES and self.last_update_success:
            self.async_set_update_error(err)
