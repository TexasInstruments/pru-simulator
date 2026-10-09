/*
 *  Copyright (C) 2024-2025 Texas Instruments Incorporated
 *
 *  Redistribution and use in source and binary forms, with or without
 *  modification, are permitted provided that the following conditions
 *  are met:
 *
 *    Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *
 *    Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the
 *    distribution.
 *
 *    Neither the name of Texas Instruments Incorporated nor the names of
 *    its contributors may be used to endorse or promote products derived
 *    from this software without specific prior written permission.
 *
 *  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 *  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 *  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 *  A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 *  OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 *  SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 *  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 *  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 *  THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 *  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 *  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

/*
 * pif_eth TC7 -- R5F host driver for the CRC-accelerator hardware test.
 *
 * C port of pru-simulator/source/pif_eth/rx_driver.py.  PRU0 runs
 * pif_eth_tx_n2 (TX), PRU1 runs pif_eth_rx_o1_raw (RX).  For each
 * n_tx in {2,4,6,8} the R5F sends 100 BERT frames, one go flag per frame,
 * and checks the RX stats block after each.  TC7 passes when every frame has
 * crc_ok = 1, symbol_errors = 0, prng_bit_errors = 0 and rx_ovf = 0.
 *
 * Setup this relies on:
 *  - SysConfig: CONFIG_PRU_ICSS0 coreClk = 250 MHz (the test plan's clock),
 *    and PruGPIO entries for PRU0 GPO0/1/2 (TX) and PRU1 GPI13 (RX ch0).
 *  - ICSSG_SA_MX_REG G_MUX_EN = 1, set below via PRUICSS_setSaMuxMode().
 *  - The ICSSG address range must be non-cached (Device) in the R5F MPU.
 *  - PRU0's TX pad must be wired to PRU1's RX pad (the simulator's "perif
 *    loopback"); nothing in this file does that.
 */

#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <kernel/dpl/DebugP.h>
#include <kernel/dpl/ClockP.h>
#include "ti_drivers_config.h"
#include "ti_drivers_open_close.h"
#include "ti_board_open_close.h"
#include <drivers/pruicss.h>

#include <pru0_load_bin.h>      /* PRU0Firmware_0: pif_eth_tx_n2 */
#include <pru1_load_bin.h>      /* PRU1Firmware_0: pif_eth_rx_o1_raw */

/*
 * n_tx = 2 (125 Mbaud) needs pif_eth_tx_n2_skipchecks; the all-checks
 * pif_eth_tx_n2 underruns and deadlocks at n=2 (PROJECT_REPORT.md 8.3).
 * Build skipchecks in the PRU0 project, copy its pru0_load_bin.h into this
 * project as pru0_skip_load_bin.h and rename the array to PRU0FirmwareSkip_0.
 * Without that header the n_tx = 2 rung is reported SKIPPED.
 */
#if defined(__has_include)
#if __has_include("pru0_skip_load_bin.h")
#include "pru0_skip_load_bin.h"
#define HAVE_TX_SKIPCHECKS  1
#endif
#endif

#include "enc_lut.h"
#include "dec_lut.h"

/* ---- Memory map (offsets as seen by the PRU cores) ----------------------- */

/* PRU0 DRAM: encode LUT at 0, TX control block at 0x400. */
#define T_NUMF_OFF          0x400u
#define T_MODE_OFF          0x404u
#define T_SEED_OFF          0x408u
#define T_PLEN_OFF          0x40Cu
#define T_FCNT_OFF          0x410u
#define T_BURST_OFF         0x414u
#define T_GOFLAG_OFF        0x418u

/* PRU1 DRAM: decode LUT at 0, capture 0x800, frame 0xE00, stats, control. */
#define FRAME_OFF           0x0E00u
#define STATS_OFF           0x0F00u
#define CTRL_OFF            0x0F40u
#define WORK_START_OFF      0x0800u
#define WORK_END_OFF        (CTRL_OFF + 0x14u)

#define S_FRAMES_OFF        (STATS_OFF + 0x00u)
#define S_OVF_OFF           (STATS_OFF + 0x08u)
#define S_SYMERR_OFF        (STATS_OFF + 0x0Cu)
#define S_CRCOK_OFF         (STATS_OFF + 0x10u)
#define S_BITERR_OFF        (STATS_OFF + 0x14u)
#define S_TOTBITS_OFF       (STATS_OFF + 0x18u)
#define S_EOF_OFF           (STATS_OFF + 0x1Cu)

