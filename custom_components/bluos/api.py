"""Async client and XML models for the BluOS HTTP API.

All requests are HTTP GET; responses are UTF-8 XML. Parsing uses the stdlib
ElementTree so the integration has no third-party runtime dependencies.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import aiohttp
from yarl import URL

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


def _float(value: str | None, default: float | None = None) -> float | None:
    """Parse a float from XML text, tolerating empty/garbage values."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def settings_has_node(text: str, node_id: str) -> bool:
    """Whether a `/Settings` menu tree contains a node with the given id.

    Used to gate optional features: e.g. only custom-install players (CI580)
    expose a `doorbell` node; standalone speakers omit it.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return False
    return any(el.get("id") == node_id for el in root.iter())


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
    prid: str | None = None  # preset revision; changes when presets are edited
    notify_url: str | None = None  # present when a service message/error is pending

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
            prid=root.findtext("prid"),
            notify_url=root.findtext("notifyurl") or None,
        )

    @property
    def volume_fixed(self) -> bool:
        """Whether this node has a fixed (non-adjustable) output level."""
        return self.volume == FIXED_VOLUME


@dataclass(slots=True)
class Notification:
    """A `/Status` `<notifyurl>` payload — a message to surface to the user.

    Fetched from the notifyurl (e.g. `/error?291`); the trailing counter makes
    each message unique. `<message>` is human text, `<url>` clears it, and
    `<action type>` is how the BluOS app would present it (e.g. "dialog").
    """

    message: str | None = None
    clear_url: str | None = None
    action: str | None = None

    @classmethod
    def from_xml(cls, text: str) -> Notification:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid notification XML: {err}") from err
        action = root.find("action")
        return cls(
            message=root.findtext("message"),
            clear_url=root.findtext("url"),
            action=action.get("type") if action is not None else None,
        )


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
    version: str | None = None  # firmware version (the `version` attribute)
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
            version=root.get("version"),
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


@dataclass(slots=True)
class Preset:
    """A saved player preset (`/Presets`)."""

    id: int
    name: str
    url: str | None = None
    image: str | None = None

    @classmethod
    def list_from_xml(cls, text: str) -> list[Preset]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /Presets XML: {err}") from err
        return [
            cls(
                id=_int(p.get("id")),
                name=p.get("name", ""),
                url=p.get("url"),
                image=p.get("image") or None,
            )
            for p in root.findall("preset")
        ]


@dataclass(slots=True)
class InputSource:
    """A physical input (`/RadioBrowse?service=Capture`)."""

    id: str
    name: str
    input_type: str | None = None  # analog, spdif, ...
    type_index: str | None = None  # e.g. "analog-1" for /Play?inputTypeIndex=
    url: str | None = None  # Capture URL (fallback play target)
    image: str | None = None

    @classmethod
    def list_from_xml(cls, text: str) -> list[InputSource]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid Capture XML: {err}") from err
        return [
            cls(
                id=item.get("id", ""),
                name=item.get("text", ""),
                input_type=item.get("inputType"),
                type_index=item.get("typeIndex"),
                url=item.get("URL"),
                image=item.get("image") or None,
            )
            for item in root.findall("item")
        ]


@dataclass(slots=True)
class AudioSetting:
    """One control from `/Settings?id=audio` (a `<setting>` node).

    The device describes each control itself: `kind` is the BluOS `class`
    (boolean | range | list | dual-range | button | text); `options` holds
    (name, display) pairs for lists; range bounds come from the child
    `<value>`; `depends_on` gates visibility on another setting's value.
    Writes POST `id=value` to `url` (the setting's own `url`, else the parent
    menu's `url` — e.g. tone controls post to `/alsa_setting`, the rest to
    `/audiomodes`).
    """

    id: str
    value: str | None
    kind: str | None
    url: str
    options: list[tuple[str, str]] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    units: str | None = None
    depends_on: tuple[str, str] | None = None


@dataclass(slots=True)
class AudioSettings:
    """Parsed `/Settings?id=audio` — a node's audio control menu, keyed by id."""

    by_id: dict[str, AudioSetting] = field(default_factory=dict)

    def get(self, setting_id: str) -> AudioSetting | None:
        return self.by_id.get(setting_id)

    @classmethod
    def from_xml(cls, text: str) -> AudioSettings:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /Settings XML: {err}") from err
        by_id: dict[str, AudioSetting] = {}
        for group in root.iter("menuGroup"):
            group_url = group.get("url") or ""
            for el in group.findall("setting"):
                sid = el.get("id")
                if not sid:
                    continue
                kind = el.get("class")
                options: list[tuple[str, str]] = []
                minimum = maximum = step = None
                units = None
                if kind == "list":
                    options = [
                        (v.get("name", ""), v.get("displayName") or v.get("name", ""))
                        for v in el.findall("value")
                    ]
                else:
                    constraint = el.find("value")
                    if constraint is not None:
                        minimum = _float(constraint.get("min"))
                        maximum = _float(constraint.get("max"))
                        step = _float(constraint.get("step"))
                        units = constraint.get("units")
                dep = el.find("dependsOn")
                by_id[sid] = AudioSetting(
                    id=sid,
                    value=el.get("value"),
                    kind=kind,
                    url=el.get("url") or group_url,
                    options=options,
                    minimum=minimum,
                    maximum=maximum,
                    step=step,
                    units=units,
                    depends_on=(
                        (dep.get("name", ""), dep.get("value", ""))
                        if dep is not None
                        else None
                    ),
                )
        return cls(by_id=by_id)


