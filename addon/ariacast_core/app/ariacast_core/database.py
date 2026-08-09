"""DatabaseService: the single source of truth (SQLite, optionally PostgreSQL-compatible
schema) plus an in-process event bus that `pubsub.py` fans out over WebSocket.

Every mutation goes through this class, and every mutation emits an event of
the shape:

    {"table": "speakers", "op": "update", "row": {...}}

so the Web UI (PC), the companion app, and the `media_player` entities in
Home Assistant all observe the same state change in real time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Iterable, Optional

import aiosqlite

from .models import AppState, Light, Room, Speaker, SpeakerStatus

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    ha_area_id TEXT,
    name TEXT NOT NULL,
    width_meters REAL NOT NULL DEFAULT 4.0,
    height_meters REAL NOT NULL DEFAULT 4.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS speakers (
    id TEXT PRIMARY KEY,
    room_id TEXT REFERENCES rooms(id) ON DELETE SET NULL,
    ha_entity_id TEXT,
    hardware_uuid TEXT UNIQUE NOT NULL,
    ip_address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 12889,
    name TEXT NOT NULL DEFAULT 'AriaCast Speaker',
    status TEXT NOT NULL DEFAULT 'offline',
    pos_x REAL NOT NULL DEFAULT 0,
    pos_y REAL NOT NULL DEFAULT 0,
    orientation_deg REAL NOT NULL DEFAULT 0,
    gain_db REAL NOT NULL DEFAULT 0,
    delay_ms REAL NOT NULL DEFAULT 0,
    volume INTEGER NOT NULL DEFAULT 50,
    is_playing INTEGER NOT NULL DEFAULT 0,
    platform TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lights (
    id TEXT PRIMARY KEY,
    room_id TEXT REFERENCES rooms(id) ON DELETE SET NULL,
    ha_entity_id TEXT NOT NULL,
    hardware_latency_ms REAL NOT NULL DEFAULT 150,
    sync_mode TEXT NOT NULL DEFAULT 'palette'
);

CREATE TABLE IF NOT EXISTS app_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_room_id TEXT,
    listener_x REAL NOT NULL DEFAULT 0,
    listener_y REAL NOT NULL DEFAULT 0,
    palette_dominant TEXT,
    palette_secondary TEXT,
    palette_accent TEXT
);
"""

EventListener = Callable[[dict[str, Any]], Awaitable[None]]


