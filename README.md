# AriaCast for Home Assistant

Every [AriaCast](https://github.com/AriaCast/AriaCast-app) receiver on your network
(an Android phone, tablet or TV set to **Receive**, or any receiver that speaks the
[AriaCast protocol](docs/protocol-spec)) shows up in Home Assistant as a `media_player`.

You get what is playing (title, artist, album, artwork, position) and play, pause,
stop, next and previous. Nothing else is needed: no add-on, no Music Assistant, no
extra Python packages.

## Install

1. In HACS, add this repository as a custom repository (category **Integration**), install
   **AriaCast** and restart Home Assistant.
2. **Settings → Devices & services.** Receivers are found automatically; confirm the ones
   you want. For a receiver on another network (VPN, VLAN), choose **Add integration →
   AriaCast** and enter its address (port 12889).

A receiver that is switched off shows as *unavailable* and reconnects by itself when it is
back. If its address changes, Home Assistant follows it.

## What it does not do

- **Volume and seek.** The Android receiver does not accept them, so they are not offered
  rather than offered and ignored.
- **Play media from Home Assistant** (TTS, announcements). AriaCast receivers play what a
  sender streams to them.

## Development

```bash
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```

The tests start a fake receiver shaped like the Android app's (`tests/fake_receiver.py`) and
run the integration against it, so they need no hardware. The protocol client
(`custom_components/ariacast/client.py`) does not import Home Assistant.

## Layout

| Path | What it is |
|---|---|
| `custom_components/ariacast/` | The integration |
| `docs/protocol-spec/` | The AriaCast wire protocol |
| `experimental/` | An unfinished add-on (room editor, audio relay, spatial audio, TTS ducking). **Not supported**; see its README |
