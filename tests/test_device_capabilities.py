"""Device-negative vectors: SPRUJB6B Table 7-35 and silicon in upstream #32."""

import logging
from pathlib import Path

import pytest

from core.pru_core import UnsupportedXFRError
from simulator import Simulator


@pytest.mark.parametrize("target,icssg", [
    ("am243x", True), ("am64x", True), ("am261x", False), ("am263x", False),
])
def test_capability_query(target, icssg):
    from core.device_capabilities import get_device_capabilities

    caps = get_device_capabilities(target.upper() + " # config comment")
    assert caps == get_device_capabilities(target)
    assert caps.core_version == "V4"  # ICSSM hardware owners, upstream #32
    assert caps.rtu is icssg
    assert caps.tx_pru is icssg
    assert caps.icssg_pwm is icssg
    icssm_ids = {0, 1, 10, 11, 12, 20, 21, 40, 0x60, 0x61, 0x62, 0x63}
    if icssg:
        # Typed from SPRUIM2J Table 6-60 (decimal IDs unprefixed) plus 0x54;
        # deliberately not derived from the implementation's own constants.
        table_6_60_extra = {
            2, 8, 9, 15, 22, 30, 31, 32, 33, 34, 35, 38, 39, 47, 48, 49,
            0x50, 0x51, 0x52, 0x53, 0x54, 0x58, 0x59, 0x64,
            0x70, 0x71, 0x72, 0x90, 0xA0, 0xA1, 0xA2, 0xF0,
        }
        assert caps.broadside_ids == icssm_ids | table_6_60_extra
    else:
        assert caps.broadside_ids == icssm_ids


def test_unknown_capability_target_is_rejected():
    from core.device_capabilities import get_device_capabilities

    with pytest.raises(ValueError, match="Unknown device target"):
        get_device_capabilities("am999x")


@pytest.mark.parametrize("target", ["am261x", "am263x"])
def test_icssm_has_only_prus_and_listed_accelerators(sim_config, target):
    sim = Simulator(sim_config(target=target))
    assert set(sim.cores) == {"pru0", "pru1"}
    for core in sim.cores.values():
        assert set(core.accelerators) == {0, 1, 0x60, 0x61, 0x62, 0x63}
        assert core.task_ring is None


@pytest.mark.parametrize("target", ["am243x", "am64x"])
def test_icssg_keeps_existing_cores_and_widgets(sim_config, target):
    sim = Simulator(sim_config(target=target))
    assert set(sim.cores) == {"pru0", "pru1", "rtu0", "rtu1"}
    for name, core in sim.cores.items():
        assert {0xA0, 0xA1, 0xA2, 0x50, 0x51, 0x52, 0x53, 0x54} <= core.accelerators.keys()
        assert (core.task_ring is not None) == name.startswith("rtu")


def _preloaded_core(sim_config, target, instruction):
    sim = Simulator(sim_config(target=target))
    core = sim.cores["pru0"]
    before = [0x12340000 + i for i in range(32)]
    core.registers.regs[:] = before
    assert core.load_asm(instruction + "\nhalt") == []
    return core, before


def test_am261x_measured_bswap_4_8_zeroes_only_transfer(sim_config, caplog):
    # LP-AM261 ICSSM0 PRU0: XIN 161, &r2, 16 -> r2..r5=0,
    # r6..r9 untouched. No BSWAP permutation or 32-byte length requirement.
    core, before = _preloaded_core(sim_config, "am261x", "xin 161, &r2, 16")
    with caplog.at_level(logging.WARNING, logger="core.pru_core"):
        core.step()
    assert core.registers.regs == before[:2] + [0] * 4 + before[6:]
    assert core.pc == 1
    assert core.counters.cycles == 1
    assert core.unsupported_xfr[161]["opcodes"] == ["XIN"]
    assert "absent on am261x" in caplog.text
    assert "silicon zero-fill observed only" in caplog.text


@pytest.mark.parametrize("target", ["am243x", "am64x"])
def test_icssg_bswap_4_8_still_swaps_both_ranges(sim_config, target):
    core, before = _preloaded_core(sim_config, target, "xin 161, &r2, 32")
    core.strict_unsupported_xfr = True
    core.step()
    assert core.registers.regs == before[:2] + before[6:10] + before[2:6] + before[10:]
    assert not core.unsupported_xfr


