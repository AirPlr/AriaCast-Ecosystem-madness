# AriaCast Direct — Home Assistant Ecosystem Architecture

This document maps the 8 sections of the master spec to what's actually
implemented in this repo, and explains the design decisions made to fit the
**real** AriaCast wire protocol (see `repos/AriaCast-Protocol-Spec`), which
this ecosystem builds on rather than reinvents.

## Ground truth: the real protocol

Before writing any of this, the existing `Ariacast-server-python` (aiohttp)
and `AriaCast-Server-GO` (gorilla/websocket) implementations were read in
full, because the ecosystem has to interoperate with the **existing**
Android sender app, not a hypothetical one. Key facts that shaped every
module below:

- Transport is **WebSocket-over-TCP** on port `12889` (`/audio`, `/control`,
  `/metadata`, `/stats`), plus **UDP broadcast** discovery on `12888` and
  optional mDNS (`_audiocast._tcp`). It is not a bespoke raw-TCP protocol.
- A receiver accepts **exactly one `/audio` Sender at a time** (HTTP 403 on
  a second connection attempt). This single fact drives the whole
  `AriaCastSocketServer` design (see §8below) — Home Assistant can't just
  open a second stream to duck in a TTS notification.
- There is **no timestamp in audio frames** and **no heartbeat** defined in
  the protocol. `SpeakerNodeManager`'s heartbeat therefore rides the
  transport-level WebSocket ping/pong on the persistent `/control`
  connection instead of inventing a JSON heartbeat message every receiver
  would need to be updated to understand.
- Metadata field names are inconsistently camelCase (Android) vs snake_case
  (servers); `core/protocol_client.py::_normalize_metadata` folds both.

## Repository layout

```
repos/                          # vendored copies of all 7 source repos (no .git)
custom_components/ariacast/     # §1 HACS integration — the HA-facing half
  core/                         # shared logic, zero `homeassistant` imports
    models.py                   # §3A schema dataclasses
    protocol_client.py          # real AriaCast WS client (control/metadata/audio)
    database.py                 # §3 DatabaseService (SQLite + pub/sub emit)
    node_manager.py              # §2 SpeakerNodeManager
    dsp.py                      # §6 RoomSpatialAudioDSP
    hue_sync.py                 # §7 AlbumArtHueSync
    pubsub.py                   # §3B WebSocket pub/sub fan-out
    ducking.py                  # §1B AudioDuckingMixer (PCM-domain)
  media_player.py               # §1 AriaCastMediaPlayer(MediaPlayerEntity)
  __init__.py, config_flow.py, coordinator.py, services.yaml
addon/ariacast_core/            # §8 the add-on: AriaCastSocketServer + Web UI
  app/socket_server.py          # §8 low-latency relay, TCP_NODELAY, adaptive buffer
  app/main.py                   # add-on entry point, REST API, hue sync wiring
  app/www/index.html            # §4 WebUIEditorCanvas
  config.yaml, Dockerfile, run.sh
scripts/build_addon.sh          # stages shared core/ into the add-on build context
```

The **HACS integration** and the **add-on** share one Python package
(`custom_components/ariacast/core`) so the DB schema, DSP math, and protocol
client are defined exactly once. The integration runs it in-process inside
Home Assistant Core for control/state (§1, §2, §3); the add-on runs the same
package standalone for the parts that need to own raw sockets and a
persistent audio pipeline (§7, §8) that don't belong inside `homeassistant`'s
event loop.

---

## §1 — HACS integration & native `media_player`

`custom_components/ariacast/media_player.py::AriaCastMediaPlayer` implements
`MediaPlayerEntity` with `PLAY/PAUSE/STOP/SEEK/VOLUME_SET/VOLUME_MUTE/
PREVIOUS_TRACK/NEXT_TRACK/TURN_ON/TURN_OFF/PLAY_MEDIA`. Every command maps
1:1 onto the real `/control` JSON messages from `control.md` — no new wire
format. Entities are created reactively as `SpeakerNodeManager` discovers or
adopts speakers, and `state`/`available` derive directly from
`SpeakerStatus` (`online`/`offline`/`unavailable`), so a sleeping PC shows
`unavailable` in HA within one heartbeat cycle.

