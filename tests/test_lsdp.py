"""Tests for the LSDP wire codec."""

from __future__ import annotations

from pathlib import Path

from custom_components.bluos.lsdp import (
    build_query,
    parse_packet,
    units_from_announces,
)

PACKET = (Path(__file__).parent / "fixtures" / "lsdp_announce_ci580.bin").read_bytes()


def test_build_query_bytes():
    assert build_query().hex() == "064c53445001055101ffff"


def test_parse_real_ci580_packet():
    announces = parse_packet(PACKET)
    # The CI580 splits its four nodes across two Announce messages.
    assert len(announces) == 2
    assert all(a.host == "192.168.1.60" for a in announces)
    assert all(a.node_id == "9056820a237c" for a in announces)


def test_units_merge_and_filter():
    units = units_from_announces(parse_packet(PACKET))
    assert len(units) == 1
    unit = units[0]
    assert unit.host == "192.168.1.60"
    assert unit.node_id == "9056820a237c"
    # Player classes only (the 0x0004 mgmt record on port 11431 is excluded).
    assert [n.port for n in unit.nodes] == [11000, 11010, 11020, 11030]
    assert [n.cls for n in unit.nodes] == [0x0001, 0x0003, 0x0003, 0x0003]
    assert unit.nodes[2].name == "Kitchen Speakers"


def test_parse_garbage_is_safe():
    assert parse_packet(b"") == []
    assert parse_packet(b"\x06XXXX\x01") == []  # bad magic
    # Truncated announce should not raise.
    assert parse_packet(PACKET[:20]) is not None
