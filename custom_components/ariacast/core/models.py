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
    delay_ms: float = 0.0
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
