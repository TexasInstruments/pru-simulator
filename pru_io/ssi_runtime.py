"""SSIRuntime -- the Python stand-in for real R5 firmware.

Real R5 firmware will eventually own: a table of named SICK SSI encoder
profiles, staged-then-validated configuration changes, an atomic commit into
the shared-memory config block described by ``ssi_config_abi.py`` /
``ssi_config_abi.inc``, and semantic decode of what the PRU1 reader publishes
into the mailbox. This module plays that role for the simulator. Neither
``ssi_generic_emulator.asm`` (PRU0) nor ``ssi_generic_reader.asm`` (PRU1) can
tell the difference between this module and real R5 firmware -- both PRU
programs only ever read/write the shared-memory config block, frame slots,
and mailbox; they have no idea who is on the other end.

Precondition on ``SSIRuntime(sim)``: Tasks 3/4's PRU programs have no
power-on-reset default of their own -- they just read whatever is in shared
memory, and a fresh ``sim.hard_reset()`` zeroes it (a degenerate, not-12-bit
config: e.g. ``frame_width_bits=0``). So the "default profile is 12-bit/
4 MHz" guarantee is this module's responsibility: the constructor immediately
stages and applies ``CUSTOM_LEGACY_12BIT_4MHZ``, which means it *blocks*
(via ``wait_for_apply``) until the loaded PRU core(s) ack it. Whichever PRU
core(s) the caller intends to use must already be loaded (``sim.load(...)``),
wired (``sim.add_gpio_wire(...)``), and ``sim.hard_reset()``-ed *before*
constructing ``SSIRuntime`` -- exactly the same order every existing
Tasks 3/4 test already uses before poking config bytes directly. Constructing
on a `sim` with no PRU core loaded at all raises ``TimeoutError`` from inside
``__init__`` (nothing will ever ack), the same way ``apply()`` would.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from pru_io import ssi_config_abi as abi
from pru_io.ssi_position_producer import SSIPositionProducer


# Real (non-header, non-handshake) config-block fields a Profile describes.
# Excludes abi_version/struct_size/requested_generation/pru0_ack_generation/
# pru1_ack_generation -- those are ABI metadata and generation-handshake
# fields owned by apply()/the PRU programs, not part of an encoder "shape".
_REAL_CONFIG_FIELD_NAMES = (
    "topology",
    "encoding_type",
    "alignment",
    "formation_mode",
    "frame_width_bits",
    "position_offset_bits",
    "position_width_bits",
    "singleturn_width_bits",
    "multiturn_width_bits",
    "error_offset_bits",
    "error_width_bits",
    "padding_width_bits",
    "clock_high_cycles",
    "clock_low_cycles",
    "sample_delay_cycles",
    "tv_cycles",
    "tm_pause_outer_iters",
    "tp_pause_outer_iters",
    "formation_pause_outer_iters",
    "sequence_hold_mode",
    "fault_mode",
    "capture_mode",
    "sequence_hold_count",
    "fault_argument",
    "fault_repeat_count",
    "producer_mode",
    "producer_period_iep_ticks",
    "producer_sample_age_limit_iep_ticks",
    "producer_prediction_horizon_limit_iep_ticks",
)

# Sentinel for "no error field" (matches SSI_CONFIG_ABI's error_offset_bits
# documentation: 0xFFFF if none).
NO_ERROR_FIELD = 0xFFFF

# Single-loop hardware ceiling (LOOP instruction caps at 256 iterations);
# see the design doc's "Units convention".
_SINGLE_LOOP_MAX = 256
# The generic reader's fixed per-bit work outside the high/low LOOP bodies:
# balanced data selection (3), 64-bit shift (5), and edge/loop bookkeeping
# (7). Keep this in the control plane so the UI reports the waveform that the
# deterministic PRU loop actually generates.
CLOCK_LOOP_OVERHEAD_CYCLES = 15


@dataclass(frozen=True)
class Profile:
    """One named SICK SSI encoder family's config-block field values.

    ``max_clock_hz`` is not a config-block field -- it is this profile's
    informational validation ceiling, enforced by ``SSIRuntime.stage()``.
    """

    name: str
    # Bit-width/offset fields: no shared default across families, always
    # given an explicit value per profile.
    frame_width_bits: int
    position_offset_bits: int
    position_width_bits: int
    singleturn_width_bits: int
    multiturn_width_bits: int
    error_offset_bits: int
    error_width_bits: int
    padding_width_bits: int
    # Timing/mode fields: shared defaults per the task brief, overridden per
    # profile where the family's docs (or lack thereof) call for it.
    topology: int = 0
    encoding_type: int = 0
    alignment: int = 0
    formation_mode: int = 0
    clock_high_cycles: int = 100
    clock_low_cycles: int = 100
    sample_delay_cycles: int = 50
    tv_cycles: int = 20
    tm_pause_outer_iters: int = 15
    tp_pause_outer_iters: int = 20
    formation_pause_outer_iters: int = 15
    sequence_hold_mode: int = 0
    fault_mode: int = 0
    capture_mode: int = 0
    sequence_hold_count: int = 1
    fault_argument: int = 0
    fault_repeat_count: int = 0
    producer_mode: int = abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE
    producer_period_iep_ticks: int = abi.DEFAULT_PRODUCER_PERIOD_IEP_TICKS
    producer_sample_age_limit_iep_ticks: int = (
        abi.DEFAULT_PRODUCER_SAMPLE_AGE_LIMIT_IEP_TICKS
    )
    producer_prediction_horizon_limit_iep_ticks: int = (
        abi.DEFAULT_PRODUCER_PREDICTION_HORIZON_LIMIT_IEP_TICKS
    )
    max_clock_hz: int = 1_500_000

    def as_dict(self) -> dict[str, int]:
        """This profile's real config-block fields as a plain dict."""
        return {name: getattr(self, name) for name in _REAL_CONFIG_FIELD_NAMES}


