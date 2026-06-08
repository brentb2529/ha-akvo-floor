#!/usr/bin/env python3
"""Fake AKVO Modbus TCP server (EXAMPLE map 725-XXXXX).

SIMULATED DEVICE ONLY. This fakes the AKVO PLC so the REAL custom_components/akvo
client can be developed and tested without any live hardware. It implements:

  * All read registers HR0..HR9 (status/fault words + signed positions/currents).
  * HR10 (command) and HR11 (external watchdog) writes.
  * A toggling AKVO watchdog (HR0 bit13) that requires the external system to
    mirror it into HR11 bit0 within 5s; otherwise it sets HR0 bit14 (bad comm)
    and STOPS any running configuration.
  * A move simulation: on a valid HR10 config bit while ready, it clears HR0
    bit15 (ready-for-external), sets HR1 bit0 (moving), ramps HR6/HR7 toward the
    target, then sets the matching HR1 "configuration achieved" bit, clears
    moving, and restores bit15.
  * Fault / e-stop injection for abort testing (via the control API below).

CONTROL API (for tests / dev): the module exposes a :class:`FakeAkvoState`
shared object. Tests drive it in-process; when run as a script it also listens
on a small JSON TCP control port so the dev container demo can inject faults.

This server may be reused by the coordinator for dev wiring (host.docker.internal).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

from pymodbus.datastore import (
    ModbusServerContext,
    ModbusSlaveContext,
    ModbusSequentialDataBlock,
)
from pymodbus.server import StartAsyncTcpServer

_LOGGER = logging.getLogger("fake-akvo")

# Bit layout (mirror of custom_components/akvo/const.py; kept independent so the
# fake has no dependency on the integration under test).
S1_SYSTEM_READY = 0
S1_SYSTEM_FAULT = 1
S1_ESTOP_INDOOR = 3
S1_ESTOP_OUTDOOR = 4
S1_WATCHDOG_FROM_AKVO = 13
S1_BAD_MODBUS_COMM = 14
S1_READY_FOR_EXTERNAL = 15

S2_FLOORS_MOVING = 0
S2_CONFIG_ACHIEVED_BASE = 2

CMD_MOVE_BASE = 1
WD_EXTERNAL = 0

# Target main/baja positions (metres x1000) for each config #1..#8.
CONFIG_TARGETS = {
    1: (0, 0),  # deck level
    2: (-200, -200),  # raised above deck
    3: (1500, 800),  # pool depth
    4: (500, 250),
    5: (1000, 500),
    6: (1200, 600),
    7: (1800, 900),
    8: (2000, 1000),
}


def _set_bit(word: int, bit: int, on: bool) -> int:
    if on:
        return word | (1 << bit)
    return word & ~(1 << bit)


def _get_bit(word: int, bit: int) -> bool:
    return bool((word >> bit) & 1)


def _to_u16(value: int) -> int:
    return value & 0xFFFF


class FakeAkvoState:
    """In-memory AKVO register model + simulation engine.

    Tests construct this directly and call :meth:`step` to advance simulated
    time deterministically. The Modbus server reads/writes through the same
    object.
    """

    def __init__(self, *, watchdog_grace: float = 5.0) -> None:
        # HR0..HR11 (we store all 12 even though HR10/11 are write targets).
        self.regs: list[int] = [0] * 12
        self._watchdog_grace = watchdog_grace
        self._last_good_mirror = time.monotonic()
        self._last_wd_toggle = time.monotonic()
        self._move_config: int | None = None
        # Initial healthy state: system ready + ready-for-external set.
        self.regs[0] = _set_bit(self.regs[0], S1_SYSTEM_READY, True)
        self.regs[0] = _set_bit(self.regs[0], S1_READY_FOR_EXTERNAL, True)

    # --- register helpers ---------------------------------------------------
    @property
    def hr0(self) -> int:
        return self.regs[0]

    @hr0.setter
    def hr0(self, v: int) -> None:
        self.regs[0] = _to_u16(v)

    @property
    def hr1(self) -> int:
        return self.regs[1]

    @hr1.setter
    def hr1(self, v: int) -> None:
        self.regs[1] = _to_u16(v)

    # --- fault / e-stop injection (for abort tests) -------------------------
    def inject_system_fault(self, on: bool = True) -> None:
        self.hr0 = _set_bit(self.hr0, S1_SYSTEM_FAULT, on)

    def inject_estop(self, *, indoor: bool = True) -> None:
        bit = S1_ESTOP_INDOOR if indoor else S1_ESTOP_OUTDOOR
        self.hr0 = _set_bit(self.hr0, bit, True)

    def inject_main_fault(self, value: int = 0x0001) -> None:
        self.regs[2] = _to_u16(value)

    def clear_faults(self) -> None:
        self.regs[2] = self.regs[3] = self.regs[4] = 0
        self.hr0 = _set_bit(self.hr0, S1_SYSTEM_FAULT, False)
        self.hr0 = _set_bit(self.hr0, S1_ESTOP_INDOOR, False)
        self.hr0 = _set_bit(self.hr0, S1_ESTOP_OUTDOOR, False)

    def force_bad_comm(self) -> None:
        self.hr0 = _set_bit(self.hr0, S1_BAD_MODBUS_COMM, True)

    def drop_ready_for_external(self) -> None:
        self.hr0 = _set_bit(self.hr0, S1_READY_FOR_EXTERNAL, False)

    # --- write handling -----------------------------------------------------
    def write_register(self, address: int, value: int) -> None:
        self.regs[address] = _to_u16(value)
        if address == 11:  # external watchdog mirror
            self._handle_watchdog_mirror()
        if address == 10:  # command word
            self._handle_command()

    def _handle_watchdog_mirror(self) -> None:
        akvo_bit = _get_bit(self.hr0, S1_WATCHDOG_FROM_AKVO)
        ext_bit = _get_bit(self.regs[11], WD_EXTERNAL)
        if ext_bit == akvo_bit:
            self._last_good_mirror = time.monotonic()
            # A correct mirror clears a previously-set bad-comm flag.
            self.hr0 = _set_bit(self.hr0, S1_BAD_MODBUS_COMM, False)

    def _handle_command(self) -> None:
        cmd = self.regs[10]
        # bit0 is System Reset — the real client never sets it; if a buggy
        # client did, we simply ignore it here (the fake does not reset faults
        # on external request; AKVO HMI only).
        for cfg in range(1, 9):
            if _get_bit(cmd, CMD_MOVE_BASE + cfg - 1):
                self._start_move(cfg)
                return
        # No move bit set -> command cleared.
        self._move_config = None

    def _start_move(self, cfg: int) -> None:
        # Only honor the request if currently ready and clean (the PLC's own
        # interlock — independent of the client's pre-check).
        if not _get_bit(self.hr0, S1_READY_FOR_EXTERNAL):
            return
        if self._unsafe():
            return
        self._move_config = cfg
        self.hr1 = _set_bit(self.hr1, S2_FLOORS_MOVING, True)
        # Clear any previously-achieved config bit.
        for c in range(1, 9):
            self.hr1 = _set_bit(self.hr1, S2_CONFIG_ACHIEVED_BASE + c - 1, False)
        # PLC withdraws external-ready while it drives the move.
        self.hr0 = _set_bit(self.hr0, S1_READY_FOR_EXTERNAL, False)

    def _unsafe(self) -> bool:
        return (
            _get_bit(self.hr0, S1_SYSTEM_FAULT)
            or _get_bit(self.hr0, S1_ESTOP_INDOOR)
            or _get_bit(self.hr0, S1_ESTOP_OUTDOOR)
            or _get_bit(self.hr0, S1_BAD_MODBUS_COMM)
            or self.regs[2] != 0
            or self.regs[3] != 0
            or self.regs[4] != 0
        )

    # --- simulation tick ----------------------------------------------------
    def step(self, *, ramp: int = 600) -> None:
        """Advance the simulation by one tick.

        ``ramp`` is metres x1000 moved per tick (large default so tests finish
        in a few ticks).
        """
        now = time.monotonic()

        # AKVO toggles its watchdog roughly twice a second.
        if now - self._last_wd_toggle >= 0.5:
            self.hr0 ^= 1 << S1_WATCHDOG_FROM_AKVO
            self._last_wd_toggle = now

        # Watchdog supervision: too long without a correct mirror -> bad comm +
        # stop any running configuration.
        if now - self._last_good_mirror > self._watchdog_grace:
            self.hr0 = _set_bit(self.hr0, S1_BAD_MODBUS_COMM, True)
            self._abort_move()

        # If a fault/e-stop appeared mid-move, stop.
        if self._move_config is not None and self._unsafe():
            self._abort_move()
            return

        if self._move_config is not None:
            self._advance_move(ramp)

    def _abort_move(self) -> None:
        if self._move_config is None:
            return
        self.hr1 = _set_bit(self.hr1, S2_FLOORS_MOVING, False)
        self._move_config = None
        # Ready-for-external stays cleared until faults clear & operator resets
        # at the HMI; for the fake, restore it only if currently clean.
        if not self._unsafe():
            self.hr0 = _set_bit(self.hr0, S1_READY_FOR_EXTERNAL, True)

    def _advance_move(self, ramp: int) -> None:
        cfg = self._move_config
        assert cfg is not None
        tgt_main, tgt_baja = CONFIG_TARGETS[cfg]
        main = self._signed(self.regs[6])
        baja = self._signed(self.regs[7])
        main = self._ramp(main, tgt_main, ramp)
        baja = self._ramp(baja, tgt_baja, ramp)
        self.regs[6] = _to_u16(main)
        self.regs[7] = _to_u16(baja)
        # Simulate motor current while moving.
        self.regs[8] = _to_u16(450)  # 4.50 A
        self.regs[9] = _to_u16(380)

        if main == tgt_main and baja == tgt_baja:
            # Arrived: set achieved bit, clear moving, restore ready, zero current.
            self.hr1 = _set_bit(self.hr1, S2_FLOORS_MOVING, False)
            self.hr1 = _set_bit(
                self.hr1, S2_CONFIG_ACHIEVED_BASE + cfg - 1, True
            )
            self.hr0 = _set_bit(self.hr0, S1_READY_FOR_EXTERNAL, True)
            self.regs[8] = self.regs[9] = 0
            self._move_config = None

    @staticmethod
    def _signed(v: int) -> int:
        v &= 0xFFFF
        return v - 0x10000 if v & 0x8000 else v

    @staticmethod
    def _ramp(cur: int, tgt: int, step: int) -> int:
        if abs(tgt - cur) <= step:
            return tgt
        return cur + step if tgt > cur else cur - step


# --- pymodbus glue ----------------------------------------------------------
class _SharedDataBlock(ModbusSequentialDataBlock):
    """Datablock that proxies HR0..HR11 to a :class:`FakeAkvoState`."""

    def __init__(self, state: FakeAkvoState) -> None:
        super().__init__(0, [0] * 12)
        self._state = state

    def getValues(self, address: int, count: int = 1) -> list[int]:
        return [self._state.regs[address + i] for i in range(count)]

    def setValues(self, address: int, values: list[int]) -> None:
        for i, v in enumerate(values):
            self._state.write_register(address + i, v)


async def _ticker(state: FakeAkvoState, interval: float) -> None:
    while True:
        state.step()
        await asyncio.sleep(interval)


def build_context(state: FakeAkvoState) -> ModbusServerContext:
    block = _SharedDataBlock(state)
    slave = ModbusSlaveContext(hr=block, zero_mode=True)
    return ModbusServerContext(slaves=slave, single=True)


def start_ticker(state: FakeAkvoState, interval: float = 0.1) -> asyncio.Task[None]:
    """Start the simulation ticker as a cancellable task.

    Callers that need clean teardown (tests) should cancel the returned task.
    """
    return asyncio.create_task(_ticker(state, interval))


async def run_server(host: str, port: int, state: FakeAkvoState) -> None:
    context = build_context(state)
    start_ticker(state)
    _LOGGER.info("Fake AKVO Modbus TCP server listening on %s:%s", host, port)
    await StartAsyncTcpServer(context=context, address=(host, port))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Fake AKVO Modbus TCP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5020)
    args = parser.parse_args()
    state = FakeAkvoState()
    asyncio.run(run_server(args.host, args.port, state))


if __name__ == "__main__":
    main()
