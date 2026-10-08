"""The emulator firmware is checked against an independent Python SSI master."""
from pathlib import Path
import struct
from math import ceil

import pytest

from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime
from simulator import Simulator

ROOT = Path(__file__).parents[1]
PHASE_STEPS = 40  # PRU cycles per clock phase; far above the firmware's needs


def read_frame(sim: Simulator, bits: int) -> int:
    """Clock one frame into PRU0 as an SSI master and return the sampled word."""
    sim.set_input("pru0", 0, True)
    # The independent master allows the configured monoflop guard between words.
    timeout_ticks = int.from_bytes(sim.memory_read(abi.EMULATOR_ADDRESS + 0x14, 4), "little")
    guard_cycles = ceil(timeout_ticks * sim.iep.core_clock_hz("pru0") / sim.iep.active_clock_hz)
    sim.step("pru0", guard_cycles + PHASE_STEPS)
    sim.set_input("pru0", 0, False)  # first falling edge latches the frame
    sim.step("pru0", PHASE_STEPS)
    word = 0
    for _ in range(bits):
        sim.set_input("pru0", 0, True)  # each rising edge presents a bit
        sim.step("pru0", PHASE_STEPS)
        word = (word << 1) | sim.io("pru0")["gpo_pins"][16]
        sim.set_input("pru0", 0, False)
        sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    return word


def independent_frame(position, error, position_bits, error_bits, gray=False):
    """Frame word with the error bits last, written without the model."""
    if gray:
        position ^= position >> 1
    assert position < 1 << position_bits and error < 1 << max(error_bits, 1)
    return (position << error_bits) | error


@pytest.fixture
def sim():
    return Simulator(str(ROOT / "memory.cfg"))


# preset, frame bits, position bits, error bits, position, error
CASES = [
    ("RM08_12BIT_4MHZ", 12, 12, 0, 0xABC, 0),
    ("AFS_AFM60_MULTITURN_30BIT", 33, 30, 3, 0x2ABCDEF1, 0b101),
    ("AHS_AHM36_MULTITURN", 27, 26, 1, 0x3FFFFFF, 1),
    ("TTK70", 26, 24, 2, 0x800001, 0b10),
]


@pytest.mark.parametrize(
    ("preset", "frame_bits", "position_bits", "error_bits", "position", "error"),
    CASES)
def test_emulator_shifts_the_host_packed_frame_out_msb_first(
        sim, preset, frame_bits, position_bits, error_bits, position, error):
    emulator = SSIEmulatorRuntime(sim, preset=preset, position=position,
                                  error_value=error)
    emulator.load()

    word = read_frame(sim, frame_bits)

    assert word == independent_frame(position, error, position_bits, error_bits, gray=preset == "TTK70")
    assert emulator.status() == 0


def test_emulator_sends_64_bit_frames_and_gray_positions(sim):
    emulator = SSIEmulatorRuntime(
        sim, resolution=64, position=0xDEADBEEF12345678, encoding="gray")
    emulator.load()

    word = read_frame(sim, 64)

    assert word == 0xDEADBEEF12345678 ^ (0xDEADBEEF12345678 >> 1)


def test_host_changes_between_frames_reach_the_next_frame(sim):
    emulator = SSIEmulatorRuntime(sim, preset="AFS_AFM60_SINGLETURN",
                                  position=0x12345, error_value=0)
    emulator.load()

    first = read_frame(sim, 21)
    emulator.set_position(0x3FFFF)
    emulator.set_error(0b110)
    second = read_frame(sim, 21)

    assert first == independent_frame(0x12345, 0, 18, 3)
    assert second == independent_frame(0x3FFFF, 0b110, 18, 3)


@pytest.mark.parametrize(("version", "frame_bits"), [
    (abi.ABI_VERSION + 1, 12), (abi.ABI_VERSION - 1, 12), (abi.ABI_VERSION, 0),
    (abi.ABI_VERSION, 65), (0, 0),
])
def test_emulator_halts_with_status_one_on_invalid_config(sim, version, frame_bits):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.memory.write(abi.EMULATOR_ADDRESS,
                     struct.pack("<IIIII", version, frame_bits, 0, 0, 0))

    read_frame(sim, 1)

    assert emulator.status() == 1

