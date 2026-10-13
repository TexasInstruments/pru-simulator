"""ICSSG Industrial Ethernet Peripheral (IEP) timer model.

Implements the counter and compare subset of IEP0 that PRU firmware polls
through the constant table (typically C26). Written from the AM243x TRM
(SPRUIM2J §6.4.13, PRU_ICSSG IEP), NOT ported from any other simulator.

Register map (offsets from the IEP base), per the TRM:

    0x00  IEP_GLOBAL_CFG_REG   [0] CNT_ENABLE, [7:4] DEFAULT_INC
    0x04  IEP_GLOBAL_STATUS_REG
    0x0C  IEP_SLOW_COMPEN_REG  (NOT the counter - see below)
    0x10  IEP_COUNT_REG0       counter, lower 32 bits
    0x14  IEP_COUNT_REG1       counter, upper 32 bits
    0x18  IEP_CAP_CFG_REG
    0x1C  IEP_CAP_STATUS_REG
    0x20  IEP_CAPR0_REG0       capture registers 0x20..0x6C
    0x70  IEP_CMP_CFG_REG      [0] CMP0_RST_CNT_EN, [16:1] CMP_EN (bit 1 -> CMP0)
    0x74  IEP_CMP_STATUS_REG   [15:0] CMP_STATUS, write 1 to clear
    0x78  IEP_CMP0_REG0        compare 0, lower 32 bits
    0x7C  IEP_CMP0_REG1        compare 0, upper 32 bits
    0x80  IEP_CMP1_REG0        ... CMPj_REG0 at 0x78 + 8*j

Offsets are from Table 14-10902, TRM pages 6902/6905. An earlier revision of this
file used 0x0C for the counter and 0x40/0x44/0x48 for the compare block - the
AM335x-era PRU-ICSS layout, not ICSSG. 0x0C is SLOW_COMPEN and 0x40-0x6C are
capture registers, so that firmware polls the wrong registers entirely.

Two TRM details are easy to get wrong and are called out because at least one
other PRU simulator gets them wrong:

  * The compare registers are **64-bit pairs** - "16x 64-bit compare registers:
    IEP_CMPj_REG0/ IEP_CMPj_REG1 (where j = 0 to 15)". So 0x4C is the UPPER
    half of CMP0, not CMP1. CMP1_REG0 lives at 0x50.
  * A CMP0 hit resets the counter only when **IEP_CMP_CFG_REG[0]
    CMP0_RST_CNT_EN** is set, and a compare only fires when its CMP_EN bit is
    set - and CMP_EN starts at bit **1**, so CMP0's enable is bit 1, not bit 0.
    Auto-reset is configuration, not implicit behaviour.

CAPTURE

  0x18  IEP_CAP_CFG_REG      [n]    CAPnR_EN     capture enable, event n
                             [n+7]  CAPnR_1ST_LAST  0 = first, 1 = last
  0x1C  IEP_CAP_STATUS_REG   [n]    CAPnR_VALID, write 1 to clear
  0x20  IEP_CAPR0_REG0       captured counter, 64-bit pairs, CAPRn at 0x20+8n

Capture exists so firmware can timestamp an asynchronous external event
against the counter - the mechanism every encoder protocol uses when it has to
relate a host trigger to a line or clock phase.

`capture_event(n)` latches the CURRENT counter into slot n, subject to the
enable and to first-vs-last:

  * first mode - the slot holds the FIRST event since the valid bit was
    cleared; later events are ignored until software clears it. This is what a
    protocol wants when it must timestamp a trigger and not have a later,
    spurious edge overwrite it.
  * last mode - every event overwrites.

The enable/mode bit positions above are the modelled convention. The TRM's
exact CAP_CFG field packing varies between ICSS generations, and firmware that
depends on a particular packing should be checked against the device TRM rather
than against this model. What IS faithful, and what firmware actually depends
on, is the latch semantics: enable gating, first-vs-last, and a valid bit that
software clears.

TIMING

The IEP runs on its own clock (ICSSG_IEP_CLK), not on any PRU core's
instruction stream. `clock_mhz` sets that rate; the Simulator reads it from the
`iep_clock_mhz` key in the config's [device] section and defaults it to the
PRU0 clock. One `tick()` is one IEP clock edge.

Cores drive time forward through `advance_core(core, core_ns)`, reporting their
elapsed time in nanoseconds (cycles, stall cycles included, over the core's own
clock). The IEP is shared by every core on the ICSSG, so its timeline follows
the core that is furthest ahead and emits one edge per IEP clock period up to
that point. Two cores stepping together therefore advance it once, not twice.
Consequences worth knowing:

  * A core that is stepped far behind another sees the leading core's counter
    value - it reads "the future". `Simulator.step_paced` keeps cores within a
    few ns of each other, which is what timing-sensitive two-core runs need.
  * A core whose time goes backwards (its cycle counter was reset) restarts
    from the IEP's current time rather than freezing the counter until it
    catches up.

Not modelled: shadow mode (IEP_CMP_CFG_REG[17] SHADOW_EN), slow compensation,
sync/EHRPWM counter reset, interrupt routing, and the pin/event routing that
decides WHICH external signal drives capture event n - here the event is raised
by the harness or by another model calling `capture_event()`. Reads of
unimplemented offsets return zero and writes are ignored, so firmware touching
them does not fault - it simply sees a counter that ignores those features.
"""

