"""UDP discovery *responder* for the ariacast_core add-on.

Every discovery-related piece elsewhere in this ecosystem
(`core/protocol_client.py::discover_udp`, the Android app's
`DiscoveryManager`) is a discovery *client* — it broadcasts
`DISCOVER_AUDIOCAST` and listens for replies. Nothing previously answered
on the add-on's own side, so despite `12888/udp` being exposed in
`config.yaml`/`docker run`, the add-on was invisible to any Sender's
broadcast: the port was open but nothing was bound to it.

This makes the add-on answer exactly like a native AriaCast receiver would
(see `repos/AriaCast-Protocol-Spec/spec/discovery.md`), so it shows up in
the Android app's existing discovered-server list / the HA integration's
discovery sweep without either of them needing protocol-level changes.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger("ariacast_core.discovery_responder")

DISCOVERY_PORT = 12888
DISCOVERY_MAGIC = "DISCOVER_AUDIOCAST"


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, server_name: str, advertise_ip: str, advertise_port: int):
        self.server_name = server_name
        self.advertise_ip = advertise_ip
        self.advertise_port = advertise_port
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        try:
            message = data.decode("utf-8").strip()
        except UnicodeDecodeError:
            return
        if message != DISCOVERY_MAGIC:
            return
        payload = json.dumps(
            {
                "server_name": self.server_name,
                "ip": self.advertise_ip,
                "port": self.advertise_port,
                "samplerate": 48000,
                "channels": 2,
                "platform": "ariacast_core_dsp",
            }
        ).encode("utf-8")
        assert self.transport is not None
        self.transport.sendto(payload, addr)
        logger.debug("Answered discovery probe from %s", addr)


async def start_discovery_responder(
    advertise_ip: str, advertise_port: int, server_name: str = "AriaCast Hub"
) -> asyncio.DatagramTransport | None:
    if not advertise_ip:
        logger.warning(
            "Skipping UDP discovery responder: no advertise IP configured "
            "(set ARIACAST_PUBLIC_URL so Senders can find this add-on)"
        )
        return None
    loop = asyncio.get_event_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(server_name, advertise_ip, advertise_port),
        local_addr=("0.0.0.0", DISCOVERY_PORT),
    )
    logger.info(
        "UDP discovery responder listening on :%s, advertising %s:%s",
        DISCOVERY_PORT, advertise_ip, advertise_port,
    )
    return transport
