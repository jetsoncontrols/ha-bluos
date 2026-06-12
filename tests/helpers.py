"""Shared test helpers (fixture loaders and a fake API client)."""

from __future__ import annotations

from pathlib import Path

from custom_components.bluos.api import PlayerStatus, SyncStatus

FIXTURES = Path(__file__).parent / "fixtures"
ZONE_PORTS = [11000, 11010, 11020, 11030]


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def status_for(port: int) -> PlayerStatus:
    return PlayerStatus.from_xml(load_fixture(f"status_{port}.xml"))


def sync_for(port: int) -> SyncStatus:
    return SyncStatus.from_xml(load_fixture(f"syncstatus_{port}.xml"))


class FakeClient:
    """Stand-in for BluOsClient that serves recorded fixtures by port."""

    def __init__(self, session, host: str, port: int = 11000) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.calls: list[tuple[str, tuple]] = []

    async def status(self, *, etag=None, timeout=None) -> PlayerStatus:
        return status_for(self.port)

    async def sync_status(self, *, etag=None, timeout=None) -> SyncStatus:
        return sync_for(self.port)

    async def _record(self, name: str, *args) -> None:
        self.calls.append((name, args))

    async def play(self):
        await self._record("play")

    async def pause(self):
        await self._record("pause")

    async def stop(self):
        await self._record("stop")

    async def skip(self):
        await self._record("skip")

    async def back(self):
        await self._record("back")

    async def set_volume(self, level):
        await self._record("set_volume", level)

    async def volume_step(self, db):
        await self._record("volume_step", db)

    async def set_mute(self, mute):
        await self._record("set_mute", mute)

    async def set_shuffle(self, shuffle):
        await self._record("set_shuffle", shuffle)

    async def set_repeat(self, repeat):
        await self._record("set_repeat", repeat)

    async def add_slave(self, host, port):
        await self._record("add_slave", host, port)

    async def remove_slave(self, host, port):
        await self._record("remove_slave", host, port)