@pytest.mark.parametrize("stopped_high", [False, True])
@pytest.mark.parametrize("bits, word", [(8, 0xA5), (1, 1), (33, 0x1000000A5), (64, 0xABCDEF00123456A5)])
@pytest.mark.parametrize("iep_mhz", [200, 300])
def test_partial_read_timeout_discards_bits(sim_config, stopped_high, bits, word, iep_mhz):
    sim = Simulator(sim_config(pru_clock_mhz=250, iep_clock_mhz=iep_mhz))
    emulator = SSIEmulatorRuntime(sim, resolution=bits, position=word, monoflop_us=4)
    emulator.load()
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, False)
    sim.step("pru0", PHASE_STEPS)
    for _ in range(min(3, bits - 1)):
        sim.set_input("pru0", 0, True)
        sim.step("pru0", PHASE_STEPS)
        sim.set_input("pru0", 0, False)
        sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, stopped_high)
    sim.step("pru0", 1500)

    assert sim.io("pru0")["gpo_pins"][16] == 1
    assert read_frame(sim, bits) == word
    assert emulator.status() == 0


def test_timeout_crosses_counter_low_word_rollover(sim):
    emulator = SSIEmulatorRuntime(sim, resolution=8, position=0xA5, monoflop_us=4)
    sim.iep.count = 0xFFFFFFFF - 100
    emulator.load()
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, False)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, True)
    sim.step("pru0", 1500)
    assert read_frame(sim, 8) == 0xA5
    assert sim.iep.count >> 32 == 1


@pytest.mark.parametrize("cfg, cmp_cfg", [(0x21, 0), (0x01, 0), (0x11, 3)])
def test_emulator_rejects_timer_not_free_running_unit_increment(sim, cfg, cmp_cfg):
    sim.iep.global_cfg = cfg
    sim.iep.cmp_cfg = cmp_cfg
    emulator = SSIEmulatorRuntime(sim)
    with pytest.raises(ValueError, match="IEP"):
        emulator.load()


def test_emulator_enables_stopped_timer_without_resetting_count(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=250, iep_clock_mhz=200))
    sim.iep.count = 123
    emulator = SSIEmulatorRuntime(sim, monoflop_us="1.001")
    emulator.load()
    assert sim.iep.count == 123
    assert sim.iep.count_enabled and sim.iep.default_inc == 1
    cfg = abi.unpack_emulator(sim.memory_read(abi.EMULATOR_ADDRESS, abi.EMULATOR_SIZE))
    assert cfg["monoflop_ticks"] == 201  # active external IEP is 200 MHz in this branch


@pytest.mark.parametrize("prior", [0x550, 0x20, 0x00])
def test_close_puts_back_a_timer_that_load_enabled_without_touching_count(sim, prior):
    sim.iep.global_cfg = prior
    sim.iep.count = 123
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    assert sim.iep.count_enabled and sim.iep.default_inc == 1
    sim.step("pru0", 50)
    count = sim.iep.count
    assert count > 123
    emulator.close()
    assert sim.iep.global_cfg == prior
    assert not sim.iep.count_enabled
    assert sim.iep.count == count
    sim.step("pru0", 50)
    assert sim.iep.count == count


@pytest.mark.parametrize("cfg", [0x11, 0x511])
def test_close_leaves_an_already_running_timer_and_its_count_alone(sim, cfg):
    sim.iep.global_cfg = cfg
    sim.iep.count = 100
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    assert sim.iep.global_cfg == cfg
    assert sim.iep.count == 100
    sim.step("pru0", 50)
    count = sim.iep.count
    assert count > 100
    emulator.close()
    assert sim.iep.global_cfg == cfg
    assert sim.iep.count == count


def test_closing_twice_restores_the_timer_only_once(sim):
    prior = sim.iep.global_cfg
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    emulator.close()
    assert sim.iep.global_cfg == prior
    sim.iep.global_cfg = 0x31  # another user starts the timer after close
    emulator.close()
    assert sim.iep.global_cfg == 0x31


def test_second_close_does_not_stop_a_timer_started_after_the_first(sim):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    emulator.close()
    sim.iep.global_cfg = 0x511  # another user starts it exactly as load had left it
    emulator.close()
    assert sim.iep.global_cfg == 0x511


def test_close_without_load_leaves_the_timer_alone(sim):
    prior = sim.iep.global_cfg
    SSIEmulatorRuntime(sim).close()
    assert sim.iep.global_cfg == prior


def test_close_after_rejected_load_leaves_the_timer_alone(sim):
    sim.iep.global_cfg = 0x51
    emulator = SSIEmulatorRuntime(sim)
    with pytest.raises(ValueError, match="DEFAULT_INC"):
        emulator.load()
    emulator.close()
    assert sim.iep.global_cfg == 0x51


def test_close_after_failed_assembly_leaves_the_timer_as_found(sim, monkeypatch):
    prior = sim.iep.global_cfg
    monkeypatch.setattr(sim, "load", lambda *args, **kwargs: ["boom"])
    emulator = SSIEmulatorRuntime(sim)
    with pytest.raises(ValueError, match="assembly failed"):
        emulator.load()
    assert not sim.iep.count_enabled  # load enables the timer only once nothing can fail
    emulator.close()
    assert sim.iep.global_cfg == prior


