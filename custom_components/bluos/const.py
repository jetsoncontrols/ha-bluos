"""Constants for the BluOS integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "bluos"
LOGGER: Final = logging.getLogger(__package__)

# --- HTTP API -------------------------------------------------------------
DEFAULT_PORT: Final = 11000
# Multi-zone rack units (e.g. NAD CI580) expose one node per HTTP port spaced
# by this step: 11000, 11010, 11020, ... Used only as a manual-entry fallback
# when discovery cannot supply the exact port list.
ZONE_PORT_STEP: Final = 10
MAX_ZONES: Final = 8  # safety cap when probing ports for a single chassis

# Long polling (seconds). The spec recommends ~60-100s and forbids re-requesting
# the same resource less than 1s apart. We use 60 so an abrupt device death
# (which holds an in-flight long-poll open) is detected within ~75s instead of
# ~130s, while staying within the spec's recommended range.
STATUS_TIMEOUT: Final = 60
SYNC_TIMEOUT: Final = 60
# aiohttp total timeout must comfortably exceed the long-poll timeout.
REQUEST_TIMEOUT: Final = STATUS_TIMEOUT + 15
# Connecting to an unreachable host must fail fast, independent of the long-poll
# read timeout, so a missing device is detected promptly on each (re)connect.
CONNECT_TIMEOUT: Final = 7
MIN_REQUEST_INTERVAL: Final = 1.0
ERROR_BACKOFF: Final = 5.0
MAX_FAILURES: Final = 2  # consecutive failures before a node is marked unavailable

# Sentinel returned by /Status and /SyncStatus volume when the output is a
# fixed line level (no software volume control on that node).
FIXED_VOLUME: Final = -1
VOLUME_STEP_DB: Final = 3  # dB applied per volume up/down step

MANUFACTURER_FALLBACK: Final = "BluOS"

# Bus event fired when a node surfaces a service message/error (/Status notifyurl).
EVENT_NOTIFICATION: Final = "bluos_notification"

# --- LSDP discovery (Lenbrook Service Discovery Protocol) -----------------
LSDP_PORT: Final = 11430
LSDP_MAGIC: Final = b"LSDP"
LSDP_VERSION: Final = 1
LSDP_ALL_CLASSES: Final = 0xFFFF
# Classes that represent a controllable player node and should become entities.
# 0x0001 = primary player, 0x0003 = CI580 secondary node.
PLAYER_CLASSES: Final = frozenset({0x0001, 0x0003})
LSDP_QUERY_INTERVAL: Final = 300.0  # periodic active re-query

# --- config entry data keys ----------------------------------------------
CONF_HOST: Final = "host"
CONF_NODES: Final = "nodes"  # list of {"port": int, "mac": str, "name": str}
CONF_MAC: Final = "mac"  # base/primary node MAC = unit identity
