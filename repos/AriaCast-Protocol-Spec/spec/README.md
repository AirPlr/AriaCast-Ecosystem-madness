# AriaCast Specification

**Version:** 1.0

This specification documents the actual wire protocol implemented by the Python server, Go server, and Android app. It is normative for interoperability.

## Documents

1. [`protocol.md`](./protocol.md) — architecture, roles, and typical data flow
2. [`discovery.md`](./discovery.md) — UDP broadcast and mDNS/NSD discovery
3. [`transport.md`](./transport.md) — WebSocket endpoints, handshakes, and connection lifecycle
4. [`audio.md`](./audio.md) — binary PCM audio frame format
5. [`control.md`](./control.md) — JSON playback control commands
6. [`metadata.md`](./metadata.md) — track metadata, HTTP POST, and WebSocket subscription
7. [`timing.md`](./timing.md) — buffering model, queue sizes, and reconnect backoff
8. [`errors.md`](./errors.md) — per-endpoint error behaviour
9. [`security.md`](./security.md) — current security model and future extensions
