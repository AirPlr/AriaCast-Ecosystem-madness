# AriaCast Architecture

**Version:** 1.0

AriaCast uses:
- UDP broadcast (and optionally mDNS/NSD) for receiver discovery
- Separate WebSocket connections per concern (`/audio`, `/control`, `/stats`, `/metadata`)
- JSON text frames for all signalling (handshakes, control, metadata)
- Binary frames for raw PCM audio data (no frame header)

## Component diagram

```
  Android Sender                  Receiver (Python or Go)
  ─────────────                   ───────────────────────
  AudioCastService ──ws /audio──► handle_audio_ws / HandleAudio
  AudioCastService ──ws /control─► handle_control_ws / HandleControl
  AudioCastService ──ws /stats───► handle_stats_ws (Python only)
  AudioCastService ──POST /metadata─► handle_metadata_api

  MediaNotificationListener ──────► (feeds metadata and artwork to AudioCastService)

  External controller ──POST /api/command──► HandleAPICommand
                       ──ws /control──►      handle_control_ws
```

## Implementations

| Component | Language | Key feature |
|---|---|---|
| Python server | Python (aiohttp + sounddevice + zeroconf) | Local audio playback, mDNS advertising, volume control |
| Go server | Go (net/http + gorilla/websocket) | Named pipe bridge to Music Assistant, web dashboard, no mDNS |
| Android app | Kotlin (Ktor + AudioRecord) | Captures system audio, mDNS/NSD discovery, media session integration |
