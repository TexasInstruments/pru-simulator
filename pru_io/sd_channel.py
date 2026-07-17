# pru_io/sd_channel.py
"""Single sigma-delta filter channel with 3 cascaded accumulators.

Models the hardware accumulator stages of the ICSS-G SCU SD filter.
Accumulators run continuously; shadow registers latch every OSR ticks.
"""

_ACC_MAX = 0x0FFFFFFF  # 28-bit max


class SDChannel:
    """One SD filter channel: 3 cascaded accumulators + shadow latch + overflow."""

    def __init__(self, osr: int = 64):
        self.osr = osr

        # Live accumulators (run continuously)
        self.acc1: int = 0
        self.acc2: int = 0
        self.acc3: int = 0

        # Shadow registers (latched every OSR ticks)
        self.shadow_acc1: int = 0
        self.shadow_acc2: int = 0
        self.shadow_acc3: int = 0

        # Status flags
        self.valid: bool = False
        self.ovf: bool = False

        # Fast Detect
        self.fd_en: bool = False
        self.fd_window_size: int = 0  # 0=4, 1=8, ..., 7=32 samples
        self.fd_one_max_limit: int = 0
        self.fd_one_min_limit: int = 0
        self.fd_zero_max_limit: int = 0
        self.fd_zero_min_limit: int = 0
        self.fd_one_max: bool = False
        self.fd_one_min: bool = False
        self.fd_zero_max: bool = False
        self.fd_zero_min: bool = False
        self._fd_shift_reg: int = 0  # holds last window_size bits
        self._fd_sample_count: int = 0  # tracks how many samples accumulated

        # Internal counter
        self._sample_count: int = 0

    def tick(self, bit: int) -> None:
        """Process one SD clock tick with input *bit* (0 or 1).

        Advances all three accumulators and checks for OSR boundary latch.
        """
        self.acc1 += bit
        self.acc2 += self.acc1
        self.acc3 += self.acc2

        # Overflow detection (28-bit) — flag only, no masking.
        # CIC integrators must grow monotonically; masking breaks the comb filter.
        if self.acc3 > _ACC_MAX:
            self.ovf = True

        # Fast Detect sliding window
        if self.fd_en:
            if not (0 <= self.fd_window_size <= 7):
                raise ValueError(f"fd_window_size must be 0-7, got {self.fd_window_size}")
            window = 4 * (self.fd_window_size + 1)  # 0→4, 1→8, 2→12, ..., 7→32
            mask = (1 << window) - 1
            self._fd_shift_reg = ((self._fd_shift_reg << 1) | bit) & mask
            self._fd_sample_count = min(self._fd_sample_count + 1, window)
            if self._fd_sample_count >= window:
                ones = bin(self._fd_shift_reg).count('1')
                zeros = window - ones
                if ones >= self.fd_one_max_limit + 1:
                    self.fd_one_max = True
                if ones <= self.fd_one_min_limit + 1:
                    self.fd_one_min = True
                if zeros >= self.fd_zero_max_limit + 1:
                    self.fd_zero_max = True
                if zeros <= self.fd_zero_min_limit + 1:
                    self.fd_zero_min = True

        self._sample_count += 1
        if self._sample_count >= self.osr:
            # Latch to shadow
            self.shadow_acc1 = self.acc1
            self.shadow_acc2 = self.acc2
            self.shadow_acc3 = self.acc3
            self.valid = True
            self._sample_count = 0

    def read_and_clear_valid(self) -> None:
        """Clear the valid flag (called when PRU reads R31)."""
        self.valid = False

    def reinit(self) -> None:
        """Reset accumulators and sample counter to zero. Shadow preserved.

        Note: fd_window_size is validated in tick() and must be 0-7 when fd_en is True.
        """
        self.acc1 = 0
        self.acc2 = 0
        self.acc3 = 0
        self._sample_count = 0
        # Reset FD internal counters (not flags — those are sticky until explicitly cleared)
        self._fd_shift_reg = 0
        self._fd_sample_count = 0

    def clear_valid_and_ovf(self) -> None:
        """Clear both valid and overflow flags (R31 write bit[24] command per Verilog)."""
        self.valid = False
        self.ovf = False

    def clear_ovf(self) -> None:
        """Clear the overflow flag."""
        self.ovf = False

    def snapshot(self) -> dict:
        """Return a copy of all mutable state for step-back."""
        return {
            "osr": self.osr,
            "acc1": self.acc1, "acc2": self.acc2, "acc3": self.acc3,
            "shadow_acc1": self.shadow_acc1, "shadow_acc2": self.shadow_acc2,
            "shadow_acc3": self.shadow_acc3,
            "valid": self.valid, "ovf": self.ovf,
            "fd_en": self.fd_en, "fd_window_size": self.fd_window_size,
            "fd_one_max_limit": self.fd_one_max_limit,
            "fd_one_min_limit": self.fd_one_min_limit,
            "fd_zero_max_limit": self.fd_zero_max_limit,
            "fd_zero_min_limit": self.fd_zero_min_limit,
            "fd_one_max": self.fd_one_max, "fd_one_min": self.fd_one_min,
            "fd_zero_max": self.fd_zero_max, "fd_zero_min": self.fd_zero_min,
            "_fd_shift_reg": self._fd_shift_reg,
            "_fd_sample_count": self._fd_sample_count,
            "_sample_count": self._sample_count,
        }

    def restore(self, snap: dict) -> None:
        """Restore all mutable state from a snapshot."""
        self.osr = snap["osr"]
        self.acc1 = snap["acc1"]
        self.acc2 = snap["acc2"]
        self.acc3 = snap["acc3"]
        self.shadow_acc1 = snap["shadow_acc1"]
        self.shadow_acc2 = snap["shadow_acc2"]
        self.shadow_acc3 = snap["shadow_acc3"]
        self.valid = snap["valid"]
        self.ovf = snap["ovf"]
        self.fd_en = snap["fd_en"]
        self.fd_window_size = snap["fd_window_size"]
        self.fd_one_max_limit = snap["fd_one_max_limit"]
        self.fd_one_min_limit = snap["fd_one_min_limit"]
        self.fd_zero_max_limit = snap["fd_zero_max_limit"]
        self.fd_zero_min_limit = snap["fd_zero_min_limit"]
        self.fd_one_max = snap["fd_one_max"]
        self.fd_one_min = snap["fd_one_min"]
        self.fd_zero_max = snap["fd_zero_max"]
        self.fd_zero_min = snap["fd_zero_min"]
        self._fd_shift_reg = snap["_fd_shift_reg"]
        self._fd_sample_count = snap["_fd_sample_count"]
        self._sample_count = snap["_sample_count"]

    def get_data(self, acc_sel: int) -> int:
        """Return the shadow accumulator value selected by *acc_sel*.

        acc_sel: 0=acc3 (sinc3), 1=acc2 (sinc2), 2=acc1 (sinc1)
        Raises ValueError for acc_sel outside {0, 1, 2}.
        """
        if acc_sel == 0:
            return self.shadow_acc3 & _ACC_MAX
        elif acc_sel == 1:
            return self.shadow_acc2 & _ACC_MAX
        elif acc_sel == 2:
            return self.shadow_acc1 & _ACC_MAX
        else:
            raise ValueError(f"acc_sel must be 0, 1, or 2; got {acc_sel}")
