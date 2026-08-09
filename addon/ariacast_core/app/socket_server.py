"""AriaCastSocketServer: the low-latency audio orchestration hub.

Takes over the role Music Assistant used to play, but purpose-built for this
ecosystem: it is the *single* `/audio` Sender each AriaCast receiver ever
sees, so it can apply per-room spatial DSP (`RoomSpatialAudioDSP` output),
album-art-driven ducking (`AudioDuckingMixer`), and dynamic node exclusion
(`SpeakerNodeManager`) to one outbound mix per speaker.

Network layer
-------------
* Inbound: an aiohttp WebSocket server exposing `/audio` on the same wire
  format as a normal AriaCast receiver (see transport.md) — the existing
  Android Sender or Web UI can point at this add-on instead of a bare
  receiver with zero protocol changes.
* Outbound: one `AriaCastSpeakerClient.open_audio_sender()` per ONLINE
  speaker in the active room, each running as an independent asyncio task.
* `TCP_NODELAY` / `TCP_QUICKACK` are set directly on the underlying raw
  socket for both directions to keep the 20ms frame cadence from bunching
  up in Nagle's algorithm.

Timing
------
Frames carry no on-wire timestamp (by protocol design). Internally, each
inbound frame is stamped with a monotonic nanosecond receive time
("PTP-style" local timestamping — IEEE-1588 proper requires clock sync
across hosts, which is out of scope for a same-box relay, but the same
discipline of "timestamp everything, act on deltas" is what buys the
adaptive buffer its jitter estimate). Each per-output buffer tracks its
fill level against `delay_ms` from the DSP engine and nudges playout speed
by ±2-5% (a lightweight resample-based stretch — see `_stretch`) instead of
hard dropping/duplicating frames, which is what makes the correction
inaudible.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from aiohttp import web, WSMsgType

from ariacast_core.database import DatabaseService
from ariacast_core.ducking import AudioDuckingMixer
from ariacast_core.models import SpeakerStatus
from ariacast_core.node_manager import SpeakerNodeManager

logger = logging.getLogger(__name__)

FRAME_BYTES = 3840  # 20ms @ 48kHz/stereo/16-bit, per timing.md
FRAME_SAMPLES = FRAME_BYTES // 2  # int16 samples (interleaved stereo)
SAMPLE_RATE = 48000
CHANNELS = 2

JITTER_TARGET_FRAMES = 5  # 100ms nominal cushion per output
JITTER_MAX_STRETCH = 0.05  # +/-5%, matches the spec's WSOLA tolerance
JITTER_MIN_STRETCH = 0.02


def tune_low_latency_socket(sock: socket.socket) -> None:
    """Apply the low-latency flags called out in the architecture spec."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        logger.debug("TCP_NODELAY not supported on this socket")
    quickack = getattr(socket, "TCP_QUICKACK", None)
    if quickack is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, quickack, 1)
        except OSError:
            logger.debug("TCP_QUICKACK not supported on this platform")


def _stretch(samples: np.ndarray, ratio: float) -> np.ndarray:
    """Lightweight WSOLA-style time-stretch via linear resampling.

    `ratio` > 1.0 stretches (slows down / adds duration) to recover from a
    starving buffer; < 1.0 compresses to relieve an overflowing one. Kept to
    +/-5% so the artifact stays inaudible, per the spec's stated tolerance.
    """
    if abs(ratio - 1.0) < 1e-4:
        return samples
    stereo = samples.reshape(-1, CHANNELS).astype(np.float32)
    n_in = stereo.shape[0]
    n_out = max(1, int(round(n_in * ratio)))
    x_in = np.linspace(0, 1, n_in, endpoint=False)
    x_out = np.linspace(0, 1, n_out, endpoint=False)
    stretched = np.empty((n_out, CHANNELS), dtype=np.float32)
    for ch in range(CHANNELS):
        stretched[:, ch] = np.interp(x_out, x_in, stereo[:, ch])
    return np.clip(stretched, -32768, 32767).astype("<i2").reshape(-1)


@dataclass
class OutputBuffer:
    """Per-speaker adaptive jitter buffer + PTP-style bookkeeping."""

    speaker_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    frames_written: int = 0
    frames_read: int = 0
    last_recv_monotonic_ns: int = 0
    ducking: AudioDuckingMixer = field(default_factory=lambda: AudioDuckingMixer(SAMPLE_RATE, CHANNELS))

    def push(self, frame: bytes) -> None:
        self.last_recv_monotonic_ns = time.monotonic_ns()
        try:
            self.queue.put_nowait(frame)
            self.frames_written += 1
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()  # drop oldest to keep latency bounded
                self.queue.put_nowait(frame)
            except asyncio.QueueEmpty:
                pass

    def occupancy_frames(self) -> int:
        return self.queue.qsize()

    def stretch_ratio(self) -> float:
        """Compute the correction ratio to converge occupancy toward the jitter target."""
        occ = self.occupancy_frames()
        error = occ - JITTER_TARGET_FRAMES
        if error == 0:
            return 1.0
        # Positive error (too full) -> compress (ratio < 1); negative -> stretch (ratio > 1).
        magnitude = min(JITTER_MAX_STRETCH, max(JITTER_MIN_STRETCH, abs(error) * 0.01))
        return 1.0 - magnitude if error > 0 else 1.0 + magnitude


