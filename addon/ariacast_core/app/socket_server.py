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
import struct
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import numpy as np
from aiohttp import web, WSMsgType

from ariacast_core.database import DatabaseService
from ariacast_core.ducking import AudioDuckingMixer
from ariacast_core.models import SpeakerStatus
from ariacast_core.node_manager import SpeakerNodeManager

MediaServiceCaller = Callable[[str, str, dict], Awaitable[None]]

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


def _wav_header(sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    """A WAV/RIFF header declaring the maximum possible data size.

    There's no real end to a live stream, so the true data length can't be
    known up front. Declaring the RIFF/data chunk sizes as 0xFFFFFFFF (the
    max a 32-bit size field can hold) is the standard trick shoutcast-style
    live-WAV servers use — most players treat it as "play until the
    connection closes" rather than truncating at a nonsensical byte count.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample
    ) + b"data" + struct.pack("<I", 0xFFFFFFFF)


class RoomAudioStream:
    """Fans a room's live mixed audio out to any number of HTTP listeners as
    a live WAV stream, so ordinary Home Assistant `media_player` entities
    (Sonos, Cast, Alexa, anything with a generic HTTP-URL play_media) can
    play what the AriaCast native speakers in the same room are playing —
    without needing to speak the AriaCast wire protocol at all.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def push(self, frame: bytes) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(frame)
                except asyncio.QueueEmpty:
                    pass

    def close_all(self) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(None)  # sentinel: tells the HTTP handler to end the response
        self._subscribers.clear()


class AriaCastSocketServer:
    def __init__(
        self,
        db: DatabaseService,
        node_manager: SpeakerNodeManager,
        *,
        call_media_service: Optional[MediaServiceCaller] = None,
        public_base_url: str = "",
    ):
        self.db = db
        self.node_manager = node_manager
        self._call_media_service = call_media_service
        self._public_base_url = public_base_url.rstrip("/")
        self._outputs: dict[str, OutputBuffer] = {}
        self._relay_tasks: dict[str, asyncio.Task] = {}
        self._active_room_id: Optional[str] = None
        self._inbound_frames = 0
        self._room_streams: dict[str, RoomAudioStream] = {}
        self._ha_speakers_started: set[str] = set()  # room_ids with HA media_players already triggered

    def attach_routes(self, app: web.Application) -> None:
        app.router.add_get("/audio", self._handle_inbound_audio)
        app.router.add_get("/stream/{room_id}.wav", self._handle_room_stream)
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
        if room_id:
            await self._ensure_ha_speakers_for_room(room_id)
        logger.info("Inbound audio session started for room=%s from %s", room_id, request.remote)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    self._inbound_frames += 1
                    self._fan_out(msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        finally:
            if room_id:
                await self._end_room_session(room_id)
            logger.info("Inbound audio session ended for room=%s", room_id)
        return ws

    def _fan_out(self, frame: bytes) -> None:
        for output in self._outputs.values():
            output.push(frame)
        stream = self._room_streams.get(self._active_room_id)
        if stream is not None and stream.has_subscribers:
            stream.push(frame)

    async def _ensure_outputs_for_room(self, room_id: Optional[str]) -> None:
        if not room_id:
            return
        speakers = await self.db.list_speakers(room_id=room_id)
        for speaker in speakers:
            if speaker.platform == "home_assistant":
                continue  # routed via _ensure_ha_speakers_for_room instead, not a native socket
            if speaker.status != SpeakerStatus.ONLINE or speaker.id in self._outputs:
                continue
            output = OutputBuffer(speaker_id=speaker.id)
            self._outputs[speaker.id] = output
            self._relay_tasks[speaker.id] = asyncio.create_task(self._relay_loop(speaker.id, output))

    async def _handle_room_stream(self, request: web.Request) -> web.StreamResponse:
        """Live WAV stream of a room's mix, for bridged HA `media_player`
        entities that were pointed here via `media_player.play_media`
        (see `_ensure_ha_speakers_for_room`) — they pull; native AriaCast
        speakers are pushed to directly over their own `/audio` socket."""
        room_id = request.match_info["room_id"]
        stream = self._room_streams.setdefault(room_id, RoomAudioStream())
        queue = stream.subscribe()

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "audio/wav", "Cache-Control": "no-cache"},
        )
        await response.prepare(request)
        await response.write(_wav_header(stream.sample_rate, stream.channels))
        try:
            while True:
                frame = await queue.get()
                if frame is None:  # session ended
                    break
                await response.write(frame)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            stream.unsubscribe(queue)
        return response

    async def _ensure_ha_speakers_for_room(self, room_id: str) -> None:
        """Point every HA-bridged speaker in this room at the room's live
        stream via `media_player.play_media` — the DSP-routed equivalent of
        opening a native `/audio` sender to a real AriaCast receiver."""
        if not self._call_media_service or not self._public_base_url:
            return
        if room_id in self._ha_speakers_started:
            return
        speakers = await self.db.list_speakers(room_id=room_id)
        ha_speakers = [s for s in speakers if s.platform == "home_assistant" and s.status == SpeakerStatus.ONLINE]
        if not ha_speakers:
            return
        self._ha_speakers_started.add(room_id)
        stream_url = f"{self._public_base_url}/stream/{room_id}.wav"
        for speaker in ha_speakers:
            try:
                await self._call_media_service(
                    speaker.ha_entity_id,
                    "play_media",
                    {"media_content_id": stream_url, "media_content_type": "music"},
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to start HA-bridged speaker %s on room %s", speaker.ha_entity_id, room_id)

    async def _end_room_session(self, room_id: str) -> None:
        self._ha_speakers_started.discard(room_id)
        stream = self._room_streams.get(room_id)
        if stream is not None:
            stream.close_all()
        if self._call_media_service:
            speakers = await self.db.list_speakers(room_id=room_id)
            for speaker in speakers:
                if speaker.platform != "home_assistant":
                    continue
                try:
                    await self._call_media_service(speaker.ha_entity_id, "media_stop", {})
                except Exception:  # noqa: BLE001
                    logger.debug("Failed to stop HA-bridged speaker %s (may already be stopped)", speaker.ha_entity_id)

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