@dataclass(slots=True)
class BrowseItem:
    """A single node in a `/Browse` result.

    Attribute values come back XML-unescaped from ElementTree, so `browse_key`,
    `context_menu_key` and `search_key` are ready to be passed straight to a
    follow-up request as a `key` parameter (which percent-encodes once). The
    URL attributes (`play_url`/`autoplay_url`/`action_url`) are already in
    final-encoded form and must be GET verbatim (see `BluOsClient.play_uri`).
    """

    type: str = ""
    text: str | None = None
    text2: str | None = None
    image: str | None = None
    browse_key: str | None = None
    play_url: str | None = None
    autoplay_url: str | None = None
    context_menu_key: str | None = None
    action_url: str | None = None  # context-menu action items
    input_type: str | None = None

    @classmethod
    def from_element(cls, el: ET.Element) -> BrowseItem:
        return cls(
            type=el.get("type", ""),
            text=el.get("text"),
            text2=el.get("text2"),
            image=el.get("image") or None,
            browse_key=el.get("browseKey"),
            play_url=el.get("playURL"),
            autoplay_url=el.get("autoplayURL"),
            context_menu_key=el.get("contextMenuKey"),
            action_url=el.get("actionURL"),
            input_type=el.get("inputType"),
        )

    @property
    def can_expand(self) -> bool:
        return self.browse_key is not None

    @property
    def can_play(self) -> bool:
        return self.play_url is not None or self.autoplay_url is not None


@dataclass(slots=True)
class BrowseResult:
    """A parsed `/Browse` response (one level of the hierarchy)."""

    type: str | None = None  # menu, items, playlists, contextMenu, ...
    service_name: str | None = None
    service_icon: str | None = None
    search_key: str | None = None
    next_key: str | None = None
    items: list[BrowseItem] = field(default_factory=list)

    @classmethod
    def from_xml(cls, text: str) -> BrowseResult:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /Browse XML: {err}") from err
        if root.tag == "error":
            message = root.findtext("message") or "browse error"
            raise BluOsConnectionError(f"Browse error: {message}")

        # Items can sit directly under <browse> or be grouped in <category>.
        items = [BrowseItem.from_element(el) for el in root.findall("item")]
        for category in root.findall("category"):
            items.extend(BrowseItem.from_element(el) for el in category.findall("item"))

        return cls(
            type=root.get("type"),
            service_name=root.get("serviceName"),
            service_icon=root.get("serviceIcon"),
            search_key=root.get("searchKey"),
            next_key=root.get("nextKey"),
            items=items,
        )


