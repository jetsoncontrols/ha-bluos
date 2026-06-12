"""Tests for the BluOS XML parsing and client URL building."""

from __future__ import annotations

import pytest

from custom_components.bluos.api import (
    BluOsClient,
    BluOsConnectionError,
    PlayerStatus,
    SyncStatus,
)

from .helpers import status_for, sync_for


def test_status_metadata_mapping():
    # Zone 2 fixture has a loaded track (title1/2/3 = track/artist/album).
    status = status_for(11010)
    assert status.title1 == "13 Beaches"
    assert status.title2 == "Lana Del Rey"
    assert status.title3 == "Lust for Life"
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
    assert sync.mac == "90:56:82:0A:23:7C:11020"
    assert sync.model_name == "CI580"
    assert sync.brand == "NAD"
    assert sync.sync_stat == "8"  # the syncStat attribute, not the etag


def test_syncstatus_grouping_parse():
    primary = SyncStatus.from_xml(
        '<SyncStatus etag="9" name="Primary" mac="aa" id="192.168.1.10:11000">'
        '<slave port="11000" id="192.168.1.11"/>'
        '<slave port="11000" id="192.168.1.12"/>'
        "</SyncStatus>"
    )
    assert primary.master is None
    assert primary.slaves == [("192.168.1.11", 11000), ("192.168.1.12", 11000)]

    secondary = SyncStatus.from_xml(
        '<SyncStatus etag="9" name="Secondary" mac="bb" id="192.168.1.11:11000">'
        '<master port="11000">192.168.1.10</master>'
        "</SyncStatus>"
    )
    assert secondary.master == ("192.168.1.10", 11000)
    assert secondary.slaves == []


def test_invalid_xml_raises():
    with pytest.raises(BluOsConnectionError):
        PlayerStatus.from_xml("not xml <<<")


def test_client_url():
    client = BluOsClient(session=None, host="1.2.3.4", port=11010)
    assert client.base_url == "http://1.2.3.4:11010"
