"""Mapping between the BluOS `/Browse` tree and Home Assistant `BrowseMedia`.

Content-id scheme (all strings, stateless):
- ``root``           the top level (Presets folder + /Browse root)
- ``presets``        the synthetic Presets folder
- ``preset:<id>``    a playable preset
- ``input:<idx>``    a playable physical input (by typeIndex)
- ``item:<b64>``     any /Browse item; the base64url-encoded JSON payload carries
                     the item's browseKey (``b``), playURL (``p``), autoplayURL
                     (``a``) and contextMenuKey (``c``) so a single id supports
                     both expanding and playing.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaType,
)

from .api import BrowseItem, BrowseResult, Preset

ROOT = "root"
PRESETS = "presets"
ITEM_PREFIX = "item:"
PRESET_PREFIX = "preset:"
INPUT_PREFIX = "input:"
DIRECTORY = "directory"

_TYPE_TO_CLASS: dict[str, MediaClass] = {
    "audio": MediaClass.MUSIC,
    "track": MediaClass.TRACK,
    "album": MediaClass.ALBUM,
    "playlist": MediaClass.PLAYLIST,
    "artist": MediaClass.ARTIST,
    "composer": MediaClass.ARTIST,
    "genre": MediaClass.GENRE,
    "folder": MediaClass.DIRECTORY,
    "link": MediaClass.DIRECTORY,
    "section": MediaClass.DIRECTORY,
    "menu": MediaClass.DIRECTORY,
}

# Container types that are "play all"-able via their context menu even when they
# expose no direct playURL/autoplayURL (e.g. local-library artists/genres).
_CONTEXT_PLAYABLE_TYPES = frozenset(
    {"artist", "album", "playlist", "composer", "genre", "folder", "track", "audio"}
)


def media_url(host: str, port: int, path: str | None) -> str | None:
    """Resolve a BluOS image/path to an absolute, redirect-safe URL."""
    if not path:
        return None
    if path.startswith("http"):
        return path
    url = f"http://{host}:{port}{path}"
    if "/Artwork" in path and "followRedirects" not in path:
        url += ("&" if "?" in path else "?") + "followRedirects=1"
    return url


# --- content-id codec ----------------------------------------------------
def encode_item(item: BrowseItem) -> str:
    payload = {
        "b": item.browse_key,
        "p": item.play_url,
        "a": item.autoplay_url,
        "c": item.context_menu_key,
    }
    raw = json.dumps({k: v for k, v in payload.items() if v}).encode()
    return ITEM_PREFIX + base64.urlsafe_b64encode(raw).decode()


def decode_item(content_id: str) -> dict[str, str]:
    raw = base64.urlsafe_b64decode(content_id[len(ITEM_PREFIX) :])
    return json.loads(raw)


# --- BrowseMedia builders ------------------------------------------------
def _media_class(item: BrowseItem) -> MediaClass:
    if item.type in _TYPE_TO_CLASS:
        return _TYPE_TO_CLASS[item.type]
    return MediaClass.DIRECTORY if item.can_expand else MediaClass.MUSIC


def _is_playable(item: BrowseItem) -> bool:
    # A direct play URL, or a "play all"-able container with a context menu.
    return item.can_play or (
        item.context_menu_key is not None and item.type in _CONTEXT_PLAYABLE_TYPES
    )


def item_to_browse_media(item: BrowseItem, host: str, port: int) -> BrowseMedia | None:
    """Convert one BluOS browse item to a BrowseMedia node, or None to skip."""
    playable = _is_playable(item)
    if not (item.can_expand or playable):
        return None  # plain text / non-actionable node
    return BrowseMedia(
        media_class=_media_class(item),
        media_content_id=encode_item(item),
        media_content_type=DIRECTORY if item.can_expand else MediaType.MUSIC,
        title=item.text or item.text2 or "",
        can_play=playable,
        can_expand=item.can_expand,
        thumbnail=media_url(host, port, item.image),
    )


def preset_to_browse_media(preset: Preset, host: str, port: int) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.MUSIC,
        media_content_id=f"{PRESET_PREFIX}{preset.id}",
        media_content_type=MediaType.MUSIC,
        title=preset.name,
        can_play=True,
        can_expand=False,
        thumbnail=media_url(host, port, preset.image),
    )


def presets_folder(presets: list[Preset], host: str, port: int) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=PRESETS,
        media_content_type=DIRECTORY,
        title="Presets",
        can_play=False,
        can_expand=True,
        children_media_class=MediaClass.MUSIC,
        children=[preset_to_browse_media(p, host, port) for p in presets],
    )


def _children_from_result(
    result: BrowseResult, host: str, port: int
) -> list[BrowseMedia]:
    children = [
        bm
        for item in result.items
        if (bm := item_to_browse_media(item, host, port)) is not None
    ]
    if result.next_key:
        # Expose paging as a "More" folder that loads the next page when opened.
        more = BrowseItem(type="link", text="More…", browse_key=result.next_key)
        more_bm = item_to_browse_media(more, host, port)
        if more_bm is not None:
            children.append(more_bm)
    return children


def root_node(
    result: BrowseResult, presets: list[Preset], host: str, port: int
) -> BrowseMedia:
    children: list[BrowseMedia] = []
    if presets:
        children.append(presets_folder(presets, host, port))
    children.extend(_children_from_result(result, host, port))
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=ROOT,
        media_content_type=DIRECTORY,
        title="BluOS",
        can_play=False,
        can_expand=True,
        children_media_class=MediaClass.DIRECTORY,
        children=children,
    )


def level_node(
    content_id: str, result: BrowseResult, host: str, port: int
) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=DIRECTORY,
        title=result.service_name or "BluOS",
        can_play=False,
        can_expand=True,
        children_media_class=MediaClass.DIRECTORY,
        children=_children_from_result(result, host, port),
    )


def search_node(query: str, result: BrowseResult, host: str, port: int) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=f"search:{query}",
        media_content_type=DIRECTORY,
        title=f'Results for "{query}"',
        can_play=False,
        can_expand=True,
        children_media_class=MediaClass.DIRECTORY,
        children=_children_from_result(result, host, port),
    )


def pick_context_action(result: BrowseResult, *wanted: str) -> str | None:
    """Return the actionURL of the first context-menu item matching a wanted type.

    `wanted` is tried in order; for each, an exact type match is preferred, then
    a same-family match (e.g. ``add-last`` also accepts ``addAll-last``).
    """
    by_type: dict[str, str] = {
        item.type: item.action_url
        for item in result.items
        if item.action_url and item.type
    }
    for want in wanted:
        if want in by_type:
            return by_type[want]
        suffix = want.split("-", 1)[-1]
        for item_type, url in by_type.items():
            if item_type.endswith(f"-{suffix}"):
                return url
    return None


def _action_params(action_url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(action_url).query).items()}


def pick_play_action(result: BrowseResult) -> str | None:
    """Return the actionURL that plays the item *now* (clear + play, no shuffle).

    BluOS overloads the context-menu ``type`` (e.g. both "Add all" and "Play all"
    are ``addAll-last``), so the reliable signal is the actionURL's ``playnow``
    parameter: ``playnow=1`` (and not ``shuffle=1``) means play now.
    """
    explicit = pick_context_action(result, "add-now", "addAll-now")
    if explicit:
        return explicit
    for item in result.items:
        if not item.action_url:
            continue
        params = _action_params(item.action_url)
        if params.get("playnow") == "1" and params.get("shuffle", "0") != "1":
            return item.action_url
    return None


def as_dict(node: BrowseMedia) -> dict[str, Any]:
    """Helper for tests."""
    return node.as_dict()
