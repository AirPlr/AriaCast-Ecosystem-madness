# AriaCast Audio Frames

**Version:** 1.0

## Format

| Parameter | Value |
|---|---|
| Codec | PCM signed 16-bit little-endian (`pcm_s16le`) |
| Sample rate | 48000 Hz |
| Channels | 2 (stereo) |
| Frame duration | 20 ms |
| Frame size | **3840 bytes** |

## Frame structure

Each WebSocket binary message contains **one frame** of exactly **3840 bytes** of raw PCM data. There is **no frame header** of any kind. The entire binary message payload is audio samples.

```text
[raw PCM bytes — exactly 3840 bytes]
```

## PCM sample layout

Stereo interleaved, little-endian signed 16-bit integers:

```text
L0, R0, L1, R1, L2, R2, ... L959, R959
```

- Each sample is a signed 16-bit integer (2 bytes, little-endian)
- One frame contains **960 sample pairs** per channel
- Total: 960 × 2 channels × 2 bytes = **3840 bytes**

## Frame size derivation

```
frame_size = sample_rate × channels × sample_width_bytes × frame_duration_ms / 1000
           = 48000 × 2 × 2 × 20 / 1000
           = 3840 bytes
```

## Sender behaviour (Android)

- The Android app allocates a `ByteBuffer` of exactly `FRAME_SIZE = 3840` bytes.
- `AudioRecord.read()` is called for exactly `3840` bytes per iteration.
- A **partial read** (fewer than 3840 bytes returned) is discarded; the frame is not sent.
- An **`AudioRecord` error** (negative return value) causes the audio session to terminate.
- Each successful read is sent as a WebSocket binary frame using `Frame.Binary(true, data)`.

## Receiver behaviour

### Python server
- Rejects frames where `len(data) != 3840` (logs a warning and discards).
- Converts bytes to a NumPy `int16` array (little-endian: `dtype='<i2'`).
- Queues the decoded samples for audio playback callback.

### Go server
- Passes the raw bytes to the pipe bridge channel and the web broadcast pool without length validation.
- Writes silence (zero bytes of the same length) to the output pipe when the stream is paused.

## Prebuffering

The Python server waits for **25 frames (500 ms)** to accumulate before starting local audio playback. This smooths over initial network jitter. After that point the server plays audio in real-time as frames arrive.

## Streaming WAV output

Both servers expose a `GET /stream.wav` HTTP endpoint that prefixes a standard 44-byte WAV header (RIFF/PCM) with `0xFFFFFFFF` as the file and data chunk sizes (indicating an open-ended stream), then streams raw PCM frames from the audio source in real-time. This allows standard media players (VLC, browsers) to connect and play the audio.
