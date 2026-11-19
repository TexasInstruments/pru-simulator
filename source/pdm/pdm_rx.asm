;******************************************************************************
; File: pdm_rx.asm
;
; Description:
;   PRU0 PDM microphone receiver of the PDM audio demo: N_CH channels, each
;   SINC3 decimation by SD_OSR in the ICSS sigma-delta (SD) hardware plus a
;   firmware comb (32 kHz), the 63-tap stage-2 FIR decimating by 2 (16 kHz),
;   a 20 Hz DC blocker and sat24. Channel k's signed 24-bit PCM goes to PCM
;   ring k in ICSS shared RAM (pdm_shared.inc), then HDR_PCM_COUNT[k].
;   Python reference: source/pdm/reference.py (pcm_chain); tests:
;   tests/test_pdm_rx.py.
;
;   Port of audio_pipe firmware/pdm_rx.asm (single channel, bit-exact
;   verified). The DSP blocks are unchanged: comb mod 2^28, the MAC in
;   multiply-only mode with signed correction and the one-NOP operand
;   latency, the Q27 FIR from fir_coefs.inc (tools/gen_fir_coefs.py from
;   decimation_fir.h), the Q32 DC blocker and sat24. New: N_CH channels
;   with per-channel state blocks, round-robin over the one R31 SD view,
;   FIR stagger, output to shared RAM.
;
;   Stimulus in the demo: PRU1 pdm_gen.asm drives the 3.072 MHz PDM clock
;   and one data line carrying two microphones: left valid at the rising
;   edge (SD ch0), right at the falling edge (SD ch1).
;
; Numbers (defaults): 3.072 MHz / 96 = 32 kHz SINC3 (6250 PRU cycles per SD
;   window at 200 MHz), FIR every 2nd sample -> 16 kHz PCM.
;   y = (sum T[j] * D[w+1+j] + 2^26) >> 27, T in Q27 scaled by
;   2^23 / (OSR^3 / 2) so full-scale SINC3 input = +-2^23.
;   DC blocker (DC_BLOCK = 1), H(z) = a (1 - z^-1) / (1 - a z^-1), a = 1 - 2^-7,
;   -3 dB at 20.1 Hz: e += (y * 2^32 - e) >> 7 (int64 Q32, floor shift; no
;   dead-band), y -= round(e / 2^32). PCM = sat24(y).
;
; Scheduling: the SD R31 interface shows one channel at a time (ch_sel in
;   R30[29:26]), so the main loop round-robins ch 0..N_CH-1 once per SD
;   window: select ch k, spin on R31[28] (valid), read the acc3 shadow,
;   clear valid, comb, store state, next channel. All channels run on one
;   PDM clock with the same OSR and are reinitialised back to back, so their
;   shadows latch within about one PDM period of each other. A shadow is
;   overwritten 6250 cycles after it latched: one round must stay below
;   that (tests/test_pdm_rx.py measures it).
;   FIR stagger: odd channels drop WARMUP_SAMPLES + 1 comb outputs, even
;   channels WARMUP_SAMPLES. Every channel runs its FIR on its own 2nd,
;   4th, ... sample, so even channels FIR on global decimation phase 0 and
;   odd channels on phase 1, and a window runs at most ceil(N_CH / 2) FIRs.
;   Odd channel k publishes PCM n one window after channel k - 1. With N_CH
;   even the last channel is the last to finish a block, so the block event
;   of ch N_CH-1 means every ring holds the block (with N_CH odd >= 3 the
;   odd channels finish one window after the event).
;
;   Cycle budget per window (PRU cycles at 200 MHz, worst cases measured in
;   pru-simulator at N_CH = 2 over 600 windows by
;   tests/test_pdm_rx.py::test_worst_window_fits_budget; a path runs from a
;   channel's spin exit to the next spin entry):
;     path                                   cycles
;     w_comb  comb + state, next channel         48
;     w_fir   FIR + DC + sat + publish on top   905   (FIR step 919..953:
;             MAC sign correction +1 per negative operand, DC/sat branches)
;     overhead: last channel (wrap, event        +7
;             check) + spin of ch1                +1
;     N_CH = 2 worst window                    1009   (budget 6250)
;     N_CH = 8: 8 w_comb + 4 w_fir + overhead = 4012 (64 %); pessimistic
;             (every FIR input negative: +64 per FIR; shadows one PDM
;             period apart: +66) = 4334 (69 %)
;   The 8-channel figure is a count of the measured paths: the simulator
;   models SD channels 0..2 only.
;
; DRAM0 (c24):
;   0x800  64 x Q27 coefficients T[j], oldest input first (FIR_T_BASE),
;          shared by all channels
;   0x900  channel block k at CH_BASE + k * CH_STRIDE (0x220), 8 blocks
;          up to 0x1A00:
;     +0x00  comb state x[-1], d3, d5 (3 words)
;     +0x0C  PCM count (copy of HDR_PCM_COUNT[k])
;     +0x10  state: b0 warm-up count, b1 decimation phase, b2 delay index w,
;            b3 SD R30.b3 value (SD_CH_SEL_B3_CHk)
;     +0x14  SAMPLE_COUNT: 32 kHz samples after the warm-up
;     +0x18  DC estimate e, int64 Q32 (low word first)
;     +0x20  128 x int32 mirrored delay line: sample at index w in D[w],
;            D[w + 64]
;   Everything stays in PRU0's own DRAM0 (c25 would be PRU1's data RAM,
;   which the host re-initialises when it loads PRU1).
;
; Shared RAM (pdm_shared.inc, base PDM_SHARED_BASE held in r22):
;   ring k, sample n at PDM_RING_BASE + k * PDM_RING_STRIDE + 4 * (n mod
;   256); HDR_PCM_COUNT[k] stored after the sample (publish order of
;   audio_pipe). An R31 event BLOCK_EVENT (host system event 16 +
;   BLOCK_EVENT) is raised when ch N_CH-1 completes a block of BLOCK_SAMPLES.
;   The init zeroes HDR_PCM_COUNT[0..7].
;
; ---- Register map --------------------------------------------
; Reg       Use                                   Live in
; r0, r1    scratch (r1.b0/b1 mvid pointers,      everywhere
;           r1.w2 FIR input offset)
; r2-r9     FIR coefficient block / sample        per channel
;           scratch (r5-r9 DC blocker)
; r10-r15   channel state block +0x00..+0x17      per channel
; r10-r17   FIR input block (state saved first)   FIR
; r18, r19  64-bit accumulator                    FIR
; r20       DRAM0 address of channel block k      main loop
; r21       channel index k                       main loop
; r22       PDM_SHARED_BASE                       main loop
; r23       28-bit mask                           main loop
; r24       R31 command: clear valid + ovf        main loop
; r25-r29   MAC                                   FIR
; R30       only R30.b3 (SD channel select + sd_en) is written; GPO
;           R30[19:0] is untouched
;
; Host preconditions (not done here, to stay device agnostic):
;   - PRU0 GPI mux in SD mode (ICSSG: GPCFG0 PRU0_GP_MUX_SEL = 3); without it
;     R31[28] never rises and PDM_WAIT spins forever.
;   - SD_CFG_REG (ICSSG C4+0x44): SHARE_EN = 0, MAN_SD_EN = 0.
;   - Pinmux for the SDn_D / SDn_CLK pins of the channels in use.
;   - INTC mapping of system event 16 + BLOCK_EVENT to a host interrupt.
;   - CTBIR C24_BLK_INDEX = 0 (c24 = PRU0 DRAM0 at local address 0).
;
; 8-microphone hardware assumption (per-channel tables below): mic pair p
;   (channels 2p, 2p+1) shares one data line on SD(2p)_D and SD(2p+1)_D.
;   Even channel k samples its own SDk_CLK (CLK_SEL 1) on the rising edge;
;   odd channel k the group clock SD(3*(k/3))_CLK (CLK_SEL 2) inverted
;   (CLK_INV 1), i.e. the falling edge. The PDM clock is therefore wired to
;   SD0/2/3/4/6_CLK. Demo and simulator: N_CH = 2, both on SD0_CLK.
;******************************************************************************

    .global PDM_RX
    .include "pdm_shared.inc"

