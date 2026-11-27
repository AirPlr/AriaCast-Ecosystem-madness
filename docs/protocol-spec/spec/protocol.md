# AriaCast Protocol

**Version:** 1.0

## Purpose

AriaCast is an open, local-first protocol for real-time audio streaming over LAN networks.

Primary goals:
- real-time LAN audio transport with low latency
- simple multi-language implementation
- local-only operation without cloud dependency
- interoperability between senders and receivers regardless of implementation

## Roles

| Role | Description |
|---|---|
| Sender | Captures system audio (e.g. Android app) and streams it to a receiver |
| Receiver | Accepts the audio stream, buffers it, and plays or re-routes it (Python or Go server) |
| Controller (optional) | Sends playback commands to an active receiver session |

## Architecture

AriaCast uses **separate WebSocket connections per concern**. All connections are initiated by the Sender (client) to the Receiver (server).

| WebSocket path | Purpose |
|---|---|
| `/audio` | Raw PCM binary audio stream (Sender → Receiver) |
| `/control` | Bidirectional JSON command channel |
| `/stats` | Buffer/playback statistics push (Receiver → Sender) |
| `/metadata` | Track metadata push and subscribe (bidirectional) |

HTTP endpoints are also present:

| HTTP endpoint | Purpose |
|---|---|
| `POST /metadata` | Sender pushes track metadata to Receiver |
| `POST /api/command` | External controller sends a playback command via HTTP |
| `GET /artwork` | Receiver serves the proxied artwork image |
| `GET /image/artwork` | Alternate artwork endpoint (Go server) |
| `GET /stream.wav` | Browser-compatible HTTP WAV audio stream |

Discovery is performed via **UDP broadcast** (all implementations) and/or **mDNS/NSD** (`_audiocast._tcp`, Python server and Android app).

## Typical data flow

1. Sender discovers Receivers using UDP broadcast or mDNS.
2. Sender opens WebSocket to `/audio`, receives `READY` handshake, starts streaming binary PCM frames.
3. Sender opens WebSocket to `/control`, listens for incoming `action` commands from the Receiver.
4. Sender opens WebSocket to `/stats`, reads buffer/playback statistics.
5. Sender POSTs track metadata to `POST /metadata` whenever the playing track changes.
6. Receiver broadcasts metadata to all `/metadata` WebSocket subscribers on every update.
7. External controllers (web UI, Music Assistant plugin) send commands via `POST /api/command` or the `/control` WebSocket.
8. Receiver forwards received commands as `{"action": "..."}` frames to all `/control` WebSocket clients (including the Sender).
9. Sender app executes received playback actions on the Android media session.

## Design philosophy

- **Local-first:** no cloud services required.
- **Open:** wire format is public and implementation-neutral.
- **Simple:** raw PCM binary frames; JSON text frames for all signalling.
- **Tolerant:** implementations MUST ignore unknown JSON fields to allow forward compatibility.
