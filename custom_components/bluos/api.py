"""Async client and XML models for the BluOS HTTP API.

All requests are HTTP GET; responses are UTF-8 XML. Parsing uses the stdlib
ElementTree so the integration has no third-party runtime dependencies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import aiohttp

from .const import (
    CONNECT_TIMEOUT,
    DEFAULT_PORT,
    FIXED_VOLUME,
    MAX_ZONES,
    REQUEST_TIMEOUT,
    ZONE_PORT_STEP,
)


class BluOsError(Exception):
    """Base error for the BluOS API."""


class BluOsConnectionError(BluOsError):
    """Raised when a player cannot be reached or returns an invalid response."""


def _int(value: str | None, default: int = 0) -> int:
    """Parse an int from XML text, tolerating empty/garbage values."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


@dataclass(slots=True)
class PlayerStatus:
    """Parsed `/Status` response (playback state)."""

    etag: str | None = None
    state: str = "stop"
    volume: int = FIXED_VOLUME
    muted: bool = False
    title1: str | None = None
    title2: str | None = None
    title3: str | None = None
    image: str | None = None
    service: str | None = None
    seconds: int = 0
    total_length: int | None = None
    shuffle: bool = False
    repeat: int = 2  # 0=all, 1=one, 2=off
    can_seek: bool = False
    is_stream: bool = False  # <streamUrl> present -> not playing from the queue
    sync_stat: str | None = None  # mirrors SyncStatus syncStat; flags grouping changes

    @classmethod
    def from_xml(cls, text: str) -> PlayerStatus:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /Status XML: {err}") from err

        return cls(
            etag=root.get("etag"),
            state=root.findtext("state") or "stop",
            volume=_int(root.findtext("volume"), FIXED_VOLUME),
            muted=root.findtext("mute") == "1",
            title1=root.findtext("title1"),
            title2=root.findtext("title2"),
            title3=root.findtext("title3"),
            image=root.findtext("image") or None,
            service=root.findtext("service") or None,
            seconds=_int(root.findtext("secs")),
            total_length=(
                _int(root.findtext("totlen")) if root.findtext("totlen") else None
            ),
            shuffle=root.findtext("shuffle") == "1",
            repeat=_int(root.findtext("repeat"), 2),
            can_seek=root.findtext("canSeek") == "1",
            is_stream=root.find("streamUrl") is not None,
            sync_stat=root.findtext("syncStat"),
        )

    @property
    def volume_fixed(self) -> bool:
        """Whether this node has a fixed (non-adjustable) output level."""
        return self.volume == FIXED_VOLUME


@dataclass(slots=True)
class SyncStatus:
    """Parsed `/SyncStatus` response (identity + grouping)."""

    etag: str | None = None  # long-poll etag (the `etag` attribute)
    sync_stat: str | None = None  # the `syncStat` attribute; mirrors /Status syncStat
    name: str | None = None
    model: str | None = None
    model_name: str | None = None
    brand: str | None = None
    icon: str | None = None
    mac: str | None = None
    node_id: str | None = None  # "ip:port"
    volume: int = FIXED_VOLUME
    schema_version: str | None = None
    group: str | None = None
    # Grouping topology. master is set when this node is a group secondary;
    # slaves is populated when this node is the group primary.
    master: tuple[str, int] | None = None
    slaves: list[tuple[str, int]] = field(default_factory=list)

    @classmethod
    def from_xml(cls, text: str) -> SyncStatus:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /SyncStatus XML: {err}") from err

        master_el = root.find("master")
        master: tuple[str, int] | None = None
        if master_el is not None and master_el.text:
            master = (master_el.text, _int(master_el.get("port"), DEFAULT_PORT))

        slaves = [
            (slave.get("id", ""), _int(slave.get("port"), DEFAULT_PORT))
            for slave in root.findall("slave")
            if slave.get("id")
        ]

        return cls(
            etag=root.get("etag"),
            sync_stat=root.get("syncStat"),
            name=root.get("name"),
            model=root.get("model"),
            model_name=root.get("modelName"),
            brand=root.get("brand"),
            icon=root.get("icon"),
            mac=root.get("mac"),
            node_id=root.get("id"),
            volume=_int(root.get("volume"), FIXED_VOLUME),
            schema_version=root.get("schemaVersion"),
            group=root.get("group"),
            master=master,
            slaves=slaves,
        )


@dataclass(slots=True)
class NodeInfo:
    """Identity of a single player node, used during discovery/enumeration."""

    host: str
    port: int
    mac: str
    name: str | None = None
    model_name: str | None = None
    brand: str | None = None


