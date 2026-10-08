"""Small host helper for loading and stepping the SSI reader firmware."""
from __future__ import annotations

from math import ceil
from pathlib import Path
from weakref import WeakSet

from pru_io import ssi_config_abi as abi
from pru_io.ssi_encoder_model import SSIEncoderModel


_SOURCE = Path(__file__).resolve().parents[1] / "source"
_FIRMWARE = _SOURCE / "ssi_generic_reader" / "ssi_generic_reader.asm"
_EMULATOR_FIRMWARE = _SOURCE / "ssi_generic_emulator.asm"
_CLOCK_PIN = 0
_DATA_PIN = 16
_EMULATOR_SIMS = WeakSet()  # Simulators with a loaded emulator; it owns the one block


def load_reader(sim, core: str, frame_bits: int, clock_delay_loops: int,
                idle_delay_loops: int) -> None:
    """Pack the reader config into shared memory and load its assembly."""
    sim.memory.write(abi.CONFIG_ADDRESS, abi.pack_config(
        frame_bits=frame_bits,
        clock_delay_loops=clock_delay_loops,
        idle_delay_loops=idle_delay_loops,
    ))
    errors = sim.load(core, _FIRMWARE.read_text(encoding="utf-8"),
                      include_paths=[str(_FIRMWARE.parent.parent)])
    if errors:
        raise ValueError("SSI reader assembly failed: " + "; ".join(errors))


class SSIRuntime:
    """Attach an SSI encoder, load the reader, and inspect its ABI mailbox.

    The helper does not replace simulator execution: ``run_until_frames``
    advances the selected PRU by calling ``Simulator.step`` one instruction
    at a time. ``preset`` names a ``pru_io.ssi_presets`` frame; other
    ``SSIEncoderModel`` options (``resolution``, ``error_bits``, ...)
    override it.
    """

    def __init__(self, sim, core: str = "pru1", position: int = 0,
                 preset: str | None = None, name: str = "ssi_encoder",
                 **encoder_options) -> None:
        self.sim = sim
        self.core = core
        options = {
            "clock_pin": _CLOCK_PIN,
            "data_pin": _DATA_PIN,
            "position": position,
            "core_clock_hz": sim.iep.core_clock_hz(core),
            "name": name,
            **encoder_options,
        }
        self.encoder = (SSIEncoderModel(**options) if preset is None
                        else SSIEncoderModel.from_preset(preset, **options))
        self._data_mask = 1 << _DATA_PIN
        sim.lease_gpio_outputs(core, self._data_mask | (1 << _CLOCK_PIN),
                               self.encoder, drive_mask=1 << _CLOCK_PIN)
        sim.attach_device(core, self.encoder)
        self._closed = False
        self._loaded = False

    def load(self, clock_delay_loops: int = 20,
             idle_delay_loops: int | None = None) -> None:
        """Pack the reader config into shared memory and load its assembly."""
        if idle_delay_loops is None:
            idle_delay_loops = ceil(self.encoder.monoflop_cycles / 2) + 8
        load_reader(self.sim, self.core, self.encoder.resolution,
                    clock_delay_loops, idle_delay_loops)
        self._loaded = True

    def step(self, count: int = 1) -> dict:
        """Execute ordinary PRU instructions on the reader core."""
        if not self._loaded:
            raise RuntimeError("load the SSI reader before stepping it")
        return self.sim.step(self.core, count)

    def mailbox(self) -> dict:
        """Read the latest seqlock mailbox sample from shared memory.

        Adds the combined ``raw_frame`` and the ``position``/``error`` fields
        decoded with the encoder's layout.
        """
        sequence_before = int.from_bytes(
            self.sim.memory_read(abi.MAILBOX_ADDRESS, 4), "little")
        mailbox = abi.unpack_mailbox(
            self.sim.memory_read(abi.MAILBOX_ADDRESS, abi.MAILBOX_SIZE))
        sequence_after = int.from_bytes(
            self.sim.memory_read(abi.MAILBOX_ADDRESS, 4), "little")
        coherent = (sequence_before == mailbox["sequence"] == sequence_after
                    and not sequence_before & 1)
        raw_frame = position = error = None
        if coherent:
            raw_frame = mailbox["raw_frame_lo"] | mailbox["raw_frame_hi"] << 32
            layout = self.encoder
            position = ((raw_frame >> layout.position_offset)
                        & ((1 << layout.position_bits) - 1))
            if layout.encoding == "gray":
                shift = position >> 1
                while shift:
                    position ^= shift
                    shift >>= 1
            error = ((raw_frame >> layout.error_offset)
                     & ((1 << layout.error_bits) - 1))
        return {**mailbox, "coherent": coherent, "raw_frame": raw_frame,
                "position": position, "error": error}

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
            if mailbox["coherent"] and mailbox["frame_count"] >= frame_count:
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