def test_close_puts_back_only_enable_and_increment_not_the_whole_register(sim):
    sim.iep.global_cfg = 0x10550  # bit 16 set before load
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.iep.global_cfg = (sim.iep.global_cfg & ~0xF00) | 0x300  # another caller: CMP_INC=3
    emulator.close()
    assert sim.iep.global_cfg == 0x10350  # CMP_INC and bit 16 survive
    assert not sim.iep.count_enabled


@pytest.mark.parametrize("changed", [0x531, 0x510, 0x551],
                         ids=["increment-3", "stopped", "increment-5"])
def test_close_leaves_enable_and_increment_a_caller_changed_while_open(sim, changed):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.iep.global_cfg = changed
    emulator.close()
    assert sim.iep.global_cfg == changed


def test_close_leaves_a_timer_another_caller_started_after_a_hard_reset(sim):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.hard_reset()
    sim.iep.global_cfg |= 1  # someone else starts the freshly reset timer
    emulator.close()
    assert sim.iep.global_cfg == 0x551
    assert sim.iep.count_enabled


def test_second_emulator_on_one_simulator_is_refused_and_the_first_is_undisturbed(sim):
    first = SSIEmulatorRuntime(sim, core="pru1", resolution=8, position=0xA5)
    second = SSIEmulatorRuntime(sim, core="pru0", resolution=12, position=0xABC)
    first.load()
    loaded_cfg = sim.iep.global_cfg
    block = sim.memory_read(abi.EMULATOR_ADDRESS, abi.EMULATOR_SIZE)
    pru0_drive_mask = sim.io("pru0")["gpo_drive_mask"]
    with pytest.raises(ValueError, match="another SSI emulator"):
        second.load()
    assert sim.memory_read(abi.EMULATOR_ADDRESS, abi.EMULATOR_SIZE) == block
    assert sim.io("pru0")["gpo_drive_mask"] == pru0_drive_mask
    second.close()  # never loaded, so it puts nothing back and frees nothing
    assert sim.iep.global_cfg == loaded_cfg
    with pytest.raises(ValueError, match="another SSI emulator"):
        second.load()
    first.close()
    assert sim.iep.global_cfg == 0x550
    second.load()  # free again once the first has closed
    assert sim.iep.count_enabled
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 16


def test_emulators_on_separate_simulators_do_not_conflict(sim):
    SSIEmulatorRuntime(sim).load()
    SSIEmulatorRuntime(Simulator(str(ROOT / "memory.cfg"))).load()


@pytest.mark.parametrize("failure, error", [
    ("unknown core", KeyError), ("lease conflict", ValueError), ("assembly", ValueError)])
def test_load_that_raises_leaves_the_timer_as_it_found_it(sim, monkeypatch, failure, error):
    if failure == "lease conflict":
        sim.lease_gpio_outputs("pru0", 1 << 16, object())  # held as an input
    if failure == "assembly":
        monkeypatch.setattr(sim, "load", lambda *args, **kwargs: ["boom"])
    prior = sim.iep.global_cfg
    emulator = SSIEmulatorRuntime(sim, core="nope" if failure == "unknown core" else "pru0")
    with pytest.raises(error):
        emulator.load()
    assert sim.iep.global_cfg == prior
    emulator.close()
    assert sim.iep.global_cfg == prior


def test_load_rejecting_an_oversized_timeout_leaves_a_stopped_timer_alone(sim):
    prior = sim.iep.global_cfg
    emulator = SSIEmulatorRuntime(sim, monoflop_us="22000000")
    with pytest.raises(ValueError, match="u32"):
        emulator.load()
    assert sim.iep.global_cfg == prior


def test_failed_load_does_not_claim_the_simulator(sim, monkeypatch):
    monkeypatch.setattr(sim, "load", lambda *args, **kwargs: ["boom"])
    with pytest.raises(ValueError, match="assembly failed"):
        SSIEmulatorRuntime(sim).load()
    monkeypatch.undo()
    SSIEmulatorRuntime(sim, core="pru1").load()


def _assembly_returns_errors(*args, **kwargs):
    return ["boom"]


def _assembly_raises(*args, **kwargs):
    raise RuntimeError("boom")


@pytest.mark.parametrize("fake_load, error, match", [
    (_assembly_returns_errors, ValueError, "assembly failed"),
    (_assembly_raises, RuntimeError, "boom"),
], ids=["errors", "exception"])
def test_failed_assembly_releases_gpio_lease_before_raising(
        sim, monkeypatch, fake_load, error, match):
    sim.set_gpio_drive_mask("pru0", 1 << 5)
    monkeypatch.setattr(sim, "load", fake_load)
    with pytest.raises(error, match=match):
        SSIEmulatorRuntime(sim).load()
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 5
    assert sim.device_bus._lease_owners == {}
    monkeypatch.undo()
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    assert sim.io("pru0")["gpo_drive_mask"] == (1 << 5) | (1 << 16)
    emulator.close()
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 5


