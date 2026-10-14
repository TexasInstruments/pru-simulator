from pru_io.sd_filter import SigmaDeltaFilter
from pru_io.device_model import DeviceModel, PUSH_PULL
from simulator import Simulator


class _AlternatingInput(DeviceModel):
    name = "alternating-input"
    nets = {3: PUSH_PULL}
    time_driven = True

    def tick(self, cycle, bus):
        return (1 << 3), ((cycle & 1) << 3)


def test_sd_filter_keeps_internal_modulator_as_default():
    sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
    sd.modulators[0].sd_clock_mhz = 200.0
    sd.modulators[0].next_bit = lambda: 0

    sd.tick()

    assert sd.channels[0].acc1 == 0
    assert sd.input_routes == [None, None, None]


def test_sd_filter_can_sample_real_gpi_pin_without_running_internal_modulator():
    sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
    sd.modulators[0].sd_clock_mhz = 200.0
    sd.route_input(0, 3)
    sd.modulators[0].next_bit = lambda: (_ for _ in ()).throw(AssertionError())

    sd.tick(1 << 3)

    assert sd.channels[0].acc1 == 1
    assert sd.get_state()["input_routes"][0] == 3


def test_sd_filter_pin_route_uses_the_gpio_pin_level_and_can_be_cleared():
    sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
    sd.modulators[1].sd_clock_mhz = 200.0
    sd.route_input(1, 4)
    sd.tick(0)
    assert sd.channels[1].acc1 == 0

    sd.tick(1 << 4)
    assert sd.channels[1].acc1 == 1

    sd.route_input(1, None)
    assert sd.get_state()["input_routes"][1] is None


def test_simulator_samples_routed_sd_input_after_each_elapsed_device_cycle():
    sim = Simulator()
    core = sim.cores["pru0"]
    sd = core.io_port.sd_filter
    sd.modulators[0].sd_clock_mhz = sd.pru_clock_mhz
    sd.route_input(0, 3)
    samples = []
    tick_channel = sd.channels[0].tick
    sd.channels[0].tick = lambda bit: (samples.append(bit), tick_channel(bit))[1]

    core.io_port.set_gpo_drive_mask(0x7)
    sim.device_bus.attach(_AlternatingInput(), port="pru0")
    assert sim.load(
        "pru0", "ldi r0, 0\nlbbo &r1, r0, 0, 4\nhalt\n"
    ) == []

    sim.step("pru0", 2)

    assert core.counters.stall_cycles == 2
    assert samples == [1, 0, 1, 0]