class BluOsClient:
    """HTTP client bound to a single player node (host + port)."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int = DEFAULT_PORT
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"

    async def _get(
        self, path: str, params: dict[str, object] | None = None, *, timeout: int = 10
    ) -> str:
        """Perform a GET and return the response body as text."""
        url = f"{self.base_url}/{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(
                    total=timeout, sock_connect=CONNECT_TIMEOUT
                ),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BluOsConnectionError(f"{url} failed: {err}") from err

    # --- queries ---------------------------------------------------------
    async def status(
        self, *, etag: str | None = None, timeout: int | None = None
    ) -> PlayerStatus:
        params: dict[str, object] = {}
        if timeout is not None:
            params["timeout"] = timeout
            if etag:
                params["etag"] = etag
        return PlayerStatus.from_xml(
            await self._get(
                "Status",
                params or None,
                timeout=REQUEST_TIMEOUT if timeout else 10,
            )
        )

    async def sync_status(
        self, *, etag: str | None = None, timeout: int | None = None
    ) -> SyncStatus:
        params: dict[str, object] = {}
        if timeout is not None:
            params["timeout"] = timeout
            if etag:
                params["etag"] = etag
        return SyncStatus.from_xml(
            await self._get(
                "SyncStatus",
                params or None,
                timeout=REQUEST_TIMEOUT if timeout else 10,
            )
        )

    # --- transport -------------------------------------------------------
    async def play(self) -> None:
        await self._get("Play")

    async def pause(self) -> None:
        await self._get("Pause")

    async def stop(self) -> None:
        await self._get("Stop")

    async def skip(self) -> None:
        await self._get("Skip")

    async def back(self) -> None:
        await self._get("Back")

    async def set_shuffle(self, shuffle: bool) -> None:
        await self._get("Shuffle", {"state": 1 if shuffle else 0})

    async def set_repeat(self, repeat: int) -> None:
        """repeat: 0=all, 1=one, 2=off."""
        await self._get("Repeat", {"state": repeat})

    # --- volume ----------------------------------------------------------
    async def set_volume(self, level: int) -> None:
        await self._get("Volume", {"level": max(0, min(100, level))})

    async def volume_step(self, db: int) -> None:
        await self._get("Volume", {"db": db})

    async def set_mute(self, mute: bool) -> None:
        await self._get("Volume", {"mute": 1 if mute else 0})

    # --- grouping --------------------------------------------------------
    async def add_slave(self, slave_host: str, slave_port: int) -> None:
        await self._get("AddSlave", {"slave": slave_host, "port": slave_port})

    async def remove_slave(self, slave_host: str, slave_port: int) -> None:
        await self._get("RemoveSlave", {"slave": slave_host, "port": slave_port})


async def async_get_node(
    session: aiohttp.ClientSession, host: str, port: int
) -> NodeInfo | None:
    """Return identity of an initialized player node, or None if absent.

    A node is considered present only when its `/SyncStatus` reports a MAC and
    is `initialized` (set up via the BluOS app).
    """
    client = BluOsClient(session, host, port)
    try:
        text = await client._get("SyncStatus", timeout=5)
    except BluOsConnectionError:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    if root.get("initialized") == "false" or not root.get("mac"):
        return None
    return NodeInfo(
        host=host,
        port=port,
        mac=root.get("mac", ""),
        name=root.get("name"),
        model_name=root.get("modelName"),
        brand=root.get("brand"),
    )


async def async_enumerate_nodes(
    session: aiohttp.ClientSession, host: str, ports: list[int] | None = None
) -> list[NodeInfo]:
    """Enumerate all player nodes on a chassis.

    When `ports` is given (e.g. from discovery), those are probed directly.
    Otherwise the CI580-style port pattern (DEFAULT_PORT + N*ZONE_PORT_STEP) is
    walked until a port stops responding, which also covers standalone units
    that only answer on DEFAULT_PORT.
    """
    if ports is not None:
        candidates = sorted(set(ports))
        results = await asyncio.gather(
            *(async_get_node(session, host, port) for port in candidates)
        )
        return [node for node in results if node is not None]

    nodes: list[NodeInfo] = []
    for index in range(MAX_ZONES):
        port = DEFAULT_PORT + index * ZONE_PORT_STEP
        node = await async_get_node(session, host, port)
        if node is None:
            break  # contiguous port block; first gap ends enumeration
        nodes.append(node)
    return nodes