;------------------------------------------------------------------------------
; Build options. Keep every value a plain literal: pru-simulator does not
; expand a .set that refers to another .set symbol. Derived literals are
; guarded below.
;------------------------------------------------------------------------------
N_CH                .set    2           ; channels 1..8 (simulator: 1..2 wired)
SD_OSR              .set    96          ; oversampling ratio 1..256
SD_SAMPLE_SIZE      .set    95          ; SD_OSR - 1
SD_HALF_SCALE       .set    442368      ; SD_OSR^3 / 2
DC_BLOCK            .set    1           ; 1 = 20 Hz DC blocker on, 0 = off
BLOCK_EVENT         .set    0           ; R31 event 0..15 per block of ch N_CH-1, -1 = none
WARMUP_SAMPLES      .set    3           ; comb outputs dropped after reinit (+1 on odd ch)
BLOCK_SAMPLES       .set    128         ; PCM samples per block event, power of two
BLOCK_MASK          .set    127         ; BLOCK_SAMPLES - 1
FIR_T_BASE          .set    0x800       ; coefficient table in DRAM0
CH_BASE             .set    0x900       ; channel block 0 in DRAM0 (FIR_T_BASE + 0x100)
CH_STRIDE           .set    0x220       ; bytes per channel block
CH_STATE_OFS        .set    0x10        ; state word in a channel block
CH_DC_OFS           .set    0x18        ; DC estimate in a channel block
CH_D_OFS            .set    0x20        ; delay line in a channel block

