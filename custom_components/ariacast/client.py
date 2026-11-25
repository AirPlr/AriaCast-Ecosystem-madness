"""Client for one AriaCast receiver.

Talks the AriaCast wire protocol (see docs/protocol-spec): the receiver pushes track
metadata over the ``/metadata`` WebSocket, and transport commands go to
``POST /api/command``. Receivers that only take commands over the ``/control``
WebSocket are handled by a fallback.

This module deliberately imports nothing from Home Assistant, so it can be tested
against a plain aiohttp server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
from typing import Any

import aiohttp

from .const import DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5)
HEARTBEAT = 15
BACKOFF_START = 1.0
BACKOFF_MAX = 30.0

# Commands every AriaCast receiver understands. Seek is left out on purpose: the
# Android receiver drops the position, so it cannot be honoured reliably.
COMMANDS = frozenset({"play", "pause", "stop", "next", "previous", "play_pause"})


class AriaCastError(Exception):
    """The receiver could not be reached or refused a command."""


@dataclass(frozen=True)
class Metadata:
    """What the receiver reports about the current track."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    artwork_url: str | None = None
    duration_ms: int = 0
    position_ms: int = 0
    is_playing: bool = False
    updated_at: datetime | None = None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


def parse_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Return the fields present in ``data``, under our names.

    Senders use snake_case (``artwork_url``) or camelCase (``artworkUrl``); the
    spec says receivers must accept both. Only fields that are present are
    returned, so an update never erases what it does not mention.
    """
    out: dict[str, Any] = {}
    for field, keys in (
        ("title", ("title",)),
        ("artist", ("artist",)),
        ("album", ("album",)),
        ("artwork_url", ("artwork_url", "artworkUrl")),
    ):
        value = _first(data, *keys)
        if isinstance(value, str):
            out[field] = value
    for field, keys in (
        ("duration_ms", ("duration_ms", "durationMs")),
        ("position_ms", ("position_ms", "positionMs")),
    ):
        value = _first(data, *keys)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[field] = max(int(value), 0)
    playing = _first(data, "is_playing", "isPlaying")
    if isinstance(playing, bool):
        out["is_playing"] = playing
    return out


class AriaCastClient:
    """Keeps a connection to one receiver and exposes its state."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int = DEFAULT_PORT
    ) -> None:
        """Set up the client; nothing connects until :meth:`async_start`."""
        self._session = session
        self._http = f"http://{host}:{port}"
        self._ws_base = f"ws://{host}:{port}"
        self.metadata = Metadata()
        self.available = False
        self._listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task[None] | None = None

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever state changes. Returns the remover."""
        self._listeners.append(listener)

        def remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001 - one bad listener must not stop the rest
                _LOGGER.exception("AriaCast listener failed")

    async def async_validate(self) -> None:
        """Raise :class:`AriaCastError` unless a receiver answers on this address."""
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                ws = await self._session.ws_connect(f"{self._ws_base}/metadata")
            await ws.close()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise AriaCastError(f"No AriaCast receiver at {self._http}: {err}") from err

    async def async_start(self) -> None:
        """Start following the receiver; reconnects on its own until stopped."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"ariacast {self._http}")

    async def async_stop(self) -> None:
        """Stop following the receiver and wait for the background task to end."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.available = False

    async def _run(self) -> None:
        backoff = BACKOFF_START
        while True:
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    ws = await self._session.ws_connect(
                        f"{self._ws_base}/metadata", heartbeat=HEARTBEAT
                    )
                try:
                    backoff = BACKOFF_START
                    self._set_available(True)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self._handle_text(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                finally:
                    await ws.close()
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                _LOGGER.debug("AriaCast %s: %s", self._http, err)
            except Exception:  # noqa: BLE001 - keep following the receiver whatever happens
                _LOGGER.exception("AriaCast %s: unexpected error", self._http)
            self._set_available(False)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _set_available(self, available: bool) -> None:
        if available != self.available:
            self.available = available
            self._notify()

    def _handle_text(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict):
            return
        # Receivers send {"type": "metadata", "data": {...}}; a flat object is tolerated.
        if message.get("type") not in (None, "metadata"):
            return
        data = message.get("data", message)
        if not isinstance(data, dict):
            return
        update = parse_metadata(data)
        self.metadata = replace(self.metadata, **update, updated_at=datetime.now(UTC))
        self._notify()

    async def async_command(self, command: str) -> None:
        """Send a transport command (see :data:`COMMANDS`)."""
        if command not in COMMANDS:
            raise ValueError(f"Unknown AriaCast command: {command}")
        try:
            async with self._session.post(
                f"{self._http}/api/command", json={"command": command}, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status in (404, 405):
                    await self._command_over_websocket(command)
                    return
                response.raise_for_status()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise AriaCastError(f"Command {command} failed: {err}") from err

    async def _command_over_websocket(self, command: str) -> None:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            ws = await self._session.ws_connect(f"{self._ws_base}/control")
        try:
            await ws.send_json({"command": command})
        finally:
            await ws.close()

    async def async_artwork(self) -> tuple[bytes, str] | None:
        """Fetch the current artwork from the receiver, if it has any."""
        try:
            async with self._session.get(
                f"{self._http}/artwork", timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status != 200:
                    return None
                return await response.read(), response.content_type
        except (aiohttp.ClientError, TimeoutError, OSError):
            return None
