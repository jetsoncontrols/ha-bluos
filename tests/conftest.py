"""Pytest configuration for the BluOS integration tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture(autouse=True)
def disable_lsdp_sockets():
    """Never open a real UDP discovery socket during tests."""
    with patch("custom_components.bluos.LsdpDiscovery.async_start", return_value=False):
        yield


@pytest.fixture(autouse=True)
def mock_clientsession():
    """Stub the shared aiohttp session so no real DNS resolver thread starts.

    Every test serves data through fakes/mocks, so the session object is never
    actually used; creating the real one would spawn a c-ares resolver thread
    that the strict test cleanup flags as a leak.
    """
    with (
        patch("custom_components.bluos.async_get_clientsession"),
        patch("custom_components.bluos.config_flow.async_get_clientsession"),
    ):
        yield
