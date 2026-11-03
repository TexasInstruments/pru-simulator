"""Silicon capabilities, distinct from which features the simulator implements.

Broadside IDs without a ``0x`` prefix in the TRM tables are decimal.
AM261x/AM263x use V4 ISA too; ICSSM differs in integrated hardware, not ISA.
See docs/device_capabilities.md for sources and modelling limits.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceCapabilities:
    core_version: str
    broadside_ids: frozenset[int]
    rtu: bool
    tx_pru: bool
    icssg_pwm: bool  # ICSSG PWM module; gates Simulator.pwm


# SPRUJB6B Table 7-35 (ICSSM). AM263x equivalence: TI hardware owners, #32.
_ICSSM_IDS = frozenset({0, 1, 10, 11, 12, 20, 21, 40, 0x60, 0x61, 0x62, 0x63})
# SPRUIM2J Table 6-60 (ICSSG). 0x54 is the fourth PSI receive thread, not in
# Table 6-60 (which lists 0x50-0x53): Table 6-108 (XFR STATUS) has four
# rx_ready/tx_ready bits and the XFRDMA text says XID N serves the N-th thread;
# TI icss_xfer_defines.inc and silicon notes in xfrdma_accelerator agree.
# This is a subsystem union, not a promise that every ID exists on every core.
_ICSSG_IDS = _ICSSM_IDS | frozenset({
    2, 8, 9, 15, *range(30, 36), 38, 39, 47, 48, 49, 22,
    *range(0x50, 0x55), 0x58, 0x59, 0x64,
    0x70, 0x71, 0x72, 0x90, 0xA0, 0xA1, 0xA2, 0xF0,
})
# icssg_pwm=False on AM261x: SPRUJB6B 7.3.1.2 lists the integrated PWM module
# as not supported.
_ICSSG = DeviceCapabilities("V4", _ICSSG_IDS, True, True, True)
_ICSSM = DeviceCapabilities("V4", _ICSSM_IDS, False, False, False)
_TARGETS = {"am243x": _ICSSG, "am64x": _ICSSG,
            "am261x": _ICSSM, "am263x": _ICSSM}


def get_device_capabilities(target: str = "am243x") -> DeviceCapabilities:
    """Query an immutable target profile; accept config case and inline comments.

    Unknown targets fail rather than silently enabling another device's widgets.
    TX_PRU presence describes silicon; TX_PRU simulation is not implemented.
    """
    key = target.split("#", 1)[0].strip().lower()
    try:
        return _TARGETS[key]
    except KeyError:
        raise ValueError(
            f"Unknown device target {target!r}; "
            "expected one of AM243x, AM64x, AM261x, AM263x"
        ) from None