; Per-channel SD clock select (1 = own SDk_CLK, 2 = group clock
; SD(3*(k/3))_CLK), clock invert (0 = rising, 1 = falling edge) and R30.b3
; value (k << 2 | sd_en).
SD_CLK_SEL_CH0      .set    1
SD_CLK_INV_CH0      .set    0
SD_CH_SEL_B3_CH0    .set    0x02
SD_CLK_SEL_CH1      .set    2
SD_CLK_INV_CH1      .set    1
SD_CH_SEL_B3_CH1    .set    0x06
SD_CLK_SEL_CH2      .set    1
SD_CLK_INV_CH2      .set    0
SD_CH_SEL_B3_CH2    .set    0x0A
SD_CLK_SEL_CH3      .set    2
SD_CLK_INV_CH3      .set    1
SD_CH_SEL_B3_CH3    .set    0x0E
SD_CLK_SEL_CH4      .set    1
SD_CLK_INV_CH4      .set    0
SD_CH_SEL_B3_CH4    .set    0x12
SD_CLK_SEL_CH5      .set    2
SD_CLK_INV_CH5      .set    1
SD_CH_SEL_B3_CH5    .set    0x16
SD_CLK_SEL_CH6      .set    1
SD_CLK_INV_CH6      .set    0
SD_CH_SEL_B3_CH6    .set    0x1A
SD_CLK_SEL_CH7      .set    2
SD_CLK_INV_CH7      .set    1
SD_CH_SEL_B3_CH7    .set    0x1E

    .if (N_CH < 1) | (N_CH > 8)
    .emsg "N_CH must be 1..8"
    .endif
    .if (SD_OSR < 1) | (SD_OSR > 256)
    .emsg "SD_OSR must be 1..256"
    .endif
    .if (SD_SAMPLE_SIZE != SD_OSR - 1) | (SD_HALF_SCALE != SD_OSR * SD_OSR * SD_OSR / 2)
    .emsg "SD_SAMPLE_SIZE / SD_HALF_SCALE do not match SD_OSR"
    .endif
    .if (SD_CH_SEL_B3_CH0 != 0x02) | (SD_CH_SEL_B3_CH1 != 0x06) | (SD_CH_SEL_B3_CH2 != 0x0A) | (SD_CH_SEL_B3_CH3 != 0x0E)
    .emsg "SD_CH_SEL_B3_CHk must be k * 4 + 2 (ch 0..3)"
    .endif
    .if (SD_CH_SEL_B3_CH4 != 0x12) | (SD_CH_SEL_B3_CH5 != 0x16) | (SD_CH_SEL_B3_CH6 != 0x1A) | (SD_CH_SEL_B3_CH7 != 0x1E)
    .emsg "SD_CH_SEL_B3_CHk must be k * 4 + 2 (ch 4..7)"
    .endif
    .if (SD_CLK_SEL_CH0 < 1) | (SD_CLK_SEL_CH0 > 2) | (SD_CLK_SEL_CH1 < 1) | (SD_CLK_SEL_CH1 > 2) | (SD_CLK_SEL_CH2 < 1) | (SD_CLK_SEL_CH2 > 2) | (SD_CLK_SEL_CH3 < 1) | (SD_CLK_SEL_CH3 > 2)
    .emsg "SD_CLK_SEL_CHk must be 1 (own clock) or 2 (group clock) (ch 0..3)"
    .endif
    .if (SD_CLK_SEL_CH4 < 1) | (SD_CLK_SEL_CH4 > 2) | (SD_CLK_SEL_CH5 < 1) | (SD_CLK_SEL_CH5 > 2) | (SD_CLK_SEL_CH6 < 1) | (SD_CLK_SEL_CH6 > 2) | (SD_CLK_SEL_CH7 < 1) | (SD_CLK_SEL_CH7 > 2)
    .emsg "SD_CLK_SEL_CHk must be 1 (own clock) or 2 (group clock) (ch 4..7)"
    .endif
    .if (SD_CLK_INV_CH0 > 1) | (SD_CLK_INV_CH1 > 1) | (SD_CLK_INV_CH2 > 1) | (SD_CLK_INV_CH3 > 1) | (SD_CLK_INV_CH4 > 1) | (SD_CLK_INV_CH5 > 1) | (SD_CLK_INV_CH6 > 1) | (SD_CLK_INV_CH7 > 1)
    .emsg "SD_CLK_INV_CHk must be 0 or 1"
    .endif
    .if (BLOCK_MASK != BLOCK_SAMPLES - 1) | (PDM_RING_SAMPLES % BLOCK_SAMPLES != 0) | (BLOCK_EVENT > 15) | (BLOCK_EVENT < -1)
    .emsg "BLOCK_SAMPLES / BLOCK_MASK / BLOCK_EVENT inconsistent"
    .endif
    .if (PDM_RING_SAMPLES != 256) | (PDM_RING_STRIDE != 0x400)
    .emsg "ring addressing (and 0xFF, lsl 10) assumes 256-sample rings at stride 0x400"
    .endif
    .if (CH_BASE != FIR_T_BASE + 0x100) | (CH_STRIDE != CH_D_OFS + 0x200) | (CH_BASE + 8 * CH_STRIDE > 0x2000)
    .emsg "FIR_T_BASE / CH_BASE / CH_STRIDE overlap or leave PRU0 DRAM0"
    .endif
    .if (CH_STATE_OFS != 0x10) | (CH_DC_OFS != 0x18) | (CH_D_OFS != 0x20) | (DC_BLOCK < 0) | (DC_BLOCK > 1)
    .emsg "channel block layout or DC_BLOCK inconsistent"
    .endif

