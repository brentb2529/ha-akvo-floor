"""AKVO Modbus TCP client + safety state machine (thin, async).

This module is intentionally small and self-contained so the SAFETY logic can be
unit-tested directly against the fake server without spinning up Home Assistant.

SAFETY MODEL
------------
The AKVO PLC is the sole safety authority. This client:
  * NEVER writes HR10 bit0 (System Reset) — there is no code path that can.
  * NEVER sets more than ONE HR10 config bit at a time.
  * Only issues a move-to-config REQUEST when HR0 bit15 "ready for external" is
    set AND there is no fault, no e-stop and comm is good.
  * Maintains the HR11 watchdog mirror (echo HR0 bit13) while a command is active.
  * ABORTS (clears HR10, stops) on ANY fault / e-stop / bad comm / bit15 drop /
    watchdog timeout.

REGISTER-MAP CONFIGURABILITY
-----------------------------
All register addresses and bit positions are carried in a :class:`RegisterMap`
(see :mod:`register_map`). The default map matches the AKVO example document
``725-XXXXX``. For a real installation the project-specific ``725-<project>``
map must be reconciled and injected via ``RegisterMap.from_config(entry.data)``.
The decode and command functions accept the map explicitly so they remain
independently testable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import time

from .const import WATCHDOG_SERVICE_INTERVAL, WATCHDOG_TIMEOUT
from .register_map import RegisterMap

_LOGGER = logging.getLogger(__name__)

# Default map (example 725-XXXXX). Used when no map is provided explicitly so
# existing tests that pass no map continue to work.
_DEFAULT_MAP = RegisterMap()


class AkvoError(Exception):
    """Base AKVO error."""


class AkvoConnectionError(AkvoError):
    """Raised when the Modbus transport fails."""


class AkvoCommandRejected(AkvoError):
    """Raised when a preset request is refused by the safety pre-check."""


def _signed16(value: int) -> int:
    """Interpret a 16-bit register as a signed int16."""
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _bit(word: int, bit: int) -> bool:
    return bool((word >> bit) & 1)


@dataclass
class AkvoState:
    """Decoded snapshot of the status/fault/position/current registers."""

    raw: list[int] = field(default_factory=list)

    # Status Word 1 (HR0 by default)
    system_ready: bool = False
    system_fault: bool = False
    control_key_on: bool = False
    estop_indoor: bool = False
    estop_outdoor: bool = False
    bad_modbus_comm: bool = False
    ready_for_external: bool = False
    watchdog_from_akvo: bool = False
    # Status Word 2 (HR1 by default)
    floors_moving: bool = False
    not_predefined_config: bool = False
    active_configuration: int | None = None  # 1..max_config or None
    # Decoded fault maps (bit name -> bool)
    main_faults: dict[str, bool] = field(default_factory=dict)
    baja_faults: dict[str, bool] = field(default_factory=dict)
    topplate_faults: dict[str, bool] = field(default_factory=dict)
    # Scaled position and current values (None when register unavailable)
    main_position_m: float | None = None
    baja_position_m: float | None = None
    main_current_a: float | None = None
    baja_current_a: float | None = None

    @property
    def estop_active(self) -> bool:
        return self.estop_indoor or self.estop_outdoor

    @property
    def any_fault(self) -> bool:
        """True if any decoded fault word is non-zero."""
        return (
            any(self.main_faults.values())
            or any(self.baja_faults.values())
            or any(self.topplate_faults.values())
        )

    @property
    def safe_for_command(self) -> bool:
        """Pre-condition gate for issuing a preset request."""
        return (
            self.ready_for_external
            and not self.any_fault
            and not self.system_fault
            and not self.estop_active
            and not self.bad_modbus_comm
        )


def decode_state(raw: list[int], reg_map: RegisterMap | None = None) -> AkvoState:
    """Decode a register block into an :class:`AkvoState`.

    *raw* is a list of register values starting at *reg_map.read_start*
    (i.e. ``raw[0]`` corresponds to ``reg_map.hr_status_1``).  When
    *reg_map* is ``None`` the default map (example 725-XXXXX) is used.

    Raises :class:`AkvoConnectionError` on a short read (fewer registers than
    the map requires).  Unknown/reserved bit positions are tolerated — only the
    explicitly named bits are decoded, so an unexpected PLC firmware revision
    that sets additional bits does not crash the integration.
    """
    if reg_map is None:
        reg_map = _DEFAULT_MAP

    if len(raw) < reg_map.read_count:
        raise AkvoConnectionError(
            f"short read: got {len(raw)} registers, need {reg_map.read_count}"
        )

    def _r(hr_addr: int) -> int:
        """Return the raw register value for *hr_addr*."""
        return raw[reg_map.reg_index(hr_addr)]

    s1 = _r(reg_map.hr_status_1)
    s2 = _r(reg_map.hr_status_2)
    fmain = _r(reg_map.hr_fault_main)
    fbaja = _r(reg_map.hr_fault_baja)
    ftop = _r(reg_map.hr_fault_topplate)

    # Active configuration: first config N whose achieved bit is set.
    active: int | None = None
    for cfg in range(reg_map.min_config, reg_map.max_config + 1):
        if _bit(s2, reg_map.s2_config_achieved_base + cfg - 1):
            active = cfg
            break

    return AkvoState(
        raw=list(raw),
        system_ready=_bit(s1, reg_map.s1_system_ready),
        system_fault=_bit(s1, reg_map.s1_system_fault),
        control_key_on=_bit(s1, reg_map.s1_control_key_on),
        estop_indoor=_bit(s1, reg_map.s1_estop_indoor),
        estop_outdoor=_bit(s1, reg_map.s1_estop_outdoor),
        bad_modbus_comm=_bit(s1, reg_map.s1_bad_modbus_comm),
        ready_for_external=_bit(s1, reg_map.s1_ready_for_external),
        watchdog_from_akvo=_bit(s1, reg_map.s1_watchdog_from_akvo),
        floors_moving=_bit(s2, reg_map.s2_floors_moving),
        not_predefined_config=_bit(s2, reg_map.s2_not_predefined_config),
        active_configuration=active,
        main_faults={
            name: _bit(fmain, b) for b, name in reg_map.drive_fault_bits.items()
        },
        baja_faults={
            name: _bit(fbaja, b) for b, name in reg_map.drive_fault_bits.items()
        },
        topplate_faults={
            f"topplate_{i + 1}": _bit(ftop, i)
            for i in range(reg_map.topplate_fault_count)
        },
        main_position_m=_signed16(_r(reg_map.hr_pos_main)) * reg_map.position_scale,
        baja_position_m=_signed16(_r(reg_map.hr_pos_baja)) * reg_map.position_scale,
        main_current_a=_signed16(_r(reg_map.hr_current_main)) * reg_map.current_scale,
        baja_current_a=_signed16(_r(reg_map.hr_current_baja)) * reg_map.current_scale,
    )


class AkvoClient:
    """Async Modbus TCP transport + safety state machine.

    The transport is injected via ``read_fn`` / ``write_fn`` so the same state
    machine runs against pymodbus in production and against the in-process fake
    in tests. Both are plain async callables — no event-loop blocking.

    ``reg_map`` carries the project-specific register layout. It defaults to the
    example map (725-XXXXX) when not provided so existing tests need no change.
    """

    def __init__(
        self,
        read_fn: Callable[[int, int], Awaitable[list[int]]],
        write_fn: Callable[[int, int], Awaitable[None]],
        *,
        reg_map: RegisterMap | None = None,
        watchdog_timeout: float = WATCHDOG_TIMEOUT,
        service_interval: float = WATCHDOG_SERVICE_INTERVAL,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._read = read_fn
        self._write = write_fn
        self._map = reg_map if reg_map is not None else _DEFAULT_MAP
        self._watchdog_timeout = watchdog_timeout
        self._service_interval = service_interval
        self._now = time_fn
        self._command_lock = asyncio.Lock()
        self.last_abort_reason: str | None = None

    async def async_read_state(self) -> AkvoState:
        """Read the status/fault/position/current block and decode it."""
        raw = await self._read(self._map.read_start, self._map.read_count)
        return decode_state(raw, self._map)

    async def async_service_watchdog(self, state: AkvoState) -> None:
        """Mirror HR0 bit13 into HR11 bit0 once (idle keep-alive).

        AKVO expects the external system to keep mirroring its watchdog even
        when no command is active; otherwise it declares comm bad after 5s and
        refuses commands. The coordinator calls this every poll. It is a no-op
        while a command holds the lock (the command loop mirrors on its own and
        we must not interleave a second writer).
        """
        if self._command_lock.locked():
            return
        mirror = 1 << self._map.wd_external if state.watchdog_from_akvo else 0
        await self._write(self._map.hr_watchdog, mirror)

    @staticmethod
    def _config_command_word(
        config: int, reg_map: RegisterMap | None = None
    ) -> int:
        """Build an HR10 value with EXACTLY ONE move bit set.

        bit0 (System Reset) is never set here by construction.
        *reg_map* defaults to the example map when not supplied (used in tests).
        """
        m = reg_map if reg_map is not None else _DEFAULT_MAP
        if not m.min_config <= config <= m.max_config:
            raise AkvoCommandRejected(
                f"config {config} out of range {m.min_config}..{m.max_config}"
            )
        return 1 << (m.cmd_move_config_base + config - 1)

    async def _clear_command(self) -> None:
        """Clear the command register (no reset bit, no config bits)."""
        await self._write(self._map.hr_command, 0)

    async def async_request_configuration(
        self,
        config: int,
        *,
        on_state: Callable[[AkvoState], None] | None = None,
    ) -> AkvoState:
        """Run the full safety state machine for a move-to-config REQUEST.

        Returns the final state once the config is achieved. Raises
        :class:`AkvoCommandRejected` if the pre-check fails or the move aborts.
        Only ONE request runs at a time (guarded by a lock).
        """
        if config < self._map.min_config or config > self._map.max_config:
            raise AkvoCommandRejected(
                f"config {config} out of range "
                f"{self._map.min_config}..{self._map.max_config}"
            )

        async with self._command_lock:
            self.last_abort_reason = None

            # (a) Pre-check: refuse unless the PLC says it is ready and clean.
            state = await self.async_read_state()
            if not state.safe_for_command:
                reason = self._unsafe_reason(state)
                _LOGGER.warning(
                    "AKVO preset request for config %s REJECTED: %s", config, reason
                )
                raise AkvoCommandRejected(reason)

            # (b) Issue exactly one move bit. Never bit0.
            command_word = self._config_command_word(config, self._map)
            _LOGGER.info(
                "AKVO issuing PLC-mediated request: move to configuration #%s", config
            )
            await self._write(self._map.hr_command, command_word)

            try:
                return await self._run_until_achieved(config, on_state)
            except AkvoCommandRejected:
                # (e) Abort path: always clear the command bit on the way out.
                await self._safe_clear("abort")
                raise
            except Exception:
                await self._safe_clear("unexpected error")
                raise

    async def _run_until_achieved(
        self,
        config: int,
        on_state: Callable[[AkvoState], None] | None,
    ) -> AkvoState:
        """Service the watchdog and hold the command until achieved or abort."""
        start = self._now()  # noqa: F841 — kept for potential future timeout
        # Track the AKVO watchdog (HR0 bit13). A healthy PLC keeps toggling it.
        # If it stops changing for longer than the timeout, the PLC is hung /
        # comm is effectively dead -> abort. This is what bounds the loop when
        # the move never completes.
        last_akvo_toggle = self._now()
        prev_akvo_bit: bool | None = None
        # bit15 legitimately drops WHILE the PLC is executing the move and is
        # restored on completion. So a bit15 drop is only an abort signal once
        # we have observed motion begin. Track whether motion has started.
        motion_started = False

        while True:
            state = await self.async_read_state()
            if on_state is not None:
                on_state(state)

            if state.floors_moving:
                motion_started = True

            # (e) Abort conditions — any one trips an immediate stop.
            abort = self._abort_reason(state, motion_started)
            if abort is not None:
                self.last_abort_reason = abort
                _LOGGER.error("AKVO ABORT during config #%s: %s", config, abort)
                raise AkvoCommandRejected(f"aborted: {abort}")

            # (c) Mirror AKVO HR0 bit13 -> HR11 bit0, continuously.
            mirror_value = (
                1 << self._map.wd_external if state.watchdog_from_akvo else 0
            )
            await self._write(self._map.hr_watchdog, mirror_value)

            # Watchdog supervision: detect a hung PLC by a stalled bit13.
            if prev_akvo_bit is None or state.watchdog_from_akvo != prev_akvo_bit:
                last_akvo_toggle = self._now()
                prev_akvo_bit = state.watchdog_from_akvo

            # (d) Done when the matching "configuration achieved" bit sets and
            # motion has stopped.
            if state.active_configuration == config and not state.floors_moving:
                await self._safe_clear("achieved")
                _LOGGER.info("AKVO configuration #%s achieved", config)
                return state

            if self._now() - last_akvo_toggle > self._watchdog_timeout:
                self.last_abort_reason = "watchdog timeout"
                await self._safe_clear("watchdog timeout")
                raise AkvoCommandRejected("aborted: watchdog timeout")

            await asyncio.sleep(self._service_interval)

    @staticmethod
    def _abort_reason(state: AkvoState, motion_started: bool) -> str | None:
        """Return a reason string if the move must abort, else None.

        ``motion_started`` is True once the floors-moving bit has been observed
        set. ready-for-external (HR0 bit15) legitimately drops while the PLC
        executes the move; a drop is only treated as the PLC withdrawing
        permission once motion has finished (moving cleared) without the config
        being achieved.
        """
        if state.estop_active:
            return "e-stop active"
        if state.system_fault:
            return "system fault"
        if state.any_fault:
            return "drive/top-plate fault"
        if state.bad_modbus_comm:
            return "bad modbus comm (AKVO-detected)"
        if (
            motion_started
            and not state.floors_moving
            and not state.ready_for_external
            and state.active_configuration is None
        ):
            return "ready-for-external (HR0 bit15) dropped before completion"
        return None

    @staticmethod
    def _unsafe_reason(state: AkvoState) -> str:
        if not state.ready_for_external:
            return "not ready for external commands (HR0 bit15 clear)"
        if state.estop_active:
            return "e-stop active"
        if state.system_fault:
            return "system fault"
        if state.any_fault:
            return "drive/top-plate fault present"
        if state.bad_modbus_comm:
            return "bad modbus comm (AKVO-detected)"
        return "not ready"

    async def _safe_clear(self, why: str) -> None:
        try:
            await self._clear_command()
        except Exception:  # noqa: BLE001 - clearing must never raise upward
            _LOGGER.exception("AKVO failed to clear command during %s", why)
