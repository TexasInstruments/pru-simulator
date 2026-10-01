from pru_io.sd_filter import SigmaDeltaFilter


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
