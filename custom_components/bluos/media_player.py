"""BluOS media_player platform."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VOLUME_STEP_DB
from .coordinator import BluOsConfigEntry, BluOsCoordinator, BluOsRuntimeData

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
)
VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
)


def chassis_identifier(base_mac: str) -> tuple[str, str]:
    """Device-registry identifier for the (entity-less) chassis parent device."""
    return (DOMAIN, f"unit-{base_mac}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BluOsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one media_player per player node on the unit."""
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