# ---------------------------------------------------------------------------
# Named profiles (SICK SSI interface families).
#
# Bit-width/error-bit-count values come directly from the SICK SSI interface
# datasheet sections reviewed earlier in this project and must not be
# changed. Timing values (clock_high/low_cycles, pause-outer counts) are
# this task's own reasonable choices within the ABI's [1,256] single-loop
# ceiling, since the datasheet excerpts available here did not give literal
# PRU-cycle timing for every family.
#
# NOTE on CUSTOM_LEGACY_12BIT_4MHZ: two of this profile's values were
# adjusted from the brief's literal numbers because the *unmodified* values
# fail this module's own stage() validation, and stage()ing this exact
# profile is what SSIRuntime.__init__ does unconditionally on every
# construction -- see the report for the full justification:
#   - clock_high_cycles=29/clock_low_cycles=31 are the generic LOOP-body
#     counts. Including the fixed 15-cycle hot path gives 75 cycles/bit and
#     exactly 4,000,000 Hz at a 300 MHz PRU clock.
#   - tp_pause_outer_iters is raised from 15 to 16 so it is strictly greater
#     than tm_pause_outer_iters=15, per stage()'s own validation rule; the
#     fixed reference programs use 15/15 (equal), which the brief's table
#     copies, but stage()'s "tp > tm" rule (also specified by the same
#     brief) rejects equal values. 16*250=4000 cycles vs 15*250=3750 is a
#     ~6.7% timing change, negligible next to the frame's own ~4 MHz bit
#     timing and well within "moderate, clearly-documented default" territory.
# ---------------------------------------------------------------------------

AHS_AHM36_SINGLETURN = Profile(
    name="AHS_AHM36_SINGLETURN",
    frame_width_bits=15,
    position_offset_bits=0,
    position_width_bits=14,
    singleturn_width_bits=14,
    multiturn_width_bits=0,
    error_offset_bits=14,
    error_width_bits=1,
    padding_width_bits=0,
)

AHS_AHM36_MULTITURN = Profile(
    name="AHS_AHM36_MULTITURN",
    frame_width_bits=27,
    position_offset_bits=0,
    position_width_bits=26,
    singleturn_width_bits=14,
    multiturn_width_bits=12,
    error_offset_bits=26,
    error_width_bits=1,
    padding_width_bits=0,
)

AFS_AFM60_SINGLETURN = Profile(
    name="AFS_AFM60_SINGLETURN",
    frame_width_bits=21,
    position_offset_bits=0,
    position_width_bits=18,
    singleturn_width_bits=18,
    multiturn_width_bits=0,
    error_offset_bits=18,
    error_width_bits=3,
    padding_width_bits=0,
)

AFS_AFM60_MULTITURN_30BIT = Profile(
    name="AFS_AFM60_MULTITURN_30BIT",
    frame_width_bits=33,
    position_offset_bits=0,
    position_width_bits=30,
    singleturn_width_bits=18,
    multiturn_width_bits=12,
    error_offset_bits=30,
    error_width_bits=3,
    padding_width_bits=0,
)

AFS_AFM60_MULTITURN_27BIT = Profile(
    name="AFS_AFM60_MULTITURN_27BIT",
    frame_width_bits=30,
    position_offset_bits=0,
    position_width_bits=27,
    singleturn_width_bits=15,
    multiturn_width_bits=12,
    error_offset_bits=27,
    error_width_bits=3,
    padding_width_bits=0,
)

AFS_AFM60S_PRO_SINGLETURN = Profile(
    name="AFS_AFM60S_PRO_SINGLETURN",
    frame_width_bits=21,
    position_offset_bits=0,
    position_width_bits=18,
    singleturn_width_bits=18,
    multiturn_width_bits=0,
    error_offset_bits=18,
    error_width_bits=3,
    padding_width_bits=0,
)

AFS_AFM60S_PRO_MULTITURN = Profile(
    name="AFS_AFM60S_PRO_MULTITURN",
    frame_width_bits=28,
    position_offset_bits=0,
    position_width_bits=25,
    singleturn_width_bits=13,
    multiturn_width_bits=12,
    error_offset_bits=25,
    error_width_bits=3,
    padding_width_bits=0,
)

ATM60_90 = Profile(
    name="ATM60_90",
    frame_width_bits=26,
    position_offset_bits=0,
    position_width_bits=25,
    singleturn_width_bits=12,
    multiturn_width_bits=13,
    error_offset_bits=25,
    error_width_bits=1,
    padding_width_bits=0,
    encoding_type=3,  # tannenbaum
    formation_mode=1,  # synchronous
    tm_pause_outer_iters=180,  # ~150 us sync monoflop
    tp_pause_outer_iters=200,
    formation_pause_outer_iters=180,
    max_clock_hz=1_500_000,  # documented sync-mode ceiling; clock_high/low
                             # stay at the 100/100 common default, already
                             # exactly at this ceiling
)

ARS60_SHORT = Profile(
    name="ARS60_SHORT",
    frame_width_bits=13,
    position_offset_bits=0,
    position_width_bits=13,
    singleturn_width_bits=13,
    multiturn_width_bits=0,
    error_offset_bits=NO_ERROR_FIELD,  # no error field in this family
    error_width_bits=0,
    padding_width_bits=0,
)

ARS60_LONG = Profile(
    name="ARS60_LONG",
    frame_width_bits=17,
    position_offset_bits=0,
    position_width_bits=15,
    singleturn_width_bits=15,
    multiturn_width_bits=0,
    error_offset_bits=15,
    error_width_bits=2,
    padding_width_bits=0,
)

TTK70 = Profile(
    name="TTK70",
    frame_width_bits=26,
    position_offset_bits=0,
    position_width_bits=24,
    singleturn_width_bits=24,
    multiturn_width_bits=0,
    error_offset_bits=24,
    error_width_bits=2,
    padding_width_bits=0,
)

KH53 = Profile(
    name="KH53",
    frame_width_bits=24,
    position_offset_bits=0,
    position_width_bits=24,
    singleturn_width_bits=24,
    multiturn_width_bits=0,
    error_offset_bits=NO_ERROR_FIELD,  # no error field in this family
    error_width_bits=0,
    padding_width_bits=0,
)

