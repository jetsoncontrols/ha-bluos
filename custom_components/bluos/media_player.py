"""BluOS media_player platform."""

from __future__ import annotations

from contextlib import suppress
from urllib.parse import quote

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    entity_platform,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import voluptuous as vol

from . import browse
from .const import DOMAIN, VOLUME_STEP_DB
from .coordinator import BluOsConfigEntry, BluOsCoordinator, BluOsRuntimeData

# Context-menu queue modes -> BluOS action types (pick_context_action handles
# the add-/addAll- family fallback).
QUEUE_MODE_TYPES: dict[str, tuple[str, ...]] = {
    "now": ("add-now",),
    "next": ("add-next",),
    "last": ("add-last",),
}

MP_DOMAIN = "media_player"

STATE_MAP: dict[str, MediaPlayerState] = {
    "play": MediaPlayerState.PLAYING,
    "stream": MediaPlayerState.PLAYING,
    "pause": MediaPlayerState.PAUSED,
    "stop": MediaPlayerState.IDLE,
    "connecting": MediaPlayerState.BUFFERING,
}

REPEAT_DEVICE_TO_HA: dict[int, RepeatMode] = {
    0: RepeatMode.ALL,
    1: RepeatMode.ONE,
    2: RepeatMode.OFF,
}
REPEAT_HA_TO_DEVICE: dict[RepeatMode, int] = {
    RepeatMode.ALL: 0,
    RepeatMode.ONE: 1,
    RepeatMode.OFF: 2,
}

BASE_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.SHUFFLE_SET
    | MediaPlayerEntityFeature.REPEAT_SET
    | MediaPlayerEntityFeature.GROUPING
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
    | MediaPlayerEntityFeature.SEARCH_MEDIA
)
VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
)


def chassis_identifier(base_mac: str) -> tuple[str, str]:
    """Device-registry identifier for the (entity-less) chassis parent device."""
    return (DOMAIN, f"unit-{base_mac}")


