"""Entry point for the `ariacast_core` Home Assistant add-on.

Runs the parts of the ecosystem that don't belong inside HA's own process:
the low-latency `AriaCastSocketServer` audio relay, and the ingress-served
Web UI Canvas editor / REST API for PC-side room configuration. Talks to the
*same* SQLite database file the `custom_components/ariacast` integration
uses (mounted from `/config` into this add-on's `/data`), so both sides of
the ecosystem share one source of truth.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ariacast_core.main")

try:
    from ariacast_core.database import DatabaseService
    from ariacast_core.dsp import RoomSpatialAudioDSP
    from ariacast_core.effects import EFFECTS_PRESETS
    from ariacast_core.hue_sync import AlbumArtHueSync
    from ariacast_core.models import Light, Room, Speaker, SpeakerStatus
    from ariacast_core.node_manager import SpeakerNodeManager
    from ariacast_core.pubsub import PubSubServer

    from socket_server import AriaCastSocketServer
    from discovery_responder import start_discovery_responder
except Exception as exc:  # pragma: no cover - startup diagnostics
    # Supervisor's add-on log fetch truncates long output, and the default
    # traceback for a failed C-extension import (e.g. numpy) can run to
    # thousands of characters — log a short, truncation-surviving summary
    # instead of letting the full traceback push the actual cause out.
    logger.error("Startup import failed: %s: %s", type(exc).__name__, str(exc)[-600:])
    raise SystemExit(1) from None

DB_PATH = os.environ.get("ARIACAST_DB_PATH", "/data/ariacast.db")
WEB_PORT = int(os.environ.get("ARIACAST_WEB_PORT", "8099"))
WWW_DIR = Path(__file__).parent / "www"

# Add-ons reach Home Assistant Core through the Supervisor proxy using
# SUPERVISOR_TOKEN, injected automatically when `homeassistant_api: true` is
# set in config.yaml (see addon/ariacast_core/config.yaml). Running standalone
# (outside Supervisor, e.g. plain `docker run` on another host) instead set
# HA_TOKEN to a long-lived access token and HA_API_BASE to the real HA URL.
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN")
HA_API_BASE = os.environ.get("HA_API_BASE", "http://supervisor/core/api")

# The LAN-reachable base URL for *this* add-on, i.e. what a bridged HA
# media_player (Sonos/Cast/Alexa/etc.) should fetch /stream/<room>.wav from.
# Supervisor-mode doesn't need this (no HA-bridged speakers without
# HA_API_BASE configured too), but standalone deployments must set it —
# there's no reliable way to guess a container's LAN-facing address from the
# inside, and guessing wrong would fail silently on real speaker hardware.
PUBLIC_BASE_URL = os.environ.get("ARIACAST_PUBLIC_URL", "")


async def _call_ha_service(domain: str, service: str, entity_id: str, params: dict) -> None:
    if not SUPERVISOR_TOKEN:
        logger.debug("No HA token available; skipping %s.%s for %s", domain, service, entity_id)
        return
    import aiohttp

    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{HA_API_BASE}/services/{domain}/{service}",
            headers=headers,
            json={"entity_id": entity_id, **params},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status >= 300:
                logger.warning("%s.%s call for %s failed: HTTP %s", domain, service, entity_id, resp.status)


async def _call_light_service(entity_id: str, params: dict) -> None:
    await _call_ha_service("light", "turn_on", entity_id, params)


async def _call_media_service(entity_id: str, service: str, params: dict) -> None:
    await _call_ha_service("media_player", service, entity_id, params)

# Recompute DSP for the actively-tracked room this often, in addition to the
# event-driven recompute triggered by listener movement / node transitions.
DSP_TICK_S = 2.0


async def _dsp_tick_loop(dsp: RoomSpatialAudioDSP, db: DatabaseService) -> None:
    import asyncio

    while True:
        state = await db.get_app_state()
        if state.current_room_id:
            await dsp.recompute_room(state.current_room_id)
        await asyncio.sleep(DSP_TICK_S)


def _build_rest_api(app: web.Application, db: DatabaseService, dsp: RoomSpatialAudioDSP, socket_server) -> None:
    async def list_rooms(request: web.Request) -> web.Response:
        return web.json_response([r.__dict__ for r in await db.list_rooms()])

    async def upsert_room(request: web.Request) -> web.Response:
        body = await request.json()
        room_id = body.get("id")
        # Look the existing row up by id when given (the Web UI Save button
        # always sends one for a room it already knows about), else by
        # ha_area_id (the HA area sync's only key, since it never knows a
        # room's id). Either way, anything the caller didn't explicitly
        # include falls back to what's already stored — a caller that only
        # cares about one slice of a room (name/size here, the effects
        # chain in set_room_effects below) must not silently blank out the
        # rest. This previously wasn't true for ha_area_id itself: the Web
        # UI's Save button never sends it, so editing an HA-linked room's
        # name/size used to null out its ha_area_id — silently orphaning it
        # and leaving the next HA sync to mint a duplicate for that area.
        existing = await db.get_room(room_id) if room_id else None
        if existing is None and body.get("ha_area_id"):
            existing = await db.get_room_by_ha_area_id(body["ha_area_id"])
        room = Room(
            id=room_id or (existing.id if existing else db.new_id()),
            name=body.get("name", existing.name if existing else body["name"]),
            ha_area_id=body["ha_area_id"] if "ha_area_id" in body else (existing.ha_area_id if existing else None),
            width_meters=float(body.get("width_meters", existing.width_meters if existing else 4.0)),
            height_meters=float(body.get("height_meters", existing.height_meters if existing else 4.0)),
            eq_bass_db=float(body.get("eq_bass_db", existing.eq_bass_db if existing else 0.0)),
            eq_mid_db=float(body.get("eq_mid_db", existing.eq_mid_db if existing else 0.0)),
            eq_treble_db=float(body.get("eq_treble_db", existing.eq_treble_db if existing else 0.0)),
            reverb_wet=float(body.get("reverb_wet", existing.reverb_wet if existing else 0.0)),
            reverb_size=float(body.get("reverb_size", existing.reverb_size if existing else 0.5)),
            compressor_enabled=bool(body.get("compressor_enabled", existing.compressor_enabled if existing else False)),
            effects_preset=body.get("effects_preset", existing.effects_preset if existing else "flat"),
        )
        await db.upsert_room(room)
        return web.json_response(room.__dict__)

    async def set_room_effects(request: web.Request) -> web.Response:
        room_id = request.match_info["room_id"]
        body = await request.json()
        existing = await db.get_room(room_id)
        if not existing:
            return web.json_response({"error": "not found"}, status=404)

        preset_name = body.get("preset")
        preset = EFFECTS_PRESETS.get(preset_name) if preset_name else None
        if preset_name and preset is None:
            return web.json_response({"error": f"unknown preset '{preset_name}'"}, status=400)

        values = {**preset} if preset else {}
        for field_name in (
            "eq_bass_db", "eq_mid_db", "eq_treble_db", "reverb_wet", "reverb_size", "compressor_enabled"
        ):
            if field_name in body:
                values[field_name] = body[field_name]

        existing.eq_bass_db = float(values.get("eq_bass_db", existing.eq_bass_db))
        existing.eq_mid_db = float(values.get("eq_mid_db", existing.eq_mid_db))
        existing.eq_treble_db = float(values.get("eq_treble_db", existing.eq_treble_db))
        existing.reverb_wet = float(values.get("reverb_wet", existing.reverb_wet))
        existing.reverb_size = float(values.get("reverb_size", existing.reverb_size))
        existing.compressor_enabled = bool(values.get("compressor_enabled", existing.compressor_enabled))
        existing.effects_preset = preset_name or existing.effects_preset

        updated = await db.update_room_effects(existing)
        if updated:
            await socket_server.update_room_effects(updated)
            return web.json_response(updated.__dict__)
        return web.json_response({"error": "not found"}, status=404)

    async def list_speakers(request: web.Request) -> web.Response:
        room_id = request.query.get("room_id")
        speakers = await db.list_speakers(room_id=room_id)
        return web.json_response([{**s.__dict__, "status": s.status.value} for s in speakers])

    async def update_speaker_position(request: web.Request) -> web.Response:
        speaker_id = request.match_info["speaker_id"]
        body = await request.json()
        speaker = await db.get_speaker(speaker_id)
        if not speaker:
            return web.json_response({"error": "not found"}, status=404)
        old_room_id = speaker.room_id
        speaker.pos_x = float(body.get("pos_x", speaker.pos_x))
        speaker.pos_y = float(body.get("pos_y", speaker.pos_y))
        speaker.orientation_deg = float(body.get("orientation_deg", speaker.orientation_deg))
        if "room_id" in body:
            speaker.room_id = body["room_id"]
        await db.upsert_speaker(speaker)
        if speaker.room_id:
            await dsp.recompute_room(speaker.room_id)
        if old_room_id and old_room_id != speaker.room_id:
            # The speaker-picker checklist can move a speaker straight out of
            # whatever room it was in — rebalance that room's remaining
            # speakers immediately instead of leaving them on stale gain/delay
            # values until the next periodic DSP tick (up to DSP_TICK_S away).
            await dsp.recompute_room(old_room_id)
        return web.json_response({**speaker.__dict__, "status": speaker.status.value})

    async def list_lights(request: web.Request) -> web.Response:
        room_id = request.query.get("room_id")
        return web.json_response([l.__dict__ for l in await db.list_lights(room_id=room_id)])

    async def upsert_light(request: web.Request) -> web.Response:
        body = await request.json()
        ha_entity_id = body["ha_entity_id"]
        existing = await db.get_light_by_ha_entity_id(ha_entity_id) if not body.get("id") else None
        light = Light(
            id=body.get("id") or (existing.id if existing else db.new_id()),
            room_id=body.get("room_id"),
            ha_entity_id=ha_entity_id,
            hardware_latency_ms=float(body.get("hardware_latency_ms", 150.0)),
            sync_mode=body.get("sync_mode", "palette"),
        )
        await db.upsert_light(light)
        return web.json_response(light.__dict__)

    async def set_listener_position(request: web.Request) -> web.Response:
        body = await request.json()
        results = await dsp.on_listener_moved(body["room_id"], float(body["x"]), float(body["y"]))
        return web.json_response([r.__dict__ for r in results])

    async def sync_ha_speakers(request: web.Request) -> web.Response:
        """Bulk-upsert existing Home Assistant `media_player` entities as
        DSP-routable speakers alongside native AriaCast hardware — pushed by
        the HACS integration (`ha_bridge_sync.py`), which is the side that
        actually enumerates `hass.states`. Keyed by a synthetic
        `hardware_uuid` (`ha:<entity_id>`) so re-syncs update in place."""
        body = await request.json()
        results = []
        for item in body.get("speakers", []):
            hardware_uuid = f"ha:{item['entity_id']}"
            existing = await db.get_speaker_by_uuid(hardware_uuid)
            speaker = Speaker(
                id=existing.id if existing else db.new_id(),
                room_id=item.get("room_id") or (existing.room_id if existing else None),
                ha_entity_id=item["entity_id"],
                hardware_uuid=hardware_uuid,
                ip_address="",
                port=0,
                name=item.get("name", item["entity_id"]),
                status=SpeakerStatus.ONLINE if item.get("available", True) else SpeakerStatus.UNAVAILABLE,
                platform="home_assistant",
                pos_x=existing.pos_x if existing else 0.0,
                pos_y=existing.pos_y if existing else 0.0,
                gain_db=existing.gain_db if existing else 0.0,
                delay_ms=existing.delay_ms if existing else 0.0,
                extra_delay_ms=existing.extra_delay_ms if existing else 0.0,
                air_cutoff_hz=existing.air_cutoff_hz if existing else 20000.0,
                volume=existing.volume if existing else 50,
            )
            await db.upsert_speaker(speaker)
            results.append({**speaker.__dict__, "status": speaker.status.value})
        return web.json_response(results)

    async def set_speaker_calibration(request: web.Request) -> web.Response:
        """Manual/calibration delay override for one speaker, additive on top
        of the DSP's geometric delay (see RoomSpatialAudioDSP.recompute_room).
        Meant for real hardware-latency differences between speakers (e.g.
        two ESPHome nodes with very different inherent playback delay) that
        pure listener-distance math can't know about — set directly from the
        Web UI, or written back by an app-driven auto-calibration flow."""
        speaker_id = request.match_info["speaker_id"]
        body = await request.json()
        speaker = await db.set_speaker_extra_delay(speaker_id, float(body["extra_delay_ms"]))
        if not speaker:
            return web.json_response({"error": "not found"}, status=404)
        if speaker.room_id:
            await dsp.recompute_room(speaker.room_id)
            speaker = await db.get_speaker(speaker_id)
        return web.json_response({**speaker.__dict__, "status": speaker.status.value})

    async def list_effects_presets(request: web.Request) -> web.Response:
        return web.json_response(EFFECTS_PRESETS)

    app.router.add_get("/api/rooms", list_rooms)
    app.router.add_post("/api/rooms", upsert_room)
    app.router.add_post("/api/rooms/{room_id}/effects", set_room_effects)
    app.router.add_get("/api/effects/presets", list_effects_presets)
    app.router.add_get("/api/speakers", list_speakers)
    app.router.add_post("/api/speakers/{speaker_id}/position", update_speaker_position)
    app.router.add_post("/api/speakers/{speaker_id}/calibration", set_speaker_calibration)
    app.router.add_post("/api/speakers/sync-ha", sync_ha_speakers)
    app.router.add_get("/api/lights", list_lights)
    app.router.add_post("/api/lights", upsert_light)
    app.router.add_post("/api/listener-position", set_listener_position)


async def _on_startup(app: web.Application) -> None:
    db: DatabaseService = app["db"]
    node_manager: SpeakerNodeManager = app["node_manager"]
    await db.start()
    await app["pubsub"].start()
    await node_manager.start()
    import asyncio

    app["dsp_task"] = asyncio.create_task(_dsp_tick_loop(app["dsp"], db))

    advertise_ip = urlparse(PUBLIC_BASE_URL).hostname if PUBLIC_BASE_URL else ""
    app["discovery_transport"] = await start_discovery_responder(advertise_ip, WEB_PORT)

    logger.info("ariacast_core add-on ready on :%s", WEB_PORT)


async def _on_cleanup(app: web.Application) -> None:
    app["dsp_task"].cancel()
    if app.get("discovery_transport") is not None:
        app["discovery_transport"].close()
    await app["node_manager"].stop()
    await app["pubsub"].stop()
    await app["db"].stop()


def create_app() -> web.Application:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = DatabaseService(DB_PATH)
    node_manager = SpeakerNodeManager(db)
    dsp = RoomSpatialAudioDSP(db)
    pubsub = PubSubServer(db)
    socket_server = AriaCastSocketServer(
        db, node_manager, call_media_service=_call_media_service, public_base_url=PUBLIC_BASE_URL
    )
    if not PUBLIC_BASE_URL:
        logger.warning(
            "ARIACAST_PUBLIC_URL not set — HA-bridged speakers (Sonos/Cast/Alexa/etc. "
            "surfaced via the HA integration) won't receive audio until it's configured "
            "to this add-on's own LAN-reachable URL (e.g. http://192.168.1.68:8099)."
        )
    hue_sync = AlbumArtHueSync(db, _call_light_service)

    app = web.Application()
    app["db"], app["node_manager"], app["dsp"], app["pubsub"], app["hue_sync"] = db, node_manager, dsp, pubsub, hue_sync

    pubsub.attach_routes(app, path="/ws")
    socket_server.attach_routes(app)
    _build_rest_api(app, db, dsp, socket_server)
    app.router.add_static("/", WWW_DIR, show_index=True)

    _seen_artwork: dict[str, str] = {}

    async def _on_metadata(speaker: Speaker, metadata: dict) -> None:
        artwork_url = metadata.get("artwork_url")
        if not artwork_url or not speaker.room_id:
            return
        if _seen_artwork.get(speaker.room_id) == artwork_url:
            return
        _seen_artwork[speaker.room_id] = artwork_url
        import aiohttp

        url = artwork_url if artwork_url.startswith("http") else f"http://{speaker.ip_address}:{speaker.port}{artwork_url}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        await hue_sync.on_new_artwork(speaker.room_id, await resp.read())
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch artwork for hue sync from %s", url)

    node_manager.on_metadata(_on_metadata)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=WEB_PORT)
