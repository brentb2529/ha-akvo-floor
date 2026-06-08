"""Watchdog timing + bit15-drop abort tests."""

from __future__ import annotations

import pytest

from custom_components.akvo.akvo_client import AkvoClient, AkvoCommandRejected
from custom_components.akvo.const import HR_COMMAND

pytestmark = pytest.mark.asyncio


async def test_watchdog_timeout_aborts(fake_state):
    """If our monotonic clock advances past the watchdog timeout without a
    successful loop completion, the command aborts and HR10 is cleared.
    """
    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)

    # A fake clock we control. The state machine reads time via time_fn.
    clock = {"t": 1000.0}
    # Healthy + ready + moving, but NEVER achieving the target config, so the
    # only thing that can end the loop is the watchdog-timeout self-guard.
    fake_state.regs[0] = (1 << 0) | (1 << 15)  # ready + ready-for-external
    fake_state.regs[1] = 1  # floors_moving, no achieved bit

    async def slow_read(address: int, count: int) -> list[int]:
        # Advance time faster than the watchdog timeout each iteration so the
        # self-guard trips even though the move never completes.
        clock["t"] += 3.0
        return [fake_state.regs[address + i] for i in range(count)]

    client = AkvoClient(
        slow_read,
        transport.write,
        watchdog_timeout=5.0,
        service_interval=0.0,
        time_fn=lambda: clock["t"],
    )
    with pytest.raises(AkvoCommandRejected, match="watchdog timeout"):
        await client.async_request_configuration(3)
    assert client.last_abort_reason == "watchdog timeout"
    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    assert cmd_writes[-1] == 0


async def test_bit15_drop_before_completion_aborts(fake_state):
    """If motion starts and then stops (moving cleared) with ready-for-external
    still low and no config achieved, the PLC has withdrawn permission -> abort.
    """
    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)
    reads = {"n": 0}

    async def scripted_read(address: int, count: int) -> list[int]:
        reads["n"] += 1
        n = reads["n"]
        # Pre-check read (n==1): ready + ready-for-external so the move is
        # permitted. Toggle bit13 each read so the watchdog stays alive.
        fake_state.regs[0] = (1 << 0) | (1 << 15)
        if n % 2 == 0:
            fake_state.regs[0] |= 1 << 13
        if n == 2:
            fake_state.regs[1] = 1  # motion begins
        if n >= 3:
            # PLC withdraws: moving cleared, ready-for-external low, NOT achieved.
            fake_state.regs[1] = 0
            fake_state.regs[0] &= ~(1 << 15)
        return [fake_state.regs[address + i] for i in range(count)]

    client = AkvoClient(
        scripted_read, transport.write, watchdog_timeout=5.0, service_interval=0.0
    )
    with pytest.raises(AkvoCommandRejected, match="bit15"):
        await client.async_request_configuration(3)
    cmd_writes = [v for addr, v in transport.writes if addr == HR_COMMAND]
    assert cmd_writes[-1] == 0


async def test_fake_server_watchdog_supervision(fake_state):
    """The FAKE server itself sets bad-comm + stops if the mirror lapses."""
    import time

    fake_state._watchdog_grace = 0.0  # any elapsed time trips it
    fake_state._last_good_mirror = time.monotonic() - 10.0
    # Start a move directly on the fake.
    fake_state.write_register(10, 1 << 3)  # config #3 move bit
    assert fake_state.regs[1] & 1  # moving
    fake_state.step()
    # bad comm set + move stopped
    assert fake_state.regs[0] & (1 << 14)
    assert not (fake_state.regs[1] & 1)


async def test_idle_watchdog_mirrors_akvo_bit(fake_state):
    """async_service_watchdog mirrors HR0 bit13 into HR11 bit0 when idle."""
    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)
    client = AkvoClient(transport.read, transport.write, service_interval=0.0)

    fake_state.regs[0] |= 1 << 13  # AKVO watchdog high
    state = await client.async_read_state()
    await client.async_service_watchdog(state)
    assert (11, 1) in transport.writes  # mirrored high

    fake_state.regs[0] &= ~(1 << 13)  # AKVO watchdog low
    state = await client.async_read_state()
    await client.async_service_watchdog(state)
    assert (11, 0) in transport.writes  # mirrored low


async def test_idle_watchdog_yields_to_active_command(fake_state):
    """While a command holds the lock, idle servicing is a no-op (no double writer)."""
    from tests.conftest import FakeTransport

    transport = FakeTransport(fake_state, auto_step=False)
    client = AkvoClient(transport.read, transport.write, service_interval=0.0)

    await client._command_lock.acquire()
    try:
        state = await client.async_read_state()
        await client.async_service_watchdog(state)
        # No HR11 write happened because the lock is held.
        assert all(addr != 11 for addr, _ in transport.writes)
    finally:
        client._command_lock.release()
