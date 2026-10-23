# Device capabilities

`core.device_capabilities.get_device_capabilities(target)` is the single query
for immutable silicon capabilities. `Simulator.capabilities` and each core's
`capabilities` expose its result. Targets are case insensitive and accept the
config's `#` inline comments. Missing targets default to AM243x; unknown names
raise `ValueError`. The target profile supplies the core version; the legacy
`core_version` config field does not select a different ISA.

Capabilities describe hardware presence, including hardware that is not yet
simulated. Broadside IDs are the union across the subsystem: existing core
restrictions still apply (for example, XFR2TR is RTU only). TX_PRU is present in
ICSSG silicon but is not simulated. All four supported targets use V4 ISA;
ICSSM is not a V3 instruction-set restriction.

| Target | Core version | Broadside ID set below | RTU | TX_PRU | `icssg_pwm` | Source |
|---|---|---|---|---|---|---|
| AM243x | V4 | ICSSG | true | true | true | SPRUIM2J §§6.4.1–6.4.4, Table 6-60, §6.4.10 (PWM) |
| AM64x | V4 | ICSSG | true | true | true | SPRUIM2J covers both AM64x and AM243x, same sections |
| AM261x | V4 | ICSSM | false | false | false | SPRUJB6B §§7.3.1–7.3.4, Table 7-35; §7.3.1.2 (integrated PWM module not supported); V4 clarification from TI hardware owners in [#32](https://github.com/TexasInstruments/pru-simulator/issues/32#issuecomment-6115296936) |
| AM263x | V4 | ICSSM | false | false | false | Inferred from the AM261x table (SPRUJB6B Table 7-35) plus the hardware-owner statement in [#32](https://github.com/TexasInstruments/pru-simulator/issues/32) that AM261x/AM263x ICSSM are equivalent; no AM263x TRM was reviewed |

The PWM flag means the **ICSSG PWM module**, not SoC ePWM or firmware-generated
PWM. No PWM peripheral is implemented by this change.

## Broadside IDs

TRM entries without `0x` are decimal. ICSSM's exact set (SPRUJB6B Table 7-35) is:

| Widget | IDs |
|---|---|
| MAC, CRC | 0, 1 |
| SPAD banks 0–2 | 10, 11, 12 |
| RX L2, TX L2 | 20, 21, 40 |
| XFR2VBUS | 0x60–0x63 |

ICSSG adds these IDs (SPRUIM2J Table 6-60, pp.572–573):

| Widget | IDs |
|---|---|
| SUM32, stitch FIFO, queue pointer | 2, 8, 9, 38, 39, 47, 49 |
| IPC SPAD, RX classifier | 15, 22 |
| FDB / broadside RAM / RX data, depending on core and mode | 30–35, 38, 48, 49 |
| XFR2PSI and shared access | 0x50–0x54, 0x58, 0x59 |
| TX_PRU XFR2VBUS read | 0x64 |
| XFR2TR | 0x70–0x72 |
| Spinlock, BSWAP, queue empty | 0x90, 0xA0–0xA2, 0xF0 |

The fourth PSI receive thread, 0x54, supplements Table 6-60's abbreviated
0x50–0x53 range: Table 6-108 (XFR STATUS has four `rx_ready`/`tx_ready` bits,
one per PSI-L thread) and the XFRDMA statement that XID N serves the N-th
thread, together with TI `icss_xfer_defines.inc` and the independent silicon
observations documented in `xfr/xfrdma_accelerator.py`, justify it.

## Absent and unmodelled transfers

AM261x/AM263x instantiate only MAC, CRC and XFR2VBUS accelerators. Shared SPAD
banks remain available. They have no BSWAP, PSI DMA, IPC SPAD or XFR2TR; the
simulator now exposes only PRU0 and PRU1 on those targets. ICSSG's existing
accelerators and four simulated PRU/RTU cores remain available.

On LP-AM261 ICSSM0 PRU0, with R2–R9 preloaded, `XIN 161, &r2, 16` zeroed R2–R5
and left R6–R9 intact ([silicon report in #32](https://github.com/TexasInstruments/pru-simulator/issues/32)).
The simulator writes zero bytes only to the requested destination range.
This silicon observation applies to **AM261x BSWAP_4_8 XIN only**; it establishes
neither other absent IDs' behaviour nor AM263x silicon behaviour.

All absent/unmodelled IDs reuse `PRUCore.unsupported_xfr`: one warning per ID,
a record for every transfer, and the existing zero-read / discard-write
compatibility fallback. Warnings explicitly identify this fallback as unverified
for other absent IDs. Set `core.strict_unsupported_xfr = True` to raise
`UnsupportedXFRError` before the transfer changes registers or bus state.
Strict mode also rejects the measured BSWAP ID so firmware can be checked for
unsupported accelerator use. Reset clears the diagnostic record.
Explicit caller registrations in `core.accelerators` still take precedence,
allowing custom models and instrumentation; built-in construction is filtered.

No repository assembly example uses BSWAP on AM26x. The external OpenPRU
`examples/fft/split_radix_fft_4k_single_core` AM261x-LP build cited in #32 does:
`fft_windowed_4k.asm` uses `xin BSWAP_4_8_XID, &CC1, 16` without a device guard.
It now receives zero data with a diagnostic instead of reaching the ICSSG
BSWAP model (which previously rejected that transfer length).
The repository's `source/xfr_test.asm` also writes IPC SPAD 15; on ICSSM this
now warns and discards the write, while its R10 scratchpad result stays 0xCAFE.

Regression tests run CRC, CRC bit-mirror, scratchpad and the multiply-only
section of the MAC example on both ICSSM targets. The complete MAC example has
an inherited discrepancy: its dot-product section returns 23 instead of the
header's 32 on both the base and this change. That section lacks the operand
latency handling needed by the existing MAC model; it is outside this change.

AM263x reserved C10 (#79), constant-table fallback rules, V3 ISA enforcement,
new accelerator/peripheral implementations and hardware validation are outside
this change. In particular C10 is unchanged pending AM263x silicon evidence.