**Ducking (§1B)**: because a receiver only takes one Sender, `play_media`
can't open a second stream. It POSTs to the add-on's `/api/notify`, which
hands the clip to `AudioDuckingMixer` (`core/ducking.py`) — the *only* thing
actually holding the receiver's `/audio` connection is
`AriaCastSocketServer`, so it's the only thing that can duck.

## §2 — Dynamic node lifecycle (`SpeakerNodeManager`)

`core/node_manager.py`. Heartbeat = the persistent `/control` WebSocket's
RFC6455 ping/pong (`AriaCastSpeakerClient.is_heartbeat_alive`), polled every
1.5s. A 6-second grace period (`OFFLINE_GRACE_S`) absorbs a single missed
beat before flipping `speakers.status` to `offline`, so a momentary Wi-Fi
blip doesn't trigger a DSP re-balance storm. `_discovery_loop` re-sweeps UDP
broadcast + mDNS every 30s to adopt new hardware automatically. On
reconnect, `ManagedNode.crossfade_factor()` exposes a 0→1 ramp over 200ms
that `AriaCastSocketServer` reads when it resumes writing frames to that
output.

## §3 — Database & real-time sync

`core/database.py::DatabaseService` — SQLite via `aiosqlite`, schema exactly
as specified (`rooms`/`speakers`/`lights`/`app_state`). Every mutating call
(`upsert_speaker`, `set_speaker_status`, `update_speaker_dsp`, …) emits an
event through an in-process listener list; `core/pubsub.py::PubSubServer`
subscribes to that and fans events out over a `/ws` WebSocket to every
connected Web UI / companion app client, plus HA's own dispatcher bus via
`coordinator.py`. One write → three UIs update in one round trip.

## §4 — Web UI / PC dashboard

`addon/ariacast_core/app/www/index.html` — the `WebUIEditorCanvas`. Plain
HTML5 Canvas (no framework, to keep the add-on image small and avoid a
build step), connects to `/ws` for the live snapshot + event stream and to
the REST API (`/api/rooms`, `/api/speakers/{id}/position`, `/api/lights`,
`/api/listener-position`) for edits. Speaker icons are colored by
`SpeakerStatus`; dragging a speaker or the listener marker persists
immediately (no "Save" button on the canvas itself — matches the "instant
persistence" requirement in §3B). Served through the add-on's Home
Assistant **Ingress** (`config.yaml: ingress: true`), which is what gives it
a sidebar panel without any extra panel-registration code in the
integration.

## §5 — Companion app: HA Mode & Room Canvas

Given the scope of a mature, existing Kotlin/Compose Android app, this pass
adds one **additive, self-contained** file rather than rewiring the existing
UI: `repos/AriaCast-app/.../ha/HaEcosystemBridge.kt`, providing:

- `HAModeManager` — a `SharedPreferences`-backed "HA Mode" toggle plus the
  add-on's base URL, ready to be wired into `SettingsActivity`.
- `HaEcosystemClient` — fetches HA-Area-backed rooms from `/api/rooms` and
  pushes listener/speaker position updates to the same REST API the Web UI
  Canvas uses, so a phone drag and a PC drag hit the identical endpoint.

Wiring this into the existing room-selector Compose screens and the mobile
Room Canvas view is the natural next PR — it's scoped separately so it can
go through the app's existing Compose UI review pattern instead of being
guessed at from outside.

## §6 — Room Spatial Audio DSP

`core/dsp.py::RoomSpatialAudioDSP`. Restricted to `SpeakerStatus.ONLINE`
speakers only (`compute()` filters before any math runs), so an offline
speaker doesn't eat a share of the panning energy budget. Two coefficients
per speaker:

- **Gain** — energy-preserving VBAP-style weighting
  (`w_i = 1/d_i² / ‖1/d²‖`, converted to dB, clamped to [-24, 0] dB).
- **Delay** — `Δt = (d_i - d_min) / 343 × 1000` ms, exactly the spec
  formula; documented in the module as a Haas-effect directional cue
  (nearest speaker = 0 added delay), not a physical wavefront-alignment
  compensation, since those would use `d_max` instead of `d_min`.

`AriaCastSocketServer` is where these numbers actually touch audio: gain is
a per-frame `float32` multiply, delay is realized as a silence-prefill on
that output's queue before real frames start flowing.

## §7 — Album Art Hue Sync

`core/hue_sync.py::AlbumArtHueSync`. Palette extraction via Pillow
median-cut quantization, filtered to drop near-black/near-white outliers.
Look-ahead scheduling implements `Offset = T_playout - Latency_hardware_luci`
literally: `T_playout` is the AriaCast pipeline's fixed prebuffer (500ms /
25 frames, from `timing.md`) plus network slack, `Latency_hardware_luci` is
per-light (`lights.hardware_latency_ms`), so a slow Zigbee bulb gets its
color command dispatched almost immediately while a fast WiFi bulb waits
most of the window — both should land on-screen within the same ~50ms of
each other and of the new track's actual audio.

