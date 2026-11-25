"""AlbumArtHueSync: extract a 3-color palette from the current track's
artwork and push it to the room's lights with look-ahead compensation so the
color change lands in the listener's eyes at (roughly) the same moment the
new track's audio reaches their ears.

    Offset = T_playout - Latency_hardware_luci

`T_playout` is estimated as the AriaCast audio pipeline's fixed prebuffer
(500ms — 25 frames @ 20ms, see AriaCast-Protocol-Spec/spec/timing.md) plus a
small metadata-propagation allowance, i.e. "how long from 'new artwork
arrived' until the listener actually hears the new track". `Latency_hardware_luci`
is per-light (Zigbee/Zwave/WiFi bulbs all differ), stored in
`lights.hardware_latency_ms`. A light with 150ms of its own lag needs its
command sent 350ms into the 500ms playout window; a slower 400ms bulb needs
it sent almost immediately.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .database import DatabaseService

logger = logging.getLogger(__name__)

# 25-frame (500ms) prebuffer before local playback starts, per timing.md,
# plus ~80ms slack for metadata HTTP POST + broadcast fan-out.
ESTIMATED_PLAYOUT_MS = 580.0

LightServiceCall = Callable[[str, dict], Awaitable[None]]


@dataclass
class Palette:
    dominant: str
    secondary: str
    accent: str


def extract_palette(image_bytes: bytes, *, num_colors: int = 6) -> Optional[Palette]:
    """Quantize the artwork into a small palette and pick 3 representative colors."""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; album art hue sync disabled")
        return None

    try:
        import io

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img.thumbnail((64, 64))
        quantized = img.quantize(colors=num_colors, method=Image.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = sorted(quantized.getcolors(), reverse=True)  # [(count, palette_index), ...]

        def rgb_at(index: int) -> tuple[int, int, int]:
            return (palette[index * 3], palette[index * 3 + 1], palette[index * 3 + 2])

        def is_near_gray(rgb: tuple[int, int, int]) -> bool:
            r, g, b = rgb
            return max(r, g, b) - min(r, g, b) < 12 and (max(r, g, b) < 25 or min(r, g, b) > 235)

        ranked = [rgb_at(idx) for _count, idx in counts]
        vivid = [c for c in ranked if not is_near_gray(c)] or ranked
        while len(vivid) < 3:
            vivid.append(vivid[-1])

        def to_hex(rgb: tuple[int, int, int]) -> str:
            return "#{:02x}{:02x}{:02x}".format(*rgb)

        return Palette(dominant=to_hex(vivid[0]), secondary=to_hex(vivid[1]), accent=to_hex(vivid[2]))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to extract album art palette")
        return None


class AlbumArtHueSync:
    def __init__(self, db: DatabaseService, call_light_service: LightServiceCall):
        self.db = db
        self._call_light_service = call_light_service
        self._pending: dict[str, asyncio.Task] = {}  # light_id -> scheduled task

    async def on_new_artwork(self, room_id: str, image_bytes: bytes) -> Optional[Palette]:
        palette = extract_palette(image_bytes)
        if palette is None:
            return None

        await self.db.update_app_state(
            palette_dominant=palette.dominant,
            palette_secondary=palette.secondary,
            palette_accent=palette.accent,
        )

        for light in await self.db.list_lights(room_id=room_id):
            if light.sync_mode == "off":
                continue
            color = palette.dominant if light.sync_mode == "dominant" else palette.secondary
            self._schedule(light.id, light.ha_entity_id, color, light.hardware_latency_ms)
        return palette

    def _schedule(self, light_id: str, entity_id: str, hex_color: str, hardware_latency_ms: float) -> None:
        existing = self._pending.pop(light_id, None)
        if existing and not existing.done():
            existing.cancel()

        offset_ms = max(0.0, ESTIMATED_PLAYOUT_MS - hardware_latency_ms)
        self._pending[light_id] = asyncio.create_task(self._apply_after(offset_ms / 1000.0, entity_id, hex_color))

    async def _apply_after(self, delay_s: float, entity_id: str, hex_color: str) -> None:
        try:
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            rgb = tuple(int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
            await self._call_light_service(entity_id, {"rgb_color": list(rgb), "transition": 0.3})
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Failed to apply hue sync to %s", entity_id)
