"""Timed serial-sample loopback: source-core TX channel -> target-core RX channel.

Wires PRU0 channel-N TX data line into core-1 channel-N RX input, applying a
configurable latency, jitter and inter-core clock drift (up to +/-100 ppm).

The two cores keep independent nanosecond timelines (advanced from their own
cycle counts).  When the target RX samples its input at target-time `t`, the
loopback maps that back to a source-time and reads the recorded source-line
level there:

    src_t = (t - latency_ns + jitter(t)) / (1 + drift_ppm / 1e6)

Because the source records timestamped line transitions, the two cores can be
stepped independently; large drift makes the RX oversampler slip bits, exactly
as on real hardware.  Jitter is deterministic (a hash of the sample time) so
runs and step-back are reproducible.
"""

from __future__ import annotations

_NUM_CHANNELS = 3


class Loopback:
    def __init__(self, source_perif, target_perif):
        self.source = source_perif
        self.target = target_perif
        # Per-channel parameters.
        self.enabled = [False] * _NUM_CHANNELS
        self.latency_ns = [0.0] * _NUM_CHANNELS
        self.jitter_ns = [0.0] * _NUM_CHANNELS
        self.drift_ppm = [0.0] * _NUM_CHANNELS

    def configure(self, channel: int, enabled: bool, latency_ns: float = 0.0,
                  jitter_ns: float = 0.0, drift_ppm: float = 0.0) -> None:
        if channel < 0 or channel >= _NUM_CHANNELS:
            raise ValueError(f"Channel {channel} out of range 0-{_NUM_CHANNELS-1}")
        self.enabled[channel] = enabled
        self.latency_ns[channel] = float(latency_ns)
        self.jitter_ns[channel] = max(0.0, min(float(jitter_ns), 1e6))
        self.drift_ppm[channel] = max(-100.0, min(float(drift_ppm), 100.0))
        # Wire (or clear) the target RX input source for this channel.
        tgt_ch = self.target.channels[channel]
        if enabled:
            tgt_ch.rx_line_source = lambda t, ch=channel: self.sample(ch, t)
        else:
            tgt_ch.rx_line_source = None

    def _jitter(self, channel: int, t_ns: float) -> float:
        j = self.jitter_ns[channel]
        if j <= 0.0:
            return 0.0
        # Deterministic pseudo-random offset in [-j, +j] from the sample time.
        h = (int(t_ns) * 2654435761) & 0xFFFFFFFF
        frac = h / 0xFFFFFFFF          # 0..1
        return (frac * 2.0 - 1.0) * j

    def sample(self, channel: int, target_ns: float) -> int:
        """Return the source TX line level seen by the target RX at *target_ns*."""
        drift = self.drift_ppm[channel]
        src_t = (target_ns - self.latency_ns[channel] + self._jitter(channel, target_ns))
        src_t /= (1.0 + drift / 1e6)
        return self.source.channels[channel].tx_line_at(src_t)

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "enabled": list(self.enabled),
            "latency_ns": list(self.latency_ns),
            "jitter_ns": list(self.jitter_ns),
            "drift_ppm": list(self.drift_ppm),
        }

    def restore(self, snap: dict) -> None:
        for ch in range(_NUM_CHANNELS):
            self.configure(ch, snap["enabled"][ch], snap["latency_ns"][ch],
                           snap["jitter_ns"][ch], snap["drift_ppm"][ch])

    def get_state(self) -> dict:
        return {
            "channels": [
                {
                    "channel": ch,
                    "enabled": self.enabled[ch],
                    "latency_ns": self.latency_ns[ch],
                    "jitter_ns": self.jitter_ns[ch],
                    "drift_ppm": self.drift_ppm[ch],
                }
                for ch in range(_NUM_CHANNELS)
            ]
        }
