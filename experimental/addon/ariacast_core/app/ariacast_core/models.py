"""Dataclass models mirroring the `rooms` / `speakers` / `lights` / `app_state` schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SpeakerStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNAVAILABLE = "unavailable"


@dataclass
class Room:
    id: str
    name: str
    ha_area_id: Optional[str] = None
    width_meters: float = 4.0
    height_meters: float = 4.0
    created_at: float = 0.0
    updated_at: float = 0.0
    # Room-level effects chain (see core/effects.py RoomEffectsChain) — all
    # default to flat/off so an existing room's sound doesn't change until
    # someone explicitly touches a slider or preset.
    eq_bass_db: float = 0.0
    eq_mid_db: float = 0.0
    eq_treble_db: float = 0.0
    reverb_wet: float = 0.0
    reverb_size: float = 0.5
    compressor_enabled: bool = False
    effects_preset: str = "flat"


@dataclass
class Speaker:
    id: str
    room_id: Optional[str]
    ha_entity_id: Optional[str]
    hardware_uuid: str
    ip_address: str
    port: int = 12889
    name: str = "AriaCast Speaker"
    status: SpeakerStatus = SpeakerStatus.OFFLINE
    pos_x: float = 0.0
    pos_y: float = 0.0
    orientation_deg: float = 0.0
    gain_db: float = 0.0
    delay_ms: float = 0.0  # total delay actually applied; overwritten every DSP tick (geometric + extra_delay_ms)
    extra_delay_ms: float = 0.0  # manual/calibration offset layered on top of the geometric delay; survives DSP ticks
    air_cutoff_hz: float = 20000.0  # DSP-computed air-absorption lowpass cutoff; ~inaudible at 20000 (bypassed)
    volume: int = 50
    is_playing: bool = False
    platform: str = ""
    last_seen: float = 0.0


@dataclass
class Light:
    id: str
    room_id: Optional[str]
    ha_entity_id: str
    hardware_latency_ms: float = 150.0
    sync_mode: str = "palette"  # "palette" | "dominant" | "off"


@dataclass
class AppState:
    current_room_id: Optional[str] = None
    listener_x: float = 0.0
    listener_y: float = 0.0
    palette_dominant: Optional[str] = None
    palette_secondary: Optional[str] = None
    palette_accent: Optional[str] = None
