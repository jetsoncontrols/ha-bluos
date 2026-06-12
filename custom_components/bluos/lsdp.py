"""Lenbrook Service Discovery Protocol (LSDP) client.

LSDP is a UDP-broadcast discovery protocol (port 11430) that BluOS devices use
because many home networks drop the multicast that mDNS relies on. A single
Announce datagram carries a node's base id (MAC), its IPv4 address, and one
record per player node with TXT metadata (``name``, ``port``, ``model`` ...).
The wire format was verified against a NAD CI580 (see
``tests/fixtures/lsdp_announce_ci580.bin``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
import socket
import struct

from .const import (
    LOGGER,
    LSDP_ALL_CLASSES,
    LSDP_MAGIC,
    LSDP_PORT,
    LSDP_QUERY_INTERVAL,
    LSDP_VERSION,
    PLAYER_CLASSES,
)


@dataclass(slots=True)
class LsdpRecord:
    """One service record within an Announce message."""

    cls: int
    txt: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LsdpAnnounce:
    """A decoded Announce message."""

    node_id: str  # hex, no separators (e.g. "9056820a237c")
    host: str  # IPv4 dotted
    records: list[LsdpRecord] = field(default_factory=list)


@dataclass(slots=True)
class LsdpNode:
    """A player node advertised for a unit."""

    port: int
    name: str | None
    cls: int


@dataclass(slots=True)
class LsdpUnit:
    """A physical unit and all of its player nodes, merged from announces."""

    node_id: str
    host: str
    nodes: list[LsdpNode] = field(default_factory=list)

    @property
    def name(self) -> str | None:
        """Best label for the unit (its primary/first node's name)."""
        return self.nodes[0].name if self.nodes else None


def build_query(classes: int = LSDP_ALL_CLASSES) -> bytes:
    """Build an LSDP broadcast Query packet for the given class id."""
    header = bytes([len(LSDP_MAGIC) + 2]) + LSDP_MAGIC + bytes([LSDP_VERSION])
    body = bytes([5, ord("Q"), 1]) + struct.pack(">H", classes)
    return header + body


def _parse_txt(data: bytes, offset: int, count: int) -> tuple[dict[str, str], int]:
    txt: dict[str, str] = {}
    for _ in range(count):
        klen = data[offset]
        offset += 1
        key = data[offset : offset + klen].decode("utf-8", "replace")
        offset += klen
        vlen = data[offset]
        offset += 1
        val = data[offset : offset + vlen].decode("utf-8", "replace")
        offset += vlen
        txt[key] = val
    return txt, offset


def parse_packet(data: bytes) -> list[LsdpAnnounce]:
    """Decode an LSDP datagram into its Announce messages.

    Non-Announce messages (Query/Delete) and malformed tails are skipped. One
    datagram may contain several Announce messages (large units such as the
    CI580 split their nodes across messages).
    """
    if len(data) < 6 or data[1:5] != LSDP_MAGIC:
        return []

    announces: list[LsdpAnnounce] = []
    offset = data[0]  # skip header (its length includes the length byte)
    try:
        while offset < len(data):
            msg_len = data[offset]
            if msg_len == 0:
                break
            msg_start = offset
            msg_type = data[offset + 1]
            if msg_type != ord("A"):  # only Announce carries node info
                offset = msg_start + msg_len
                continue

            pos = offset + 2
            nid_len = data[pos]
            pos += 1
            node_id = data[pos : pos + nid_len].hex()
            pos += nid_len
            addr_len = data[pos]
            pos += 1
            host = ".".join(str(b) for b in data[pos : pos + addr_len])
            pos += addr_len
            rec_count = data[pos]
            pos += 1

            records: list[LsdpRecord] = []
            for _ in range(rec_count):
                cls = struct.unpack(">H", data[pos : pos + 2])[0]
                pos += 2
                txt_count = data[pos]
                pos += 1
                txt, pos = _parse_txt(data, pos, txt_count)
                records.append(LsdpRecord(cls=cls, txt=txt))

            announces.append(LsdpAnnounce(node_id=node_id, host=host, records=records))
            offset = msg_start + msg_len
    except (IndexError, struct.error) as err:
        LOGGER.debug("Truncated LSDP packet (%d bytes): %s", len(data), err)

    return announces


def units_from_announces(announces: list[LsdpAnnounce]) -> list[LsdpUnit]:
    """Merge announces by node id and keep only player-class nodes."""
    units: dict[str, LsdpUnit] = {}
    for ann in announces:
        unit = units.setdefault(
            ann.node_id, LsdpUnit(node_id=ann.node_id, host=ann.host)
        )
        unit.host = ann.host
        for rec in ann.records:
            if rec.cls not in PLAYER_CLASSES:
                continue
            port = rec.txt.get("port")
            if not port or not port.isdigit():
                continue
            unit.nodes.append(
                LsdpNode(port=int(port), name=rec.txt.get("name"), cls=rec.cls)
            )
    # Keep nodes in a stable, primary-first order.
    for unit in units.values():
        unit.nodes.sort(key=lambda n: n.port)
    return [unit for unit in units.values() if unit.nodes]


class _LsdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes], None]) -> None:
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._on_packet(data)

    def error_received(self, exc: Exception) -> None:  # pragma: no cover - rare
        LOGGER.debug("LSDP socket error: %s", exc)


class LsdpDiscovery:
    """Active LSDP listener that reports discovered units via a callback."""

    def __init__(self, on_unit: Callable[[LsdpUnit], Awaitable[None]]) -> None:
        self._on_unit = on_unit
        self._transport: asyncio.DatagramTransport | None = None
        self._query_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def async_start(self) -> bool:
        """Bind the socket and start periodic querying. Returns success."""
        self._loop = asyncio.get_running_loop()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with suppress(AttributeError, OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", LSDP_PORT))
            sock.setblocking(False)
            self._transport, _ = await self._loop.create_datagram_endpoint(
                lambda: _LsdpProtocol(self._handle_packet), sock=sock
            )
        except OSError as err:
            LOGGER.info(
                "LSDP discovery unavailable (could not bind UDP %d): %s", LSDP_PORT, err
            )
            return False

        async def _query_loop() -> None:
            try:
                while True:
                    self.send_query()
                    await asyncio.sleep(LSDP_QUERY_INTERVAL)
            except asyncio.CancelledError:
                raise

        self._query_task = self._loop.create_task(_query_loop())
        return True

    def send_query(self) -> None:
        if self._transport is None:
            return
        packet = build_query()
        for target in ("255.255.255.255", "<broadcast>"):
            try:
                self._transport.sendto(packet, (target, LSDP_PORT))
                return
            except OSError:
                continue

    def _handle_packet(self, data: bytes) -> None:
        if self._loop is None:
            return
        for unit in units_from_announces(parse_packet(data)):
            self._loop.create_task(self._on_unit(unit))

    async def async_stop(self) -> None:
        if self._query_task is not None:
            self._query_task.cancel()
            self._query_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
