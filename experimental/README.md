# Experimental (not supported)

This folder keeps the earlier, much larger design: an add-on with a room editor, an audio
relay, 2D spatial audio, TTS ducking and album-art light sync. It is **not** installed by the
integration, is not covered by tests, and does not work end to end today. It is kept so the
ideas are not lost, not as something to install.

## What is known to be broken

From an audit before the integration was simplified:

- `addon/ariacast_core/app/main.py` — creating or updating a room raises `NameError`
  (`room_id` is never defined).
- `www/index.html` — API and WebSocket URLs are absolute, so under Home Assistant Ingress every
  call goes to Home Assistant's root and fails.
- No `build.yaml`, so the Supervisor's default base image has no Python and the build fails.
- The UI posts to `/api/rooms/{id}/effects` and `/api/effects/presets`, which the server never
  registers.
- The add-on and the old integration each used their own SQLite database while the docs claimed
  one shared file; two writers on one file would also lock it.
- The relay never cancels its per-speaker tasks, uses one global active room, and queries the
  database for every 20 ms frame.
- No authentication on any route, and `/api/notify` fetches arbitrary URLs.
- `README` claims about "no middleware" and positional audio did not match the code.

`android-bridge/HaEcosystemBridge.kt` used to live inside a vendored copy of an old AriaCast
app (version 1.1.7) and was not part of the upstream app; it is kept here untouched.
`ARCHITECTURE-legacy.md` describes the old design, including that file.

If you pick this up, start by deciding on one owner for the data and one owner for each
speaker connection, and add tests before adding features.