class DatabaseService:
    """Async SQLite-backed persistence + pub/sub event source."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        self._listeners: list[EventListener] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.execute(
            "INSERT OR IGNORE INTO app_state (id, current_room_id, listener_x, listener_y) VALUES (1, NULL, 0, 0)"
        )
        await self._db.commit()
        logger.info("DatabaseService ready at %s", self.db_path)

    async def stop(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Register a callback invoked for every mutation. Returns an unsubscribe fn."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def _emit(self, table: str, op: str, row: dict[str, Any]) -> None:
        event = {"table": table, "op": op, "row": row, "ts": time.time()}
        for listener in list(self._listeners):
            try:
                await listener(event)
            except Exception:  # noqa: BLE001
                logger.exception("pubsub listener raised for event on %s", table)

    # -- rooms ----------------------------------------------------------

    async def upsert_room(self, room: Room) -> Room:
        assert self._db is not None
        now = time.time()
        async with self._lock:
            existing = await self.get_room(room.id)
            room.created_at = existing.created_at if existing else now
            room.updated_at = now
            await self._db.execute(
                """INSERT INTO rooms (id, ha_area_id, name, width_meters, height_meters, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     ha_area_id=excluded.ha_area_id, name=excluded.name,
                     width_meters=excluded.width_meters, height_meters=excluded.height_meters,
                     updated_at=excluded.updated_at""",
                (room.id, room.ha_area_id, room.name, room.width_meters, room.height_meters, room.created_at, room.updated_at),
            )
            await self._db.commit()
        await self._emit("rooms", "upsert", room.__dict__)
        return room

    async def get_room(self, room_id: str) -> Optional[Room]:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM rooms WHERE id = ?", (room_id,))
        row = await cur.fetchone()
        return Room(**dict(row)) if row else None

    async def list_rooms(self) -> list[Room]:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM rooms ORDER BY name")
        rows = await cur.fetchall()
        return [Room(**dict(r)) for r in rows]

    async def delete_room(self, room_id: str) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        await self._db.commit()
        await self._emit("rooms", "delete", {"id": room_id})

    # -- speakers ---------------------------------------------------------

    async def upsert_speaker(self, speaker: Speaker) -> Speaker:
        assert self._db is not None
        async with self._lock:
            await self._db.execute(
                """INSERT INTO speakers (id, room_id, ha_entity_id, hardware_uuid, ip_address, port, name,
                                          status, pos_x, pos_y, orientation_deg, gain_db, delay_ms, volume,
                                          is_playing, platform, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     room_id=excluded.room_id, ha_entity_id=excluded.ha_entity_id,
                     ip_address=excluded.ip_address, port=excluded.port, name=excluded.name,
                     status=excluded.status, pos_x=excluded.pos_x, pos_y=excluded.pos_y,
                     orientation_deg=excluded.orientation_deg, gain_db=excluded.gain_db,
                     delay_ms=excluded.delay_ms, volume=excluded.volume, is_playing=excluded.is_playing,
                     platform=excluded.platform, last_seen=excluded.last_seen""",
                (
                    speaker.id, speaker.room_id, speaker.ha_entity_id, speaker.hardware_uuid,
                    speaker.ip_address, speaker.port, speaker.name, speaker.status.value,
                    speaker.pos_x, speaker.pos_y, speaker.orientation_deg, speaker.gain_db,
                    speaker.delay_ms, speaker.volume, int(speaker.is_playing), speaker.platform,
                    speaker.last_seen,
                ),
            )
            await self._db.commit()
        await self._emit("speakers", "upsert", self._speaker_to_dict(speaker))
        return speaker

    async def get_speaker(self, speaker_id: str) -> Optional[Speaker]:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM speakers WHERE id = ?", (speaker_id,))
        row = await cur.fetchone()
        return self._row_to_speaker(row) if row else None

    async def get_speaker_by_uuid(self, hardware_uuid: str) -> Optional[Speaker]:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM speakers WHERE hardware_uuid = ?", (hardware_uuid,))
        row = await cur.fetchone()
        return self._row_to_speaker(row) if row else None

    async def list_speakers(self, room_id: Optional[str] = None) -> list[Speaker]:
        assert self._db is not None
        if room_id:
            cur = await self._db.execute("SELECT * FROM speakers WHERE room_id = ?", (room_id,))
        else:
            cur = await self._db.execute("SELECT * FROM speakers")
        rows = await cur.fetchall()
        return [self._row_to_speaker(r) for r in rows]

    async def set_speaker_status(self, speaker_id: str, status: SpeakerStatus) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE speakers SET status = ?, last_seen = ? WHERE id = ?",
            (status.value, time.time(), speaker_id),
        )
        await self._db.commit()
        speaker = await self.get_speaker(speaker_id)
        if speaker:
            await self._emit("speakers", "status", self._speaker_to_dict(speaker))

    async def update_speaker_dsp(self, speaker_id: str, gain_db: float, delay_ms: float) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE speakers SET gain_db = ?, delay_ms = ? WHERE id = ?",
            (gain_db, delay_ms, speaker_id),
        )
        await self._db.commit()
        speaker = await self.get_speaker(speaker_id)
        if speaker:
            await self._emit("speakers", "dsp", self._speaker_to_dict(speaker))

    async def update_speaker_playback(self, speaker_id: str, *, volume: Optional[int] = None, is_playing: Optional[bool] = None) -> None:
        assert self._db is not None
        sets, params = [], []
        if volume is not None:
            sets.append("volume = ?")
            params.append(volume)
        if is_playing is not None:
            sets.append("is_playing = ?")
            params.append(int(is_playing))
        if not sets:
            return
        params.append(speaker_id)
        await self._db.execute(f"UPDATE speakers SET {', '.join(sets)} WHERE id = ?", params)
        await self._db.commit()
        speaker = await self.get_speaker(speaker_id)
        if speaker:
            await self._emit("speakers", "playback", self._speaker_to_dict(speaker))

    async def delete_speaker(self, speaker_id: str) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
        await self._db.commit()
        await self._emit("speakers", "delete", {"id": speaker_id})

    @staticmethod
    def _row_to_speaker(row: aiosqlite.Row) -> Speaker:
        d = dict(row)
        d["status"] = SpeakerStatus(d["status"])
        d["is_playing"] = bool(d["is_playing"])
        return Speaker(**d)

    @staticmethod
    def _speaker_to_dict(speaker: Speaker) -> dict[str, Any]:
        d = dict(speaker.__dict__)
        d["status"] = speaker.status.value
        return d

    # -- lights -------------------------------------------------------------

    async def upsert_light(self, light: Light) -> Light:
        assert self._db is not None
        await self._db.execute(
            """INSERT INTO lights (id, room_id, ha_entity_id, hardware_latency_ms, sync_mode)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET room_id=excluded.room_id, ha_entity_id=excluded.ha_entity_id,
                 hardware_latency_ms=excluded.hardware_latency_ms, sync_mode=excluded.sync_mode""",
            (light.id, light.room_id, light.ha_entity_id, light.hardware_latency_ms, light.sync_mode),
        )
        await self._db.commit()
        await self._emit("lights", "upsert", light.__dict__)
        return light

    async def list_lights(self, room_id: Optional[str] = None) -> list[Light]:
        assert self._db is not None
        if room_id:
            cur = await self._db.execute("SELECT * FROM lights WHERE room_id = ?", (room_id,))
        else:
            cur = await self._db.execute("SELECT * FROM lights")
        rows = await cur.fetchall()
        return [Light(**dict(r)) for r in rows]

    # -- app_state ------------------------------------------------------

    async def get_app_state(self) -> AppState:
        assert self._db is not None
        cur = await self._db.execute("SELECT * FROM app_state WHERE id = 1")
        row = await cur.fetchone()
        d = dict(row)
        d.pop("id", None)
        return AppState(**d)

    async def update_app_state(self, **fields: Any) -> AppState:
        assert self._db is not None
        if not fields:
            return await self.get_app_state()
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._db.execute(f"UPDATE app_state SET {sets} WHERE id = 1", list(fields.values()))
        await self._db.commit()
        state = await self.get_app_state()
        await self._emit("app_state", "update", state.__dict__)
        return state

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]
