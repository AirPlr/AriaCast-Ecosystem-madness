"""The protocol client, against a receiver shaped like the Android app's."""

import asyncio

import aiohttp
import pytest

from custom_components.ariacast import client as client_module
from custom_components.ariacast.client import (
    AriaCastClient,
    AriaCastError,
    parse_metadata,
)

from .fake_receiver import JPEG, FakeReceiver


async def wait_for(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest.fixture
async def client(session, receiver: FakeReceiver):
    c = AriaCastClient(session, "127.0.0.1", receiver.port)
    yield c
    await c.async_stop()


def test_parse_metadata_accepts_both_casings() -> None:
    snake = {"artwork_url": "/artwork", "duration_ms": 1000, "position_ms": 5, "is_playing": True}
    camel = {"artworkUrl": "/artwork", "durationMs": 1000, "positionMs": 5, "isPlaying": True}
    assert parse_metadata(snake) == parse_metadata(camel)
    assert parse_metadata(snake)["is_playing"] is True


def test_parse_metadata_returns_only_what_is_present() -> None:
    assert parse_metadata({"title": "Song"}) == {"title": "Song"}
    assert parse_metadata({}) == {}


def test_parse_metadata_ignores_wrong_types() -> None:
    assert parse_metadata({"title": 5, "duration_ms": "long", "is_playing": "yes", "position_ms": True}) == {}
    assert parse_metadata({"position_ms": -40})["position_ms"] == 0


async def test_becomes_available_with_current_metadata(client, receiver) -> None:
    receiver.metadata = {"title": "Song", "artist": "Band", "is_playing": True}
    await client.async_start()
    await wait_for(lambda: client.available and client.metadata.title == "Song")
    assert client.metadata.artist == "Band"
    assert client.metadata.is_playing is True


async def test_updates_merge_instead_of_erasing(client, receiver) -> None:
    receiver.metadata = {"title": "Song", "artist": "Band"}
    await client.async_start()
    await wait_for(lambda: client.metadata.title == "Song")
    await receiver.push(isPlaying=True)  # camelCase, mentions nothing else
    await wait_for(lambda: client.metadata.is_playing)
    assert client.metadata.title == "Song"
    assert client.metadata.artist == "Band"


async def test_listeners_hear_about_changes_and_can_leave(client, receiver) -> None:
    calls: list[int] = []
    remove = client.add_listener(lambda: calls.append(1))
    await client.async_start()
    await wait_for(lambda: client.available)
    before = len(calls)
    await receiver.push(title="New")
    await wait_for(lambda: len(calls) > before)
    remove()
    seen = len(calls)
    await receiver.push(title="Newer")
    await wait_for(lambda: client.metadata.title == "Newer")
    assert len(calls) == seen


async def test_a_failing_listener_does_not_break_the_others(client, receiver) -> None:
    good: list[int] = []

    def bad() -> None:
        raise RuntimeError("boom")

    client.add_listener(bad)
    client.add_listener(lambda: good.append(1))
    await client.async_start()
    await wait_for(lambda: client.available)
    assert good


async def test_reconnects_after_the_receiver_drops_the_connection(client, receiver, monkeypatch) -> None:
    monkeypatch.setattr(client_module, "BACKOFF_START", 0.01)
    await client.async_start()
    await wait_for(lambda: client.available)
    await receiver.drop_metadata_connections()
    await wait_for(lambda: not client.available)
    await wait_for(lambda: client.available)
    await receiver.push(title="Back")
    await wait_for(lambda: client.metadata.title == "Back")


async def test_stays_unavailable_while_nothing_listens_then_connects(session, monkeypatch) -> None:
    monkeypatch.setattr(client_module, "BACKOFF_START", 0.01)
    fake = FakeReceiver()
    await fake.start()
    port = fake.port
    await fake.stop()
    c = AriaCastClient(session, "127.0.0.1", port)
    await c.async_start()
    await asyncio.sleep(0.1)
    assert not c.available
    await c.async_stop()


async def test_stop_ends_the_background_task(client, receiver) -> None:
    await client.async_start()
    await wait_for(lambda: client.available)
    await client.async_stop()
    assert not client.available
    assert client._task is None


async def test_validate_succeeds_against_a_receiver(client) -> None:
    await client.async_validate()


async def test_validate_fails_when_nothing_answers(session) -> None:
    fake = FakeReceiver()
    await fake.start()
    port = fake.port
    await fake.stop()
    with pytest.raises(AriaCastError):
        await AriaCastClient(session, "127.0.0.1", port).async_validate()


@pytest.mark.parametrize("command", ["play", "pause", "stop", "next", "previous", "play_pause"])
async def test_commands_reach_the_receiver(client, receiver, command) -> None:
    await client.async_command(command)
    assert receiver.commands == [command]


async def test_unknown_commands_are_refused_locally(client, receiver) -> None:
    with pytest.raises(ValueError):
        await client.async_command("explode")
    assert receiver.commands == []


async def test_a_refused_command_is_an_error(client, receiver) -> None:
    receiver.command_status = 500
    with pytest.raises(AriaCastError):
        await client.async_command("play")


async def test_an_unreachable_receiver_is_an_error(session) -> None:
    fake = FakeReceiver()
    await fake.start()
    port = fake.port
    await fake.stop()
    with pytest.raises(AriaCastError):
        await AriaCastClient(session, "127.0.0.1", port).async_command("play")


async def test_falls_back_to_the_control_socket(session) -> None:
    fake = FakeReceiver(command_api=False)
    await fake.start()
    try:
        await AriaCastClient(session, "127.0.0.1", fake.port).async_command("pause")
        await wait_for(lambda: fake.commands == ["pause"])
    finally:
        await fake.stop()


async def test_artwork(client, receiver) -> None:
    assert await client.async_artwork() == (JPEG, "image/jpeg")
    receiver.has_artwork = False
    assert await client.async_artwork() is None
