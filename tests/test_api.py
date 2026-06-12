"""Tests for the BluOS XML parsing and client URL building."""

from __future__ import annotations

import pytest

from custom_components.bluos.api import (
    BluOsClient,
    BluOsConnectionError,
    BrowseResult,
    InputSource,
    PlayerStatus,
    Preset,
    SyncStatus,
)

from .helpers import load_fixture, status_for, sync_for


def test_status_metadata_mapping():
    # Zone 2 fixture has a loaded track (title1/2/3 = track/artist/album).
    status = status_for(11010)
    assert status.title1 == "Coastline"
    assert status.title2 == "Aurora"
    assert status.title3 == "Daydream"
    assert status.total_length == 296
    assert status.image and status.image.startswith("/Artwork")
    assert status.state == "stop"


@pytest.mark.parametrize(
    ("port", "fixed", "volume"),
    [(11000, True, -1), (11010, True, -1), (11020, False, 25), (11030, True, -1)],
)
def test_volume_fixed_detection(port, fixed, volume):
    status = status_for(port)
    assert status.volume == volume
    assert status.volume_fixed is fixed


def test_syncstatus_identity():
    sync = sync_for(11020)
    assert sync.name == "Kitchen Speakers"
    assert sync.mac == "AA:BB:CC:00:11:22:11020"
    assert sync.model_name == "CI580"
    assert sync.brand == "NAD"
    assert sync.sync_stat == "8"  # the syncStat attribute, not the etag


def test_syncstatus_grouping_parse():
    primary = SyncStatus.from_xml(
        '<SyncStatus etag="9" name="Primary" mac="aa" id="192.0.2.20:11000">'
        '<slave port="11000" id="192.168.1.11"/>'
        '<slave port="11000" id="192.168.1.12"/>'
        "</SyncStatus>"
    )
    assert primary.master is None
    assert primary.slaves == [("192.168.1.11", 11000), ("192.168.1.12", 11000)]

    secondary = SyncStatus.from_xml(
        '<SyncStatus etag="9" name="Secondary" mac="bb" id="192.168.1.11:11000">'
        '<master port="11000">192.0.2.20</master>'
        "</SyncStatus>"
    )
    assert secondary.master == ("192.0.2.20", 11000)
    assert secondary.slaves == []


def test_invalid_xml_raises():
    with pytest.raises(BluOsConnectionError):
        PlayerStatus.from_xml("not xml <<<")


def test_client_url():
    client = BluOsClient(session=None, host="1.2.3.4", port=11010)
    assert client.base_url == "http://1.2.3.4:11010"


def test_status_parses_prid():
    assert status_for(11000).prid == "0"


def test_presets_parse():
    presets = Preset.list_from_xml(load_fixture("presets.xml"))
    assert [p.id for p in presets] == [6, 7]
    assert presets[0].name == "Serenity"
    # XML entities are unescaped (&amp; -> &), ready to use as-is.
    assert "&id=" in presets[1].url


def test_inputs_parse():
    inputs = InputSource.list_from_xml(load_fixture("radiobrowse_capture.xml"))
    assert [i.name for i in inputs] == ["Analog Input", "Optical Input"]
    assert inputs[0].type_index == "analog-1"
    assert inputs[1].input_type == "spdif"


def test_browse_root_parse():
    result = BrowseResult.from_xml(load_fixture("browse_root.xml"))
    assert result.type == "menu"
    by_text = {i.text: i for i in result.items}
    assert by_text["Playlists"].can_expand and not by_text["Playlists"].can_play
    assert by_text["Analog Input"].can_play and not by_text["Analog Input"].can_expand
    assert by_text["Radio Paradise"].browse_key == "RadioParadise:"


def test_browse_flattens_categories():
    result = BrowseResult.from_xml(load_fixture("browse_radioparadise.xml"))
    assert len(result.items) >= 5
    assert all(i.type == "audio" for i in result.items)
    assert result.items[0].play_url and result.items[0].context_menu_key


def test_context_menu_parse():
    result = BrowseResult.from_xml(load_fixture("browse_contextmenu_rich.xml"))
    assert result.type == "contextMenu"
    actions = {i.type: i.action_url for i in result.items}
    assert actions["favourite-add"].startswith("/AddFavourite?")
    assert actions["add-now"].startswith("/Add?")


def test_browse_error_raises():
    with pytest.raises(BluOsConnectionError):
        BrowseResult.from_xml("<error><message>nope</message></error>")


class _Resp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def read(self):
        return b""


class _RecordingSession:
    def __init__(self):
        self.url = None

    def get(self, url, **kwargs):
        self.url = url
        return _Resp()


async def test_play_uri_does_not_double_encode():
    session = _RecordingSession()
    client = BluOsClient(session, "1.2.3.4", 11000)
    await client.play_uri("/Play?url=Capture%3Aplughw%2C0&title=Main")
    url = str(session.url)
    assert url.startswith("http://1.2.3.4:11000/Play?url=Capture%3A")
    assert "%253A" not in url  # already-encoded %3A must be preserved verbatim