## §8 — Network architecture & the `AriaCastSocketServer`

`addon/ariacast_core/app/socket_server.py`. This is the piece that makes
§1B, §2B, §6, and §7 actually affect audio instead of just sitting in a
database:

- Accepts one inbound `/audio` Sender (phone or Web UI) on the *same* wire
  format a bare receiver would (zero protocol changes needed upstream).
- Opens one outbound `AriaCastSpeakerClient.open_audio_sender()` per
  `ONLINE` speaker in the active room and fans frames out.
- `TCP_NODELAY` / `TCP_QUICKACK` set directly on the raw socket via
  `tune_low_latency_socket()`, both on the inbound aiohttp connection and
  every outbound sender.
- No per-frame protocol timestamp exists (by spec), so each `OutputBuffer`
  stamps **local monotonic receive time** on every push — a
  PTP-*discipline* (timestamp everything, act on deltas) rather than true
  IEEE-1588 clock sync, which needs cross-host synchronization this
  same-process relay doesn't have.
- `_stretch()` is a linear-resample time-stretch (±2-5%, matching the
  spec's stated WSOLA tolerance) that nudges each output's jitter buffer
  back toward its 100ms target instead of hard-dropping or duplicating
  frames — this is what keeps the correction inaudible.
- Per-output `AudioDuckingMixer` does the actual sample-domain mixing for
  TTS/notification playback described in §1B.

### Design decision: Python/asyncio, not C++/Rust

Both existing reference receivers (`Ariacast-server-python`,
`AriaCast-Server-GO`) are already async single-process servers handling the
same 20ms/3840-byte frame cadence in production. `AriaCastSocketServer`
follows that precedent instead of introducing a third language/runtime into
the ecosystem purely to satisfy a "C++/Rust" checkbox — the actual
performance-sensitive path (`TCP_NODELAY`, direct socket buffer writes,
`numpy` for the gain/stretch math) is where a Python rewrite in C++ would
have mattered, and that's exactly the path implemented here in vectorized
`numpy` rather than pure-Python loops.

---

## What's explicitly out of scope in this pass

- **True IEEE-1588 PTP** cross-host clock sync (would need a dedicated PTP
  daemon/NIC hardware timestamping; the local monotonic-timestamp discipline
  above covers the same-box relay case this add-on actually runs).
- **Full companion-app UI rewiring** (§5) — the additive bridge file is
  ready to be wired into the existing Compose screens, but rewriting that
  UI blind, from outside the app's own architecture review process, is
  higher risk than it's worth; see the note in §5 above.
- **ffmpeg-based arbitrary-codec transcoding** for TTS notifications —
  `_fetch_notification_pcm` handles WAV directly (what most local
  HA TTS engines emit) and documents where an `ffmpeg` shell-out would slot
  in for MP3/OGG sources.
