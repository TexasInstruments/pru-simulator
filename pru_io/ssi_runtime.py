"""Small host helper for loading and stepping the SSI reader firmware."""
from __future__ import annotations

from math import ceil
from pathlib import Path

from pru_io import ssi_config_abi as abi
from pru_io.ssi_encoder_model import SSIEncoderModel


_FIRMWARE = (Path(__file__).resolve().parents[1]
             / "source" / "ssi_generic_reader" / "ssi_generic_reader.asm")
_CLOCK_PIN = 0
_DATA_PIN = 16


class SSIRuntime:
    """Attach an SSI encoder, load the reader, and inspect its ABI mailbox.

    The helper does not replace simulator execution: ``run_until_frames``
    advances the selected PRU by calling ``Simulator.step`` one instruction
    at a time.
    """

    def __init__(self, sim, core: str = "pru1", position: int = 0,
                 resolution: int = 12, encoding: str = "binary",
                 f_max_hz=4_000_000, monoflop_us=20.5,
                 name: str = "ssi_encoder") -> None:
        self.sim = sim
        self.core = core
        core_clock_hz = sim.iep.core_clock_hz(core)
        self.encoder = SSIEncoderModel(
            clock_pin=_CLOCK_PIN,
            data_pin=_DATA_PIN,
            position=position,
            resolution=resolution,
            encoding=encoding,
            f_max_hz=f_max_hz,
            monoflop_us=monoflop_us,
            core_clock_hz=core_clock_hz,
            name=name,
        )
        self._data_mask = 1 << _DATA_PIN
        sim.lease_gpio_outputs(core, self._data_mask, self.encoder)
        sim.attach_device(core, self.encoder)
        self._closed = False
        self._loaded = False

    def load(self, clock_delay_loops: int = 20,
             idle_delay_loops: int | None = None) -> None:
        """Pack the reader config into shared memory and load its assembly."""
        if idle_delay_loops is None:
            idle_delay_loops = ceil(self.encoder.monoflop_cycles / 2) + 8
        config = abi.pack_config(
            frame_bits=self.encoder.resolution,
            clock_delay_loops=clock_delay_loops,
            idle_delay_loops=idle_delay_loops,
        )
        self.sim.memory.write(abi.CONFIG_ADDRESS, config)
        firmware = _FIRMWARE.read_text(encoding="utf-8")
        errors = self.sim.load(
            self.core, firmware, include_paths=[str(_FIRMWARE.parent.parent)])
        if errors:
            raise ValueError("SSI reader assembly failed: " + "; ".join(errors))
        self._loaded = True

    def step(self, count: int = 1) -> dict:
        """Execute ordinary PRU instructions on the reader core."""
        if not self._loaded:
            raise RuntimeError("load the SSI reader before stepping it")
        return self.sim.step(self.core, count)

    def mailbox(self) -> dict:
        """Read the latest seqlock mailbox sample from shared memory."""
        return abi.unpack_mailbox(
            self.sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))

    def run_until_frames(self, frame_count: int, max_steps: int = 20_000) -> dict:
        """Step until the mailbox reaches ``frame_count`` or the budget ends."""
        if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count < 0:
            raise ValueError("frame_count must be a non-negative integer")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 0:
            raise ValueError("max_steps must be a non-negative integer")
        if not self._loaded:
            raise RuntimeError("load the SSI reader before running it")
        for steps in range(max_steps + 1):
            mailbox = self.mailbox()
            if mailbox["frame_count"] >= frame_count:
                return {"reached": True, "steps": steps, "mailbox": mailbox}
            if steps == max_steps:
                break
            self.sim.step(self.core, 1)
        return {"reached": False, "steps": max_steps,
                "mailbox": self.mailbox()}

    def close(self) -> None:
        """Detach the model and restore the reader's data-pin direction."""
        if self._closed:
            return
        self.sim.detach_device(self.encoder)
        self._closed = True

    def __enter__(self) -> "SSIRuntime":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