CUSTOM_LEGACY_12BIT_4MHZ = Profile(
    name="CUSTOM_LEGACY_12BIT_4MHZ",
    frame_width_bits=12,
    position_offset_bits=0,
    position_width_bits=12,
    singleturn_width_bits=12,
    multiturn_width_bits=0,
    error_offset_bits=12,
    error_width_bits=0,
    padding_width_bits=0,
    clock_high_cycles=29,
    clock_low_cycles=31,  # 29+31 LOOP cycles plus 15 fixed cycles = 75
    sample_delay_cycles=15,
    tv_cycles=10,
    tm_pause_outer_iters=15,
    tp_pause_outer_iters=16,  # see module-level NOTE above (brief says 15)
    max_clock_hz=4_000_000,
)

_POSITION_METADATA_FIELDS = (
    "encoding_type",
    "alignment",
    "frame_width_bits",
    "position_offset_bits",
    "position_width_bits",
    "singleturn_width_bits",
    "multiturn_width_bits",
    "error_offset_bits",
    "error_width_bits",
    "padding_width_bits",
)

DEFAULT_RUNTIME_FRAMES = (0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2)


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        AHS_AHM36_SINGLETURN,
        AHS_AHM36_MULTITURN,
        AFS_AFM60_SINGLETURN,
        AFS_AFM60_MULTITURN_30BIT,
        AFS_AFM60_MULTITURN_27BIT,
        AFS_AFM60S_PRO_SINGLETURN,
        AFS_AFM60S_PRO_MULTITURN,
        ATM60_90,
        ARS60_SHORT,
        ARS60_LONG,
        TTK70,
        KH53,
        CUSTOM_LEGACY_12BIT_4MHZ,
    )
}