#define C_MODE_OFF          (CTRL_OFF + 0x00u)
#define C_SEED_OFF          (CTRL_OFF + 0x04u)
#define C_PLEN_OFF          (CTRL_OFF + 0x08u)
#define C_GO_OFF            (CTRL_OFF + 0x0Cu)
#define C_RXCFG_OFF         (CTRL_OFF + 0x10u)

/* ICSSG CFG space: PRU-local 0x260E4 is TXCFG for PRU0 channel 0. */
#define TXCFG_PRU0_OFF      0x0E4u
#define SA_MX_REG_OFF       0x040u          /* CSL_ICSSCFG_PIN_MX */
#define SA_MX_G_MUX_EN      (1u << 7)

/* ---- Test parameters ----------------------------------------------------- */

#define BERT_PAYLOAD_LEN    128u
#define DEFAULT_SEED        0x1BADC0DEu
#define FRAMES_PER_RUNG     100u
#define MAX_PAYLOAD_LEN     252u        /* 256 B frame buffer - 4 B FCS */
#define FRAME_TIMEOUT_US    50000u
#define TX_PROLOGUE_US      200u        /* PRU0 reaches its go-flag spin */

/* n=2 last: if its TX deadlocks it can leave the channel stuck for later rungs. */
static const uint32_t gLadder[] = { 4u, 6u, 8u, 2u };

/* ---- Globals ------------------------------------------------------------- */

PRUICSS_Handle gPruIcss0Handle;

static uintptr_t gDram0;    /* PRU0 DRAM, host view */
static uintptr_t gDram1;    /* PRU1 DRAM, host view */
static uintptr_t gCfg;      /* ICSSG CFG registers, host view */

static inline void wr32(uintptr_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline uint32_t rd32(uintptr_t a)         { return *(volatile uint32_t *)a; }

static void copy_words(uintptr_t dst, const uint32_t *src, uint32_t n_words)
{
    for (uint32_t i = 0; i < n_words; i++) {
        wr32(dst + 4u * i, src[i]);
    }
}

/* ---- Config words (rx_driver.py txcfg_word / rxcfg_word) ------------------ */

/* clk_sel=core, frac=0, div_factor = n_tx - 1 */
static uint32_t txcfg_word(uint32_t n_tx) { return ((n_tx - 1u) << 16) | 0x10u; }

/* sample_size=7, sb_pol=1, clk_sel=core; n_rx = n_tx / 2 (2x oversampling) */
static uint32_t rxcfg_word(uint32_t n_rx) { return ((n_rx - 1u) << 16) | 0x1Fu; }

/* ---- Reference payload + FCS (prng.py, crc32.py) --------------------------- */

static uint32_t xorshift32(uint32_t s)
{
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    return s;
}

/* zlib CRC-32 (reflected, poly 0xEDB88320), bitwise. */
static uint32_t crc32_zlib(const uint8_t *p, uint32_t n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n--) {
        c ^= *p++;
        for (int k = 0; k < 8; k++) {
            c = (c >> 1) ^ (0xEDB88320u & (0u - (c & 1u)));
        }
    }
    return ~c;
}

/*
 * The BERT PRNG runs continuously across frames: each payload is the next
 * payload_len bytes (low byte of each state) of one stream, as in both
 * firmwares.  out holds payload_len + 4 bytes, FCS appended little-endian.
 */
static void make_expected_frame(uint32_t *state, uint32_t payload_len, uint8_t *out)
{
    for (uint32_t i = 0; i < payload_len; i++) {
        *state = xorshift32(*state);
        out[i] = (uint8_t)(*state & 0xFFu);
    }
    uint32_t fcs = crc32_zlib(out, payload_len);
    for (uint32_t i = 0; i < 4; i++) {
        out[payload_len + i] = (uint8_t)(fcs >> (8u * i));
    }
}

/* ---- One rung -------------------------------------------------------------- */

