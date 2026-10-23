"""ICSSG PWM module: trip logic, state registers and per-pad state machine.

SPRUIM2J 6.4.10 (PWM0/PWM1 driven by IEP0; PWM2/PWM3 need IEP1, which this
simulator does not model, so only their pad side is missing).  The trip
registers ``ICSSG_PWM0..3`` and the state words ``ICSSG_PWMm_k`` follow
register-level reads and writes measured on LP-AM243 ICSSG0 (software trip
sources only).  Pad levels, edge timing, dead time, the glitch filter and the
trip sources 2..8 were not measured; see docs/icssg_pwm.md for what is measured
and what is an assumption.

Pads are numbered ``set * 6 + phase * 2 + (1 if NEG else 0)``.  A pad level is
``None`` (Z), 0 or 1.  Levels are never reflected in R31.
"""
from __future__ import annotations

import math
from collections import deque

PWM_CFG = 0x130          # ICSSG_PWM0..3 trip configuration, 4 words
PWM_STATE = 0x140        # ICSSG_PWMm_k state configuration, 12 words
NUM_SETS = 4
IEP0_SETS = 2            # sets 0 and 1 are driven by IEP0 CMP1..CMP12
NUM_SOURCES = 9          # TRIP_VEC encodings 0..8
SRC_POS_ERR, SRC_OVER_ERR, SRC_SD_SHORT0, SRC_TZ_IN, SRC_E1_0 = 0, 1, 2, 5, 6
_FILTERED = range(5, 9)  # trip_e2 (TZ_IN) and trip_e1_0..2 pass the glitch filter

# ICSSG_PWMn bit fields
_DEBOUNCE = 0xFF
_MASK_SHIFT = 8
_CMP0_EN = 1 << 17
_RESET = 1 << 18
_OVER_ERR = 1 << 19
_POS_ERR = 1 << 20
_VEC_SHIFT = 21
_TRIP_S = 1 << 30
_TRIP_S_CLEAR = 1 << 23   # write 1 (TRIP_VEC[2] position): clears TRIP_S, see write32
_STATE_STORED = 0xFFF           # bits 11:0 of ICSSG_PWMm_k are stored

_INIT, _ACTIVE, _TRIP = "initial", "active", "trip"
_SHIFT = {(_INIT, 0): 0, (_INIT, 1): 2, (_TRIP, 0): 4, (_TRIP, 1): 6,
          (_ACTIVE, 0): 8, (_ACTIVE, 1): 10}
EDGE_LOG_DEPTH = 4096


