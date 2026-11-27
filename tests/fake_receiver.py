"""A stand-in for an AriaCast receiver, shaped like the Android app's.

Behavior mirrors ReceiverServer.kt in the AriaCast app: ``/metadata`` pushes
``{"type": "metadata", "data": {...}}`` on connect and on every update,
``POST /api/command`` takes ``{"command": ...}``, ``GET /artwork`` serves a JPEG.
"""

from __future__ import annotations

from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class FakeReceiver:
    """Records what clients do to it."""

    def __init__(self, *, command_api: bool = True) -> None:
        self.metadata: dict[str, Any] = {}
        self.commands: list[str] = []
        self.command_status = 200
        self.has_artwork = True
        self._command_api = command_api
        self._metadata_sockets: list[web.WebSocketResponse] = []
        app = web.Application()
        app.router.add_get("/metadata", self._metadata)
        app.router.add_get("/control", self._control)
        app.router.add_post("/api/command", self._command)
        app.router.add_get("/artwork", self._artwork)
        self._server = TestServer(app)

    @property
    def port(self) -> int:
        return self._server.port

    async def start(self) -> None:
        await self._server.start_server(host="127.0.0.1")

    async def stop(self) -> None:
        # Open sockets would otherwise hold the server's shutdown for tens of seconds.
        await self.drop_metadata_connections()
        await self._server.close()

    async def push(self, **data: Any) -> None:
        """Update the receiver's metadata and tell everyone listening."""
        self.metadata.update(data)
        for ws in list(self._metadata_sockets):
            await ws.send_json({"type": "metadata", "data": self.metadata})

    async def drop_metadata_connections(self) -> None:
        """Close every /metadata socket from the server side."""
        for ws in list(self._metadata_sockets):
            await ws.close()

    async def _metadata(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._metadata_sockets.append(ws)
        await ws.send_json({"type": "metadata", "data": self.metadata})
        try:
            async for _ in ws:
                pass
        finally:
            self._metadata_sockets.remove(ws)
        return ws

    async def _control(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                body = msg.json()
                self.commands.append(body.get("command") or body.get("action"))
        return ws

    async def _command(self, request: web.Request) -> web.Response:
        if not self._command_api:
            return web.Response(status=404)
        if self.command_status != 200:
            return web.Response(status=self.command_status)
        body = await request.json()
        self.commands.append(body.get("command") or body.get("action"))
        return web.json_response({"success": True})

    async def _artwork(self, request: web.Request) -> web.Response:
        if not self.has_artwork:
            return web.Response(status=404)
        return web.Response(body=JPEG, content_type="image/jpeg")
