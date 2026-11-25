# AriaCast Security

**Version:** 1.0

## Current model

AriaCast assumes a **trusted local network**.

- Transport uses unencrypted `ws://` and `http://`
- No authentication or authorisation in any implementation
- No pairing or identity verification
- All implementations bind to `0.0.0.0` (all interfaces)

## Threat considerations

On untrusted LANs, attackers may:
- Inject audio frames into an open `/audio` endpoint
- Send arbitrary control commands to `/api/command` or `/control`
- Read metadata from `/metadata` WebSocket subscribers
- Retrieve audio via `/stream.wav` or `/artwork`
- Disrupt playback by connecting as the single-slot audio client

**AriaCast MUST only be used on trusted, private LAN networks.**

## Future security extensions

These are not implemented in any current version:

- **Pairing:** receiver enters pairing mode; sender proves possession of a shared code
- **Authentication:** `Authorization` header or token on WebSocket upgrade request
- **TLS:** `wss://` and `https://` endpoint variants
- **Allowlists:** per-IP or per-identity sender allowlists
