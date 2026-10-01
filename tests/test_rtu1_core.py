"""RTU_PRU1 support used by the simple SSI realtime rebuild."""

from simulator import Simulator


def test_rtu1_is_a_slice1_core_with_shared_iep_and_dram1_mapping():
    sim = Simulator(config_path="config/memory_am243x.cfg")

    assert set(("pru0", "rtu0", "pru1", "rtu1")) <= set(sim.cores)
    assert sim.cores["rtu1"].name == "RTU1"
    assert sim.cores["rtu1"].dram_swap is True
    assert sim.cores["rtu1"].io_port.perif is None

    assert sim.load("rtu1", "ldi r5, 0x1234\nsbco r5, c24, 0x20, 4\nhalt\n") == []
    sim.step("rtu1", 8)
    assert int.from_bytes(sim.memory_read(0x2020, 4), "little") == 0x1234
    assert int.from_bytes(sim.memory_read(0x0020, 4), "little") == 0


def test_rtu1_advances_the_existing_shared_iep_timeline():
    sim = Simulator(config_path="config/memory_am243x.cfg")
    sim.load("rtu1", "nop\njmp 0\n")
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    sim.iep.write_count(low=0, high=0)

    sim.step("rtu1", 17)

    assert sim.iep.count == 17


def test_am243x_padcfg_mmio_accepts_real_ssi_pin_setup_writes():
    sim = Simulator(config_path="config/memory_am243x.cfg")

    sim.memory.write(0x000F0000, (0x12345678).to_bytes(4, "little"))

    assert sim.memory_read(0x000F0000, 4) == (0x12345678).to_bytes(4, "little")