class SSIRuntime:
    """Plays the role real R5 firmware will eventually play: profiles,
    staged-then-validated configuration, atomic apply, and position decode.
    """

    def __init__(self, sim):
        self.sim = sim
        self._staged: dict[str, int] | None = None
        self._active_profile: Profile | None = None
        self._staged_profile: Profile | None = None
        self._active_config: dict[str, int] | None = None
        # The simulator-side producer is the same control-plane boundary that
        # the R5 firmware will expose.  It is deliberately constructed in
        # static mode; Apply never starts it implicitly.
        self.producer = SSIPositionProducer(sim)
        # Gray-excess selects a product-specific window in the full Gray
        # code. The fixed shared ABI carries the wire frame, while this
        # host-side metadata keeps packing and semantic readback paired.
        self._position_count: int | None = None
        self._gray_excess_offset: int | None = None
        self._active_position_count: int | None = None
        self._active_gray_excess_offset: int | None = None
        # Tasks 3/4's PRU programs have no sensible behavior on raw zeroed
        # shared memory (a fresh hard_reset() leaves e.g. frame_width_bits=0)
        # -- this module is responsible for the "default profile is 12-bit/
        # 4 MHz" guarantee, not the PRU programs. This blocks until whichever
        # core(s) are loaded ack it; see the module docstring's precondition.
        self.stage(CUSTOM_LEGACY_12BIT_4MHZ)
        # Populate slots before publishing generation 1. This prevents a
        # just-loaded pair from clocking zero/sentinel frames while the host
        # is still installing the documented default sequence.
        self.set_raw_frames(list(DEFAULT_RUNTIME_FRAMES))
        self.apply()

    # ------------------------------------------------------------------
    # Stage
    # ------------------------------------------------------------------

    def stage(self, profile: "Profile | str | None" = None, **overrides) -> None:
        """Validate and stage a config-field dict; nothing touches shared
        memory here (that's apply()'s job).

        ``profile`` may be a Profile instance, a name string looked up in
        PROFILES, or None (stage on top of whatever was staged/applied last,
        for a single-field tweak without re-specifying a whole profile).
        """
        if profile is None:
            resolved_profile = getattr(self, "_staged_profile", None)
            if resolved_profile is None:
                resolved_profile = self._active_profile
            base = dict(self._staged) if self._staged is not None else {}
        elif isinstance(profile, str):
            if profile not in PROFILES:
                raise ValueError(
                    f"unknown profile name: {profile!r} "
                    f"(known: {sorted(PROFILES)})"
                )
            resolved_profile = PROFILES[profile]
            base = resolved_profile.as_dict()
        elif isinstance(profile, Profile):
            resolved_profile = profile
            base = resolved_profile.as_dict()
        else:
            raise TypeError(
                f"profile must be a Profile, str, or None, got {type(profile)!r}"
            )

        for key in overrides:
            if key not in abi._CONFIG_FIELDS:
                raise ValueError(f"unknown config field: {key!r}")

        fields = dict(base)
        fields.update(overrides)

        for name in ("clock_high_cycles", "clock_low_cycles",
                     "sample_delay_cycles", "tv_cycles"):
            value = fields.get(name, 0)
            if not (1 <= value <= _SINGLE_LOOP_MAX):
                raise ValueError(
                    f"{name}={value} out of the single-loop hardware range "
                    f"[1, {_SINGLE_LOOP_MAX}]"
                )

        clock_high = fields["clock_high_cycles"]
        sample_delay = fields["sample_delay_cycles"]
        if not (0 < sample_delay < clock_high):
            raise ValueError(
                f"sample_delay_cycles={sample_delay} must be > 0 and "
                f"< clock_high_cycles={clock_high}"
            )

        tv_cycles = fields["tv_cycles"]
        if not (tv_cycles < sample_delay):
            raise ValueError(
                f"tv_cycles={tv_cycles} must be < "
                f"sample_delay_cycles={sample_delay}"
            )

        tm_pause = fields.get("tm_pause_outer_iters", 0)
        tp_pause = fields.get("tp_pause_outer_iters", 0)
        formation_pause = fields.get("formation_pause_outer_iters", 0)
        for name, value in (
            ("tm_pause_outer_iters", tm_pause),
            ("tp_pause_outer_iters", tp_pause),
            ("formation_pause_outer_iters", formation_pause),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError(f"{name} must be an unsigned 32-bit integer")
        if not (tp_pause > tm_pause):
            raise ValueError(
                f"tp_pause_outer_iters={tp_pause} must be > "
                f"tm_pause_outer_iters={tm_pause}"
            )

        if resolved_profile is not None:
            clock_low = fields["clock_low_cycles"]
            derived_hz = 300_000_000 / (
                clock_high + clock_low + CLOCK_LOOP_OVERHEAD_CYCLES
            )
            if derived_hz > resolved_profile.max_clock_hz:
                raise ValueError(
                    f"derived clock frequency {derived_hz:.0f} Hz exceeds "
                    f"profile {resolved_profile.name!r}'s "
                    f"max_clock_hz={resolved_profile.max_clock_hz}"
                )

        frame_width = fields.get("frame_width_bits", 0)
        if isinstance(frame_width, bool) or not isinstance(frame_width, int):
            raise ValueError("frame_width_bits must be an integer")
        if not 1 <= frame_width <= 64:
            raise ValueError(
                f"frame_width_bits={frame_width} must be in the 1..64-bit "
                f"hardware range"
            )

        alignment = fields.get("alignment", 0)
        if alignment not in (0, 1):
            raise ValueError(
                f"alignment={alignment} must be 0 (left) or 1 (right)"
            )

        def nonnegative_int(name: str) -> int:
            value = fields.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
            return value

        position_offset = nonnegative_int("position_offset_bits")
        position_width = nonnegative_int("position_width_bits")
        singleturn_width = nonnegative_int("singleturn_width_bits")
        multiturn_width = nonnegative_int("multiturn_width_bits")
        error_width = nonnegative_int("error_width_bits")
        padding_width = nonnegative_int("padding_width_bits")
        if not 1 <= position_width <= 64:
            raise ValueError(
                f"position_width_bits={position_width} must be in the 1..64-bit "
                f"hardware range"
            )
        if singleturn_width + multiturn_width != position_width:
            raise ValueError(
                "singleturn_width_bits+multiturn_width_bits must equal "
                "position_width_bits"
            )
        if error_width > 0xFFFF:
            raise ValueError("error_width_bits exceeds the ABI field width")

        effective_position_offset = position_offset
        if alignment == 1 and position_offset == 0:
            effective_position_offset = (
                frame_width - position_width - error_width - padding_width
            )
        position_end = effective_position_offset + position_width
        required = position_width + error_width + padding_width
        if effective_position_offset < 0 or position_end > frame_width:
            raise ValueError(
                f"position field does not fit in the {frame_width}-bit SSI "
                f"frame (required field span: {required} bits)"
            )

        error_offset = fields.get("error_offset_bits", NO_ERROR_FIELD)
        if error_width:
            if error_offset == NO_ERROR_FIELD:
                raise ValueError(
                    "error_offset_bits is required when error_width_bits is non-zero"
                )
            if not isinstance(error_offset, int) or error_offset < 0:
                raise ValueError("error_offset_bits must be a valid bit offset")
            error_end = error_offset + error_width
            if error_end > frame_width:
                raise ValueError("error field does not fit in the SSI frame")
            if error_offset < position_end and error_end > effective_position_offset:
                raise ValueError("position and error fields overlap")
        elif error_offset != NO_ERROR_FIELD and error_offset < 0:
            raise ValueError("error_offset_bits must be non-negative or 0xFFFF")

        for name, maximum in (
            ("topology", 1),
            ("encoding_type", 3),
            ("formation_mode", 1),
            ("sequence_hold_mode", 1),
            ("fault_mode", 8),
            ("capture_mode", 2),
        ):
            value = fields.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ValueError(f"{name}={value!r} is outside the supported range 0..{maximum}")

        producer_mode = fields.get(
            "producer_mode", abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE
        )
        if producer_mode not in (
            abi.SSI_PRODUCER_MODE_STATIC_SEQUENCE,
            abi.SSI_PRODUCER_MODE_TIMESTAMPED,
        ):
            raise ValueError(f"producer_mode={producer_mode!r} is unsupported")
        for name in (
            "producer_period_iep_ticks",
            "producer_sample_age_limit_iep_ticks",
            "producer_prediction_horizon_limit_iep_ticks",
        ):
            value = fields.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF_FFFF:
                raise ValueError(f"{name} must be an unsigned 32-bit integer")
        if fields["producer_period_iep_ticks"] == 0:
            raise ValueError("producer_period_iep_ticks must be non-zero")

        if fields["formation_mode"] == 1 and tp_pause <= formation_pause:
            raise ValueError(
                "tp_pause_outer_iters must be greater than "
                "formation_pause_outer_iters in synchronous formation mode"
            )

        previous = self._staged
        if previous is not None and any(
            fields.get(name) != previous.get(name)
            for name in _POSITION_METADATA_FIELDS
        ):
            # Position-count/Gray-excess metadata belongs to the staged
            # packing layout. Do not carry it into a different wire layout;
            # the already-applied metadata remains untouched until commit.
            self._position_count = None
            self._gray_excess_offset = None

        self._staged = fields
        self._staged_profile = resolved_profile

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, timeout_steps: int = 200_000) -> None:
        """Commit the staged config atomically: write everything except
        requested_generation, then bump requested_generation last (the
        commit signal), then block until both PRU cores ack it.
        """
        if self._staged is None:
            raise RuntimeError("apply() called before stage()")

        current_gen = int.from_bytes(
            self.sim.memory_read(
                abi.CONFIG_BASE + abi.CONFIG_REQUESTED_GENERATION_OFF, 4
            ),
            "little",
        )
        pru0_ack = int.from_bytes(
            self.sim.memory_read(
                abi.CONFIG_BASE + abi.CONFIG_PRU0_ACK_GENERATION_OFF, 4
            ),
            "little",
        )
        pru1_ack = int.from_bytes(
            self.sim.memory_read(
                abi.CONFIG_BASE + abi.CONFIG_PRU1_ACK_GENERATION_OFF, 4
            ),
            "little",
        )

        fields = dict(self._staged)
        fields["abi_version"] = 1
        fields["struct_size"] = 256
        fields["requested_generation"] = current_gen  # unchanged in this write
        fields["pru0_ack_generation"] = pru0_ack       # preserve, don't clobber
        fields["pru1_ack_generation"] = pru1_ack

        self.sim.memory.write(abi.CONFIG_BASE, abi.pack_config(**fields))

        new_gen = current_gen + 1
        self.sim.memory.write(
            abi.CONFIG_BASE + abi.CONFIG_REQUESTED_GENERATION_OFF,
            new_gen.to_bytes(4, "little"),
        )

        self.wait_for_apply(timeout_steps)
        # The PRUs have acknowledged the generation, so this is the only
        # point at which staged semantic metadata becomes active readback
        # metadata. A staged edit must never reinterpret an older mailbox or
        # trace record before Apply completes.
        self._active_config = dict(fields)
        self._active_profile = getattr(self, "_staged_profile", None)
        self._active_position_count = getattr(self, "_position_count", None)
        self._active_gray_excess_offset = getattr(
            self, "_gray_excess_offset", None
        )
        producer = getattr(self, "producer", None)
        if producer is not None:
            producer.configure(
                mode=fields["producer_mode"],
                period_iep_ticks=fields["producer_period_iep_ticks"],
                sample_age_limit_iep_ticks=fields[
                    "producer_sample_age_limit_iep_ticks"
                ],
                prediction_horizon_limit_iep_ticks=fields[
                    "producer_prediction_horizon_limit_iep_ticks"
                ],
                generation=new_gen,
            )

    def stage_and_apply(
        self,
        profile: "Profile | str | None" = None,
        frame_values: list[int] | None = None,
        timeout_steps: int = 200_000,
        **overrides,
    ) -> None:
        """Validate and commit one complete dashboard configuration.

        The dashboard's Apply button must not leave a new staged layout next
        to old frame slots when one of the requested frames is invalid.  This
        helper therefore stages the layout, validates/writes the optional
        frame sequence, and only then calls :meth:`apply`.  If validation
        fails before the generation commit starts, the previous staged state
        and frame slots are restored.
        """
        previous_staged = self._staged
        previous_staged_profile = getattr(self, "_staged_profile", None)
        previous_position_count = getattr(self, "_position_count", None)
        previous_gray_excess_offset = getattr(self, "_gray_excess_offset", None)
        previous_frames = None
        if frame_values is not None:
            previous_frames = [
                self.sim.memory_read(
                    abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE,
                    abi.FRAME_SLOT_SIZE,
                )
                for index in range(16)
            ]

        apply_started = False
        try:
            self.stage(profile, **overrides)
            if frame_values is not None:
                self.set_raw_frames(frame_values)
            apply_started = True
            self.apply(timeout_steps)
        except Exception:
            # Once apply() starts it may already have published a new
            # generation.  Do not pretend that host-side metadata was rolled
            # back after that point; the caller must report the commit failure.
            if not apply_started:
                if previous_frames is not None:
                    for index, raw in enumerate(previous_frames):
                        self.sim.memory.write(
                            abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE,
                            raw,
                        )
                self._staged = previous_staged
                self._staged_profile = previous_staged_profile
                self._position_count = previous_position_count
                self._gray_excess_offset = previous_gray_excess_offset
            raise

    def set_raw_frames(self, frame_values: list[int]) -> None:
        """Write the emulator's raw MSB-first frame sequence into shared RAM.

        Values are complete wire frames, not semantic positions.  Keeping
        this operation separate from ``stage`` preserves the atomic config
        generation contract: callers write the sequence, then call ``apply``
        after staging any matching width/timing changes.
        """
        if self._staged is None:
            raise RuntimeError("set_raw_frames() called before stage()")
        if not 1 <= len(frame_values) <= 16:
            raise ValueError("SSI frame sequence must contain 1 to 16 values")

        width = int(self._staged.get("frame_width_bits", 0))
        if not 1 <= width <= 64:
            raise ValueError(f"frame width must be in [1, 64], got {width}")
        maximum = (1 << width) - 1

        for index, value in enumerate(frame_values):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"SSI frame {index} is not an integer")
            if not 0 <= value <= maximum:
                raise ValueError(
                    f"SSI frame {index}=0x{value:X} does not fit in {width} bits"
                )
            self.sim.memory.write(
                abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE,
                abi.pack_frame_slot(value),
            )

        for index in range(len(frame_values), 16):
            self.sim.memory.write(
                abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE,
                abi.pack_frame_slot(abi.FRAME_SLOT_UNUSED_SENTINEL),
            )

    def read_raw_frames(self) -> list[int]:
        """Read the active raw-frame sequence until its sentinel slot."""
        frames = []
        for index in range(16):
            raw = int.from_bytes(
                self.sim.memory_read(
                    abi.FRAMES_BASE + index * abi.FRAME_SLOT_SIZE, 8
                ),
                "little",
            )
            if raw == abi.FRAME_SLOT_UNUSED_SENTINEL:
                break
            frames.append(raw)
        return frames

    def wait_for_apply(self, timeout_steps: int = 200_000) -> None:
        """Step the simulator until both PRU cores' ack fields catch up to
        the currently requested_generation (or just PRU1's, for topology==1
        reader-only, since PRU0 isn't loaded in that case and never acks).
        """

        def read_gen(offset: int) -> int:
            return int.from_bytes(
                self.sim.memory_read(abi.CONFIG_BASE + offset, 4), "little"
            )

        target = read_gen(abi.CONFIG_REQUESTED_GENERATION_OFF)
        topology = self._staged.get("topology", 0) if self._staged else 0

        for _ in range(timeout_steps):
            self.sim.step_paced("pru1", "pru0")
            pru1_ack = read_gen(abi.CONFIG_PRU1_ACK_GENERATION_OFF)
            if topology == 1:
                if pru1_ack == target:
                    return
            else:
                pru0_ack = read_gen(abi.CONFIG_PRU0_ACK_GENERATION_OFF)
                if pru0_ack == target and pru1_ack == target:
                    return

        raise TimeoutError(
            f"generation {target} not acked within {timeout_steps} steps "
            f"(pru0_ack={read_gen(abi.CONFIG_PRU0_ACK_GENERATION_OFF)}, "
            f"pru1_ack={read_gen(abi.CONFIG_PRU1_ACK_GENERATION_OFF)}, "
            f"topology={topology})"
        )

    # ------------------------------------------------------------------
    # Read back: mailbox (seqlock-safe) and trace buffer.
    # ------------------------------------------------------------------

    def read_mailbox(self) -> dict:
        """Seqlock-safe read of the latest-sample mailbox.

        Retries while ``seq`` is odd (a write is in progress) or changed
        between the first and second read of ``seq`` (a write straddled
        this read) -- the exact protocol documented on the mailbox's
        ``seq`` field in the ABI schema. Returns ``unpack_mailbox``'s dict
        with ``position_value`` replaced by its semantically decoded form
        (via ``decode_position``, using the currently-staged
        ``encoding_type``/``position_width_bits``) rather than the raw
        wire-encoded bits.
        """

        def read_seq() -> int:
            return int.from_bytes(
                self.sim.memory_read(abi.MAILBOX_BASE + abi.MAILBOX_SEQ_OFF, 4),
                "little",
            )

        while True:
            seq_before = read_seq()
            if seq_before % 2 != 0:
                continue
            data = self.sim.memory_read(abi.MAILBOX_BASE, 64)
            seq_after = read_seq()
            if seq_after == seq_before:
                break

        mailbox = abi.unpack_mailbox(data)
        active_config = getattr(self, "_active_config", None)
        config = active_config or self._staged or {}
        encoding_type = config.get("encoding_type", 0)
        width = config.get("position_width_bits", 0)
        position_count = (
            getattr(self, "_active_position_count", None)
            if active_config is not None
            else getattr(self, "_position_count", None)
        )
        gray_excess_offset = (
            getattr(self, "_active_gray_excess_offset", None)
            if active_config is not None
            else getattr(self, "_gray_excess_offset", None)
        )
        raw_position = mailbox["position_value"]
        mailbox["raw_position_value"] = raw_position
        try:
            mailbox["position_value"] = self.decode_position(
                raw_position, encoding_type, width,
                position_count=position_count,
                gray_excess_offset=gray_excess_offset,
            )
        except ValueError as exc:
            # A generation change can precede the first frame in the new
            # layout. Keep the raw mailbox sample visible, but do not decode
            # an old wider sample as if it belonged to the new generation.
            mailbox["position_value"] = None
            mailbox["position_decode_error"] = str(exc)
        return mailbox

    def read_trace(self, newest_first: bool = True, limit: int | None = None) -> list[dict]:
        """Read back currently-valid trace records, respecting the ring
        buffer's oldest-overwritten semantics (see the design doc's capture
        section): once ``trace_write_index`` exceeds 1,024, the oldest
        surviving record is the slot about to be overwritten next.

        Returns oldest-to-newest by default reversed into newest-first
        (``newest_first=True``, the default) or left oldest-first
        (``newest_first=False``). ``limit`` is applied after computing the
        full ordered list: the most recent N records if ``newest_first``,
        else the oldest N.
        """
        write_index = int.from_bytes(
            self.sim.memory_read(
                abi.CAPTURE_BASE + abi.CAPTURE_TRACE_WRITE_INDEX_OFF, 4
            ),
            "little",
        )
        valid_count = min(write_index, 1024)

        if write_index <= 1024:
            slots = list(range(valid_count))
        else:
            oldest_slot = write_index % 1024
            slots = [(oldest_slot + i) % 1024 for i in range(1024)]

        records = []
        for slot in slots:
            data = self.sim.memory_read(
                abi.TRACE_BASE + slot * abi.TRACE_RECORD_SIZE, abi.TRACE_RECORD_SIZE
            )
            records.append(abi.unpack_trace_record(data))

        if newest_first:
            records.reverse()

        active_config = getattr(self, "_active_config", None)
        config = active_config or self._staged or {}
        encoding_type = config.get("encoding_type", 0)
        width = config.get("position_width_bits", 0)
        position_count = (
            getattr(self, "_active_position_count", None)
            if active_config is not None
            else getattr(self, "_position_count", None)
        )
        gray_excess_offset = (
            getattr(self, "_active_gray_excess_offset", None)
            if active_config is not None
            else getattr(self, "_gray_excess_offset", None)
        )
        for record in records:
            try:
                record["position_value_decoded"] = self.decode_position(
                    record["position_value"], encoding_type, width,
                    position_count=position_count,
                    gray_excess_offset=gray_excess_offset,
                )
            except ValueError as exc:
                record["position_value_decoded"] = None
                record["position_decode_error"] = str(exc)

        if limit is not None:
            records = records[:limit]

        return records

    def read_producer_diagnostics(self) -> dict[str, int]:
        """Read the fixed Task D producer-estimator diagnostics block."""
        data = self.sim.memory_read(
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_BASE,
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_SIZE,
        )

        def u32(offset: int) -> int:
            return int.from_bytes(data[offset:offset + 4], "little")

        def u64(offset: int) -> int:
            return int.from_bytes(data[offset:offset + 8], "little")

        raw_position = u64(
            abi.PRODUCER_SAMPLE_DIAGNOSTICS_LAST_ESTIMATE_POSITION_Q31_32_OFF
        )
        if raw_position & (1 << 63):
            raw_position -= 1 << 64
        return {
            "latest_write_seq": u64(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_WRITE_SEQ_OFF
            ),
            "accepted_count": u32(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_ACCEPTED_COUNT_OFF
            ),
            "coherence_retry_count": u32(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_COHERENCE_RETRY_COUNT_OFF
            ),
            "stale_sample_count": u32(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_STALE_SAMPLE_COUNT_OFF
            ),
            "ring_overrun_count": u32(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_RING_OVERRUN_COUNT_OFF
            ),
            "last_request_timestamp_iep": u64(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_LAST_REQUEST_TIMESTAMP_IEP_OFF
            ),
            "last_estimate_position_q31_32": raw_position,
            "status": u32(abi.PRODUCER_SAMPLE_DIAGNOSTICS_STATUS_OFF),
            "generation": u32(abi.PRODUCER_SAMPLE_DIAGNOSTICS_GENERATION_OFF),
            "head_seq": u32(abi.PRODUCER_SAMPLE_DIAGNOSTICS_HEAD_SEQ_OFF),
            "latest_slot_index": u32(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_SLOT_INDEX_OFF
            ),
            "latest_stable_sample_seq": u64(
                abi.PRODUCER_SAMPLE_DIAGNOSTICS_LATEST_STABLE_SAMPLE_SEQ_OFF
            ),
        }

    def start_producer(self) -> bool:
        """Explicitly start the timestamped producer after Apply."""
        return self.producer.start()

    def stop_producer(self) -> None:
        """Stop timestamped publication without changing the active SSI config."""
        self.producer.stop()

    def step_producer(self, timestamp_iep: int | None = None) -> dict:
        """Publish one producer sample and return its JSON-safe fields."""
        sample = self.producer.step(timestamp_iep)
        return {
            "write_seq": sample.write_seq,
            "timestamp_iep": sample.timestamp_iep,
            "position_q31_32": sample.position_q31_32,
            "generation": sample.generation,
            "flags": sample.flags,
        }

    def configure_producer_engineering(
        self,
        *,
        trajectory: str | None = None,
        initial_position: int | None = None,
        velocity_counts_per_second: int | float | str | None = None,
        triangle_low: int | None = None,
        triangle_high: int | None = None,
        period_iep_ticks: int | None = None,
    ) -> dict:
        """Configure the simulator-side producer in encoder-count units.

        Shared-memory samples remain signed Q31.32 and velocities remain
        Q31.32 counts per 300-MHz IEP tick.  This control-plane helper keeps
        those representation details out of UI and MCP clients.
        """
        q_one = 1 << abi.PRODUCER_SAMPLE_Q_FRACTION_BITS
        kwargs = {}
        if trajectory is not None:
            kwargs["trajectory"] = trajectory
        if initial_position is not None:
            kwargs["initial_position_q31_32"] = int(initial_position) * q_one
        if velocity_counts_per_second is not None:
            scaled = (
                Decimal(str(velocity_counts_per_second))
                * Decimal(q_one)
                / Decimal(abi.IEP_TICK_HZ)
            )
            kwargs["velocity_q31_32_per_iep_tick"] = int(
                scaled.to_integral_value(rounding=ROUND_HALF_EVEN)
            )
        if triangle_low is not None:
            kwargs["triangle_low_q31_32"] = int(triangle_low) * q_one
        if triangle_high is not None:
            kwargs["triangle_high_q31_32"] = int(triangle_high) * q_one
        if period_iep_ticks is not None:
            kwargs["period_iep_ticks"] = int(period_iep_ticks)
        # The hardware producer receives discrete encoder counts from the R5.
        # Keep UI/MCP-generated trajectories on that same integer-count
        # contract even when their engineering velocity is fractional per
        # 960-ns publication interval.
        kwargs["quantize_generated_positions"] = True
        self.producer.configure(**kwargs)
        return self.producer_state()

    def producer_state(self) -> dict:
        """Return control state and counters for UI/MCP diagnostics."""
        config = self.producer.configuration()
        q_one = 1 << abi.PRODUCER_SAMPLE_Q_FRACTION_BITS
        velocity_per_second = (
            Decimal(config.velocity_q31_32_per_iep_tick)
            * Decimal(abi.IEP_TICK_HZ)
            / Decimal(q_one)
        )
        error = self.producer.error_state
        return {
            "running": self.producer.running,
            "mode": config.mode,
            "period_iep_ticks": config.period_iep_ticks,
            "period_ns": config.period_iep_ticks * 1_000_000_000 // abi.IEP_TICK_HZ,
            "sample_age_limit_iep_ticks": config.sample_age_limit_iep_ticks,
            "prediction_horizon_limit_iep_ticks": (
                config.prediction_horizon_limit_iep_ticks
            ),
            "trajectory": config.trajectory,
            "initial_position": config.initial_position_q31_32 / q_one,
            "velocity_counts_per_second": float(velocity_per_second),
            "triangle_low": config.triangle_low_q31_32 / q_one,
            "triangle_high": config.triangle_high_q31_32 / q_one,
            "integer_samples": config.quantize_generated_positions,
            "generation": self.producer.generation,
            "published_count": self.producer.published_count,
            "skipped_overwritten_count": self.producer.skipped_overwritten_count,
            "error": None if error is None else {
                "type": error.error_type,
                "message": error.message,
                "timestamp_iep": error.timestamp_iep,
            },
        }

    # ------------------------------------------------------------------
    # Semantic position encoding/packing
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_position_width(width: int) -> int:
        if isinstance(width, bool) or not isinstance(width, int):
            raise ValueError(f"position width must be an integer, got {width!r}")
        if not 1 <= width <= 64:
            raise ValueError(f"position width must be in [1, 64], got {width}")
        return (1 << width) - 1

    @staticmethod
    def _gray_to_binary(raw: int, width: int) -> int:
        result = 0
        previous = (raw >> (width - 1)) & 1
        result |= previous << (width - 1)
        for bit in range(width - 2, -1, -1):
            decoded = ((raw >> bit) & 1) ^ previous
            result |= decoded << bit
            previous = decoded
        return result

    @staticmethod
    def _gray_excess_offset(
        width: int,
        position_count: int,
        gray_excess_offset: int | None,
    ) -> int:
        full_count = 1 << width
        if isinstance(position_count, bool) or not isinstance(position_count, int):
            raise ValueError("gray-excess position_count must be an integer")
        if not 1 <= position_count <= full_count:
            raise ValueError(
                f"gray-excess position_count must be in [1, {full_count}], "
                f"got {position_count}"
            )
        if gray_excess_offset is None:
            return (full_count - position_count) // 2
        if isinstance(gray_excess_offset, bool) or not isinstance(
            gray_excess_offset, int
        ):
            raise ValueError("gray-excess offset must be an integer")
        if not 0 <= gray_excess_offset <= full_count - position_count:
            raise ValueError(
                "gray-excess offset must leave the selected code window "
                "inside the full Gray code"
            )
        return gray_excess_offset

    def encode_position(
        self,
        position: int,
        encoding_type: int,
        width: int,
        *,
        position_count: int | None = None,
        gray_excess_offset: int | None = None,
    ) -> int:
        """Encode one natural position into its SSI wire representation.

        Binary and Tannenbaum profiles use the natural value unchanged.
        Gray uses the standard reflected Gray transform. Gray-excess selects
        a contiguous window from the full Gray code, centered by default; the
        optional offset makes the window explicit for a product-family profile.
        No value is silently masked or truncated.
        """
        mask = self._validate_position_width(width)
        if isinstance(position, bool) or not isinstance(position, int):
            raise ValueError(f"position must be an integer, got {position!r}")
        if not 0 <= position <= mask:
            raise ValueError(
                f"position {position} does not fit in {width} bits"
            )

        if encoding_type in (0, 3):
            return position
        if encoding_type == 1:
            return position ^ (position >> 1)
        if encoding_type == 2:
            offset = self._gray_excess_offset(
                width, position_count if position_count is not None else 0,
                gray_excess_offset,
            )
            encoded_index = position + offset
            if encoded_index >= offset + position_count:
                raise ValueError(
                    f"position {position} is outside the configured "
                    f"gray-excess range of {position_count} positions"
                )
            return encoded_index ^ (encoded_index >> 1)
        raise ValueError(f"unknown encoding_type: {encoding_type!r}")

    def pack_position_frame(
        self,
        *,
        position: int,
        status: int = 0,
        frame_width: int,
        position_offset: int,
        position_width: int,
        error_offset: int,
        error_width: int,
        padding_width: int,
        encoding_type: int,
        alignment: int = 0,
        position_count: int | None = None,
        gray_excess_offset: int | None = None,
    ) -> int:
        """Pack natural position/status fields into a right-aligned wire frame."""
        if not 1 <= frame_width <= 64:
            raise ValueError(f"frame width must be in [1, 64], got {frame_width}")
        if alignment not in (0, 1):
            raise ValueError(f"alignment must be 0 or 1, got {alignment}")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                position_offset,
                position_width,
                error_width,
                padding_width,
            )
        ):
            raise ValueError("frame field widths and offsets must be non-negative integers")
        self._validate_position_width(position_width)

        if alignment == 1 and position_offset == 0:
            position_offset = (
                frame_width - position_width - error_width - padding_width
            )
        position_end = position_offset + position_width
        if position_offset < 0 or position_end > frame_width:
            raise ValueError("position field does not fit in the SSI frame")

        wire_position = self.encode_position(
            position,
            encoding_type,
            position_width,
            position_count=position_count,
            gray_excess_offset=gray_excess_offset,
        )
        frame = wire_position << (frame_width - position_end)

        if error_width:
            if error_offset == NO_ERROR_FIELD:
                raise ValueError("error_offset is required when error_width is non-zero")
            if error_offset < 0 or error_offset + error_width > frame_width:
                raise ValueError("error field does not fit in the SSI frame")
            if (
                error_offset < position_end
                and error_offset + error_width > position_offset
            ):
                raise ValueError("position and error fields overlap")
            if isinstance(status, bool) or not isinstance(status, int):
                raise ValueError("status must be an integer")
            status_mask = (1 << error_width) - 1
            if not 0 <= status <= status_mask:
                raise ValueError(
                    f"status {status} does not fit in {error_width} bits"
                )
            frame |= status << (frame_width - error_offset - error_width)
        elif status:
            raise ValueError("status must be zero when the frame has no error field")

        return frame

    def set_positions(
        self,
        positions: list[int],
        statuses: list[int] | None = None,
        *,
        position_count: int | None = None,
        gray_excess_offset: int | None = None,
    ) -> None:
        """Pack natural positions using the staged layout and write frame slots."""
        if self._staged is None:
            raise RuntimeError("set_positions() called before stage()")
        if statuses is None:
            statuses = [0] * len(positions)
        if len(statuses) != len(positions):
            raise ValueError("statuses must have the same length as positions")

        self._position_count = position_count
        self._gray_excess_offset = gray_excess_offset
        fields = self._staged
        frames = [
            self.pack_position_frame(
                position=position,
                status=status,
                frame_width=fields["frame_width_bits"],
                position_offset=fields["position_offset_bits"],
                position_width=fields["position_width_bits"],
                error_offset=fields["error_offset_bits"],
                error_width=fields["error_width_bits"],
                padding_width=fields["padding_width_bits"],
                encoding_type=fields["encoding_type"],
                alignment=fields["alignment"],
                position_count=position_count,
                gray_excess_offset=gray_excess_offset,
            )
            for position, status in zip(positions, statuses)
        ]
        self.set_raw_frames(frames)

    # ------------------------------------------------------------------
    # Decode
    # ------------------------------------------------------------------

    def decode_position(
        self,
        raw: int,
        encoding_type: int,
        width: int,
        *,
        position_count: int | None = None,
        gray_excess_offset: int | None = None,
    ) -> int:
        """Decode a mailbox/trace position_value into a natural position.

        PRU1 only ever does structural offset/width extraction (see
        ssi_generic_reader.asm); semantic decode of *how* the extracted bits
        are encoded is this module's job.
        """
        mask = self._validate_position_width(width)
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= mask:
            raise ValueError(f"raw position {raw!r} does not fit in {width} bits")

        if encoding_type in (0, 3):
            # binary: no recoding needed. tannenbaum: a frame-layout
            # convention already handled by position_offset_bits/
            # singleturn_width_bits/multiturn_width_bits at the structural-
            # extraction layer; no additional bit-recoding at decode time.
            return raw

        if encoding_type == 1:
            # Gray-to-binary: MSB stays the same; each lower bit is the XOR
            # of that Gray bit with the binary bit just decoded above it.
            return self._gray_to_binary(raw, width)

        if encoding_type == 2:
            offset = self._gray_excess_offset(
                width, position_count if position_count is not None else 0,
                gray_excess_offset,
            )
            encoded_index = self._gray_to_binary(raw, width)
            if not offset <= encoded_index < offset + position_count:
                raise ValueError(
                    "Gray-excess wire value is outside the configured code window"
                )
            return encoded_index - offset

        raise ValueError(f"unknown encoding_type: {encoding_type!r}")