typedef struct {
    uint32_t n_tx;
    uint32_t frames_done;
    uint32_t frames_ok;
    uint32_t total_bit_errors;
    bool     timed_out;
    bool     skipped;
} RungResult;

/*
 * Diagnostic for the n=2 symbol_errors=1 pattern: print the raw capture
 * length plus the first 6 and last 10 captured oversample bytes, for the
 * first few bad and the first few good frames, so they can be compared.
 */
#define CAPTURE_OFF         0x0800u
#define S_CAPBYTES_OFF      (STATS_OFF + 0x04u)
#define DUMP_FRAMES_MAX     4u

static void dump_capture(const char *tag, uint32_t n_tx, uint32_t i)
{
    uint32_t n = rd32(gDram1 + S_CAPBYTES_OFF);
    volatile const uint8_t *cap = (volatile const uint8_t *)(gDram1 + CAPTURE_OFF);

    if (n > 1024u) {
        DebugP_log("  [%s] n_tx=%u frame %u: cap_bytes=%u (bad)\r\n", tag, n_tx, i, n);
        return;
    }
    DebugP_log("  [%s] n_tx=%u frame %u: cap_bytes=%u tx_pushed=%u head=%02X %02X %02X %02X %02X %02X"
               " tail=%02X %02X %02X %02X %02X %02X %02X %02X %02X %02X\r\n",
               tag, n_tx, i, n, rd32(gDram0 + T_BURST_OFF),
               cap[0], cap[1], cap[2], cap[3], cap[4], cap[5],
               cap[n - 10u], cap[n - 9u], cap[n - 8u], cap[n - 7u], cap[n - 6u],
               cap[n - 5u], cap[n - 4u], cap[n - 3u], cap[n - 2u], cap[n - 1u]);
}

static bool check_frame(uint32_t n_tx, uint32_t i, uint32_t payload_len,
                        const uint8_t *expected, RungResult *r)
{
    static uint32_t gDumpBad, gDumpGood;
    uint32_t ovf    = rd32(gDram1 + S_OVF_OFF);
    uint32_t symerr = rd32(gDram1 + S_SYMERR_OFF);
    uint32_t crcok  = rd32(gDram1 + S_CRCOK_OFF);
    uint32_t biterr = rd32(gDram1 + S_BITERR_OFF);
    uint32_t tot    = rd32(gDram1 + S_TOTBITS_OFF);
    uint32_t eof    = rd32(gDram1 + S_EOF_OFF);
    bool ok = true;

    r->total_bit_errors += biterr;

    if (n_tx == 2u) {
        if (symerr != 0 && gDumpBad < DUMP_FRAMES_MAX) {
            gDumpBad++;
            dump_capture("bad ", n_tx, i);
        } else if (symerr == 0 && gDumpGood < DUMP_FRAMES_MAX) {
            gDumpGood++;
            dump_capture("good", n_tx, i);
        }
    }

    if (ovf != 0)    { DebugP_log("  n_tx=%u frame %u: rx_ovf=%u\r\n", n_tx, i, ovf); ok = false; }
    if (symerr != 0) { DebugP_log("  n_tx=%u frame %u: symbol_errors=%u\r\n", n_tx, i, symerr); ok = false; }
    if (crcok != 1)  { DebugP_log("  n_tx=%u frame %u: crc_ok=%u\r\n", n_tx, i, crcok); ok = false; }
    if (biterr != 0) { DebugP_log("  n_tx=%u frame %u: prng_bit_errors=%u\r\n", n_tx, i, biterr); ok = false; }
    if (eof != 1)    { DebugP_log("  n_tx=%u frame %u: eof_status=%u\r\n", n_tx, i, eof); ok = false; }
    if (tot != payload_len * 8u) {
        DebugP_log("  n_tx=%u frame %u: tot_bits=%u\r\n", n_tx, i, tot);
        ok = false;
    }

    /* The frame buffer holds the last received frame: payload + FCS. */
    for (uint32_t w = 0; w < (payload_len + 4u) / 4u; w++) {
        uint32_t got = rd32(gDram1 + FRAME_OFF + 4u * w);
        uint32_t exp = (uint32_t)expected[4u * w] |
                       ((uint32_t)expected[4u * w + 1u] << 8) |
                       ((uint32_t)expected[4u * w + 2u] << 16) |
                       ((uint32_t)expected[4u * w + 3u] << 24);
        if (got != exp) {
            DebugP_log("  n_tx=%u frame %u: frame buffer word %u = 0x%08X, expected 0x%08X\r\n",
                       n_tx, i, w, got, exp);
            ok = false;
            break;
        }
    }
    return ok;
}

