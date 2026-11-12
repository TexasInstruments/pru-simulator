"""Bool masks are rejected: bool is an int subclass, so True would act as 1."""

import pytest

from simulator import Simulator


@pytest.mark.parametrize("flag", [True, False])
def test_lease_core_outputs_rejects_bool_mask_without_mutation(flag):
    sim = Simulator()
    before = sim.device_bus.snapshot()
    with pytest.raises(ValueError, match="lease mask"):
        sim.lease_gpio_outputs("pru0", flag, object())
    assert sim.device_bus.snapshot() == before
    assert sim.io("pru0")["gpo_drive_mask"] == 0


@pytest.mark.parametrize("flag", [True, False])
def test_set_gpio_drive_mask_rejects_bool_without_mutation(flag):
    sim = Simulator()
    sim.set_gpio_drive_mask("pru0", 1 << 2)
    with pytest.raises(ValueError, match="drive mask"):
        sim.set_gpio_drive_mask("pru0", flag)
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 2


def test_integer_masks_still_drive_and_lease_gpio():
    sim = Simulator()
    sim.set_gpio_drive_mask("pru0", 1 << 2)
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 2
    sim.lease_gpio_outputs("pru0", 0, object())
    owner = object()
    sim.lease_gpio_outputs("pru0", 1 << 3, owner, drive_mask=1 << 3)
    assert sim.io("pru0")["gpo_drive_mask"] == (1 << 2) | (1 << 3)
    sim.device_bus.release_core_outputs(owner)
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 2