;------------------------------------------------------------------------------
; m_pdm_ch_init: configure SD channel k and its state block (init only).
;   Pseudo code: SD_CLK_SEL_REGk = clk_inv << 2 | clk_sel (ACC_SEL = acc3);
;     SD_SAMPLE_SIZE_REGk = SD_SAMPLE_SIZE; state(k) = {warm-up, 0, 0,
;     ch_sel_b3}; r20 += CH_STRIDE.
;   PEAK cycles: 15 instructions (3 SBCO). Registers modified: r0, r14, r20.
;   Parameters: clk_sel 1..2, clk_inv 0..1, ch_sel_b3 = k * 4 + 2,
;     clk_ofs = 0x48 + 8k, ss_ofs = 0x4C + 8k, odd = k & 1.
;   r20 = CH_BASE + k * CH_STRIDE on entry.
;------------------------------------------------------------------------------
m_pdm_ch_init .macro clk_sel, clk_inv, ch_sel_b3, clk_ofs, ss_ofs, odd
    ldi     r0, clk_inv
    lsl     r0, r0, 2
    or      r0, r0, clk_sel                 ; ACC_SEL[5:4] = 0 (acc3)
    sbco    &r0, c4, clk_ofs, 4             ; SD_CLK_SEL_REGk
    ldi     r0, SD_SAMPLE_SIZE
    sbco    &r0, c4, ss_ofs, 4              ; SD_SAMPLE_SIZE_REGk
    ldi     r14.b0, WARMUP_SAMPLES
    add     r14.b0, r14.b0, odd             ; FIR stagger: odd ch one more
    ldi     r14.b1, 0                       ; decimation phase
    ldi     r14.b2, 0                       ; delay index w
    ldi     r14.b3, ch_sel_b3
    add     r0, r20, CH_STATE_OFS
    sbco    &r14, c24, r0, 4
    ldi     r0, CH_STRIDE
    add     r20, r20, r0
    .endm