static RungResult run_rung(uint32_t n_tx, uint32_t num_frames, uint32_t seed,
                           uint32_t payload_len)
{
    RungResult r = { n_tx, 0, 0, 0, false, false };
    uint8_t expected[MAX_PAYLOAD_LEN + 4u];
    uint32_t prng_state = seed;
    const uint32_t *tx_fw;
    uint32_t tx_fw_size;

    /* Pick the TX image from n_tx, never from the caller: skipchecks is
     * only valid at n=2 and silently corrupts data at any other divider. */
    if (n_tx == 2u) {
#ifdef HAVE_TX_SKIPCHECKS
        tx_fw = PRU0FirmwareSkip_0;
        tx_fw_size = sizeof(PRU0FirmwareSkip_0);
#else
        r.skipped = true;
        return r;
#endif
    } else {
        tx_fw = PRU0Firmware_0;
        tx_fw_size = sizeof(PRU0Firmware_0);
    }

    DebugP_assert((n_tx % 2u) == 0u);              /* exact 2x oversampling */
    DebugP_assert(payload_len <= MAX_PAYLOAD_LEN);
    DebugP_assert((payload_len % 4u) == 0u);       /* send_frame needs core_len % 4 == 0 */

    PRUICSS_disableCore(gPruIcss0Handle, PRUICSS_PRU0);
    PRUICSS_disableCore(gPruIcss0Handle, PRUICSS_PRU1);

    /* LUTs: encode -> PRU0 DRAM 0x0000, decode -> PRU1 DRAM 0x0000. */
    copy_words(gDram0 + PIF_ETH_ENC_LUT_ADDR, enc_lut, PIF_ETH_ENC_LUT_ENTRIES);
    copy_words(gDram1 + PIF_ETH_DEC_LUT_ADDR, dec_lut, PIF_ETH_DEC_LUT_ENTRIES / 2u);

    /* TX control block (mode 0 = PRNG/BERT). */
    wr32(gDram0 + T_NUMF_OFF, num_frames);
    wr32(gDram0 + T_MODE_OFF, 0);
    wr32(gDram0 + T_SEED_OFF, seed);
    wr32(gDram0 + T_PLEN_OFF, payload_len);
    wr32(gDram0 + T_FCNT_OFF, 0);
    wr32(gDram0 + T_BURST_OFF, 0);
    wr32(gDram0 + T_GOFLAG_OFF, 0);

    /* RX capture/frame/stats/control area, then its control block. */
    for (uintptr_t o = WORK_START_OFF; o < WORK_END_OFF; o += 4u) {
        wr32(gDram1 + o, 0);
    }
    wr32(gDram1 + C_MODE_OFF, 0);
    wr32(gDram1 + C_SEED_OFF, seed);
    wr32(gDram1 + C_PLEN_OFF, payload_len);
    wr32(gDram1 + C_GO_OFF, 0);
    wr32(gDram1 + C_RXCFG_OFF, rxcfg_word(n_tx / 2u));

    /* RX first, so it is parked at its go flag before TX can emit anything.
     * PRUICSS_loadFirmware disables, loads, resets and enables the core. */
    int32_t status;
    status = PRUICSS_loadFirmware(gPruIcss0Handle, PRUICSS_PRU1,
                                  PRU1Firmware_0, sizeof(PRU1Firmware_0));
    DebugP_assert(SystemP_SUCCESS == status);
    status = PRUICSS_loadFirmware(gPruIcss0Handle, PRUICSS_PRU0,
                                  tx_fw, tx_fw_size);
    DebugP_assert(SystemP_SUCCESS == status);

    /* The TX image self-configures TXCFG for n=2, then spins on its go flag.
     * Override TXCFG for this rung once it has got there (rx_driver.py does
     * the same after stepping PRU0 past its prologue). */
    ClockP_usleep(TX_PROLOGUE_US);
    wr32(gCfg + TXCFG_PRU0_OFF, txcfg_word(n_tx));

    for (uint32_t i = 0; i < num_frames; i++) {
        make_expected_frame(&prng_state, payload_len, expected);

        /* RX go first: PRU0 spends >1000 cycles in prng_fill + crc32 before
         * its first symbol, so RX is armed well before the burst starts. */
        wr32(gDram1 + C_GO_OFF, 1);
        wr32(gDram0 + T_GOFLAG_OFF, 1);

        uint32_t t0 = ClockP_getTimeUsec();
        while (rd32(gDram1 + S_FRAMES_OFF) != i + 1u) {
            if ((uint32_t)(ClockP_getTimeUsec() - t0) > FRAME_TIMEOUT_US) {
                DebugP_log("  n_tx=%u frame %u: timeout (rx frames=%u, tx frames=%u)\r\n",
                           n_tx, i, rd32(gDram1 + S_FRAMES_OFF),
                           rd32(gDram0 + T_FCNT_OFF));
                r.timed_out = true;
                goto done;
            }
        }
        r.frames_done++;

        if (check_frame(n_tx, i, payload_len, expected, &r)) {
            r.frames_ok++;
        }
    }

done:
    PRUICSS_disableCore(gPruIcss0Handle, PRUICSS_PRU0);
    PRUICSS_disableCore(gPruIcss0Handle, PRUICSS_PRU1);
    return r;
}

