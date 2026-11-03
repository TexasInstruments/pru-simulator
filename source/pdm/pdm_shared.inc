; =============================================================
; pdm_shared.inc -- ICSS shared-RAM map of the PDM audio demo.
; Python mirror: source/pdm/pdm_layout.py (tests/test_pdm_layout.py keeps
; the two in sync).  Offsets are relative to PDM_SHARED_BASE (0x10000).
;
; Every .set is a plain literal: the pru-simulator assembler does not
; expand a .set whose value refers to another .set symbol.  Derived values
; are written out and guarded by the .if/.emsg checks at the end.
;
;   +0x0000 header          written by the host unless noted
;   +0x0100 PCM rings       8 x 256 x int32 (24-bit signed), written by PRU0
;   +0x2100 clip            interleaved L/R int16 frames, read by PRU1
; =============================================================
PDM_SHARED_BASE     .set    0x10000     ; ICSS shared RAM
PDM_HDR_MAGIC       .set    0x00        ; u32 = 0x50444D31 ("PDM1"), host
PDM_HDR_N_CH        .set    0x04        ; u8  channels in the clip (2), host
PDM_HDR_BLOCK       .set    0x06        ; u16 PCM samples per block (128), host
PDM_HDR_PCM_RATE    .set    0x08        ; u32 PCM sample rate (16000), host
PDM_HDR_CLIP_LEN    .set    0x0C        ; u32 clip length in frames, host
PDM_HDR_GEN_POS     .set    0x10        ; u32 frames consumed, PRU1
PDM_HDR_GEN_DONE    .set    0x14        ; u32 0/1 clip fully played, PRU1
PDM_HDR_PCM_COUNT   .set    0x20        ; u32[8] samples written per ring, PRU0
                                        ; (stored after the sample itself)
PDM_RING_BASE       .set    0x100       ; ring k at +k*PDM_RING_STRIDE
PDM_RING_STRIDE     .set    0x400       ; bytes per ring
PDM_RING_SAMPLES    .set    256         ; int32 entries per ring; sample n at
                                        ; RING_BASE + k*STRIDE + 4*(n % 256)
PDM_CLIP_BASE       .set    0x2100      ; = RING_BASE + 8*RING_STRIDE
PDM_CLIP_MAX_FRAMES .set    8192        ; 0.512 s at 16 kHz

    .if PDM_CLIP_BASE != (PDM_RING_BASE + 8 * PDM_RING_STRIDE)
    .emsg "PDM_CLIP_BASE must follow the eight rings"
    .endif
    .if PDM_RING_STRIDE != (4 * PDM_RING_SAMPLES)
    .emsg "PDM_RING_STRIDE must be 4 * PDM_RING_SAMPLES"
    .endif
