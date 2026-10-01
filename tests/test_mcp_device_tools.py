from fractions import Fraction
from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP
from pru_io.device_model import DeviceModel, PUSH_PULL


ELF_FIXTURE = Path(__file__).parents[1] / "references" / "pru_encoding_test.out"


def fresh_mcp():
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


class _FixedPushPullDevice(DeviceModel):
    def __init__(self, name, pin, value):
        self.name = name
        self.nets = {pin: PUSH_PULL}
        self.pin = pin
        self.value = value

    def tick(self, cycle, bus):
        return 1 << self.pin, self.value << self.pin


def test_generic_discovery_attach_state_and_detach_preserve_output_ownership():
    mcp = fresh_mcp()
    initial_mask = mcp.sim.io("pru1")["gpo_drive_mask"]

    discovery = mcp.pru_device_discover()
    assert set(discovery["profiles"]) == {"ssi_encoder", "tca9538", "foc_motor"}

    attached = mcp.pru_device_attach(
        core="pru1", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 16, "resolution": 8},
    )
    assert attached["device"]["name"] == "axis"
    assert mcp.sim.io("pru1")["gpo_drive_mask"] == initial_mask & ~(1 << 16)
    assert mcp.pru_device_state()["devices"][0]["name"] == "axis"

    detached = mcp.pru_device_detach(device_name="axis")

    assert detached["success"] is True
    assert mcp.pru_device_state()["devices"] == []
    assert mcp.sim.io("pru1")["gpo_drive_mask"] == initial_mask


def test_attach_ssi_preset_and_report_layout_through_generic_tools():
    mcp = fresh_mcp()
    assert "TTK70" in mcp.pru_device_discover()["profiles"]["ssi_encoder"]["presets"]

    attached = mcp.pru_device_attach(
        core="pru1", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 16, "preset": "TTK70",
                "position": 0x123456, "error_value": 2, "encoding": "gray"},
    )

    device = attached["device"]
    assert (device["preset"], device["resolution"], device["position_bits"],
            device["error_bits"], device["error"], device["encoding"]) == (
        "TTK70", 26, 24, 2, 2, "gray")
    with pytest.raises(ValueError):
        mcp.pru_device_attach(core="pru1", profile="ssi_encoder",
                              config={"name": "bad", "preset": "TTK70",
                                      "error_value": 9})
    assert [d["name"] for d in mcp.pru_device_state()["devices"]] == ["axis"]


def test_ssi_profile_defaults_to_exact_selected_core_clock(tmp_path):
    config = tmp_path / "clock.cfg"
    config.write_text(
        "[device]\n"
        "pru_clock_mhz = 200.123456789123456\n"
        "pru1_clock_mhz = 201.987654321987654\n",
        encoding="utf-8",
    )
    mcp = PRUSimulatorMCP(config_path=str(config))
    defaults = mcp.pru_device_discover()["profiles"]["ssi_encoder"]["defaults"]

    assert "core_clock_hz" not in defaults
    mcp.pru_device_attach(
        "ssi_encoder", core="pru0", config={**defaults, "name": "axis0"})
    mcp.pru_device_attach(
        "ssi_encoder", core="pru1", config={**defaults, "name": "axis1"})
    axis0, axis1 = mcp.sim.device_bus.devices

    assert axis0.core_clock_hz == Fraction("200.123456789123456") * 1_000_000
    assert axis1.core_clock_hz == Fraction("201.987654321987654") * 1_000_000


@pytest.mark.parametrize("initially_output", [True, False])
@pytest.mark.parametrize("before_attachment", [True, False])
def test_device_snapshot_restores_gpio_leases(initially_output, before_attachment):
    mcp = fresh_mcp()
    initial = mcp.sim.io("pru0")["gpo_drive_mask"]
    if not initially_output:
        initial &= ~(1 << 8)
        mcp.sim.set_gpio_drive_mask("pru0", initial)
    before = mcp.sim.device_bus.snapshot()
    mcp.pru_device_attach("ssi_encoder", config={"name": "axis_a"})
    mcp.pru_device_attach("ssi_encoder", config={"name": "axis_b"})
    attached = mcp.sim.device_bus.snapshot()
    if not before_attachment:
        mcp.pru_device_detach("axis_a")
        mcp.pru_device_detach("axis_b")
    mcp.sim.device_bus.restore(before if before_attachment else attached)
    if not before_attachment:
        mcp.pru_device_detach("axis_a")
        assert not mcp.sim.io("pru0")["gpo_drive_mask"] & (1 << 8)
        mcp.pru_device_detach("axis_b")
    else:
        mcp.pru_device_attach("ssi_encoder", config={"name": "axis_c"})
        mcp.pru_device_detach("axis_c")
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == initial


