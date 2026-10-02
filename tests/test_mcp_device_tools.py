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
    assert set(discovery["profiles"]) == {"ssi_encoder", "tca9538"}

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


def test_ssi_profile_defaults_to_exact_selected_core_clock(tmp_path):
    config = tmp_path / "clock.cfg"
    config.write_text(
        "[device]\n"
        "pru_clock_mhz = 200.123456789123456\n"
        "pru1_clock_mhz = 201.987654321987654\n",
        encoding="utf-8",
    )
    mcp = PRUSimulatorMCP(config_path=str(config))

    mcp.pru_device_attach("ssi_encoder", core="pru0", config={"name": "axis0"})
    mcp.pru_device_attach("ssi_encoder", core="pru1", config={"name": "axis1"})
    axis0, axis1 = mcp.sim.device_bus.devices

    assert axis0.core_clock_hz == Fraction("200.123456789123456") * 1_000_000
    assert axis1.core_clock_hz == Fraction("201.987654321987654") * 1_000_000


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