@pytest.mark.parametrize("ocp, expected", [(False, 301), (True, 251)])
def test_timeout_rounds_up_selected_iep_source_and_reload_updates_it(sim_config, ocp, expected):
    sim = Simulator(sim_config(pru_clock_mhz=250, iep_clock_mhz=300))
    sim.iep.write_iepclk(int(ocp))
    sim.iep.global_cfg = 0x11
    sim.iep.count = 12345
    emulator = SSIEmulatorRuntime(sim, monoflop_us="1.001")
    emulator.load()
    assert emulator.monoflop_ticks == expected
    assert sim.iep.count == 12345
    assert sim.iep.global_cfg == 0x11
    sim.iep.write_iepclk(int(not ocp))
    assert emulator.monoflop_ticks == expected  # configuration is explicitly load-time
    emulator.load()
    assert emulator.monoflop_ticks == (251 if not ocp else 301)


def test_host_can_update_frame_before_loading_emulator(sim):
    emulator = SSIEmulatorRuntime(sim, resolution=8)
    emulator.set_position(0xA5)
    emulator.set_error(0)
    emulator.load()
    assert read_frame(sim, 8) == 0xA5


def test_emulator_rejects_zero_timeout_config(sim):
    emulator = SSIEmulatorRuntime(sim)
    emulator.load()
    sim.memory.write(abi.EMULATOR_ADDRESS + 0x14, b"\x00" * 4)
    read_frame(sim, 1)
    assert emulator.status() == 1


@pytest.mark.parametrize("late_edge", ["rise", "fall"])
def test_edge_observed_after_shared_timer_expiry_aborts_partial_frame(sim_config, late_edge):
    sim = Simulator(sim_config(pru_clock_mhz=250, pru1_clock_mhz=250, iep_clock_mhz=200))
    emulator = SSIEmulatorRuntime(sim, resolution=8, position=0x25, monoflop_us=4)
    emulator.load()
    assert sim.load("pru1", "nop\njmp 0") == []
    core = sim.cores["pru0"]
    loop_name = "wait_for_" + late_edge
    loop_pc = next(instruction.operands[0].resolved_addr
                   for instruction in core.instructions
                   if instruction.opcode == "QBA"
                   and instruction.operands[0].name == loop_name)
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, False)
    sim.step("pru0", PHASE_STEPS)
    if late_edge == "fall":
        sim.set_input("pru0", 0, True)
        sim.step("pru0", PHASE_STEPS)
    for _ in range(30):
        if core.pc == loop_pc:
            break
        sim.step("pru0")
    assert core.pc == loop_pc
    last_fall = core.registers.read(8, 0, 32) | core.registers.read(9, 0, 32) << 32
    sim.step("pru1", 2000)
    assert sim.iep.count - last_fall > emulator.monoflop_ticks

    sim.set_input("pru0", 0, late_edge == "rise")
    observed_data = []
    for _ in range(PHASE_STEPS):
        sim.step("pru0")
        observed_data.append(sim.io("pru0")["gpo_pins"][16])
    if late_edge == "rise":
        assert observed_data == [1] * PHASE_STEPS  # expired rising edge never shifts a bit

    assert sim.io("pru0")["gpo_pins"][16] == 1  # expired frame restores idle DATA
    assert core.registers.read(5, 0, 32) == 8  # no stale falling-edge bit decrement
    assert read_frame(sim, 8) == 0x25
    assert emulator.status() == 0


def test_emulator_closing_fall_holds_data_low_until_monoflop(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=250, iep_clock_mhz=250))
    emulator = SSIEmulatorRuntime(sim, resolution=8, position=0xA5, monoflop_us=4)
    emulator.load()
    sim.set_input("pru0", 0, True)
    sim.step("pru0", PHASE_STEPS)
    sim.set_input("pru0", 0, False)
    sim.step("pru0", PHASE_STEPS)
    sampled = []
    for bit in range(8):
        sim.set_input("pru0", 0, True)
        sim.step("pru0", PHASE_STEPS)
        sampled.append(sim.io("pru0")["gpo_pins"][16])
        sim.set_input("pru0", 0, False)
        sim.step("pru0", PHASE_STEPS)
    assert sampled == [1, 0, 1, 0, 0, 1, 0, 1]
    sim.set_input("pru0", 0, True)
    sim.step("pru0", 400)  # well within Tm after closing fall
    assert sim.io("pru0")["gpo_pins"][16] == 0
    sim.step("pru0", 1200)  # past Tm, ordinary instruction polling restores idle
    assert sim.io("pru0")["gpo_pins"][16] == 1
    assert emulator.status() == 0
