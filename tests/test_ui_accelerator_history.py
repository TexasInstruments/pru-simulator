"""Shared history must replay the CRC engine brought in by main."""
import pytest
from simulator import Simulator


def test_crc_instruction_replays_after_restoring_shared_history(monkeypatch):
    from ui import server
    sim = Simulator()
    monkeypatch.setattr(server, "sim", sim)
    assert sim.load("pru0", "ldi r29.b0, 1\nxout 1, &r29.b0, 1\nhalt") == []
    sim.step("pru0")
    saved = server._snapshot("pru0")
    crc = sim.cores["pru0"].accelerators[1]

    sim.step("pru0")
    assert crc.crc_reg == 0x8303
    server._restore("pru0", saved)
    assert crc.crc_reg == 0
    sim.step("pru0")
    assert crc.crc_reg == 0x8303


@pytest.mark.parametrize("control,crc32,modified", [
    (0, False, False), (4, False, True), (1, True, False),
])
def test_crc_mode_seed_and_destructive_read_restore_together(monkeypatch, control, crc32, modified):
    from ui import server
    sim = Simulator()
    monkeypatch.setattr(server, "sim", sim)
    assert sim.load("pru0", "xin 1, &r29, 4\nhalt") == []
    crc = sim.cores["pru0"].accelerators[1]
    crc.xout(25, bytes([control]))
    crc.xout(28, (0x1234).to_bytes(4, "little"))
    crc.xout(29, b"\x01")
    saved = server._snapshot("pru0")
    sim.step("pru0")
    first_read = sim.registers("pru0")[29]
    crc.xout(25, bytes([5 if control == 0 else 0]))
    crc.xout(28, (0xABCD).to_bytes(4, "little"))

    server._restore("pru0", saved)
    assert (crc.crc32_mode, crc.mod_en, crc.seed) == (crc32, modified, 0x1234)
    sim.step("pru0")
    assert sim.registers("pru0")[29] == first_read
    assert crc.crc_reg == 0x1234
