### Task 4: PRU1 edge-polling peripheral firmware — **Opus** (use the `pru-assembly` skill)

**Files:**
- Create: `source/spi/spi_peripheral.asm`
- Modify: `pru_io/spi_link.py` (`load_peripheral`, `peripheral_status() -> dict`, `preload_peripheral_tx(addr: int, data: bytes)`, `read_peripheral_rx(addr: int) -> bytes`)
- Test: `tests/test_spi_peripheral.py`

**Interfaces:**
- Consumes: Task 3 `SpiLinkRuntime` and controller firmware.
- Produces: the PRU1 firmware contract of spec §3/§5.2: `peripheral_status()` returns `{"frames", "errors", "aborts", "last_cmd", "last_addr"}` from P_STATUS. RX is committed only when the frame completes.
- [ ] **Step 1: Write failing tests**, at 200 MHz/k=10 (10 MHz) mode 0 and mode 1, hop delay 5:
  - `test_write_lands_in_rx_block`: WRITE addr 0x05 with `walking1` → `read_peripheral_rx(5) == walking1`; P_TX unchanged.
  - `test_read_returns_tx_block`: preload P_TX block 0x7F, READ 0x7F → `c_rx == block`, MISO header bytes == 00 00 00.
  - `test_rw_full_duplex`: R/W addr 0x02 with `count` → `c_rx == PATTERNS["count"][1]` and RX block 2 == `PATTERNS["count"][0]`.
  - `test_unknown_command_counted` (cmd 0x04 → errors +1, MISO data all 0, nothing written). `test_out_of_range_address` (0x80 → same).
  - `test_abort_then_back_to_back_frame`: `transfer(..., abort_after_bits=50)` (Task 3 API). The next frame at the minimum gap is correct; aborts +1; RX of the aborted frame is not committed.
  - `test_peripheral_is_edge_polling`: assemble the peripheral **once** and run the controller at 200 MHz with k=10 and then k=4 (10 and 25 MHz), reloading only PRU0. Both pass. This shows PRU1 does not depend on the rate. The peripheral's header therefore contains only `SPI_MODE`; it must not use `HALF_K` or `MISO_DELAY` (`render_header(cfg, role="peripheral")`).
- [ ] **Step 2:** `pytest tests/test_spi_peripheral.py -v` → FAIL.
- [ ] **Step 3:** Write `spi_peripheral.asm` with the spec §5.2 techniques: one masked R31 snapshot per poll for CLK+MOSI+/CS, sample timed from the previous observed edge, MISO driven by one R30 write of a pre-shifted byte (check that writing other GPO bits is harmless under the pru1 drive mask), idle-byte fetch of 16 bytes with `lbco`. Mode 0 and mode 1 may be separate code paths. Count the per-bit cycle cost by single-stepping and record it in a comment.
- [ ] **Step 4:** `pytest tests/test_spi_peripheral.py tests/test_spi_controller.py -v` → PASS.
- [ ] **Step 5:** Commit `feat(spi): PRU1 edge-polling peripheral firmware`.