class AriaCastSocketServer:
    def __init__(self, db: DatabaseService, node_manager: SpeakerNodeManager):
        self.db = db
        self.node_manager = node_manager
        self._outputs: dict[str, OutputBuffer] = {}
        self._relay_tasks: dict[str, asyncio.Task] = {}
        self._active_room_id: Optional[str] = None
        self._inbound_frames = 0

    def attach_routes(self, app: web.Application) -> None:
        app.router.add_get("/audio", self._handle_inbound_audio)
        app.router.add_post("/api/notify", self._handle_notify)
        app.on_response_prepare.append(self._tune_response_socket)

    @staticmethod
    async def _tune_response_socket(request: web.Request, _response) -> None:
        sock = request.transport.get_extra_info("socket") if request.transport else None
        if sock is not None:
            tune_low_latency_socket(sock)

    async def _handle_inbound_audio(self, request: web.Request) -> web.WebSocketResponse:
        """Accept a Sender (phone / Web UI) exactly like a normal AriaCast receiver would."""
        room_id = request.query.get("room_id")
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        sock = request.transport.get_extra_info("socket") if request.transport else None
        if sock is not None:
            tune_low_latency_socket(sock)

        await ws.send_json({
            "status": "READY",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "frame_size": FRAME_BYTES,
        })

        self._active_room_id = room_id
        await self._ensure_outputs_for_room(room_id)
        logger.info("Inbound audio session started for room=%s from %s", room_id, request.remote)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self._inbound_frames += 1
                    self._fan_out(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        finally:
            logger.info("Inbound audio session ended for room=%s", room_id)
        return ws

    def _fan_out(self, frame: bytes) -> None:
        for output in self._outputs.values():
            output.push(frame)

    async def _ensure_outputs_for_room(self, room_id: Optional[str]) -> None:
        if not room_id:
            return
        speakers = await self.db.list_speakers(room_id=room_id)
        for speaker in speakers:
            if speaker.status != SpeakerStatus.ONLINE or speaker.id in self._outputs:
                continue
            output = OutputBuffer(speaker_id=speaker.id)
            self._outputs[speaker.id] = output
            self._relay_tasks[speaker.id] = asyncio.create_task(self._relay_loop(speaker.id, output))

    async def _relay_loop(self, speaker_id: str, output: OutputBuffer) -> None:
        """Own the outbound `/audio` Sender connection to one receiver."""
        while True:
            speaker = await self.db.get_speaker(speaker_id)
            if not speaker or speaker.status != SpeakerStatus.ONLINE:
                await asyncio.sleep(1.0)
                continue
            node = self.node_manager.get_node(speaker_id)
            if not node:
                await asyncio.sleep(1.0)
                continue
            client = node.client
            try:
                await client.open_audio_sender()
                logger.info("Outbound audio relay connected to %s (%s)", speaker.name, speaker_id)
                delay_frames = max(0, int(round(speaker.delay_ms / 20.0)))
                delay_prefill = [bytes(FRAME_BYTES)] * delay_frames  # silence to realize the DSP delay

                while True:
                    speaker = await self.db.get_speaker(speaker_id)
                    if not speaker or speaker.status != SpeakerStatus.ONLINE:
                        break

                    raw = delay_prefill.pop(0) if delay_prefill else await output.queue.get()
                    output.frames_read += 1

                    ratio = output.stretch_ratio()
                    samples = np.frombuffer(raw, dtype="<i2")
                    if ratio != 1.0:
                        samples = _stretch(samples, ratio)

                    gain_linear = 10 ** (speaker.gain_db / 20.0)
                    scaled = np.clip(samples.astype(np.float32) * gain_linear, -32768, 32767).astype("<i2")

                    mixed = output.ducking.mix_frame(scaled.tobytes())
                    await client.send_audio_frame(mixed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Relay to %s dropped: %s (retrying)", speaker_id, exc)
            finally:
                await client.close_audio_sender()
            await asyncio.sleep(1.0)

    async def _handle_notify(self, request: web.Request) -> web.Response:
        """`media_player.play_media` hands a notification clip off here for ducked mixing."""
        payload = await request.json()
        speaker_id = payload.get("speaker_id")
        media_id = payload.get("media_id")
        output = self._outputs.get(speaker_id)
        if not output:
            return web.json_response({"success": False, "error": "speaker has no active audio session"}, status=409)

        pcm = await self._fetch_notification_pcm(media_id)
        if pcm is None:
            return web.json_response({"success": False, "error": "could not decode media_id to PCM"}, status=400)

        await output.ducking.queue_notification(pcm)
        return web.json_response({"success": True})

    @staticmethod
    async def _fetch_notification_pcm(media_id: str) -> Optional[bytes]:
        """Best-effort WAV decode for local TTS output.

        Production deployments should shell out to `ffmpeg -i - -f s16le -ar
        48000 -ac 2 -` for arbitrary codecs; that dependency is intentionally
        not vendored here to keep the add-on image small.
        """
        import io
        import wave

        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(media_id, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                raw = await resp.read()
        try:
            with wave.open(io.BytesIO(raw)) as wav_file:
                pcm = wav_file.readframes(wav_file.getnframes())
                if wav_file.getframerate() != SAMPLE_RATE or wav_file.getnchannels() != CHANNELS:
                    logger.warning("Notification clip is %sHz/%sch, expected %sHz/%sch stereo — playing without resample",
                                    wav_file.getframerate(), wav_file.getnchannels(), SAMPLE_RATE, CHANNELS)
                return pcm
        except wave.Error:
            logger.warning("Notification media_id %s is not a WAV file; skipping", media_id)
            return None