def test_overlapping_ssi_outputs_stay_released_until_last_device_detaches():
    mcp = fresh_mcp()
    initial_mask = mcp.sim.io("pru0")["gpo_drive_mask"]
    config = {"data_pin": 8}
    mcp.pru_device_attach(
        profile="ssi_encoder", config={**config, "name": "axis_a"})
    mcp.pru_device_attach(
        profile="ssi_encoder", config={**config, "name": "axis_b"})

    mcp.pru_device_detach("axis_a")
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == initial_mask & ~(1 << 8)

    mcp.pru_device_detach("axis_b")
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == initial_mask


def test_foc_motor_attach_owns_only_pru0_pwm_and_current_pins():
    mcp = fresh_mcp()
    initial_mask = mcp.sim.io("pru0")["gpo_drive_mask"]

    attached = mcp.pru_device_attach(profile="foc_motor")

    assert attached["success"] is True
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == 0x7
    assert mcp.sim.device_bus.devices[0].nets == {3: "push_pull", 4: "push_pull"}
    assert mcp.pru_device_state()["contentions"] == 0

    mcp.pru_device_detach("foc_motor")
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == initial_mask


def test_foc_motor_owns_its_core_output_mask_exclusively():
    mcp = fresh_mcp()
    mcp.pru_device_attach(profile="foc_motor")

    with pytest.raises(ValueError, match="cannot share that core"):
        mcp.pru_device_attach(profile="ssi_encoder", config={"name": "axis"})

    assert [device.name for device in mcp.sim.device_bus.devices] == ["foc_motor"]
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == 0x7

    mcp.pru_device_detach("foc_motor")

    other = fresh_mcp()
    other.pru_device_attach(profile="ssi_encoder", config={"name": "axis"})
    with pytest.raises(ValueError, match="cannot share that core"):
        other.pru_device_attach(profile="foc_motor")
    assert [device.name for device in other.sim.device_bus.devices] == ["axis"]


def test_foc_motor_profile_uses_configured_pru_clock(sim_config):
    mcp = PRUSimulatorMCP(config_path=sim_config(pru_clock_mhz=250,
                                                 iep_clock_mhz=200))
    mcp.pru_device_attach(profile="foc_motor")

    assert mcp.sim.device_bus.devices[0].core_clock_hz == 250_000_000


def test_foc_motor_profile_rejects_non_object_config():
    mcp = fresh_mcp()
    with pytest.raises(ValueError, match="device profile config must be an object"):
        mcp.pru_device_attach(profile="foc_motor", config="invalid")


def test_sd_route_mcp_tool_samples_a_real_pin_and_can_restore_default():
    mcp = fresh_mcp()
    routed = mcp.pru_sd_route_input(channel=0, pin=3)
    assert routed == {"channel": 0, "pin": 3}
    assert mcp.sim.sd_state("pru0")["input_routes"][0] == 3

    mcp.pru_sd_route_input(channel=0, pin=-1)
    assert mcp.sim.sd_state("pru0")["input_routes"][0] is None


def test_tca_profile_keeps_sda_and_scl_owned_as_open_drain_master_lines():
    mcp = fresh_mcp()
    initial_mask = mcp.sim.io("pru0")["gpo_drive_mask"]

    result = mcp.pru_device_attach(
        profile="tca9538", config={"name": "expander", "address": 0x24})

    assert result["success"] is True
    assert mcp.sim.io("pru0")["gpo_drive_mask"] == initial_mask
    assert mcp.sim.device_bus.devices[0].nets == {
        0: "open_drain", 1: "open_drain",
    }


def test_attached_ssi_profile_reset_clears_reports_and_keeps_device():
    mcp = fresh_mcp()
    mcp.pru_device_attach(
        core="pru1", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 16, "resolution": 2,
                "core_clock_hz": 100_000_000, "f_max_hz": 10_000_000,
                "monoflop_us": 1},
    )
    encoder = mcp.sim.device_bus.devices[0]

    for cycle, clock in ((0, 1), (1, 0), (11, 1), (12, 0), (13, 1)):
        encoder.tick(cycle, clock)
    assert encoder.faults()

    mcp.sim.reset("pru1")

    assert mcp.sim.device_bus.devices == [encoder]
    assert encoder.get_state()["state"] == "idle"
    assert encoder.events() == []
    assert encoder.faults() == []


