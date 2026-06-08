"""Shared fixtures for AKVO tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Make custom_components importable as a top-level package.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fake.fake_akvo_server import FakeAkvoState  # noqa: E402

from custom_components.akvo.akvo_client import AkvoClient  # noqa: E402


@pytest.fixture
def enable_custom_integrations(enable_custom_integrations):  # noqa: PT004
    """Enable loading of custom_components/akvo in HA tests."""
    return enable_custom_integrations


class FakeTransport:
    """In-process async transport wired to a FakeAkvoState.

    Every read advances the simulation one tick, so the safety state machine
    sees a moving floor progress to "achieved" deterministically without real
    timers. ``auto_step`` can be disabled for fine-grained control.
    """

    def __init__(self, state: FakeAkvoState, *, auto_step: bool = True) -> None:
        self.state = state
        self.auto_step = auto_step
        self.writes: list[tuple[int, int]] = []

    async def read(self, address: int, count: int) -> list[int]:
        if self.auto_step:
            self.state.step()
        return [self.state.regs[address + i] for i in range(count)]

    async def write(self, address: int, value: int) -> None:
        self.writes.append((address, value))
        self.state.write_register(address, value)


@pytest.fixture
def fake_state() -> FakeAkvoState:
    # Large watchdog grace so timing tests are deterministic unless overridden.
    return FakeAkvoState(watchdog_grace=5.0)


@pytest.fixture
def fake_transport(fake_state: FakeAkvoState) -> FakeTransport:
    return FakeTransport(fake_state)


@pytest.fixture
def client(fake_transport: FakeTransport) -> AkvoClient:
    # Short service interval so the watchdog loop iterates quickly in tests.
    return AkvoClient(
        fake_transport.read,
        fake_transport.write,
        watchdog_timeout=5.0,
        service_interval=0.0,
    )