def _audio_only(item: BrowseMedia) -> bool:
    """media_source content filter: keep audio playables (folders are kept)."""
    return bool(item.media_content_type) and item.media_content_type.startswith(
        "audio/"
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one media_player per player node on the unit."""
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "add_to_queue",
        {
            vol.Required("media_content_id"): cv.string,
            vol.Required("mode"): vol.In(list(QUEUE_MODE_TYPES)),
        },
        "async_add_to_queue",
    )
    platform.async_register_entity_service(
        "add_favourite",
        {vol.Required("media_content_id"): cv.string},
        "async_add_favourite",
    )

    unit = entry.runtime_data
    async_add_entities(
        BluOsMediaPlayer(coordinator, unit) for coordinator in unit.coordinators
    )


class BluOsMediaPlayer(CoordinatorEntity[BluOsCoordinator], MediaPlayerEntity):
    """A single BluOS player node."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator: BluOsCoordinator, unit: BluOsRuntimeData) -> None:
        super().__init__(coordinator)
        self._unit = unit
        self._attr_unique_id = coordinator.mac

        sync = coordinator.data.sync
        device = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mac)},
            name=sync.name,
            manufacturer=sync.brand,
            model=sync.model_name,
            configuration_url=f"http://{coordinator.host}",
        )
        if unit.is_multi:
            device["via_device"] = chassis_identifier(unit.chassis_mac)
        self._attr_device_info = device

    # --- helpers ---------------------------------------------------------
    @property
    def _status(self):
        return self.coordinator.data.status

    @property
    def _sync(self):
        return self.coordinator.data.sync

    # --- core state ------------------------------------------------------
    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        features = BASE_FEATURES
        if not self._status.volume_fixed:
            features |= VOLUME_FEATURES
        return features

    @property
    def state(self) -> MediaPlayerState:
        return STATE_MAP.get(self._status.state, MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        if self._status.volume_fixed:
            return None
        return self._status.volume / 100

    @property
    def is_volume_muted(self) -> bool:
        return self._status.muted

    @property
    def media_title(self) -> str | None:
        return self._status.title1

    @property
    def media_artist(self) -> str | None:
        return self._status.title2

    @property
    def media_album_name(self) -> str | None:
        return self._status.title3

    @property
    def media_duration(self) -> int | None:
        return self._status.total_length

    @property
    def media_position(self) -> int | None:
        return self._status.seconds

    @property
    def media_position_updated_at(self):
        return self.coordinator.status_updated_at

    @property
    def media_image_url(self) -> str | None:
        image = self._status.image
        if not image:
            return None
        if image.startswith("http"):
            return image
        url = f"http://{self.coordinator.host}:{self.coordinator.port}{image}"
        if "/Artwork" in image and "followRedirects" not in image:
            url += ("&" if "?" in image else "?") + "followRedirects=1"
        return url

    @property
    def shuffle(self) -> bool:
        return self._status.shuffle

    @property
    def repeat(self) -> RepeatMode:
        return REPEAT_DEVICE_TO_HA.get(self._status.repeat, RepeatMode.OFF)

    @property
    def group_members(self) -> list[str]:
        return self._compute_group_members()

    # --- sources (inputs + presets) -------------------------------------
    @property
    def source_list(self) -> list[str] | None:
        names = [i.name for i in self.coordinator.inputs]
        names += [p.name for p in self.coordinator.presets]
        return names or None

    @property
    def source(self) -> str | None:
        # Best-effort: only physical inputs are identifiable from /Status.
        status = self._status
        if status.service and status.service.lower() == "capture":
            for inp in self.coordinator.inputs:
                if inp.name == status.title1:
                    return inp.name
        return None

    async def async_select_source(self, source: str) -> None:
        for inp in self.coordinator.inputs:
            if inp.name == source:
                if inp.type_index:
                    await self.coordinator.client.select_input(inp.type_index)
                elif inp.url:
                    await self.coordinator.client.play_uri(f"/Play?url={inp.url}")
                return
        for preset in self.coordinator.presets:
            if preset.name == source:
                await self.coordinator.client.load_preset(preset.id)
                return

    # --- media browse / play --------------------------------------------
    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        client = self.coordinator.client
        cid = media_content_id

        if cid and media_source.is_media_source_id(cid):
            return await media_source.async_browse_media(
                self.hass, cid, content_filter=_audio_only
            )

        if cid in (None, browse.ROOT):
            result = await client.browse()
            node = browse.root_node(
                result, self.coordinator.presets, client.host, client.port
            )
            # media_source may have nothing browsable; that's fine.
            with suppress(BrowseError):
                node.children.append(await self._media_library_root())
            return node

        if cid == browse.PRESETS:
            return browse.presets_folder(
                self.coordinator.presets, client.host, client.port
            )

        if cid.startswith(browse.ITEM_PREFIX):
            key = browse.decode_item(cid).get("b")
            if not key:
                raise BrowseError(f"Item {cid} is not browsable")
            result = await client.browse(key=key)
            return browse.level_node(cid, result, client.host, client.port)

        raise BrowseError(f"Unknown media content id: {cid}")

    async def _media_library_root(self) -> BrowseMedia:
        """The Home Assistant media-source library, for TTS/local media."""
        return await media_source.async_browse_media(
            self.hass, None, content_filter=_audio_only
        )

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        client = self.coordinator.client
        enqueue: MediaPlayerEnqueue | None = kwargs.get("enqueue")
        cid = media_id

        if media_source.is_media_source_id(cid):
            resolved = await media_source.async_resolve_media(
                self.hass, cid, self.entity_id
            )
            url = async_process_play_media_url(self.hass, resolved.url)
            await client.play_uri(f"/Play?url={quote(url, safe='')}")
            return

        if cid.startswith(browse.PRESET_PREFIX):
            await client.load_preset(int(cid[len(browse.PRESET_PREFIX) :]))
            return

        if cid.startswith(browse.INPUT_PREFIX):
            await client.select_input(cid[len(browse.INPUT_PREFIX) :])
            return

        if cid.startswith(browse.ITEM_PREFIX):
            await self._play_item(browse.decode_item(cid), enqueue)
            return

        if cid.startswith(("http://", "https://")):
            await client.play_uri(f"/Play?url={quote(cid, safe='')}")
            return

        raise ValueError(f"Unsupported media content id: {cid}")

    async def _play_item(
        self, payload: dict[str, str], enqueue: MediaPlayerEnqueue | None
    ) -> None:
        client = self.coordinator.client
        play_url = payload.get("p")
        autoplay_url = payload.get("a")
        context_key = payload.get("c")
        browse_key = payload.get("b")
        add_modes = (MediaPlayerEnqueue.ADD, MediaPlayerEnqueue.NEXT)

        if enqueue in add_modes:
            wanted = (
                ("add-last", "addAll-last")
                if enqueue is MediaPlayerEnqueue.ADD
                else ("add-next", "addAll-next")
            )
            if context_key:
                menu = await client.context_menu(context_key)
                action = browse.pick_context_action(menu, *wanted)
                if action:
                    await client.play_uri(action)
                    return
            if autoplay_url:  # fallback: add + autofill
                await client.play_uri(autoplay_url)
                return

        # Play now (REPLACE / PLAY / default).
        if play_url:
            await client.play_uri(play_url)  # clears the queue and plays
            return
        if autoplay_url:
            await client.play_uri(autoplay_url)
            return
        # "Play all" containers (e.g. local-library artists) expose this only via
        # their context menu.
        if context_key:
            menu = await client.context_menu(context_key)
            action = browse.pick_play_action(menu)
            if action:
                await client.play_uri(action)
                return
        # Genres/composers have neither: synthesize a /Add from the browseKey.
        synthesized = browse.synthesize_play_all(browse_key)
        if synthesized:
            await client.play_uri(synthesized)

    # --- search ----------------------------------------------------------
    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        client = self.coordinator.client
        search_key: str | None = None
        cid = query.media_content_id
        if cid and cid.startswith(browse.ITEM_PREFIX):
            browse_key = browse.decode_item(cid).get("b")
            if browse_key:
                context = await client.browse(key=browse_key)
                search_key = context.search_key
        result = await client.browse(key=search_key, q=query.search_query)
        node = browse.search_node(query.search_query, result, client.host, client.port)
        return SearchMedia(result=node.children)

    # --- context-menu services (queue / favourite) ----------------------
    async def async_add_to_queue(self, media_content_id: str, mode: str) -> None:
        menu = await self._resolve_context_menu(media_content_id)
        action = (
            browse.pick_play_action(menu)
            if mode == "now"
            else browse.pick_context_action(menu, *QUEUE_MODE_TYPES[mode])
        )
        await self._run_action(action, f"queue ({mode})")

    async def async_add_favourite(self, media_content_id: str) -> None:
        menu = await self._resolve_context_menu(media_content_id)
        await self._run_action(
            browse.pick_context_action(menu, "favourite-add"), "favourite"
        )

    async def _resolve_context_menu(self, media_content_id: str):
        if not media_content_id.startswith(browse.ITEM_PREFIX):
            raise HomeAssistantError(f"{media_content_id} is not a browse item")
        context_key = browse.decode_item(media_content_id).get("c")
        if not context_key:
            raise HomeAssistantError("This item has no context-menu actions")
        return await self.coordinator.client.context_menu(context_key)

    async def _run_action(self, action: str | None, what: str) -> None:
        if not action:
            raise HomeAssistantError(f"No {what} action available for this item")
        await self.coordinator.client.play_uri(action)

    # --- transport commands ---------------------------------------------
    async def async_media_play(self) -> None:
        await self.coordinator.client.play()

    async def async_media_pause(self) -> None:
        await self.coordinator.client.pause()

    async def async_media_stop(self) -> None:
        await self.coordinator.client.stop()

    async def async_media_next_track(self) -> None:
        await self.coordinator.client.skip()

    async def async_media_previous_track(self) -> None:
        await self.coordinator.client.back()

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self.coordinator.client.set_shuffle(shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self.coordinator.client.set_repeat(REPEAT_HA_TO_DEVICE[repeat])

    # --- volume commands -------------------------------------------------
    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.client.set_volume(round(volume * 100))

    async def async_volume_up(self) -> None:
        await self.coordinator.client.volume_step(VOLUME_STEP_DB)

    async def async_volume_down(self) -> None:
        await self.coordinator.client.volume_step(-VOLUME_STEP_DB)

    async def async_mute_volume(self, mute: bool) -> None:
        await self.coordinator.client.set_mute(mute)

    # --- grouping --------------------------------------------------------
    async def async_join_players(self, group_members: list[str]) -> None:
        """Make this player the group primary and attach the given members."""
        for entity_id in group_members:
            target = self._coordinator_for_entity(entity_id)
            if target is None or target is self.coordinator:
                continue
            await self.coordinator.client.add_slave(target.host, target.port)

    async def async_unjoin_player(self) -> None:
        """Remove this player from its group."""
        me = (self.coordinator.host, self.coordinator.port)
        # Prefer the primary's authoritative slave list: it updates immediately
        # when AddSlave is issued, whereas this node's own SyncStatus master may
        # still be catching up right after a join (avoids a join->unjoin race).
        for coordinator in self._domain_data().coordinators_by_addr.values():
            data = coordinator.data
            if data is not None and me in data.sync.slaves:
                await coordinator.client.remove_slave(*me)
                return

        sync = self._sync
        if sync.master is not None:
            master = self._coordinator_for_addr(sync.master)
            if master is not None:
                await master.client.remove_slave(*me)
            return
        # This node is the group primary: detach every secondary.
        for host, port in sync.slaves:
            await self.coordinator.client.remove_slave(host, port)

    # --- grouping helpers ------------------------------------------------
    def _compute_group_members(self) -> list[str]:
        sync = self._sync
        if sync.master is not None:
            primary_addr = sync.master
            master = self._coordinator_for_addr(sync.master)
            slave_addrs = (
                list(master.data.sync.slaves)
                if master is not None
                else [(self.coordinator.host, self.coordinator.port)]
            )
        elif sync.slaves:
            primary_addr = (self.coordinator.host, self.coordinator.port)
            slave_addrs = list(sync.slaves)
        else:
            return []  # not grouped

        addrs = [primary_addr, *slave_addrs]
        members = [self._entity_id_for_addr(addr) for addr in addrs]
        return [m for m in members if m]

    def _domain_data(self):
        return self.hass.data[DOMAIN]

    def _coordinator_for_addr(self, addr: tuple[str, int]) -> BluOsCoordinator | None:
        return self._domain_data().coordinators_by_addr.get(addr)

    def _entity_id_for_addr(self, addr: tuple[str, int]) -> str | None:
        coordinator = self._coordinator_for_addr(addr)
        if coordinator is None:
            return None
        return er.async_get(self.hass).async_get_entity_id(
            MP_DOMAIN, DOMAIN, coordinator.mac
        )

    def _coordinator_for_entity(self, entity_id: str) -> BluOsCoordinator | None:
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.platform != DOMAIN:
            return None
        return self._domain_data().coordinators_by_mac.get(entry.unique_id)