class IcssgPwm:
    """Four PWM sets with the same trip registers; sets 0 and 1 also drive pads.

    Register behaviour (measured on LP-AM243 ICSSG0, register level):

    * TRIP_MASK: a set bit enables the source; TRIP_VEC is one-hot.
    * TRIP_VEC follows every asserted enabled source while any is asserted and
      freezes at its last value when all deassert; there is no priority and no
      first-cause latch.  TRIP_S is set whenever TRIP_VEC becomes non-zero.
    * TRIP_RESET (bit 18) is a level that reads back what was written.  While
      it is 1, TRIP_VEC is 0 and no trip is latched.  It does not clear TRIP_S.
    * A write with bit 23 set clears TRIP_S when TRIP_VEC was 0 or bit 18 is 1
      in that write; it is ignored while TRIP_VEC is latched.  Other writes to
      TRIP_S and TRIP_VEC have no effect.
    * ``ICSSG_PWMn`` stores bits 20:0; ``ICSSG_PWMm_k`` stores bits 11:0.

    Sets 2 and 3 latch TRIP_S/TRIP_VEC like sets 0 and 1.  Their pad state
    machine would be driven by IEP1, which this simulator does not model, so
    they have no pads and no state.

    The IEP compare listener is registered on the first write of a non-zero
    value to a PWM register (or the first trip input), because a registered
    listener forces the IEP into per-clock event mode.  Compare events before
    that point are not seen by the module.
    """

    def __init__(self, iep, edge_log_depth=EDGE_LOG_DEPTH):
        self.iep = iep
        self.edge_log_depth = edge_log_depth
        self._armed = False
        self.ext = None
        self.reset()
        iep.add_reset_listener(self.reset)

    def _arm(self):
        if not self._armed:
            self._armed = True
            self.iep.add_compare_listener(self._compare)

    @property
    def efficiency_mode(self):
        """ICSSG_SA_MX_REG.PWM_EFC_EN; it has no effect on the pads here."""
        return self.iep.pwm_efc_en

    # -- reset / state ---------------------------------------------------
    def reset(self):
        """Module reset.  External pad levels (``ext``) survive; everything
        else returns to its reset value and held filtered pads restart."""
        held = self.ext
        self.debounce = [0] * NUM_SETS
        self.mask = [0] * NUM_SETS
        self.cmp0_en = [False] * NUM_SETS
        self.rst = [False] * NUM_SETS      # TRIP_RESET level
        self.sw = [[False, False] for _ in range(NUM_SETS)]   # POS_ERR, OVER_ERR
        self.trip_s = [False] * NUM_SETS
        self.trip_vec = [0] * NUM_SETS     # one bit per asserted enabled source
        self.state_cfg = [0] * (NUM_SETS * 3)
        self.state = [_INIT] * NUM_SETS
        self.ext = held or [[False] * NUM_SOURCES for _ in range(NUM_SETS)]   # pin levels
        self.filtered = [[False] * NUM_SOURCES for _ in range(NUM_SETS)]
        self.pending = {}          # (set, source) -> IEP tick at which it passes
        self._levels = [None] * (NUM_SETS * 6)
        self._edges = deque(maxlen=self.edge_log_depth)   # (iep tick, pad, level)
        self.edges_dropped = 0
        for s in range(IEP0_SETS):
            self._apply_state(s, 0)
        tick = self.iep.clock_ticks
        for setno in range(NUM_SETS):
            for source in _FILTERED:
                if self.ext[setno][source]:        # pad still held: restart filter
                    self._filter_rise(setno, source, tick)

    @property
    def levels(self):
        """Pad levels (None = Z, 0, 1) with due debounce timers applied."""
        self.sync()
        return self._levels

    @property
    def edges(self):
        """Pad edge log ``(IEP clock, pad, level)``, last ``edge_log_depth``
        entries; ``edges_dropped`` counts the older ones."""
        self.sync()
        return self._edges

    # -- pad drive -------------------------------------------------------
    @staticmethod
    def _field(word, state, neg):
        return (word >> _SHIFT[(state, int(neg))]) & 3

    def _drive(self, pad, level, tick):
        if self._levels[pad] != level:
            self._levels[pad] = level
            if len(self._edges) == self._edges.maxlen:
                self.edges_dropped += 1
            self._edges.append((tick, pad, level))

    def _apply_pad(self, pad, state, tick):
        v = self._field(self.state_cfg[pad // 2], state, pad & 1)
        if state == _ACTIVE:
            if v == 0:                       # toggle; a Z pad toggles to H
                self._drive(pad, 1 if self._levels[pad] in (None, 0) else 0, tick)
            elif v in (1, 2):
                self._drive(pad, 0 if v == 1 else 1, tick)
            return                           # reserved 3: hold
        self._drive(pad, 0 if v == 1 else 1 if v == 2 else None, tick)

    def _apply_state(self, setno, tick):
        for pad in range(setno * 6, setno * 6 + 6):
            self._apply_pad(pad, self.state[setno], tick)

    # -- trip logic ------------------------------------------------------
    def _enabled(self, setno, source):
        return bool(self.mask[setno] >> source & 1)

    def _asserted(self, setno, source):
        if source == SRC_POS_ERR:
            return self.sw[setno][0] or self.ext[setno][0]
        if source == SRC_OVER_ERR:
            return self.sw[setno][1] or self.ext[setno][1]
        if source in _FILTERED:
            return self.filtered[setno][source]
        return self.ext[setno][source]

    def _evaluate(self, setno, tick):
        """Level-sensitive trip.  TRIP_VEC tracks all asserted enabled sources
        while any is asserted and keeps its last value otherwise.  Nothing
        trips while TRIP_RESET is held."""
        if self.rst[setno]:
            return
        vec = 0
        for source in range(NUM_SOURCES):
            if self._asserted(setno, source) and self._enabled(setno, source):
                vec |= 1 << source
        if not vec:
            return
        self.trip_vec[setno] = vec
        self.trip_s[setno] = True
        if setno < IEP0_SETS and self.state[setno] != _TRIP:
            self.state[setno] = _TRIP
            self._apply_state(setno, tick)

    def _return_to_initial(self, setno, tick):
        """Pad state machine back to *initial*; TRIP_S/TRIP_VEC are untouched."""
        self.state[setno] = _INIT
        self._apply_state(setno, tick)
        self._evaluate(setno, tick)

    def sync(self):
        """Apply debounce timers that have expired at the current IEP clock."""
        self._sync(self.iep.clock_ticks)

    def _sync(self, tick):
        """Let debounce timers that expired on or before *tick* take effect."""
        due = sorted((t, k) for k, t in self.pending.items() if t <= tick)
        for t, (setno, source) in due:
            del self.pending[(setno, source)]
            self.filtered[setno][source] = True
            self._evaluate(setno, t)

    def debounce_ticks(self, setno):
        """DEBOUNCE_VALUE core clocks expressed in IEP clocks (rounded up)."""
        ratio = self.iep.active_clock_hz / self.iep.core_clock_hz("pru0")
        return math.ceil(self.debounce[setno] * ratio)

    def set_trip_input(self, setno, source, level):
        """Drive trip source *source* (TRIP_VEC encoding 0..8) of a set.

        Source 5 is the PWMn_TZ_IN pad (active high).  Sources 5..8 pass the
        glitch filter: a rising level must stay high DEBOUNCE_VALUE core clocks
        before it counts; a pulse of exactly that width passes.
        """
        if not (0 <= setno < NUM_SETS and 0 <= source < NUM_SOURCES):
            raise ValueError("PWM trip input out of range")
        level = bool(level)
        self._arm()
        tick = self.iep.clock_ticks
        self._sync(tick)
        if self.ext[setno][source] == level:
            return
        self.ext[setno][source] = level
        if source in _FILTERED:
            if not level:
                self.pending.pop((setno, source), None)
                self.filtered[setno][source] = False
            else:
                self._filter_rise(setno, source, tick)
        self._evaluate(setno, tick)

    def _filter_rise(self, setno, source, tick):
        if self.debounce[setno] == 0:
            self.filtered[setno][source] = True
        else:
            self.pending[(setno, source)] = tick + self.debounce_ticks(setno)

    # -- IEP0 compare events ----------------------------------------------
    def _compare(self, event):
        self._sync(event.tick)
        if event.index == 0:
            for s in range(IEP0_SETS):
                if self.cmp0_en[s] and self.state[s] != _INIT:
                    self._return_to_initial(s, event.tick)
            return
        if not 1 <= event.index <= 12:
            return
        pad = event.index - 1
        setno = pad // 6
        if self.state[setno] == _TRIP:
            return
        self.state[setno] = _ACTIVE
        self._apply_pad(pad, _ACTIVE, event.tick)

    # -- registers --------------------------------------------------------
    def read32(self, off):
        if PWM_CFG <= off < PWM_CFG + 16 and off % 4 == 0:
            self._sync(self.iep.clock_ticks)
            n = (off - PWM_CFG) // 4
            return (self.debounce[n] | self.mask[n] << _MASK_SHIFT
                    | (_CMP0_EN if self.cmp0_en[n] else 0)
                    | (_RESET if self.rst[n] else 0)
                    | (_POS_ERR if self.sw[n][0] else 0)
                    | (_OVER_ERR if self.sw[n][1] else 0)
                    | self.trip_vec[n] << _VEC_SHIFT
                    | (_TRIP_S if self.trip_s[n] else 0))
        if PWM_STATE <= off < PWM_STATE + 48 and off % 4 == 0:
            return self.state_cfg[(off - PWM_STATE) // 4]
        return 0

    def write32(self, off, value):
        value &= 0xFFFFFFFF
        if value:
            self._arm()
        tick = self.iep.clock_ticks
        if PWM_CFG <= off < PWM_CFG + 16 and off % 4 == 0:
            self._sync(tick)
            n = (off - PWM_CFG) // 4
            self.debounce[n] = value & _DEBOUNCE
            self.mask[n] = (value >> _MASK_SHIFT) & 0x1FF
            self.cmp0_en[n] = bool(value & _CMP0_EN)
            self.sw[n] = [bool(value & _POS_ERR), bool(value & _OVER_ERR)]
            self.rst[n] = bool(value & _RESET)
            if value & _TRIP_S_CLEAR and (self.trip_vec[n] == 0 or self.rst[n]):
                self.trip_s[n] = False
            if self.rst[n]:
                self.trip_vec[n] = 0
                if n < IEP0_SETS:
                    self.state[n] = _INIT
                    self._apply_state(n, tick)
            self._evaluate(n, tick)
        elif PWM_STATE <= off < PWM_STATE + 48 and off % 4 == 0:
            index = (off - PWM_STATE) // 4
            self.state_cfg[index] = value & _STATE_STORED
            setno = index // 3
            # A pad follows its configured initial/trip level straight away; in
            # the active state the new action is used at the pad's next compare.
            if setno < IEP0_SETS and self.state[setno] != _ACTIVE:
                for pad in range(index * 2, index * 2 + 2):
                    self._apply_pad(pad, self.state[setno], tick)

    def read(self, off, length):
        return bytes((self.read32((off + i) & ~3) >> (((off + i) & 3) * 8)) & 255
                     for i in range(length))

    def write(self, off, data):
        words = {}
        for i, b in enumerate(data):
            words.setdefault((off + i) & ~3, {})[(off + i) & 3] = b
        for w, parts in words.items():
            cur = self.read32(w)
            self.write32(w, sum(parts.get(k, (cur >> 8 * k) & 255) << 8 * k
                                for k in range(4)))

    # -- snapshot ----------------------------------------------------------
    def snapshot(self):
        return {
            "debounce": list(self.debounce), "mask": list(self.mask),
            "cmp0_en": list(self.cmp0_en), "rst": list(self.rst), "sw": [list(x) for x in self.sw],
            "trip_s": list(self.trip_s), "trip_vec": list(self.trip_vec),
            "state_cfg": list(self.state_cfg), "state": list(self.state),
            "ext": [list(x) for x in self.ext],
            "filtered": [list(x) for x in self.filtered],
            "pending": dict(self.pending), "levels": list(self._levels),
            "edges": list(self._edges), "edges_dropped": self.edges_dropped,
        }

    def restore(self, snap):
        self.debounce = list(snap["debounce"]); self.mask = list(snap["mask"])
        self.cmp0_en = list(snap["cmp0_en"]); self.rst = list(snap["rst"])
        self.sw = [list(x) for x in snap["sw"]]
        self.trip_s = list(snap["trip_s"]); self.trip_vec = list(snap["trip_vec"])
        self.state_cfg = list(snap["state_cfg"]); self.state = list(snap["state"])
        self.ext = [list(x) for x in snap["ext"]]
        self.filtered = [list(x) for x in snap["filtered"]]
        self.pending = dict(snap["pending"]); self._levels = list(snap["levels"])
        self._edges = deque(snap["edges"], maxlen=self.edge_log_depth)
        self.edges_dropped = snap["edges_dropped"]
        if (any(self.state_cfg) or any(self.debounce) or any(self.mask)
                or any(self.cmp0_en) or any(self.rst) or any(any(x) for x in self.sw)
                or any(any(x) for x in self.ext)):
            self._arm()