GLOBAL_CFG = 0x00
GLOBAL_STATUS = 0x04
COUNT_REG0 = 0x10
COUNT_REG1 = 0x14
CMP_CFG = 0x70
CMP_STATUS = 0x74
CAP_CFG = 0x18
CAP_STATUS = 0x1C
CAPR0_REG0 = 0x20
CMP0_REG0 = 0x78

NUM_COMPARE = 16
# 0x20..0x6C is 0x50 bytes = ten 64-bit pairs.
NUM_CAPTURE = 10
IEP_SIZE = 0x100

_MASK32 = 0xFFFFFFFF
_MASK64 = 0xFFFFFFFFFFFFFFFF


class IepTimer:
    """Counter + compare subset of the ICSSG IEP.

    `tick()` advances the counter by DEFAULT_INC when CNT_ENABLE is set, then
    evaluates the enabled compares against the new count.
    """

    def __init__(self, clock_mhz: float = 200.0):
        if clock_mhz <= 0:
            raise ValueError(f"IEP clock must be positive, got {clock_mhz}")
        self.clock_mhz = float(clock_mhz)
        # Timeline state: survives register reset() so a core reset or an
        # IEP register reset never rewinds simulated time.
        self._now_ns = 0.0                   # furthest core time seen
        self._edges = 0                      # IEP clock edges emitted so far
        self._origin_ns = 0.0                # time at the last clock change
        self._origin_edges = 0               # edges emitted at that time
        self._core_base: dict[str, float] = {}
        self._core_last: dict[str, float] = {}
        self.reset()

    def reset(self) -> None:
        self.global_cfg = 0
        self.global_status = 0
        self.count = 0                       # 64-bit
        self.cmp_cfg = 0
        self.cmp_status = 0                  # 16 bits, write-1-to-clear
        self.compare = [0] * NUM_COMPARE     # each 64-bit
        self.cap_cfg = 0
        self.cap_status = 0                  # valid bits, write-1-to-clear
        self.capture = [0] * NUM_CAPTURE     # each 64-bit

    # -- configuration views --------------------------------------------
    @property
    def count_enabled(self) -> bool:
        return bool(self.global_cfg & 0x1)

    @property
    def default_inc(self) -> int:
        """IEP_GLOBAL_CFG_REG[7:4]. A DEFAULT_INC of 0 does not advance."""
        return (self.global_cfg >> 4) & 0xF

    @property
    def cmp0_rst_cnt_en(self) -> bool:
        return bool(self.cmp_cfg & 0x1)

    def cmp_enabled(self, j: int) -> bool:
        """IEP_CMP_CFG_REG[16:1]: CMP_EN bit 1 maps to CMP0."""
        return bool(self.cmp_cfg & (1 << (j + 1)))

    def cap_enabled(self, n: int) -> bool:
        return bool(self.cap_cfg & (1 << n))

    def cap_last_mode(self, n: int) -> bool:
        return bool(self.cap_cfg & (1 << (n + 7)))

    def cap_valid(self, n: int) -> bool:
        return bool(self.cap_status & (1 << n))

    # -- capture ---------------------------------------------------------
    def capture_event(self, n: int) -> bool:
        """Latch the current counter into capture slot *n*.

        Returns True if a value was stored. A disabled slot stores nothing, and
        in first mode an already-valid slot is left alone - which is the whole
        point of first mode, so a later edge cannot overwrite the trigger the
        firmware is trying to timestamp.
        """
        if not 0 <= n < NUM_CAPTURE:
            raise ValueError(f"capture slot {n} out of range")
        if not self.cap_enabled(n):
            return False
        if self.cap_valid(n) and not self.cap_last_mode(n):
            return False
        self.capture[n] = self.count
        self.cap_status |= 1 << n
        return True

    # -- timeline --------------------------------------------------------
    @property
    def now_ns(self) -> float:
        return self._now_ns

    def set_clock_mhz(self, clock_mhz: float) -> None:
        """Change the IEP clock from now on; edges already emitted stay put."""
        if clock_mhz <= 0:
            raise ValueError(f"IEP clock must be positive, got {clock_mhz}")
        self._origin_ns = self._now_ns
        self._origin_edges = self._edges
        self.clock_mhz = float(clock_mhz)

    def advance_core(self, core: str, core_ns: float) -> int:
        """Bring the IEP up to *core*'s elapsed time; return edges emitted.

        *core_ns* is that core's own elapsed time since its last reset. The
        IEP timeline is the maximum over all cores, so this only emits edges
        when *core* is the one furthest ahead.
        """
        last = self._core_last.get(core)
        if last is None:
            self._core_base[core] = 0.0             # cores start together at 0
        elif core_ns < last:
            self._core_base[core] = self._now_ns    # core was reset
        self._core_last[core] = core_ns
        t = self._core_base[core] + core_ns
        if t <= self._now_ns:
            return 0
        self._now_ns = t
        # The 1e-6 guards against a period like 1000/333.33 landing a hair
        # short of an exact edge through float rounding.
        target = self._origin_edges + int(
            (t - self._origin_ns) * self.clock_mhz / 1000.0 + 1e-6)
        n = target - self._edges
        self._edges = target
        if self.count_enabled:
            for _ in range(n):
                self.tick()
        return n

    def tick(self) -> None:
        """Advance one ICSSG_IEP_CLK cycle."""
        if not self.count_enabled:
            return
        self.count = (self.count + self.default_inc) & _MASK64

        hit0 = False
        for j in range(NUM_COMPARE):
            if self.cmp_enabled(j) and self.count == self.compare[j]:
                self.cmp_status |= 1 << j
                if j == 0:
                    hit0 = True

        # "IEP_CMP_CFG_REG[0] CMP0_RST_CNT_EN, if enabled, will reset the
        # controller counter on the next ICSSG_IEP_CLK/ICSSG_ICLK cycle."
        if hit0 and self.cmp0_rst_cnt_en:
            self.count = 0

    # -- register access -------------------------------------------------
    def read32(self, offset: int) -> int:
        if offset == GLOBAL_CFG:
            return self.global_cfg
        if offset == GLOBAL_STATUS:
            return self.global_status
        if offset == COUNT_REG0:
            return self.count & _MASK32
        if offset == COUNT_REG1:
            return (self.count >> 32) & _MASK32
        if offset == CMP_CFG:
            return self.cmp_cfg
        if offset == CMP_STATUS:
            return self.cmp_status
        if offset == CAP_CFG:
            return self.cap_cfg
        if offset == CAP_STATUS:
            return self.cap_status
        if CAPR0_REG0 <= offset < CAPR0_REG0 + NUM_CAPTURE * 8:
            n, half = divmod(offset - CAPR0_REG0, 8)
            v = self.capture[n]
            return v & _MASK32 if half == 0 else (v >> 32) & _MASK32
        if CMP0_REG0 <= offset < CMP0_REG0 + NUM_COMPARE * 8:
            j, half = divmod(offset - CMP0_REG0, 8)
            v = self.compare[j]
            return v & _MASK32 if half == 0 else (v >> 32) & _MASK32
        return 0

    def write32(self, offset: int, value: int) -> None:
        value &= _MASK32
        if offset == GLOBAL_CFG:
            self.global_cfg = value
        elif offset == GLOBAL_STATUS:
            self.global_status = value
        elif offset == COUNT_REG0:
            self.count = (self.count & ~_MASK32) | value
        elif offset == COUNT_REG1:
            self.count = (self.count & _MASK32) | (value << 32)
        elif offset == CMP_CFG:
            self.cmp_cfg = value
        elif offset == CMP_STATUS:
            # 16 status bits, write 1h to clear.
            self.cmp_status &= ~(value & 0xFFFF)
        elif offset == CAP_CFG:
            self.cap_cfg = value
        elif offset == CAP_STATUS:
            # Valid bits, write 1 to clear - the same convention as CMP_STATUS.
            self.cap_status &= ~value
        elif CAPR0_REG0 <= offset < CAPR0_REG0 + NUM_CAPTURE * 8:
            # Capture registers are read-only in hardware; a write is ignored
            # rather than silently corrupting a timestamp.
            pass
        elif CMP0_REG0 <= offset < CMP0_REG0 + NUM_COMPARE * 8:
            j, half = divmod(offset - CMP0_REG0, 8)
            if half == 0:
                self.compare[j] = (self.compare[j] & ~_MASK32) | value
            else:
                self.compare[j] = (self.compare[j] & _MASK32) | (value << 32)
        # Unimplemented offsets are ignored rather than faulting.

    # -- byte-addressed access used by the memory bus --------------------
    def read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        for i in range(length):
            byte_off = offset + i
            word = self.read32(byte_off & ~0x3)
            out.append((word >> ((byte_off & 0x3) * 8)) & 0xFF)
        return bytes(out)

    def write(self, offset: int, data: bytes) -> None:
        # Group by 32-bit word so write-1-to-clear and the 64-bit halves see a
        # whole word, which is how firmware always accesses these registers.
        words: dict[int, list[int | None]] = {}
        for i, b in enumerate(data):
            byte_off = offset + i
            w = byte_off & ~0x3
            words.setdefault(w, [None, None, None, None])[byte_off & 0x3] = b
        for w, parts in words.items():
            if any(p is None for p in parts):
                cur = self.read32(w)
                parts = [p if p is not None else (cur >> (k * 8)) & 0xFF
                         for k, p in enumerate(parts)]
            self.write32(w, parts[0] | (parts[1] << 8) | (parts[2] << 16) | (parts[3] << 24))

    def snapshot(self) -> dict:
        return {
            "global_cfg": self.global_cfg,
            "count": self.count,
            "cmp_cfg": self.cmp_cfg,
            "cmp_status": self.cmp_status,
            "compare": list(self.compare),
            "clock_mhz": self.clock_mhz,
            "now_ns": self._now_ns,
        }
