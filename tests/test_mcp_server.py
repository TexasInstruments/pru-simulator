"""Tests for MCP Server tool implementations."""
import json
from pathlib import Path

import pytest
from mcp_server.server import PRUSimulatorMCP


SOURCE_DIR = Path(__file__).parent.parent / "source"
AM243X_CONFIG = Path(__file__).parent.parent / "config" / "memory_am243x.cfg"
SSI_READER_SRC = (SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm").read_text()
SSI_EMULATOR_SRC = (SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm").read_text()
FOC_SRC = (SOURCE_DIR / "foc_open_loop" / "foc_open_loop.asm").read_text()

SSI_READER_CLK_PIN = 0
SSI_READER_DATA_PIN = 16
SSI_EMULATOR_CLK_IN_PIN = 8
SSI_EMULATOR_DATA_OUT_PIN = 0


class TestMCPTools:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path=str(AM243X_CONFIG))

    def test_pru_load(self):
        result = self.mcp.pru_load(source="ldi r0, 42\nhalt", core="pru0")
        assert result["success"] is True
        assert result["line_count"] == 2
        assert result["errors"] == []

    def test_pru_step(self):
        self.mcp.pru_load(source="ldi r0, 1\nldi r1, 2\nhalt", core="pru0")
        result = self.mcp.pru_step(core="pru0", count=2)
        assert result["pc"] == 2
        assert result["cycles"] == 2

    def test_pru_registers(self):
        self.mcp.pru_load(source="ldi r0, 0xFF\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_registers(core="pru0")
        assert result["r0"] == "0x000000ff"
        assert result["carry"] is False

    def test_pru_io(self):
        self.mcp.pru_load(source="ldi r30, 5\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_io(core="pru0")
        assert result["gpo_pins"][0] == 1
        assert result["gpo_pins"][2] == 1
        assert len(result["gpo_pins"]) == 20

    def test_pru_set_input(self):
        self.mcp.pru_set_input(core="pru0", pin=5, value=True)
        self.mcp.pru_load(source="mov r0, r31\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        regs = self.mcp.pru_registers(core="pru0")
        assert int(regs["r0"], 16) == (1 << 5)

    def test_pru_reset(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        self.mcp.pru_reset(core="pru0")
        regs = self.mcp.pru_registers(core="pru0")
        assert regs["r0"] == "0x00000000"

    def test_pru_status(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=2)
        status = self.mcp.pru_status()
        assert "pru0" in status["cores"]
        assert status["cores"]["pru0"]["halted"] is True

    def test_pru_run_until(self):
        self.mcp.pru_load(source="ldi r0, 1\nldi r1, 2\nhalt", core="pru0")
        result = self.mcp.pru_run_until(core="pru0")
        assert result["reason"] == "halted"

    def test_pru_memory(self):
        self.mcp.pru_load(source="ldi r1, 0\nldi r2, 0xABCD\nsbbo &r2, r1, 0, 4\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=3)
        result = self.mcp.pru_memory(addr=0, length=4)
        assert "cd" in result["hex_dump"] or "ab" in result["hex_dump"]

    def test_pru_breakpoint(self):
        result = self.mcp.pru_breakpoint(core="pru0", address=5)
        assert "id" in result

    def test_pru_ssi_inject(self):
        reader = (
            Path(__file__).parent.parent
            / "source"
            / "ssi_reader_4mhz_12bit"
            / "ssi_reader_4mhz_12bit.asm"
        ).read_text()
        result = self.mcp.pru_ssi_inject(
            source=reader, value=0xABC, bits=12, max_steps=20_000
        )
        assert result["match"] is True
        assert result["captured"] == 0xABC
        assert result["frames_captured"] >= 1

    def test_pru_foc_inject_bootstraps_runtime_and_returns_shared_state(self):
        result = self.mcp.pru_foc_inject(
            source="halt",
            speed_rpm=400.0,
            id_ref=0.0,
            iq_ref=0.25,
            ramp_rate=0.02,
            max_steps=2,
        )

        assert result["status"] == "success"
        assert result["control"]["speed_ref_q24"] == round(
            0.4 * (1 << 24)
        )
        assert result["control"]["iq_ref_q24"] == round(
            0.25 * (1 << 24)
        )
        assert "pwm" in result and "fb" in result

    def test_pru_foc_inject_loads_real_assembly_with_generated_include(self):
        result = self.mcp.pru_foc_inject(
            source=FOC_SRC,
            speed_rpm=400.0,
            iq_ref=0.25,
            ramp_rate=0.01,
            max_steps=2_000,
        )

        assert result["status"] == "success"
        assert result["clock"]["loop_frequency_hz"] == 100_000
        assert result["fault"] is None

    def test_pru_i2c_attach(self):
        result = self.mcp.pru_i2c_attach(core="pru0", enabled=True, address=0x23)
        assert result["success"] is True
        assert result["address"] == 0x23
        io_state = self.mcp.sim.i2c_state("pru0")
        assert io_state["address"] == 0x23
        assert io_state["config_reg"] == 0xFF

        detach = self.mcp.pru_i2c_attach(core="pru0", enabled=False)
        assert detach["success"] is True
        assert self.mcp.sim.i2c_state("pru0") is None

    # ------------------------------------------------------------------
    # SSI runtime tools (ssi_profile_list/ssi_stage/ssi_apply/
    # ssi_read_mailbox/ssi_read_trace).
    # ------------------------------------------------------------------

    def _load_ssi_paired(self):
        """Load+wire+hard_reset the generic SSI emulator (pru0) / reader
        (pru1) pair, matching pru_io/ssi_runtime.py's constructor
        precondition. There is no dedicated MCP tool for wiring/hard_reset,
        so this test bootstrapping goes straight through self.mcp.sim (the
        same Simulator every other test in this file already reaches for,
        e.g. test_pru_i2c_attach's self.mcp.sim.i2c_state()); every SSI
        config/capture operation after this point goes through the ssi_*
        MCP tool methods only."""
        assert self.mcp.pru_load(
            source=SSI_READER_SRC, core="pru1", include_paths=[str(SOURCE_DIR)]
        )["success"]
        assert self.mcp.pru_load(
            source=SSI_EMULATOR_SRC, core="pru0", include_paths=[str(SOURCE_DIR)]
        )["success"]
        self.mcp.sim.add_gpio_wire(
            "pru1", SSI_READER_CLK_PIN, "pru0", SSI_EMULATOR_CLK_IN_PIN
        )
        self.mcp.sim.add_gpio_wire(
            "pru0", SSI_EMULATOR_DATA_OUT_PIN, "pru1", SSI_READER_DATA_PIN
        )
        self.mcp.sim.hard_reset()

    def test_ssi_profile_list(self):
        result = self.mcp.ssi_profile_list()
        assert "CUSTOM_LEGACY_12BIT_4MHZ" in result["profiles"]
        assert "AHS_AHM36_SINGLETURN" in result["profiles"]

    def test_ssi_stage_rejects_unknown_profile(self):
        self._load_ssi_paired()
        result = self.mcp.ssi_stage(profile="NOT_A_REAL_PROFILE")
        assert result["status"] == "error"
        assert "NOT_A_REAL_PROFILE" in result["error"]

    def test_ssi_stage_apply_read_mailbox_and_trace_end_to_end(self):
        """Profile switch (to reader-only + trace capture) plus several
        captured frames, driven entirely through ssi_stage/ssi_apply/
        ssi_read_mailbox/ssi_read_trace -- confirms the whole staged-apply
        + mailbox/trace read-back path works through this MCP API surface
        alone."""
        self._load_ssi_paired()

        stage_result = self.mcp.ssi_stage(
            profile="CUSTOM_LEGACY_12BIT_4MHZ",
            overrides_json=json.dumps({"topology": 1, "capture_mode": 2}),
        )
        assert stage_result["status"] == "success"
        assert stage_result["staged"]["topology"] == 1
        assert stage_result["staged"]["capture_mode"] == 2

        apply_result = self.mcp.ssi_apply()
        assert apply_result["status"] == "success"
        assert apply_result["pru1_ack_generation"] == apply_result["requested_generation"]

        # topology=1 is reader-only from here on; pru0 is never stepped
        # again. Advance pru1 alone until a few frames have been captured.
        mb = None
        for _ in range(4000):
            self.mcp.pru_step(core="pru1", count=500)
            mb = self.mcp.ssi_read_mailbox()
            if mb["frame_counter"] >= 3:
                break
        assert mb is not None and mb["frame_counter"] >= 3

        trace_result = self.mcp.ssi_read_trace(newest_first=True, limit=10)
        assert trace_result["overrun_count"] == 0
        assert trace_result["write_index"] == mb["frame_counter"]
        assert len(trace_result["records"]) >= 1
        assert trace_result["records"][0]["timestamp_cycles"] > 0

    def test_ssi_set_positions_uses_the_shared_packing_path(self):
        self._load_ssi_paired()
        result = self.mcp.ssi_set_positions(
            positions_json=json.dumps(["ABC", "12A"])
        )
        assert result["status"] == "success"
        assert result["frames"] == [0xABC, 0x12A]

    def test_ssi_timestamped_producer_controls_run_through_mcp(self):
        self._load_ssi_paired()
        assert self.mcp.ssi_stage(
            profile="CUSTOM_LEGACY_12BIT_4MHZ",
            overrides_json=json.dumps({
                "producer_mode": 1,
                "producer_sample_age_limit_iep_ticks": 100_000,
                "producer_prediction_horizon_limit_iep_ticks": 100_000,
            }),
        )["status"] == "success"
        assert self.mcp.ssi_apply()["status"] == "success"

        configured = self.mcp.ssi_producer_configure(
            trajectory="linear",
            initial_position="0x400",
            velocity_counts_per_second=250_000.0,
            period_iep_ticks=288,
        )
        assert configured["status"] == "success"
        assert configured["producer"]["trajectory"] == "linear"
        assert self.mcp.ssi_producer_start()["running"] is True

        mailbox = None
        for step in range(30_000):
            self.mcp.sim.step_paced("pru1", "pru0")
            if step % 100 == 0:
                mailbox = self.mcp.ssi_read_mailbox()
                if mailbox["frame_counter"] >= 4:
                    break

        state = self.mcp.ssi_producer_read()
        assert mailbox is not None and mailbox["frame_counter"] >= 4
        assert mailbox["position_value"] != 0xABC
        assert state["running"] is True
        assert state["published_count"] > 1
        assert state["diagnostics"]["accepted_count"] >= 2
        assert state["diagnostics"]["status"] == 0
        assert self.mcp.ssi_producer_stop()["running"] is False
