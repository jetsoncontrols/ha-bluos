# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A HACS-installable Home Assistant custom integration (`domain: bluos`) for BluOS media players (Bluesound / NAD / DALI). Integration code lives in `custom_components/bluos/`. The BluOS protocol is HTTP GET / UTF-8 XML on port 11000 (multi-zone rack units like the NAD CI580 use 11000/11010/11020/…); the authoritative spec is the BluOS Custom Integration API v1.7 PDF. `iot_class` is `local_push` (state arrives via long-polling).

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt        # HA test stack + ruff
pytest tests/ -q                             # full suite
pytest tests/test_api.py -q                  # one module
pytest tests/test_init.py::test_unload -q    # one test
ruff check custom_components/ tests/         # lint
ruff format --check custom_components/ tests/
```

Tests use `pytest-homeassistant-custom-component` and recorded fixtures in `tests/fixtures/` (real `/Status`, `/SyncStatus`, and a captured LSDP packet) — no hardware needed. CI (`.github/workflows/`) runs hassfest + HACS validation and the ruff/pytest job.

## Architecture

**One config entry per physical unit** (keyed by the primary node's MAC). At setup it creates **one coordinator + one `media_player` per player node**, each with its own long-poll loops so a stuck zone can't block its siblings.

Module responsibilities:
- `api.py` — HA-agnostic async client + ElementTree XML models (`PlayerStatus`, `SyncStatus`, `NodeInfo`). `async_enumerate_nodes()` discovers a chassis's nodes (explicit ports from discovery, else probes the `11000 + N*10` pattern). No third-party runtime deps.
- `coordinator.py` — `BluOsCoordinator` (per node, `update_interval=None`). Two background long-poll loops (`/Status`, `/SyncStatus`) using etag + `timeout`, honouring the spec's ">1s between identical requests" rule, with failure backoff. `normalize_mac()` is the single source of truth for MAC/unique-id canonicalisation (lowercase) — secondary nodes report a pseudo-MAC `<base>:<port>` that `format_mac` won't normalise, so always go through `normalize_mac`.
- `media_player.py` — entity. **Dynamic `supported_features`**: volume features are added only when `volume != -1` (fixed-output zones omit them). Metadata maps `title1/title2/title3 → title/artist/album` (spec mandate). Grouping resolves entities through the cross-unit registry in `hass.data[DOMAIN]`.
- `lsdp.py` — Lenbrook LSDP codec + UDP-broadcast discovery (port 11430). Player classes = `{0x0001, 0x0003}`; verified against `tests/fixtures/lsdp_announce_ci580.bin`.
- `config_flow.py` — `user` / `zeroconf` / `integration_discovery` (LSDP) steps; dedupes a unit's many node advertisements to one entry via the base MAC. `ZeroconfServiceInfo` is imported only under `TYPE_CHECKING` — importing the zeroconf component at runtime pulls in aiohttp's c-ares resolver (a real thread).
- `__init__.py` — starts LSDP discovery in `async_setup`; per-entry setup builds coordinators, registers the chassis parent device + per-node devices (`via_device`), and maintains the `hass.data[DOMAIN]` registry (`coordinators_by_mac`, `coordinators_by_addr`) used for grouping.

## Conventions & gotchas

- All MAC/unique-id handling goes through `normalize_mac` (in `coordinator.py`); never use raw `format_mac` for node ids.
- Device hierarchy: chassis device identifier is `(DOMAIN, "unit-<base_mac>")` (distinct from the primary node's `(DOMAIN, <base_mac>)` to avoid a collision); secondary nodes set `via_device` to the chassis. Standalone units have no chassis device.
- In tests, never create a real aiohttp session or UDP socket — `tests/conftest.py` autouse-stubs `async_get_clientsession` and `LsdpDiscovery.async_start`; the strict cleanup check flags the leaked resolver/socket threads otherwise.
- Test helpers (fixture loaders, `FakeClient`) live in `tests/helpers.py`, not `conftest.py`, so test modules can import them.
- Discovery only needs to surface a unit's host; the config flow enumerates the full node list itself.
