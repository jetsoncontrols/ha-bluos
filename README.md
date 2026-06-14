# BluOS for Home Assistant

A [Home Assistant](https://www.home-assistant.io/) custom integration for **BluOS** media players — the streaming platform behind **Bluesound**, **NAD**, **DALI** and other brands.

[![Release](https://img.shields.io/github/v/release/jetsoncontrols/ha-bluos?display_name=tag)](https://github.com/jetsoncontrols/ha-bluos/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.x-41BDF5.svg)](https://www.home-assistant.io/)
[![Tests](https://img.shields.io/github/actions/workflow/status/jetsoncontrols/ha-bluos/test.yml?branch=main&label=tests)](https://github.com/jetsoncontrols/ha-bluos/actions/workflows/test.yml)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE)

It exposes one `media_player` entity per player **node**. Most BluOS products are standalone single-zone players, but rack-mount units such as the **NAD CI580** present several independent players on a single IP (one per HTTP port); each becomes its own entity, grouped under a parent device.

## Features

- **Auto-discovery** — zeroconf/mDNS **and** Lenbrook **LSDP** (UDP broadcast, for networks where multicast is unreliable), plus manual IP entry that auto-enumerates every zone of a multi-zone unit.
- **One entity per player node** — multi-zone units appear as a parent device (e.g. *NAD CI580*) with each zone "connected via" it.
- **Transport** — play, pause, stop, next, previous, shuffle, repeat, and **seek** (on seekable sources).
- **Volume & mute** — shown only on nodes with an adjustable level; fixed line-level outputs correctly omit volume controls.
- **Speaker grouping** — join/unjoin from the HA media UI, including across separate BluOS units; the group leader's volume/mute moves the whole group (preserving each speaker's balance).
- **Per-zone audio settings** — tone controls, treble/bass, replay-gain, output mode and output-level-fixed exposed as configuration entities (where the hardware supports them).
- **Firmware updates** — surfaced as a native Home Assistant `update` entity (Settings → Updates), with an install button.
- **Device maintenance** — reboot, reindex music library, and a unit-wide doorbell chime, on the chassis device.
- **Status events** — service messages/errors from the player are emitted as a `bluos_notification` bus event for automations.
- **Sources** — physical inputs (Analog, Optical, …) and saved presets in the Source dropdown.
- **Browse, search & play** — the full BluOS tree (services, radio, playlists, your library) plus the Home Assistant media library; **"Play all"** on albums, artists and genres; `play_media` with `enqueue`; TTS/announcements via `media_source`.
- **Play queue** — view the current queue in the media browser (tap a track to jump), plus services to clear/save/remove/move tracks.
- **Local push** — near-instant updates over BluOS long-polling; per-node availability.

## Installation

> [!NOTE]
> This integration is licensed for **noncommercial** use, so it is distributed as a **HACS custom repository** (not the default HACS store).

### HACS (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jetsoncontrols&repository=ha-bluos&category=integration)

1. Click the button above, or in HACS go to **⋮ → Custom repositories**, add `https://github.com/jetsoncontrols/ha-bluos` with category **Integration**.
2. Install **BluOS** and **restart** Home Assistant.

### Manual

Copy `custom_components/bluos` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Discovered players appear under **Settings → Devices & Services** — just accept the prompt. To add one manually, choose **+ Add Integration → BluOS** and enter the player's IP. For a multi-zone unit, enter the IP once and every zone is added.

## Services

| Service | Description |
|-|-|
| `bluos.add_to_queue` | Add a browsed item to the queue (`now` / `next` / `last`) |
| `bluos.add_favourite` | Mark a browsed item as a favourite in its music service |
| `bluos.clear_queue` | Clear the play queue |
| `bluos.save_queue` | Save the current queue as a named BluOS playlist |
| `bluos.remove_from_queue` | Remove the track at a queue position |
| `bluos.move_in_queue` | Move a track to a new queue position |

Adding to the queue is also supported natively via `media_player.play_media` with the `enqueue` option.

## Supported devices

Any BluOS player implementing the [BluOS Custom Integration API](https://bluos.io/) (Bluesound, NAD, DALI, …). Developed and verified against a **NAD CI580** 4-zone unit; standalone players use the same code path. Requires **Home Assistant 2026.x**.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check custom_components/ tests/
ruff format --check custom_components/ tests/
pytest tests/ -q
```

Tests run against recorded fixtures under `tests/fixtures/` (sanitized `/Status`, `/SyncStatus`, browse, queue and LSDP captures), so no hardware is required.

## License

**PolyForm Noncommercial License 1.0.0** — free to use, modify and share for any **noncommercial** purpose. Commercial use requires a separate license from the author. See [LICENSE](LICENSE).

## Trademarks

BluOS is a trademark of Lenbrook Industries Limited. This is an unofficial, community integration, not affiliated with or endorsed by Lenbrook.
