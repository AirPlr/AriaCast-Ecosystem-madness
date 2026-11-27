"""Shared fixtures."""

from collections.abc import AsyncIterator

import pytest

from .fake_receiver import FakeReceiver


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ariacast."""


@pytest.fixture(autouse=True)
def allow_localhost(socket_enabled):
    """The fake receiver is a real server on localhost."""


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    """A running fake receiver."""
    fake = FakeReceiver()
    await fake.start()
    yield fake
    await fake.stop()
