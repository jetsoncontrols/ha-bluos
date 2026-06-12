# ha-bluos

A [Home Assistant](https://www.home-assistant.io/) custom integration for **BluOS** media players — the streaming platform used by Bluesound, NAD, DALI and other brands. Installable via [HACS](https://hacs.xyz/).

It exposes one `media_player` entity per player **node**. Most BluOS products are standalone single-zone players, but rack-mount units such as the **NAD CI580** present several independent players on a single IP address (one per HTTP port); each becomes its own entity, grouped under a parent device.

## Features

- **Auto-discovery** via zeroconf/mDNS **and** Lenbrook's **LSDP** (UDP broadcast), which works on networks where multicast/mDNS is unreliable. Manual entry by IP is always available and automatically enumerates every zone of a multi-zone unit.
- **One entity per player node.** Multi-zone units appear as a parent device (e.g. *NAD CI580*) with each zone "connected via" it.
- **Transport controls:** play, pause, stop, next, previous, shuffle, repeat.
- **Volume & mute** — advertised only on nodes with an adjustable level. Fixed line-level outputs (which BluOS reports as `volume = -1`) correctly omit volume controls.
- **Speaker grouping** — join/unjoin players from the Home Assistant media UI, including across separate BluOS units.
- **Local push** — state updates arrive over BluOS long-polling, so changes show up near-instantly without aggressive polling.

Media browsing is **not** included in this initial release.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a **Custom repository** (category: *Integration*): `https://github.com/jetsoncontrols/ha-bluos`.
2. Install **BluOS** and restart Home Assistant.

### Manual

Copy `custom_components/bluos` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

Players are discovered automatically — accept the discovery prompt under **Settings → Devices & Services**. To add one manually, choose **+ Add Integration → BluOS** and enter the player's IP address. For a multi-zone unit, enter the unit's IP once and every zone is added.

## Supported devices

Any BluOS player that implements the [BluOS Custom Integration API](https://bluos.io/) (Bluesound, NAD, DALI, …). Developed and verified against a **NAD CI580** 4-zone unit; standalone players are supported through the same code path.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
ruff check custom_components/ tests/
ruff format --check custom_components/ tests/
pytest tests/ -q
```

Tests run against recorded fixtures (real `/Status`, `/SyncStatus`, and an LSDP packet captured from a CI580) under `tests/fixtures/`, so no hardware is required.

## License & trademarks

BluOS is a trademark of Lenbrook Industries Limited. This is an unofficial, community integration and is not affiliated with or endorsed by Lenbrook.
