# tests/test_perif_integration.py
"""Phase-2 integration: GPCFG mode select + R30/R31 routing through the core."""
import os
import simulator

_ASM_DIR = os.path.join(os.path.dirname(__file__), "asm")


def _sim():
    return simulator.Simulator()


def test_gpcfg_enables_perif_mode():
    s = _sim()
    assert s._perif["pru0"].enabled is False
    s.gpcfg_write("pru0", 1)          # PRU_GP_MUX_SEL = 1 (peripheral)
    assert s._perif["pru0"].enabled is True
    s.gpcfg_write("pru0", 0)          # back to GP
    assert s._perif["pru0"].enabled is False


def test_gpcfg_via_memory_write():
    """Writing GPCFG0 through the memory bus selects peripheral mode."""
    s = _sim()
    s.memory.write(0x26008, ((1 << 26)).to_bytes(4, "little"))  # mux_sel=1
    assert s._perif["pru0"].enabled is True


def test_r30_byte0_push_and_r31_go():
    s = _sim()
    s.gpcfg_write("pru0", 1)
    # CH0CFG0 @ 0x260E8: tx_frame_size = 8 bits (bits [15:11])
    s.write_perif_register("pru0", 0x260E8, 8 << 11)
    asm = """
        ldi r30.b2, 0        ; channel sel 0, clk mode 0 (byte2 strobe, no push)
        ldi r30.b0, 0xB6     ; push 0xB6 to ch0 TX FIFO (byte0 strobe)
        ldi r0, 0
        ldi r0.w2, 0x0004    ; r0 = 0x00040000 = bit 18 (TX go)
        mov r31, r0          ; issue TX go
    """
    assert s.load("pru0", asm) == []
    s.step("pru0", 2)                 # after the two R30 writes
    ch0 = s.perif_state("pru0")["channels"][0]
    assert ch0["tx_fifo"] == [0xB6]   # byte0 strobe pushed exactly one byte
    s.step("pru0", 3)                 # execute the go sequence
    ch0 = s.perif_state("pru0")["channels"][0]
    assert ch0["busy"] is True
    assert ch0["fsm"] == "TRANSMIT"


def test_r30_full_write_does_not_double_push():
    """A full R30 write strobes all 4 bytes → exactly one FIFO push (byte0)."""
    s = _sim()
    s.gpcfg_write("pru0", 1)
    asm = "ldi r30, 0x0000005A\n"     # full write, byte0=0x5A, channel sel 0
    assert s.load("pru0", asm) == []
    s.step("pru0", 1)
    ch0 = s.perif_state("pru0")["channels"][0]
    assert ch0["tx_fifo"] == [0x5A]


def test_r31_read_returns_perif_status():
    s = _sim()
    s.gpcfg_write("pru0", 1)
    pru = s.cores["pru0"]
    pru.io_port.perif.channels[0].push_tx(0x11)   # FIFO count = 1
    status = pru.io_port.read_r31()
    # TX status byte for ch0: count in bits [4:2] → 1
    assert (status & 0xFF) >> 2 == 1


def test_sd_mode_still_works_when_perif_disabled():
    """With perif disabled, R31 routing falls through to SD (regression)."""
    s = _sim()
    pru = s.cores["pru0"]
    assert pru.io_port.perif.enabled is False
    # sd_en via R30 bit25 still selects SD status path
    pru.io_port.write_r30(1 << 25)
    assert pru.io_port.sd_filter.sd_en is True


def test_demo_asm_transmits_via_stepping():
    """Load the TX demo, step through it, and confirm the core's per-step tick
    runs the TX frame to completion (busy clears, FIFO flushes, line toggled)."""
    s = _sim()
    s.gpcfg_write("pru0", 1)
    s.write_perif_register("pru0", 0x260E8, 8 << 11)   # ch0 tx_frame_size = 8 bits
    with open(os.path.join(_ASM_DIR, "perif_tx_demo.asm")) as f:
        assert s.load("pru0", f.read()) == []
    ch0 = s.cores["pru0"].io_port.perif.channels[0]
    s.step("pru0", 5)                    # through the "mov r31, r0" (TX go)
    assert ch0.busy is True
    s.step("pru0", 30)                   # spin loop advances the ns timeline
    assert ch0.busy is False             # 8-bit frame finished
    assert ch0.tx_fifo == []             # flushed after frame
    assert len(ch0.tx_transitions) > 1   # serial line was driven


def test_hard_reset_clears_perif_status_but_keeps_config():
    """UI HW Reset: latched status bits (overrun, valid/ovf, busy) go away,
    while the perif config the user entered survives."""
    s = _sim()
    s.gpcfg_write("pru0", 1)
    s.write_perif_register("pru0", 0x260E8, 8 << 11)   # CH0CFG0: tx_frame_size = 8
    perif = s._perif["pru0"]
    for i in range(5):                                 # 5th push → TX overrun
        perif.channels[0].push_tx(i)
    perif.channels[0].tx_go(0.0)
    perif.channels[1].rx_en = True
    perif.channels[1].rx_valid = True
    perif.channels[1].rx_ovf = True
    ch0 = s.perif_state("pru0")["channels"][0]
    assert ch0["tx_overrun"] is True

    s.hard_reset()

    st = s.perif_state("pru0")
    ch0, ch1 = st["channels"][0], st["channels"][1]
    assert ch0["tx_overrun"] is False
    assert ch0["tx_underrun"] is False
    assert ch0["tx_fifo"] == []
    assert ch0["busy"] is False
    assert ch0["fsm"] == "IDLE"
    assert ch1["rx_valid"] is False
    assert ch1["rx_ovf"] is False
    assert ch1["rx_en"] is False
    assert s.cores["pru0"].io_port.read_r31() == 0
    # Config kept: still in peripheral mode with the frame size we programmed.
    assert st["enabled"] is True
    assert ch0["config"]["tx_frame_size"] == 8


def test_core_reset_clears_perif_status():
    """The per-core Reset button clears that core's peripheral state too."""
    s = _sim()
    s.gpcfg_write("pru0", 1)
    perif = s._perif["pru0"]
    for i in range(5):
        perif.channels[0].push_tx(i)
    assert perif.channels[0].tx_overrun is True
    s.reset("pru0")
    assert perif.channels[0].tx_overrun is False
    assert perif.channels[0].tx_fifo == []