;------------------------------------------------------------------------------
; m_pdm_ch_reinit: select SD channel k, reinit its accumulators, drop a
;   stale valid shadow (reinit keeps it). PEAK cycles: 3.
;   Registers modified: r30.b3. Inputs: r0 = 0x00800000, r24 = clear command.
;------------------------------------------------------------------------------
m_pdm_ch_reinit .macro ch_sel_b3
    ldi     r30.b3, ch_sel_b3               ; R30[29:26] = k, R30[25] = sd_en
    mov     r31, r0                         ; one 32-bit write: reinit only
    mov     r31, r24                        ; clear valid + ovf
    .endm

PDM_RX:
    ; --- zero the 8 channel blocks and HDR_PCM_COUNT[0..7] -----------------
    ldi     r10, 0
    ldi     r11, 0
    ldi     r12, 0
    ldi     r13, 0
    ldi     r14, 0
    ldi     r15, 0
    ldi     r16, 0
    ldi     r17, 0
    ldi     r0, CH_BASE
    loop    PDM_ZERO, 136                   ; 8 * CH_STRIDE / 32
    sbco    &r10, c24, r0, 32
    add     r0, r0, 32
PDM_ZERO:
    ldi32   r22, PDM_SHARED_BASE
    sbbo    &r10, r22, PDM_HDR_PCM_COUNT, 32

    ; --- coefficient table T to DRAM0 FIR_T_BASE (generated) ---------------
    ldi     r1, FIR_T_BASE
    .include "fir_coefs.inc"
    .if FIR_COEF_OSR != SD_OSR
    .emsg "fir_coefs.inc was generated for a different SD_OSR"
    .endif

    ; --- MAC: multiply-only mode -------------------------------------------
    ldi     r25, 0
    xout    0, &r25, 1

    ; --- SD channels: clock, OSR, state blocks; then reinit back to back ----
    ldi32   r23, 0x0FFFFFFF                 ; 28-bit accumulator mask
    ldi32   r24, 0x01000000                 ; R31 bit 24: clear valid + ovf
    ldi     r20, CH_BASE
    m_pdm_ch_init SD_CLK_SEL_CH0, SD_CLK_INV_CH0, SD_CH_SEL_B3_CH0, 0x48, 0x4C, 0
    .if N_CH > 1
    m_pdm_ch_init SD_CLK_SEL_CH1, SD_CLK_INV_CH1, SD_CH_SEL_B3_CH1, 0x50, 0x54, 1
    .endif
    .if N_CH > 2
    m_pdm_ch_init SD_CLK_SEL_CH2, SD_CLK_INV_CH2, SD_CH_SEL_B3_CH2, 0x58, 0x5C, 0
    .endif
    .if N_CH > 3
    m_pdm_ch_init SD_CLK_SEL_CH3, SD_CLK_INV_CH3, SD_CH_SEL_B3_CH3, 0x60, 0x64, 1
    .endif
    .if N_CH > 4
    m_pdm_ch_init SD_CLK_SEL_CH4, SD_CLK_INV_CH4, SD_CH_SEL_B3_CH4, 0x68, 0x6C, 0
    .endif
    .if N_CH > 5
    m_pdm_ch_init SD_CLK_SEL_CH5, SD_CLK_INV_CH5, SD_CH_SEL_B3_CH5, 0x70, 0x74, 1
    .endif
    .if N_CH > 6
    m_pdm_ch_init SD_CLK_SEL_CH6, SD_CLK_INV_CH6, SD_CH_SEL_B3_CH6, 0x78, 0x7C, 0
    .endif
    .if N_CH > 7
    m_pdm_ch_init SD_CLK_SEL_CH7, SD_CLK_INV_CH7, SD_CH_SEL_B3_CH7, 0x80, 0x84, 1
    .endif
    ldi32   r0, 0x00800000                  ; R31 bit 23: reinit accumulators
    m_pdm_ch_reinit SD_CH_SEL_B3_CH0
    .if N_CH > 1
    m_pdm_ch_reinit SD_CH_SEL_B3_CH1
    .endif
    .if N_CH > 2
    m_pdm_ch_reinit SD_CH_SEL_B3_CH2
    .endif
    .if N_CH > 3
    m_pdm_ch_reinit SD_CH_SEL_B3_CH3
    .endif
    .if N_CH > 4
    m_pdm_ch_reinit SD_CH_SEL_B3_CH4
    .endif
    .if N_CH > 5
    m_pdm_ch_reinit SD_CH_SEL_B3_CH5
    .endif
    .if N_CH > 6
    m_pdm_ch_reinit SD_CH_SEL_B3_CH6
    .endif
    .if N_CH > 7
    m_pdm_ch_reinit SD_CH_SEL_B3_CH7
    .endif
    ldi     r20, CH_BASE
    ldi     r21, 0

    ; --- main loop: one channel per pass, N_CH passes per SD window ---------
