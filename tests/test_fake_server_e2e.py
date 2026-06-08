"""End-to-end test: real pymodbus client <-> real fake AKVO Modbus TCP server.

This validates the ModbusTransport adapter and the full state machine over an
actual socket (loopback), not just the in-process fake. No hardware involved.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from contextlib import asynccontextmanager

from pymodbus.server import ServerAsyncStop

from custom_components.akvo.akvo_client import AkvoClient, AkvoCommandRejected
from custom_components.akvo.modbus_transport import ModbusTransport

from fake.fake_akvo_server import (
    FakeAkvoState,
    build_context,
    start_ticker,
)

from pymodbus.server import StartAsyncTcpServer

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("socket_enabled"),
]


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@asynccontextmanager
async def _running_server(state: FakeAkvoState):
    """Start the fake server + ticker, yield the port, and tear both down."""
    port = _free_port()
    context = build_context(state)
    ticker = start_ticker(state)
    server_task = asyncio.create_task(
        StartAsyncTcpServer(context=context, address=("127.0.0.1", port))
    )
    await asyncio.sleep(0.4)  # let the server bind
    try:
        yield port
    finally:
        await ServerAsyncStop()
        ticker.cancel()
        server_task.cancel()
        for task in (ticker, server_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


async def test_e2e_read_and_command():
    state = FakeAkvoState(watchdog_grace=30.0)
    state.regs[6] = 1500
    async with _running_server(state) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.async_connect()
        try:
            client = AkvoClient(
                transport.read_holding,
                transport.write_holding,
                watchdog_timeout=10.0,
                service_interval=0.05,
            )

            # READ populates correctly over the wire.
            snap = await client.async_read_state()
            assert snap.main_position_m == pytest.approx(1.5, abs=0.05)
            assert snap.ready_for_external

            # COMMAND completes to achieved over the wire.
            result = await client.async_request_configuration(3)
            assert result.active_configuration == 3
        finally:
            await transport.async_close()


async def test_e2e_abort_on_injected_estop():
    state = FakeAkvoState(watchdog_grace=30.0)
    async with _running_server(state) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.async_connect()
        try:
            client = AkvoClient(
                transport.read_holding,
                transport.write_holding,
                watchdog_timeout=10.0,
                service_interval=0.05,
            )

            async def injector():
                await asyncio.sleep(0.15)
                state.inject_estop(indoor=False)

            inj = asyncio.create_task(injector())
            with pytest.raises(AkvoCommandRejected, match="aborted"):
                await client.async_request_configuration(8)
            await inj
            assert client.last_abort_reason is not None
            # HR10 was cleared to 0 on abort (write register, not in the read
            # block; inspect the fake's model directly).
            assert state.regs[10] == 0
        finally:
            await transport.async_close()
