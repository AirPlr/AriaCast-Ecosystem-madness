# AriaCast Timing and Buffering

**Version:** 1.0

## Audio frame timing

There are **no timestamps** embedded in audio frames. Timing is implicit from the continuous stream rate.

| Parameter | Value |
|---|---|
| Sample rate | 48000 Hz |
| Frame duration | 20 ms |
| Samples per frame per channel | 960 |
| Frame size | 3840 bytes |

---

## Buffering — Python server

The Python server uses a `deque`-based queue per audio player instance.

| Parameter | Value |
|---|---|
| Queue capacity | 100 frames (2 seconds) |
| Prebuffer threshold | 25 frames (500 ms) |
| Overflow behaviour | Frame discarded, `overruns` counter incremented |
| Underflow behaviour | Zero-fill silence inserted, `underruns` counter incremented |

**Startup:** Audio playback does not begin until 25 frames have been buffered, ensuring a smooth start despite initial network jitter.

**Steady state:** Frames are consumed by the `sounddevice` audio hardware callback. If the queue runs dry, the callback fills the output buffer with silence.

---

## Buffering — Go server

The Go server uses a Go channel for the pipe bridge.

| Parameter | Value |
|---|---|
| Channel capacity | 100 frames |
| Overflow behaviour | Frame silently dropped |
| Pause behaviour | Silence (zero bytes, `frame_size` bytes long) written to pipe instead of audio data |

When a `pause` command is received, the Go server atomically sets a `pipePaused` flag. On each subsequent pipe write, the server writes a pre-allocated silent frame instead of the received audio data, keeping the downstream consumer (e.g. Music Assistant) receiving a continuous stream without gaps.

---

## Web broadcast pool

Both servers maintain a pool of per-client channels for the `/stream.wav` HTTP streaming endpoint.

| Parameter | Value |
|---|---|
| Per-client channel capacity | 50 frames (Python) / 50 frames (Go) |
| Overflow behaviour | Frame silently dropped for that client |

---

## Reconnect / backoff — Android Sender

The Android app reconnects automatically on WebSocket disconnection using **exponential backoff**:

```
delay_ms = initial_backoff_ms × 2^min(attempt, 5)
initial_backoff_ms = 1000
```

| Attempt | Delay |
|---|---|
| 1 | 2 000 ms |
| 2 | 4 000 ms |
| 3 | 8 000 ms |
| 4 | 16 000 ms |
| 5+ | 32 000 ms |

This applies independently to the `/audio`, `/control`, `/stats`, and `/video` WebSocket sessions.

---

## Stats push interval

The Python server pushes a stats frame to all `/stats` WebSocket clients every **1 second**.

---

## Metadata refresh interval

The Android app re-sends current metadata every **10 seconds** and every **1 second** while playback is active (for position tracking).