PDM_CH:
    lbco    &r10, c24, r20, 24              ; comb, PCM count, state, SAMPLE_COUNT
    mov     r30.b3, r14.b3                  ; select SD channel k
PDM_WAIT:
    qbbc    PDM_WAIT, r31, 28               ; wait for shadow valid
    and     r2, r31, r23                    ; acc3 shadow
    mov     r31, r24                        ; one 32-bit write: clear valid + ovf

    ; --- comb3: y = x - 3x[-1] + 3x[-2] - x[-3], modulo 2^28 ---------------
    rsb     r6, r10, r2
    mov     r10, r2
    rsb     r7, r11, r6
    mov     r11, r6
    rsb     r8, r12, r7
    mov     r12, r7
    and     r8, r8, r23

    qbeq    PDM_SAMPLE, r14.b0, 0           ; drop comb warm-up outputs
    sub     r14.b0, r14.b0, 1
    sbco    &r10, c24, r20, 24
    qba     PDM_NEXT

PDM_SAMPLE:
    ldi32   r0, SD_HALF_SCALE
    sub     r8, r8, r0                      ; signed 32 kHz sample
    add     r15, r15, 1                     ; SAMPLE_COUNT
    lsl     r0, r14.b2, 2                   ; D[w] and D[w + 64]
    add     r0, r0, r20
    add     r0, r0, CH_D_OFS
    sbco    &r8, c24, r0, 4
    ldi     r1, 0x100                       ; mirror distance: 64 words
    add     r0, r0, r1
    sbco    &r8, c24, r0, 4
    xor     r14.b1, r14.b1, 1               ; decimation phase
    qbeq    PDM_FIR, r14.b1, 0              ; every 2nd sample: FIR
    add     r14.b2, r14.b2, 1               ; w = (w + 1) mod 64
    and     r14.b2, r14.b2, 63
    sbco    &r10, c24, r20, 24
    qba     PDM_NEXT

    ; --- FIR: acc = sum_j T[j] * D[w + 1 + j], 8 blocks of 8 taps ----------
PDM_FIR:
    sbco    &r10, c24, r20, 24              ; save state (r10-r17 become inputs)
    add     r0, r14.b2, 1
    lsl     r0, r0, 2
    add     r0, r0, CH_D_OFS
    add     r0, r0, r20
    mov     r1.w2, r0.w0                    ; input offset of D[w + 1]
    ldi     r0, FIR_T_BASE                  ; coefficient offset
    ldi     r18, 0
    ldi     r19, 0
    loop    PDM_FIR_END, 8
    lbco    &r2, c24, r0, 32                ; T[8b .. 8b+7]
    lbco    &r10, c24, r1.w2, 32            ; D[w+1+8b .. w+8+8b]
    ldi     r1.b0, 8                        ; mvid pointer -> r2
    ldi     r1.b1, 40                       ; mvid pointer -> r10
PDM_TAP:
    mvid    r28, *r1.b0++                   ; coefficient
    mvid    r29, *r1.b1++                   ; input
    nop
    xin     0, &r26, 8                      ; unsigned 64-bit product r27:r26
    qbbc    PDM_SGN1, r28, 31               ; signed correction
    sub     r27, r27, r29
PDM_SGN1:
    qbbc    PDM_SGN2, r29, 31
    sub     r27, r27, r28
