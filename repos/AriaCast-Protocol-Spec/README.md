# AriaCast Protocol Specification

AriaCast is an open, local-first protocol for real-time audio streaming over LAN networks.

This repository is the **normative reference** for the AriaCast wire protocol. It documents the exact message formats, endpoint layout, and behaviour implemented by the Python server, Go server, and Android app.

## Repository layout

- `spec/` — normative protocol specification
- `schemas/` — JSON Schemas for all protocol messages
- `docs/` — design notes and roadmap
- `examples/` — minimal implementation guidance

## Quick start

1. Read [`spec/protocol.md`](spec/protocol.md) for an overview of roles and architecture.
2. Implement discovery in [`spec/discovery.md`](spec/discovery.md).
3. Implement the transport endpoints in [`spec/transport.md`](spec/transport.md).
4. Implement binary audio framing in [`spec/audio.md`](spec/audio.md).
5. Implement the control channel in [`spec/control.md`](spec/control.md).
6. Implement metadata in [`spec/metadata.md`](spec/metadata.md).

## Key facts

| Property | Value |
|---|---|
| Discovery (UDP) | Broadcast `DISCOVER_AUDIOCAST` to `255.255.255.255:12888` |
| Discovery (mDNS) | `_audiocast._tcp` |
| Streaming port | `12889` |
| Audio format | PCM S16LE, 48 kHz, stereo |
| Frame size | 3840 bytes (20 ms) |
| Frame header | None — raw PCM only |
| Handshake | Server sends `{"status":"READY",...}` on `/audio` connect |
| Control direction | Server sends `{"action":"play"}` etc.; Sender sends `{"command":"volume",...}` etc. |
| Metadata transport | `POST /metadata` (HTTP) + `ws://.../metadata` (WebSocket) |

## Versioning

This specification targets **AriaCast v1.1** (current stable implementations).