class SSIEmulatorRuntime:
    """Load the SSI emulator firmware and write its host-packed frame word.

    ``layout`` is an unattached ``SSIEncoderModel`` used only to pack the
    position and error fields; the emulator firmware just shifts the word out.
    The firmware re-reads the block at every frame start, so
    ``set_position``/``set_error`` apply to the next frame.
    """

    def __init__(self, sim, core: str = "pru0", position: int = 0,
                 preset: str | None = None, **encoder_options) -> None:
        self.sim = sim
        self.core = core
        options = {"position": position, **encoder_options}
        self.layout = (SSIEncoderModel(**options) if preset is None
                       else SSIEncoderModel.from_preset(preset, **options))
        self.monoflop_ticks = ceil(
            self.layout.monoflop_us * self.sim.iep.active_clock_hz / 1_000_000)
        self._timer_was_cfg = None  # IEP GLOBAL_CFG from before load() enabled it
        self._loaded = False

    def write_config(self) -> None:
        """Pack the current frame into the emulator block in shared memory."""
        frame = self.layout.pack_frame(self.layout.position, self.layout.error)
        self.sim.memory.write(abi.EMULATOR_ADDRESS, abi.pack_emulator_config(
            self.layout.resolution, frame & 0xFFFFFFFF, frame >> 32,
            self.monoflop_ticks))

    def load(self) -> None:
        """Write the emulator block and load its assembly.

        Two emulators on one ``Simulator`` would overwrite each other's block,
        so a second ``load`` raises until the first has been closed. The shared
        IEP is enabled last; a ``load`` that raises leaves it as it found it.
        """
        timer = self.sim.iep
        if self.sim in _EMULATOR_SIMS and not self._loaded:
            raise ValueError("another SSI emulator is loaded on this Simulator; "
                             "they share one emulator block, so close() it first")
        if timer.count_enabled and timer.default_inc != 1:
            raise ValueError("SSI emulator requires IEP DEFAULT_INC=1")
        if timer.cmp0_rst_cnt_en and timer.cmp_enabled(0):
            raise ValueError("SSI emulator requires a free-running IEP without CMP0 reset")
        self.monoflop_ticks = ceil(
            self.layout.monoflop_us * timer.active_clock_hz / 1_000_000)
        if not 1 <= self.monoflop_ticks <= 0xFFFFFFFF:
            raise ValueError("SSI emulator IEP timeout must fit a positive u32")
        self.write_config()
        self.sim.lease_gpio_outputs(
            self.core, (1 << _CLOCK_PIN) | (1 << _DATA_PIN), self,
            drive_mask=1 << _DATA_PIN)
        try:
            errors = self.sim.load(
                self.core, _EMULATOR_FIRMWARE.read_text(encoding="utf-8"),
                include_paths=[str(_EMULATOR_FIRMWARE.parent)])
            if errors:
                raise ValueError("SSI emulator assembly failed: " + "; ".join(errors))
            if not timer.count_enabled:  # last, so no failure above leaves it running
                self._timer_was_cfg = timer.global_cfg
                timer.write32(0x00, (timer.global_cfg & ~0xF0) | 0x11)
        except BaseException:
            self.sim.device_bus.release_core_outputs(self)
            raise
        _EMULATOR_SIMS.add(self.sim)
        self._loaded = True

    def close(self) -> None:
        """Restore the emulator's leased clock/data GPIO directions.

        The IEP is shared, so only a timer that ``load`` enabled is put back
        (CNT_ENABLE and DEFAULT_INC), and only while both are still as
        ``load`` left them. A timer that was already running, one someone else
        has since started, stopped or retuned, and every count, stay as they
        are. Closing also frees the ``Simulator`` for another emulator.
        """
        self.sim.device_bus.release_core_outputs(self)
        if self._timer_was_cfg is not None:
            timer = self.sim.iep
            if (timer.global_cfg & 0xF1) == 0x11:
                timer.write32(0x00, (timer.global_cfg & ~0xF1)
                              | (self._timer_was_cfg & 0xF1))
            self._timer_was_cfg = None
        if self._loaded:
            _EMULATOR_SIMS.discard(self.sim)
            self._loaded = False

    def set_position(self, position: int) -> None:
        self.layout.set_position(position)
        self.write_config()

    def set_error(self, error: int) -> None:
        self.layout.set_error(error)
        self.write_config()

    def status(self) -> int:
        """Return the emulator status: 0 running, 1 halted on invalid config."""
        return abi.unpack_emulator(self.sim.memory_read(
            abi.EMULATOR_ADDRESS, abi.EMULATOR_SIZE))["status"]