PDM_SGN2:
    add     r18, r18, r26                   ; 64-bit accumulate
    adc     r19, r19, r27
    qbgt    PDM_TAP, r1.b0, 40              ; 8 taps per block
    add     r0, r0, 32
    add     r1.w2, r1.w2, 32
PDM_FIR_END:

    ; --- round, >> 27, saturate to +-(2^23 - 1) -----------------------------
    ldi32   r0, 0x04000000
    add     r18, r18, r0
    adc     r19, r19, 0
    lsr     r2, r18, 27
    lsl     r3, r19, 5
    or      r2, r2, r3                      ; y = acc >> 27
    .if DC_BLOCK != 0
    ; --- DC blocker: e += (y * 2^32 - e) >> 7, y -= round(e / 2^32) -------
    ; Borrow and sign are handled with branches, not suc/rsc (their carry
    ; convention is not needed, so the V3/V4 carry semantics do not matter).
    add     r0, r20, CH_DC_OFS
    lbco    &r6, c24, r0, 8                 ; e = r7:r6
    rsb     r8, r6, 0                       ; d = y * 2^32 - e: low 0 - e_lo
    sub     r9, r2, r7                      ;   high y - e_hi ...
    qbeq    PDM_DC_NB, r6, 0
    sub     r9, r9, 1                       ;   ... - borrow (e_lo != 0)
PDM_DC_NB:
    lsr     r8, r8, 7                       ; d >> 7, 64-bit arithmetic
    lsl     r5, r9, 25
    or      r8, r8, r5
    qbbs    PDM_DC_NEG, r9, 31
    lsr     r9, r9, 7
    qba     PDM_DC_ADD
PDM_DC_NEG:
    not     r9, r9                          ; floor shift of a negative word:
    lsr     r9, r9, 7                       ;   ~(~x >> 7)
    not     r9, r9
PDM_DC_ADD:
    add     r6, r6, r8                      ; e += d >> 7
    adc     r7, r7, r9
    sbco    &r6, c24, r0, 8
    lsr     r5, r6, 31                      ; round(e / 2^32) = e_hi + e_lo[31]
    add     r5, r7, r5
    sub     r2, r2, r5                      ; y - e[n]
    .endif
    ldi32   r0, 8388607
    add     r3, r2, r0                      ; y + max, in 0..2*max when in range
    ldi32   r4, 16777214
    qbge    PDM_SAT_OK, r3, r4
    qbbc    PDM_SAT_POS, r3, 31
    rsb     r2, r0, 0                       ; -max
    qba     PDM_SAT_OK
PDM_SAT_POS:
    mov     r2, r0                          ; +max
PDM_SAT_OK:

    ; --- publish: sample to ring k, then HDR_PCM_COUNT[k] --------------------
    lbco    &r10, c24, r20, 24              ; reload state
    and     r0, r13, 0xFF                   ; n mod PDM_RING_SAMPLES
    lsl     r0, r0, 2
    lsl     r1, r21, 10                     ; k * PDM_RING_STRIDE
    add     r0, r0, r1
    ldi     r1, PDM_RING_BASE
    add     r0, r0, r1
    sbbo    &r2, r22, r0, 4                 ; PCM sample first ...
    add     r13, r13, 1
    lsl     r0, r21, 2
    add     r0, r0, PDM_HDR_PCM_COUNT
    sbbo    &r13, r22, r0, 4                ; ... then PCM_COUNT[k]
    add     r14.b2, r14.b2, 1               ; w = (w + 1) mod 64
    and     r14.b2, r14.b2, 63
    sbco    &r10, c24, r20, 24
    .if BLOCK_EVENT >= 0
    add     r0, r21, 1                      ; block event of ch N_CH-1 only
    qbne    PDM_NEXT, r0, N_CH
    and     r0, r13, BLOCK_MASK
    qbne    PDM_NEXT, r0, 0
    ldi     r0, 32                          ; R31 bit 5 = strobe, [3:0] = event
    or      r0, r0, BLOCK_EVENT
    mov     r31, r0                         ; one 32-bit write, SD bits 23/24 = 0
    .endif

PDM_NEXT:
    add     r21, r21, 1
    ldi     r0, CH_STRIDE
    add     r20, r20, r0
    qbgt    PDM_CH, r21, N_CH               ; next channel while k < N_CH
    ldi     r21, 0
    ldi     r20, CH_BASE
    qba     PDM_CH
