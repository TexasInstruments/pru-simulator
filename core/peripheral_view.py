"""Read-only views of per-core and chip-level peripheral state.

Shared by the dashboard (ui/server.py) and the MCP server so both show the
same data.
"""

from perif.gpcfg import MUX_SD
from xfr.crc_accelerator import CRCAccelerator
from xfr.xfr_bus import SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD


def read_mac(core) -> dict:
    """Return MAC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(0)
    if acc is None:
        return {"mode": False, "acc_carry": False}
    return {"mode": acc.mac_mode, "acc_carry": acc.acc_carry}


def read_crc(core) -> dict:
    """Return CRC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(CRCAccelerator.DEVICE_ID)
    if acc is None:
        return {"mode": "—", "value": "—"}
    if acc.crc32_mode:
        mode = "CRC32"
        value = f"{acc.crc_reg:08X}"
    else:
        mode = "CRC16-MOD" if acc.mod_en else "CRC16"
        value = f"{acc.crc_reg:04X}"
    return {"mode": mode, "value": value}


def read_spad(sim_obj) -> dict:
    """Return scratchpad contents as hex words per bank.

    Banks 0/1/2 hold R0–R29 (30 words); IPC holds R2–R9 (8 words).  A bank
    whose device id is not a broadside id on the target (IPC on ICSS-M) is
    left out, since firmware cannot reach it.
    """
    result = {}
    for device_id, key, count in [
        (SPAD_BANK0, "bank0", 30),
        (SPAD_BANK1, "bank1", 30),
        (SPAD_BANK2, "bank2", 30),
        (IPC_SPAD,   "ipc",   8),
    ]:
        if device_id not in sim_obj.capabilities.broadside_ids:
            continue
        raw = sim_obj.xfr._pads[device_id].data
        result[key] = [
            f"0x{int.from_bytes(raw[i*4:i*4+4], 'little'):08X}"
            for i in range(count)
        ]
    return result


def io_mode(sim_obj, core, sd_data=None, perif_data=None, mux_sel=None) -> str:
    """Which IO view owns the pads: "perif", "sd" or "gpio".

    The SD view switches on the GPCFG mux select (same as Peripheral mode) so
    the IO window shows Sigma-Delta as soon as the mode is configured, not only
    once firmware asserts sd_en (R30 bit 25).
    """
    if sd_data is None:
        sd_data = sim_obj.sd_state(core)
    if perif_data is None:
        perif_data = sim_obj.perif_state(core)
    if mux_sel is None:
        mux_sel = sim_obj.gpcfg_state(core)["mux_sel"]
    if bool(perif_data and perif_data.get("enabled")):
        return "perif"
    if mux_sel == MUX_SD or bool(sd_data and sd_data.get("sd_en")):
        return "sd"
    return "gpio"
