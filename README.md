# AriaCast Ecosystem — Direct Home Assistant Integration

This repo is the monorepo home for the AriaCast ⇄ Home Assistant ecosystem:
a native HACS integration + companion Home Assistant add-on that register
every AriaCast speaker on the LAN as a real `media_player` entity, with 2D
positional audio, dynamic node lifecycle handling, and Album Art → light
sync — no Music Assistant or other middleware required.

**Start here:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — maps every
piece below to the original spec and explains the design decisions.

## Layout

| Path | What it is |
|---|---|
| `custom_components/ariacast/` | HACS integration — `media_player` entities, config flow, services |
| `custom_components/ariacast/core/` | Shared logic used by both the integration and the add-on (DB, DSP, protocol client, node manager, hue sync, pub/sub, ducking) |
| `addon/ariacast_core/` | Home Assistant add-on (Docker) — the low-latency `AriaCastSocketServer` audio relay + the Web UI Canvas room editor |
| `repos/` | Vendored copies of the upstream AriaCast repos this ecosystem builds on (protocol spec, Python/Go receivers, Android app + receiver, cross-platform sender, plugins) |
| `scripts/build_addon.sh` | Stages the shared `core/` package into the add-on's Docker build context |

## Quick start

1. **HACS integration**: copy `custom_components/ariacast` into your HA
   `config/custom_components/`, restart HA, add the "AriaCast Direct"
   integration. Every AriaCast receiver discovered on the LAN (UDP broadcast
   + mDNS) becomes a `media_player.ariacast_*` entity automatically.
2. **Add-on** (optional, needed for TTS ducking + spatial DSP audio path +
   the Web UI Canvas editor): `bash scripts/build_addon.sh --build`, then
   install the resulting image as a local add-on, or point the Supervisor's
   add-on store at `addon/ariacast_core/`.
3. Open the add-on's sidebar panel (Ingress) to lay out rooms and drag
   speakers into position — everything syncs live over WebSocket to Home
   Assistant and the companion app.

## Protocol

This ecosystem is a consumer of the existing AriaCast wire protocol, not a
new one — see `repos/AriaCast-Protocol-Spec`. WebSocket-over-TCP on port
`12889` (`/audio`, `/control`, `/metadata`, `/stats`), UDP discovery on
`12888`, optional mDNS (`_audiocast._tcp`).
