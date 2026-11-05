"""Ruling R16: the SPI firmware files assemble and run as the editor shows them.

The SPI scenario opens source/spi/spi_controller.asm and spi_peripheral.asm
in the editor. Each carries a default-config block (the scenario default:
mode 1, 25 MHz at a 200 MHz core) that render_source() replaces, so the
unmodified text must assemble and pass a frame on its own.
"""
import pytest

import pru_io.spi_link as sl
from pru_io.scenarios import spi_default_row
from pru_io.spi_link import (CMD_RW, CONTROLLER_SOURCE, PATTERNS, PERIPHERAL_SOURCE,
                             SpiConfig, SpiLinkRuntime, render_source, run_test_sequence)
from simulator import Simulator


def _default_cfg():
    row = spi_default_row()
    return SpiConfig(row["mode"], row["core_mhz"], row["half_k"], row["miso_delay"])


def test_default_block_matches_scenario_default():
    cfg = _default_cfg()
    assert (cfg.mode, cfg.core_mhz, cfg.spi_mhz) == (1, 200, 25.0)
    ctrl = CONTROLLER_SOURCE.read_text(encoding="utf-8")
    assert f"SPI_MODE   .set {cfg.mode}\n" in ctrl
    assert f"HALF_K     .set {cfg.half_k}\n" in ctrl
    assert f"MISO_DELAY .set {cfg.miso_delay}\n" in ctrl
    assert f"SPI_MODE   .set {cfg.mode}\n" in PERIPHERAL_SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["controller", "peripheral"])
def test_render_source_replaces_block(role):
    cfg = SpiConfig(0, 300, 3, 7)
    text = render_source(cfg, role)
    assert text.count("SPI_MODE .set 0") == 1
    assert "SPI_MODE   .set 1" not in text          # the default is gone, not shadowed
    assert ("HALF_K .set 3" in text) == (role == "controller")


@pytest.mark.parametrize("path,core", [(CONTROLLER_SOURCE, "pru0"),
                                       (PERIPHERAL_SOURCE, "pru1")])
def test_file_assembles_unmodified(sim_config, path, core):
    sim = Simulator(sim_config(pru_clock_mhz=200, pru1_clock_mhz=200))
    errors = sim.load(core, path.read_text(encoding="utf-8"),
                      include_paths=[str(path.parent)])
    assert errors == []
    # the frame bodies are there (not skipped by an undefined SPI_MODE)
    assert len(sim.cores[core].instructions) > 500


def test_default_config_frame_passes_unmodified(sim_config, monkeypatch):
    """Both files loaded byte for byte as on disk run the acceptance sequence."""
    monkeypatch.setattr(sl, "render_source", lambda cfg, role="controller": (
        CONTROLLER_SOURCE if role == "controller" else PERIPHERAL_SOURCE
    ).read_text(encoding="utf-8"))
    cfg = _default_cfg()
    sim = Simulator(sim_config(pru_clock_mhz=cfg.core_mhz, pru1_clock_mhz=cfg.core_mhz))
    rt = SpiLinkRuntime(sim, cfg)
    rt.load_controller()
    rt.load_peripheral()
    rt.wire()
    c_tx, p_tx = PATTERNS["count"]
    rt.preload_peripheral_tx(4, p_tx)
    res = rt.transfer(CMD_RW, 4, c_tx)
    assert res["miso"] == bytes(3) + p_tx
    assert rt.read_peripheral_rx(4) == c_tx
    seq = run_test_sequence(rt)
    assert seq["ok"] and seq["byte_errors"] == 0
    rt.close()