def test_ui_step_back_restores_attached_ssi_profile_guard(monkeypatch):
    from ui import server as ui_server

    mcp = fresh_mcp()
    sim = mcp.sim
    monkeypatch.setattr(ui_server, "sim", sim)
    mcp.pru_device_attach(
        core="pru1", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 16, "resolution": 2,
                "core_clock_hz": 1_000_000, "f_max_hz": 100_000,
                "monoflop_us": 100},
    )
    encoder = sim.device_bus.devices[0]
    for cycle, clock in ((0, 1), (10, 0), (20, 1), (30, 0),
                         (40, 1), (50, 0)):
        encoder.tick(cycle, clock)
    snapshot = ui_server._snapshot("pru1")

    encoder.tick(55, 1)
    encoder.tick(150, 1)
    assert any(event["kind"] == "frame" for event in encoder.events())

    ui_server._restore("pru1", snapshot)
    assert sim.device_bus.devices == [encoder]
    assert encoder.get_state()["state"] == "guard"
    assert encoder.events() == []
    assert encoder._post_word_fall_seen is True

    encoder.tick(55, 1)
    encoder.tick(150, 1)
    assert [event["kind"] for event in encoder.events()] == ["frame"]


def test_generic_device_events_and_faults_are_queryable_by_name():
    mcp = fresh_mcp()
    mcp.pru_device_attach(
        core="pru1", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 16, "position": 0b10,
                "resolution": 2, "f_max_hz": 10_000_000,
                "core_clock_hz": 100_000_000, "monoflop_us": 1},
    )
    model = mcp.sim.device_bus.devices[0]
    model.tick(0, 1)
    model.tick(1, 0)
    model.tick(11, 1)
    model.tick(21, 0)
    model.tick(31, 1)
    model.tick(41, 0)
    model.tick(42, 1)
    model.tick(52, 0)
    model.tick(62, 1)

    events = mcp.pru_device_events(device_name="axis")["events"]
    faults = mcp.pru_device_faults(device_name="axis")["faults"]

    assert any(event["kind"] == "fault" for event in events)
    assert any("past the end" in fault for fault in faults)


@pytest.mark.parametrize(
    ("profile", "config"),
    [
        ("ssi_encoder", {"idle_value": 1.0}),
        ("tca9538", {"address": 0x100}),
        ("tca9538", {"sda_pin": 0}),
    ],
)
def test_generic_attach_rejects_invalid_profile_config(profile, config):
    with pytest.raises(ValueError):
        fresh_mcp().pru_device_attach(profile=profile, config=config)


def test_failed_elf_load_preserves_generic_device_and_direction():
    mcp = fresh_mcp()
    mcp.pru_device_attach(
        core="pru0", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 8},
    )
    previous = mcp.sim
    previous_mask = previous.io("pru0")["gpo_drive_mask"]

    result = mcp.pru_elf_load(b64="not-base64!")

    assert result["success"] is False
    assert mcp.sim is previous
    assert mcp.pru_device_state()["devices"][0]["name"] == "axis"
    assert previous.io("pru0")["gpo_drive_mask"] == previous_mask


def test_successful_elf_load_cleans_up_generic_device_output_ownership():
    mcp = fresh_mcp()
    mcp.pru_device_attach(
        core="pru0", profile="ssi_encoder",
        config={"name": "axis", "data_pin": 8},
    )
    previous = mcp.sim

    result = mcp.pru_elf_load(path=str(ELF_FIXTURE))

    assert result["success"] is True
    assert mcp.sim is not previous
    assert previous.device_bus.devices == []
    assert previous.io("pru0")["gpo_drive_mask"] == (1 << 20) - 1
    assert mcp.pru_device_state()["devices"] == []


def test_each_device_receives_structured_contentions_with_exact_name_matching():
    mcp = fresh_mcp()
    sim = mcp.sim
    sim.set_gpio_drive_mask("pru0", 0)
    sim.attach_device("pru0", _FixedPushPullDevice("axis_a", 8, 0))
    sim.attach_device("pru0", _FixedPushPullDevice("axis_b", 8, 1))
    sim.attach_device("pru0", _FixedPushPullDevice("prefix_axis_a", 9, 0))
    sim.attach_device("pru0", _FixedPushPullDevice("axis_a_suffix", 9, 1))
    sim.cores["pru0"].io_port.tick_devices(1)

    axis_a = mcp.pru_device_faults(device_name="axis_a")
    axis_b = mcp.pru_device_faults(device_name="axis_b")
    prefixed = mcp.pru_device_faults(device_name="prefix_axis_a")
    suffixed = mcp.pru_device_faults(device_name="axis_a_suffix")

    assert axis_a["faults"] and axis_b["faults"] == axis_a["faults"]
    assert {driver["name"] for driver in axis_b["contentions"][0]["drivers"]} == {
        "axis_a", "axis_b",
    }
    assert {record["pin"] for record in axis_a["contentions"]} == {8}
    assert {record["pin"] for record in prefixed["contentions"]} == {9}
    assert suffixed["faults"] and suffixed["contentions"][0]["pin"] == 9