@dataclass(slots=True)
class QueueTrack:
    """One track in the current play queue (`/Playlist`)."""

    id: int  # position in the queue (0-based)
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    image: str | None = None
    seconds: int | None = None


@dataclass(slots=True)
class Playlist:
    """The current play queue."""

    id: str | None = None
    length: int = 0
    tracks: list[QueueTrack] = field(default_factory=list)

    @classmethod
    def from_xml(cls, text: str) -> Playlist:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise BluOsConnectionError(f"Invalid /Playlist XML: {err}") from err
        tracks = [
            QueueTrack(
                id=_int(song.get("id")),
                title=song.findtext("title"),
                artist=song.findtext("art"),
                album=song.findtext("alb"),
                image=song.findtext("image") or None,
                seconds=(
                    _int(song.findtext("time")) if song.findtext("time") else None
                ),
            )
            for song in root.findall("song")
        ]
        return cls(id=root.get("id"), length=_int(root.get("length")), tracks=tracks)


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

    async def _post(self, path: str, data: dict[str, object]) -> str:
        """POST x-www-form-urlencoded data; return the response body text."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            async with self._session.post(
                url,
                data=data,
                timeout=aiohttp.ClientTimeout(total=10, sock_connect=CONNECT_TIMEOUT),
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

    async def seek(self, position: int) -> None:
        """Seek to an absolute position (seconds) in the current track."""
        await self._get("Play", {"seek": max(0, position)})

    async def set_shuffle(self, shuffle: bool) -> None:
        await self._get("Shuffle", {"state": 1 if shuffle else 0})

    async def set_repeat(self, repeat: int) -> None:
        """repeat: 0=all, 1=one, 2=off."""
        await self._get("Repeat", {"state": repeat})

    # --- volume ----------------------------------------------------------
    async def set_volume(self, level: int, *, tell_slaves: bool = False) -> None:
        params: dict[str, object] = {"level": max(0, min(100, level))}
        if tell_slaves:
            params["tell_slaves"] = 1
        await self._get("Volume", params)

    async def volume_step(self, db: int, *, tell_slaves: bool = False) -> None:
        params: dict[str, object] = {"db": db}
        if tell_slaves:
            params["tell_slaves"] = 1
        await self._get("Volume", params)

    async def set_mute(self, mute: bool, *, tell_slaves: bool = False) -> None:
        params: dict[str, object] = {"mute": 1 if mute else 0}
        if tell_slaves:
            params["tell_slaves"] = 1
        await self._get("Volume", params)

    # --- grouping --------------------------------------------------------
    async def add_slave(self, slave_host: str, slave_port: int) -> None:
        await self._get("AddSlave", {"slave": slave_host, "port": slave_port})

    async def remove_slave(self, slave_host: str, slave_port: int) -> None:
        await self._get("RemoveSlave", {"slave": slave_host, "port": slave_port})

    # --- sources: presets, inputs ---------------------------------------
    async def presets(self) -> list[Preset]:
        return Preset.list_from_xml(await self._get("Presets"))

    async def load_preset(self, preset_id: int) -> None:
        await self._get("Preset", {"id": preset_id})

    async def inputs(self) -> list[InputSource]:
        return InputSource.list_from_xml(
            await self._get("RadioBrowse", {"service": "Capture"})
        )

    async def select_input(self, type_index: str) -> None:
        await self._get("Play", {"inputTypeIndex": type_index})

    # --- audio settings (per node) --------------------------------------
    async def audio_settings(self) -> AudioSettings:
        """Fetch the node's audio control menu (`/Settings?id=audio`)."""
        return AudioSettings.from_xml(await self._get("Settings", {"id": "audio"}))

    async def set_audio_setting(self, name: str, value: str, *, url: str) -> None:
        """Write one audio setting (POST `name=value` to its endpoint)."""
        await self._post(url, {name: value})

    # --- maintenance (undocumented endpoints) ---------------------------
    async def reboot(self) -> None:
        """Soft-reboot this node (`POST /reboot`)."""
        await self._post("reboot", {"yes": 1})

    async def reindex(self) -> None:
        """Rebuild the music-library index (`GET /Reindex`)."""
        await self._get("Reindex")

    async def doorbell(self) -> None:
        """Play this node's configured doorbell chime (`/Doorbell?play=1`)."""
        await self._get("Doorbell", {"play": 1})

    async def supports_doorbell(self) -> bool:
        """Whether the player exposes the doorbell feature.

        The root `/Settings` menu carries a `doorbell` node on players that
        support it (custom-install units like the CI580); standalone speakers
        (e.g. Bluesound PULSE M) omit it.
        """
        return settings_has_node(await self._get("Settings"), "doorbell")

    async def firmware_update_available(self) -> bool:
        """True if `/upgrade` offers an install (the 'Upgrade Now' button)."""
        body = await self._get("upgrade")
        return "upgrade: 'old'" in body

    async def install_firmware_update(self) -> None:
        """Trigger the pending firmware install (`GET /upgrade?upgrade=old`)."""
        await self._get("upgrade", {"upgrade": "old"})

    # --- browse / search -------------------------------------------------
    async def browse(
        self, key: str | None = None, q: str | None = None
    ) -> BrowseResult:
        """Browse (or search) the content tree.

        `key` is an XML-unescaped browseKey/searchKey/contextMenuKey value; it is
        passed as a parameter so it is percent-encoded exactly once.
        """
        params: dict[str, object] = {}
        if key is not None:
            params["key"] = key
        if q is not None:
            params["q"] = q
        return BrowseResult.from_xml(
            await self._get("Browse", params or None, timeout=15)
        )

    async def context_menu(self, key: str) -> BrowseResult:
        """Fetch a browse item's context menu (a `<browse type="contextMenu">`)."""
        return await self.browse(key=key)

    # --- play queue ------------------------------------------------------
    async def playlist(
        self, start: int | None = None, end: int | None = None
    ) -> Playlist:
        params: dict[str, object] = {}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        return Playlist.from_xml(
            await self._get("Playlist", params or None, timeout=15)
        )

    async def clear_queue(self) -> None:
        await self._get("Clear")

    async def delete_track(self, position: int) -> None:
        await self._get("Delete", {"id": position})

    async def move_track(self, old: int, new: int) -> None:
        await self._get("Move", {"old": old, "new": new})

    async def save_queue(self, name: str) -> None:
        await self._get("Save", {"name": name})

    async def play_uri(self, uri: str) -> None:
        """GET a ready-made play/action URI verbatim.

        `uri` is a relative path (e.g. `/Play?url=...`, an autoplayURL or a
        context-menu actionURL) already in final-encoded form, so it must not be
        re-encoded — yarl's `encoded=True` preserves it as-is.
        """
        path = uri if uri.startswith("/") else f"/{uri}"
        full = URL(f"{self.base_url}{path}", encoded=True)
        try:
            async with self._session.get(
                full,
                timeout=aiohttp.ClientTimeout(total=15, sock_connect=CONNECT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                await resp.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BluOsConnectionError(f"{full} failed: {err}") from err

    async def notification(self, notify_url: str) -> Notification:
        """Fetch a `/Status` notifyurl payload (GET verbatim, already-encoded)."""
        path = notify_url if notify_url.startswith("/") else f"/{notify_url}"
        full = URL(f"{self.base_url}{path}", encoded=True)
        try:
            async with self._session.get(
                full,
                timeout=aiohttp.ClientTimeout(total=10, sock_connect=CONNECT_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                return Notification.from_xml(await resp.text())
        except (TimeoutError, aiohttp.ClientError) as err:
            raise BluOsConnectionError(f"{full} failed: {err}") from err


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
