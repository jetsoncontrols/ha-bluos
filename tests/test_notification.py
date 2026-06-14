"""The bluos_notification bus event (from /Status notifyurl)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.bluos.api import Notification, PlayerStatus
from custom_components.bluos.const import DOMAIN, EVENT_NOTIFICATION

from .helpers import load_fixture
from .test_init import BASE_MAC, _setup


def test_notification_parse():
    note = Notification.from_xml(load_fixture("notification_error.xml"))
    assert note.message == "Error: 502 Bad Gateway\nTuneIn:s23833"
    assert note.clear_url == "/error?clear=1&counter=291"
    assert note.action == "dialog"


def test_status_parses_notify_url():
    assert (
        PlayerStatus.from_xml(
            "<status><notifyurl>/error?5</notifyurl></status>"
        ).notify_url
        == "/error?5"
    )
    assert PlayerStatus.from_xml("<status/>").notify_url is None


async def test_notification_event_fires_and_dedupes(hass: HomeAssistant):
    await _setup(hass)
    coordinator = hass.data[DOMAIN].coordinators_by_addr[("192.0.2.10", 11000)]
    events = async_capture_events(hass, EVENT_NOTIFICATION)

    coordinator._maybe_fire_notification(PlayerStatus(notify_url="/error?291"))
    await hass.async_block_till_done()
    assert len(events) == 1
    data = events[0].data
    assert data["message"] == "Error: 502 Bad Gateway\nTuneIn:s23833"
    assert data["action"] == "dialog"
    assert data["mac"] == BASE_MAC
    assert data["entity_id"].startswith("media_player.")

    # Same notifyurl in the steady-state long-poll must not refire.
    coordinator._maybe_fire_notification(PlayerStatus(notify_url="/error?291"))
    await hass.async_block_till_done()
    assert len(events) == 1

    # A new message (counter changed) fires again.
    coordinator._maybe_fire_notification(PlayerStatus(notify_url="/error?292"))
    await hass.async_block_till_done()
    assert len(events) == 2
