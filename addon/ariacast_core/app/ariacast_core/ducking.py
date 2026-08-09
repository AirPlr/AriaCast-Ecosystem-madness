"""Automatic audio ducking for `media_player.play_media` notifications.

An AriaCast receiver only accepts a single `/audio` Sender at a time (see
transport.md "Single-client constraint"), so Home Assistant cannot simply
open a second stream to play a doorbell chime over the phone's music cast.
Instead `AriaCastSocketServer` is the *only* Sender the receiver ever sees;
it mixes a `music` bus (relayed from the phone/companion app) and a
`notification` bus (TTS/alert PCM handed to it by the HA integration) into
one outbound PCM stream per receiver.

`AudioDuckingMixer` owns that mix: on `duck()` it fades the music bus down
over `FADE_MS`, plays the notification bus to completion, then fades music
back up over `FADE_MS`. All fades are linear in the amplitude domain, applied
per-sample so there's no audible "step" at 20ms frame boundaries.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

FADE_MS = 300
DUCK_FLOOR = 0.22  # music drops to ~22% amplitude (~ -13dB) while a notification plays
FRAME_MS = 20


class DuckState(enum.Enum):
    NORMAL = "normal"
    DUCKING = "ducking"
    DUCKED = "ducked"
    RESTORING = "restoring"


class AudioDuckingMixer:
    """Per-speaker (or per-room-bus) ducking envelope + PCM mixer.

    Feed it 20ms int16 PCM frames from the music bus via `mix_frame()`; call
    `duck()` before you start writing notification frames, and `restore()`
    once the notification is done. The envelope multiplier for the *music*
    bus is exposed via `music_gain` so callers can also just read the
    current ramp value if they are mixing frames themselves.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.state = DuckState.NORMAL
        self._music_gain = 1.0
        self._target_gain = 1.0
        self._step_per_frame = 0.0
        self._notification_queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

    @property
    def music_gain(self) -> float:
        return self._music_gain

    def duck(self) -> None:
        if self.state in (DuckState.DUCKING, DuckState.DUCKED):
            return
        self.state = DuckState.DUCKING
        self._target_gain = DUCK_FLOOR
        frames = max(1, FADE_MS // FRAME_MS)
        self._step_per_frame = (self._music_gain - DUCK_FLOOR) / frames
        logger.debug("Ducking music bus -> %.2f over %dms", DUCK_FLOOR, FADE_MS)

    def restore(self) -> None:
        if self.state in (DuckState.RESTORING, DuckState.NORMAL):
            return
        self.state = DuckState.RESTORING
        self._target_gain = 1.0
        frames = max(1, FADE_MS // FRAME_MS)
        self._step_per_frame = (1.0 - self._music_gain) / frames
        logger.debug("Restoring music bus -> 1.0 over %dms", FADE_MS)

    def _advance_envelope(self) -> None:
        if self.state == DuckState.DUCKING:
            self._music_gain = max(self._target_gain, self._music_gain - self._step_per_frame)
            if self._music_gain <= self._target_gain + 1e-4:
                self._music_gain = self._target_gain
                self.state = DuckState.DUCKED
        elif self.state == DuckState.RESTORING:
            self._music_gain = min(self._target_gain, self._music_gain + self._step_per_frame)
            if self._music_gain >= self._target_gain - 1e-4:
                self._music_gain = self._target_gain
                self.state = DuckState.NORMAL

    async def queue_notification(self, pcm: bytes) -> None:
        """Enqueue a full notification clip (already resampled to sample_rate/channels)."""
        self.duck()
        frame_bytes = self.channels * 2 * (self.sample_rate * FRAME_MS // 1000)
        for offset in range(0, len(pcm), frame_bytes):
            await self._notification_queue.put(pcm[offset : offset + frame_bytes])
        await self._notification_queue.put(None)  # sentinel: end of clip

    def mix_frame(self, music_frame: bytes) -> bytes:
        """Combine one 20ms music frame with a pending notification frame, if any."""
        self._advance_envelope()

        music = np.frombuffer(music_frame, dtype="<i2").astype(np.float32) * self._music_gain

        notif_frame: Optional[bytes] = None
        try:
            notif_frame = self._notification_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        if notif_frame is None:
            if self.state == DuckState.DUCKED and self._notification_queue.empty():
                self.restore()
            mixed = music
        else:
            notif = np.frombuffer(notif_frame, dtype="<i2").astype(np.float32)
            if len(notif) < len(music):
                notif = np.pad(notif, (0, len(music) - len(notif)))
            mixed = music + notif

        clipped = np.clip(mixed, -32768, 32767).astype("<i2")
        return clipped.tobytes()
