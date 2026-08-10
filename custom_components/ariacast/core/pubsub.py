"""WebSocket pub/sub bus fanning `DatabaseService` mutations out to every
live view of the system: the Web UI Canvas editor on PC, the companion app's
Room Canvas, and (indirectly) Home Assistant entity state.

Every connected client receives the exact same event stream, so a speaker
dragged on the PC canvas, a room renamed from the phone, and a speaker going
OFFLINE all show up everywhere within one WebSocket round trip — this is
what "instant persistence" means for this project: there is no polling
anywhere in the UI layer.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from aiohttp import web, WSMsgType

from .database import DatabaseService

logger = logging.getLogger(__name__)


class PubSubServer:
    def __init__(self, db: DatabaseService):
        self.db = db
        self._clients: set[web.WebSocketResponse] = set()
        self._unsubscribe = None

    def attach_routes(self, app: web.Application, path: str = "/ws") -> None:
        app.router.add_get(path, self._handle_ws)

    async def start(self) -> None:
        self._unsubscribe = self.db.subscribe(self._on_db_event)

    async def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        for ws in list(self._clients):
            await ws.close()
        self._clients.clear()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def _on_db_event(self, event: dict[str, Any]) -> None:
        await self.broadcast({"type": "db_event", **event})

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients:
            return
        payload = json.dumps(message)
        dead = []
        for ws in self._clients:
            try:
                await ws.send_str(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)
        self._clients.add(ws)
        logger.info("pubsub client connected (%d total)", len(self._clients))

        try:
            # Full snapshot on connect so the UI doesn't have to wait for the next mutation.
            await ws.send_str(json.dumps({"type": "snapshot", "data": await self._snapshot()}))

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        request_data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue
                    if request_data.get("type") == "get_snapshot":
                        await ws.send_str(json.dumps({"type": "snapshot", "data": await self._snapshot()}))
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        finally:
            self._clients.discard(ws)
            logger.info("pubsub client disconnected (%d remaining)", len(self._clients))

        return ws

    async def _snapshot(self) -> dict[str, Any]:
        rooms = await self.db.list_rooms()
        speakers = await self.db.list_speakers()
        lights = await self.db.list_lights()
        app_state = await self.db.get_app_state()
        return {
            "rooms": [r.__dict__ for r in rooms],
            "speakers": [{**s.__dict__, "status": s.status.value} for s in speakers],
            "lights": [l.__dict__ for l in lights],
            "app_state": app_state.__dict__,
        }
