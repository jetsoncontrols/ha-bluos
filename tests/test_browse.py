"""Tests for source selection, media browse, play, search and queue services."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEnqueue,
    SearchMediaQuery,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_component, entity_registry as er

from custom_components.bluos.api import BrowseItem, BrowseResult
from custom_components.bluos.browse import (
    decode_item,
    encode_item,
    item_to_browse_media,
    pick_context_action,
    pick_play_action,
    synthesize_play_all,
)
from custom_components.bluos.const import DOMAIN

from .helpers import load_fixture
from .test_init import _setup

KITCHEN_MAC = "90:56:82:0a:23:7c:11020"


def _entity(hass: HomeAssistant, unique_id: str):
    entity_id = er.async_get(hass).async_get_entity_id(
        "media_player", DOMAIN, unique_id
    )
    component = hass.data[entity_component.DATA_INSTANCES]["media_player"]
    return component.get_entity(entity_id)


async def _kitchen(hass: HomeAssistant):
    await _setup(hass)
    return _entity(hass, KITCHEN_MAC)


# --- pure browse.py unit tests ------------------------------------------
def test_content_id_codec_round_trip():
    item = BrowseItem(
        type="album",
        browse_key="Svc:key/1",
        play_url="/Play?url=x",
        autoplay_url="/Add?url=x",
        context_menu_key="Svc:CM/1",
    )
    payload = decode_item(encode_item(item))
    assert payload == {
        "b": "Svc:key/1",
        "p": "/Play?url=x",
        "a": "/Add?url=x",
        "c": "Svc:CM/1",
    }


def test_pick_context_action_family_fallback():
    menu = BrowseResult.from_xml(load_fixture("browse_contextmenu_rich.xml"))
    # exact match
    assert pick_context_action(menu, "favourite-add").startswith("/AddFavourite?")
    # family fallback: "add-next" -> "addAll-next"
    assert "where=nextAlbum" in pick_context_action(menu, "add-next")
    assert "where=last" in pick_context_action(menu, "add-last")
    assert pick_context_action(menu, "nonexistent") is None


def test_pick_play_action_uses_playnow_param():
    # Local-library artist: both "Add all" and "Play all" are type addAll-last;
    # the play-now action is the one with playnow=1 (and not shuffle).
    menu = BrowseResult.from_xml(load_fixture("browse_contextmenu_artist.xml"))
    action = pick_play_action(menu)
    assert "playnow=1" in action and "shuffle=0" in action


def test_pick_play_action_prefers_explicit_add_now():
    menu = BrowseResult.from_xml(load_fixture("browse_contextmenu_rich.xml"))
    assert pick_play_action(menu).startswith("/Add?service=Deezer&playnow=1")


def test_context_only_container_is_playable():
    artist = BrowseItem(type="artist", browse_key="LM:a", context_menu_key="LM:CM/a")
    node = item_to_browse_media(artist, "h", 11000)
    assert node.can_play and node.can_expand  # "Play all" + drill-in
    section = BrowseItem(type="section", browse_key="LM:s")
    node2 = item_to_browse_media(section, "h", 11000)
    assert node2.can_expand and not node2.can_play


def test_synthesize_play_all_from_localmusic_filter():
    assert (
        synthesize_play_all("LocalMusic:MG/Genres?genre=Acoustic+Rock")
        == "/Add?genre=Acoustic+Rock&service=LocalMusic&playnow=1"
    )
    assert (
        synthesize_play_all("LocalMusic:MG/LocalMusic-Composer?composer=Dan+Reynolds")
        == "/Add?composer=Dan+Reynolds&service=LocalMusic&playnow=1"
    )
    # No filter / not LocalMusic / a plain list -> nothing to synthesize.
    assert synthesize_play_all("LocalMusic:GG/Genres") is None
    assert synthesize_play_all("TuneIn:Foo?x=1") is None
    assert synthesize_play_all(None) is None


def test_genre_node_is_playable_via_synthesis():
    genre = BrowseItem(type="genre", browse_key="LocalMusic:MG/Genres?genre=Jazz")
    node = item_to_browse_media(genre, "h", 11000)
    assert node.can_play and node.can_expand


# --- source selection ----------------------------------------------------
async def test_source_list_inputs_and_presets(hass: HomeAssistant):
    entity = await _kitchen(hass)
    assert entity.source_list == [
        "Analog Input",
        "Optical Input",
        "Serenity",
        "1980s Alternative Rock Classics",
    ]


async def test_select_source_input(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await entity.async_select_source("Optical Input")
    assert ("select_input", ("spdif-1",)) in entity.coordinator.client.calls


async def test_select_source_preset(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await entity.async_select_source("Serenity")
    assert ("load_preset", (6,)) in entity.coordinator.client.calls


# --- browse --------------------------------------------------------------
async def test_browse_root(hass: HomeAssistant):
    entity = await _kitchen(hass)
    root = await entity.async_browse_media()
    titles = [c.title for c in root.children]
    assert "Presets" in titles
    assert "Radio Paradise" in titles
    analog = next(c for c in root.children if c.title == "Analog Input")
    assert analog.can_play and not analog.can_expand


async def test_browse_into_service(hass: HomeAssistant):
    entity = await _kitchen(hass)
    root = await entity.async_browse_media()
    rp = next(c for c in root.children if c.title == "Radio Paradise")
    assert rp.can_expand
    level = await entity.async_browse_media(media_content_id=rp.media_content_id)
    assert len(level.children) >= 5
    assert all(c.can_play for c in level.children)


async def test_browse_presets_folder(hass: HomeAssistant):
    entity = await _kitchen(hass)
    presets = await entity.async_browse_media(media_content_id="presets")
    assert [c.title for c in presets.children] == [
        "Serenity",
        "1980s Alternative Rock Classics",
    ]
    assert presets.children[0].media_content_id == "preset:6"


# --- play (through the real media_player.play_media service) -------------
async def _play_media(hass, entity, content_id, **extra):
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": entity.entity_id,
            "media_content_id": content_id,
            "media_content_type": "music",
            **extra,
        },
        blocking=True,
    )


async def test_play_preset(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await _play_media(hass, entity, "preset:7")
    assert ("load_preset", (7,)) in entity.coordinator.client.calls


async def test_play_item_now(hass: HomeAssistant):
    entity = await _kitchen(hass)
    cid = encode_item(BrowseItem(type="audio", play_url="/Play?url=Main"))
    await _play_media(hass, entity, cid)
    assert ("play_uri", ("/Play?url=Main",)) in entity.coordinator.client.calls


async def test_play_item_enqueue_next_uses_context_action(hass: HomeAssistant):
    entity = await _kitchen(hass)
    cid = encode_item(
        BrowseItem(type="album", play_url="/Play?x", context_menu_key="CMK")
    )
    await _play_media(hass, entity, cid, enqueue=MediaPlayerEnqueue.NEXT)
    play_uris = [c for c in entity.coordinator.client.calls if c[0] == "play_uri"]
    assert play_uris and "where=nextAlbum" in play_uris[-1][1][0]


async def test_play_all_container_via_context_menu(hass: HomeAssistant):
    # An artist node has no playURL — "Play all" comes from its context menu.
    entity = await _kitchen(hass)
    cid = encode_item(
        BrowseItem(
            type="artist", browse_key="LM:a", context_menu_key="LocalMusic:CM/artist"
        )
    )
    await _play_media(hass, entity, cid)
    play_uris = [c for c in entity.coordinator.client.calls if c[0] == "play_uri"]
    assert play_uris
    assert "playnow=1" in play_uris[-1][1][0] and "shuffle=0" in play_uris[-1][1][0]


async def test_play_all_genre_synthesizes_add(hass: HomeAssistant):
    # A genre has no playURL or context menu; play-all is synthesized from /Add.
    entity = await _kitchen(hass)
    cid = encode_item(
        BrowseItem(type="genre", browse_key="LocalMusic:MG/Genres?genre=Jazz")
    )
    await _play_media(hass, entity, cid)
    play_uris = [c for c in entity.coordinator.client.calls if c[0] == "play_uri"]
    assert ("play_uri", ("/Add?genre=Jazz&service=LocalMusic&playnow=1",)) in play_uris


# --- play queue (view + jump-to-track) ----------------------------------
async def test_root_has_play_queue(hass: HomeAssistant):
    entity = await _kitchen(hass)
    root = await entity.async_browse_media()
    assert "Play queue" in [c.title for c in root.children]


async def test_browse_queue_lists_tracks(hass: HomeAssistant):
    entity = await _kitchen(hass)
    queue = await entity.async_browse_media(media_content_id="queue")
    assert len(queue.children) == 3
    first = queue.children[0]
    assert first.can_play and "Anything but the Truth" in first.title
    # selecting a queue track jumps to that position
    await _play_media(hass, entity, first.media_content_id)
    assert ("play_uri", ("/Play?id=0",)) in entity.coordinator.client.calls


# --- play-queue services -------------------------------------------------
async def test_clear_queue_service(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await hass.services.async_call(
        DOMAIN, "clear_queue", {"entity_id": entity.entity_id}, blocking=True
    )
    assert ("clear_queue", ()) in entity.coordinator.client.calls


async def test_save_queue_service(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await hass.services.async_call(
        DOMAIN,
        "save_queue",
        {"entity_id": entity.entity_id, "name": "Dinner"},
        blocking=True,
    )
    assert ("save_queue", ("Dinner",)) in entity.coordinator.client.calls


async def test_remove_and_move_queue_services(hass: HomeAssistant):
    entity = await _kitchen(hass)
    await hass.services.async_call(
        DOMAIN,
        "remove_from_queue",
        {"entity_id": entity.entity_id, "position": 3},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "move_in_queue",
        {"entity_id": entity.entity_id, "from_position": 5, "to_position": 0},
        blocking=True,
    )
    calls = entity.coordinator.client.calls
    assert ("delete_track", (3,)) in calls
    assert ("move_track", (5, 0)) in calls


# --- search --------------------------------------------------------------
async def test_search_returns_results(hass: HomeAssistant):
    entity = await _kitchen(hass)
    result = await entity.async_search_media(SearchMediaQuery(search_query="jazz"))
    assert result.result  # list[BrowseMedia]
    assert all(item.can_play for item in result.result)


# --- context-menu services ----------------------------------------------
async def test_add_to_queue_service(hass: HomeAssistant):
    entity = await _kitchen(hass)
    cid = encode_item(BrowseItem(type="album", context_menu_key="CMK"))
    await hass.services.async_call(
        DOMAIN,
        "add_to_queue",
        {"entity_id": entity.entity_id, "media_content_id": cid, "mode": "last"},
        blocking=True,
    )
    play_uris = [c for c in entity.coordinator.client.calls if c[0] == "play_uri"]
    assert play_uris and "where=last" in play_uris[-1][1][0]


async def test_add_favourite_service(hass: HomeAssistant):
    entity = await _kitchen(hass)
    cid = encode_item(BrowseItem(type="album", context_menu_key="CMK"))
    await hass.services.async_call(
        DOMAIN,
        "add_favourite",
        {"entity_id": entity.entity_id, "media_content_id": cid},
        blocking=True,
    )
    play_uris = [c for c in entity.coordinator.client.calls if c[0] == "play_uri"]
    assert play_uris and play_uris[-1][1][0].startswith("/AddFavourite?")
