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

from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ariacast_core.main")

try:
    from ariacast_core.database import DatabaseService
    from ariacast_core.dsp import RoomSpatialAudioDSP
    from ariacast_core.hue_sync import AlbumArtHueSync
    from ariacast_core.models import Light, Room, Speaker
    from ariacast_core.node_manager import SpeakerNodeManager
    from ariacast_core.pubsub import PubSubServer

    from socket_server import AriaCastSocketServer
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

# Add-ons reach Home Assistant Core through the Supervisor proxy using this
# token, injected automatically when `homeassistant_api: true` is set in
# config.yaml (see addon/ariacast_core/config.yaml).
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
HA_API_BASE = "http://supervisor/core/api"


async def _call_light_service(entity_id: str, params: dict) -> None:
    if not SUPERVISOR_TOKEN:
        logger.debug("No SUPERVISOR_TOKEN available; skipping light service call for %s", entity_id)
        return
    import aiohttp

    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{HA_API_BASE}/services/light/turn_on",
            headers=headers,
            json={"entity_id": entity_id, **params},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status >= 300:
                logger.warning("Light service call for %s failed: HTTP %s", entity_id, resp.status)

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


def _build_rest_api(app: web.Application, db: DatabaseService, dsp: RoomSpatialAudioDSP) -> None:
    async def list_rooms(request: web.Request) -> web.Response:
        return web.json_response([r.__dict__ for r in await db.list_rooms()])

    async def upsert_room(request: web.Request) -> web.Response:
        body = await request.json()
        room = Room(
            id=body.get("id") or db.new_id(),
            name=body["name"],
            ha_area_id=body.get("ha_area_id"),
            width_meters=float(body.get("width_meters", 4.0)),
            height_meters=float(body.get("height_meters", 4.0)),
        )
        await db.upsert_room(room)
        return web.json_response(room.__dict__)

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
        speaker.pos_x = float(body.get("pos_x", speaker.pos_x))
        speaker.pos_y = float(body.get("pos_y", speaker.pos_y))
        speaker.orientation_deg = float(body.get("orientation_deg", speaker.orientation_deg))
        if "room_id" in body:
            speaker.room_id = body["room_id"]
        await db.upsert_speaker(speaker)
        if speaker.room_id:
            await dsp.recompute_room(speaker.room_id)
        return web.json_response({**speaker.__dict__, "status": speaker.status.value})

    async def list_lights(request: web.Request) -> web.Response:
        room_id = request.query.get("room_id")
        return web.json_response([l.__dict__ for l in await db.list_lights(room_id=room_id)])

    async def upsert_light(request: web.Request) -> web.Response:
        body = await request.json()
        light = Light(
            id=body.get("id") or db.new_id(),
            room_id=body.get("room_id"),
            ha_entity_id=body["ha_entity_id"],
            hardware_latency_ms=float(body.get("hardware_latency_ms", 150.0)),
            sync_mode=body.get("sync_mode", "palette"),
        )
        await db.upsert_light(light)
        return web.json_response(light.__dict__)

    async def set_listener_position(request: web.Request) -> web.Response:
        body = await request.json()
        results = await dsp.on_listener_moved(body["room_id"], float(body["x"]), float(body["y"]))
        return web.json_response([r.__dict__ for r in results])

    app.router.add_get("/api/rooms", list_rooms)
    app.router.add_post("/api/rooms", upsert_room)
    app.router.add_get("/api/speakers", list_speakers)
    app.router.add_post("/api/speakers/{speaker_id}/position", update_speaker_position)
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
    logger.info("ariacast_core add-on ready on :%s", WEB_PORT)


async def _on_cleanup(app: web.Application) -> None:
    app["dsp_task"].cancel()
    await app["node_manager"].stop()
    await app["pubsub"].stop()
    await app["db"].stop()


def create_app() -> web.Application:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = DatabaseService(DB_PATH)
    node_manager = SpeakerNodeManager(db)
    dsp = RoomSpatialAudioDSP(db)
    pubsub = PubSubServer(db)
    socket_server = AriaCastSocketServer(db, node_manager)
    hue_sync = AlbumArtHueSync(db, _call_light_service)

    app = web.Application()
    app["db"], app["node_manager"], app["dsp"], app["pubsub"], app["hue_sync"] = db, node_manager, dsp, pubsub, hue_sync

    pubsub.attach_routes(app, path="/ws")
    socket_server.attach_routes(app)
    _build_rest_api(app, db, dsp)
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