/* ---- Entry point ------------------------------------------------------------ */

void empty_example_main(void *args)
{
    int status;
    int fail = 0;

    Drivers_open();
    status = Board_driversOpen();
    DebugP_assert(SystemP_SUCCESS == status);

    gPruIcss0Handle = PRUICSS_open(CONFIG_PRU_ICSS0);
    DebugP_assert(gPruIcss0Handle != NULL);

    const PRUICSS_HwAttrs *attrs = PRUICSS_getAttrs(CONFIG_PRU_ICSS0);
    DebugP_assert(attrs != NULL);
    gDram0 = attrs->pru0DramBase;
    gDram1 = attrs->pru1DramBase;
    gCfg   = attrs->cfgRegBase;

    /* Peripheral-mode pin mux: ICSSG_SA_MX_REG (CFG +0x40) bit 7, G_MUX_EN = 1.
     * The PRU firmware sets GPCFG0/1 mux_sel itself but not this bit.  The SDK
     * maps mode PRUICSS_SA_MUX_MODE_SD_ENDAT (1) onto bit 7 of this register. */
    status = PRUICSS_setSaMuxMode(gPruIcss0Handle, PRUICSS_SA_MUX_MODE_SD_ENDAT);
    DebugP_assert(SystemP_SUCCESS == status);
    DebugP_assert((rd32(gCfg + SA_MX_REG_OFF) & SA_MX_G_MUX_EN) != 0u);

    DebugP_log("TC7: PRU0 TX -> PRU1 RX, %u BERT frames x %u B per rung\r\n",
               FRAMES_PER_RUNG, BERT_PAYLOAD_LEN);

    for (uint32_t k = 0; k < sizeof(gLadder) / sizeof(gLadder[0]); k++) {
        RungResult r = run_rung(gLadder[k], FRAMES_PER_RUNG, DEFAULT_SEED,
                                BERT_PAYLOAD_LEN);
        bool pass = !r.skipped && !r.timed_out && r.frames_ok == FRAMES_PER_RUNG;

        if (r.skipped) {
            DebugP_log("n_tx=%u: SKIPPED (no pru0_skip_load_bin.h) -> FAIL\r\n", r.n_tx);
        } else {
            DebugP_log("n_tx=%u: %u/%u frames ok, bit_errors=%u%s -> %s\r\n",
                       r.n_tx, r.frames_ok, FRAMES_PER_RUNG, r.total_bit_errors,
                       r.timed_out ? ", TIMEOUT" : "", pass ? "PASS" : "FAIL");
        }
        if (!pass) {
            fail = 1;
        }
    }

    DebugP_log("TC7 overall: %s\r\n", fail ? "FAIL" : "PASS");

    while (1)
    {
        ClockP_usleep(1000);
    }

    Board_driversClose();
    Drivers_close();
}