@pytest.mark.parametrize("target", ["am261x", "am263x"])
@pytest.mark.parametrize("xid", [15, 30, 0x50, 0x54, 0x70, 0xA0, 0xA1, 0xA2])
@pytest.mark.parametrize("opcode", ["xin", "xout", "xchg"])
def test_absent_ids_use_diagnostic_fallback(sim_config, caplog, target, xid, opcode):
    core, before = _preloaded_core(sim_config, target, f"{opcode} {xid}, &r2.b1, 6")
    # IPC SPAD exists on the generic bus but is absent on ICSSM. Do not
    # dispatch to it even if a caller seeded this shared simulator bus.
    core.xfr.xout(15, 0, b"\xCC" * 32)
    bus_before = core.xfr.snapshot()
    with caplog.at_level(logging.WARNING, logger="core.pru_core"):
        core.step()
    expected = bytearray(b"".join(x.to_bytes(4, "little") for x in before))
    if opcode != "xout":
        expected[9:15] = bytes(6)
    assert core.registers.regs == [int.from_bytes(expected[i:i+4], "little")
                                   for i in range(0, 128, 4)]
    assert core.xfr.snapshot() == bus_before
    assert core.unsupported_xfr[xid]["count"] == 1
    assert core.unsupported_xfr[xid]["opcodes"] == [opcode.upper()]
    assert f"absent on {target}" in caplog.text
    assert "unverified hardware behaviour" in caplog.text


@pytest.mark.parametrize("target", ["am261x", "am263x"])
@pytest.mark.parametrize("xid", [15, 0x50, 0xA0, 0xA1, 0xA2])
@pytest.mark.parametrize("opcode", ["xin", "xout", "xchg"])
def test_strict_absent_xfr_raises_before_transfer(sim_config, target, xid, opcode):
    core, before = _preloaded_core(sim_config, target, f"{opcode} {xid}, &r2, 16")
    core.strict_unsupported_xfr = True
    bus_before = core.xfr.snapshot()
    with pytest.raises(UnsupportedXFRError, match=f"absent on {target}"):
        core.step()
    assert core.registers.regs == before
    assert core.xfr.snapshot() == bus_before
    assert core.pc == 0
    assert core.unsupported_xfr[xid]["count"] == 1


@pytest.mark.parametrize("target", ["am261x", "am263x"])
def test_icssm_retains_shared_spad(sim_config, target):
    sim = Simulator(sim_config(target=target))
    sim.cores["pru0"].registers.regs[2] = 0x12345678
    assert sim.load("pru0", "xout 10, &r2, 4") == []
    assert sim.load("pru1", "xin 10, &r2, 4") == []
    for name in ("pru0", "pru1"):
        sim.cores[name].strict_unsupported_xfr = True
        sim.step(name)
    assert sim.registers("pru1")[2] == 0x12345678


@pytest.mark.parametrize("target", ["am261x", "am263x"])
@pytest.mark.parametrize("filename,expected", [
    ("mac_example.asm", {26: 1250, 27: 0}),  # multiply-only section: 50 * 25
    ("crc_example.asm", {10: 0xC9D7, 11: 0x383AD48D}),  # silicon-verified CRCs
    ("crc_bitswap_example.asm", {10: 0x1C5C2BB1, 11: 0xB12B5C1C, 12: 0x383AD48D,
                                 13: 0x1C5C2BB1, 14: 0xB12B5C1C, 15: 0x383AD48D}),
    ("xfr_test.asm", {10: 0xCAFE}),  # source header's scratchpad round trip
])
def test_icssm_accelerator_examples(sim_config, target, filename, expected):
    core = Simulator(sim_config(target=target)).cores["pru0"]
    source = Path(__file__).resolve().parents[1] / "source" / filename
    asm = source.read_text(encoding="utf-8")
    if filename == "mac_example.asm":
        # Exercise the latency-correct multiply section. The later dot-product
        # section already disagrees with its header on the base revision.
        asm = asm.split("mac_demo:", 1)[0] + "\nhalt"
    assert core.load_asm(asm) == []
    core.run(2000)
    assert core.halted
    assert core.fault is None
    assert {reg: core.registers.regs[reg] for reg in expected} == expected
    # xfr_test also writes IPC SPAD (15): now discarded on ICSSM, while
    # banks 10..12 and the expected R10 result continue to work.
    assert set(core.unsupported_xfr) == ({15} if filename == "xfr_test.asm" else set())
