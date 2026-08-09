"""RoomSpatialAudioDSP: 2D positional audio engine.

Given the listener's tracked (x, y) position in a room (from `app_state`)
and the (x, y) position of every speaker in that room, computes:

  * `gain_db`  — an energy-preserving VBAP-style panning weight so speakers
    nearer the listener are louder, while total perceived loudness stays
    roughly constant as the listener moves.
  * `delay_ms` — a directional micro-delay using the precedence/Haas effect:
    the nearest speaker gets zero added delay, and every other speaker gets
    delayed by how much farther it is than the nearest one. This is a
    *directional cue*, not a physical wavefront-alignment compensation
    (which would instead delay every speaker but the *farthest* one) — the
    Haas effect is what actually makes a source feel like it's coming from
    the nearest speaker without needing sub-millisecond a/v sync tricks.

Recalculation is restricted to `SpeakerStatus.ONLINE` speakers only, so a
speaker that just went offline is excluded from panning math immediately
(no silent gap, no phantom/ghost energy budgeted to a dead node) and the
remaining online speakers absorb its share of the mix.

The computed coefficients are persisted to `speakers.gain_db` /
`speakers.delay_ms` via `DatabaseService`, which fans the update out over
pub/sub. The actual sample-domain application (gain multiply + delay-line
insert on the outbound PCM) happens in `AriaCastSocketServer`, which reads
these coefficients per output right before writing frames to each
receiver's `/audio` WebSocket.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .database import DatabaseService
from .models import Room, Speaker, SpeakerStatus

logger = logging.getLogger(__name__)

SPEED_OF_SOUND_M_S = 343.0
MIN_DISTANCE_M = 0.15  # floor to avoid division blow-up when listener stands on a speaker
GAIN_FLOOR_DB = -24.0
GAIN_CEIL_DB = 0.0
VBAP_POWER = 2.0  # inverse-square falloff exponent


@dataclass
class SpeakerDspResult:
    speaker_id: str
    gain_db: float
    delay_ms: float
    distance_m: float


class RoomSpatialAudioDSP:
    def __init__(self, db: DatabaseService):
        self.db = db

    @staticmethod
    def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
        return max(MIN_DISTANCE_M, math.hypot(x2 - x1, y2 - y1))

    def compute(self, listener_x: float, listener_y: float, speakers: list[Speaker]) -> list[SpeakerDspResult]:
        """Pure function: online speakers in -> (gain_db, delay_ms) out. No I/O."""
        online = [s for s in speakers if s.status == SpeakerStatus.ONLINE]
        if not online:
            return []

        distances = {s.id: self._distance(listener_x, listener_y, s.pos_x, s.pos_y) for s in online}
        d_min = min(distances.values())

        # Energy-preserving VBAP weighting: w_i = 1/d_i^p, normalized so that
        # sum(w_i_normalized^2) == 1 (constant total acoustic energy).
        raw_weights = {sid: 1.0 / (d**VBAP_POWER) for sid, d in distances.items()}
        norm = math.sqrt(sum(w**2 for w in raw_weights.values())) or 1.0

        results: list[SpeakerDspResult] = []
        for s in online:
            w = raw_weights[s.id] / norm
            gain_db = 20.0 * math.log10(max(w, 1e-6))
            gain_db = max(GAIN_FLOOR_DB, min(GAIN_CEIL_DB, gain_db))
            delay_ms = (distances[s.id] - d_min) / SPEED_OF_SOUND_M_S * 1000.0
            results.append(SpeakerDspResult(speaker_id=s.id, gain_db=gain_db, delay_ms=delay_ms, distance_m=distances[s.id]))
        return results

    async def recompute_room(self, room_id: str) -> list[SpeakerDspResult]:
        """Recompute and persist DSP coefficients for one room's online speakers."""
        state = await self.db.get_app_state()
        if state.current_room_id != room_id:
            listener_x, listener_y = 0.0, 0.0
            # Fall back to room center if this isn't the actively-tracked room.
            room = await self.db.get_room(room_id)
            if room:
                listener_x, listener_y = room.width_meters / 2, room.height_meters / 2
        else:
            listener_x, listener_y = state.listener_x, state.listener_y

        speakers = await self.db.list_speakers(room_id=room_id)
        results = self.compute(listener_x, listener_y, speakers)
        speakers_by_id = {s.id: s for s in speakers}
        for r in results:
            # Layer the user's manual/calibration offset on top of the pure
            # geometric delay so hardware with real playback latency (e.g.
            # two ESPHome speakers with very different inherent delay) can be
            # compensated for — see Speaker.extra_delay_ms.
            extra = speakers_by_id[r.speaker_id].extra_delay_ms
            await self.db.update_speaker_dsp(r.speaker_id, r.gain_db, r.delay_ms + extra)

        # Any offline speaker in the room is parked at unity/no-delay so it
        # snaps to a sane baseline the moment it reconnects, before the next
        # recompute tick lands.
        offline_ids = {s.id for s in speakers if s.status != SpeakerStatus.ONLINE}
        for sid in offline_ids:
            await self.db.update_speaker_dsp(sid, 0.0, 0.0)

        logger.debug("DSP recompute room=%s listener=(%.2f,%.2f) -> %d online speakers", room_id, listener_x, listener_y, len(results))
        return results

    async def on_listener_moved(self, room_id: str, x: float, y: float) -> list[SpeakerDspResult]:
        await self.db.update_app_state(current_room_id=room_id, listener_x=x, listener_y=y)
        return await self.recompute_room(room_id)
