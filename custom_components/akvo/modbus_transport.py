"""pymodbus TCP transport adapter for the AKVO client.

Kept separate from :mod:`akvo_client` so the safety state machine has no hard
dependency on pymodbus and can be tested against an in-process fake. This module
only translates ``read_fn(address, count) -> list[int]`` and
``write_fn(address, value)`` into pymodbus holding-register calls. All calls are
native-async (pymodbus AsyncModbusTcpClient) — nothing blocks the event loop.
"""

from __future__ import annotations

import logging

from pymodbus.client import AsyncModbusTcpClient

from .akvo_client import AkvoConnectionError

_LOGGER = logging.getLogger(__name__)


class ModbusTransport:
    """Thin async holding-register transport over pymodbus TCP."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._client = AsyncModbusTcpClient(host, port=port)

    async def async_connect(self) -> None:
        connected = await self._client.connect()
        if not connected:
            raise AkvoConnectionError(f"cannot connect to {self._host}:{self._port}")

    async def async_close(self) -> None:
        self._client.close()

    async def read_holding(self, address: int, count: int) -> list[int]:
        result = await self._client.read_holding_registers(address, count=count)
        if result.isError():
            raise AkvoConnectionError(f"read error @ {address}+{count}: {result}")
        return list(result.registers)

    async def write_holding(self, address: int, value: int) -> None:
        result = await self._client.write_register(address, value)
        if result.isError():
            raise AkvoConnectionError(f"write error @ {address}={value}: {result}")
