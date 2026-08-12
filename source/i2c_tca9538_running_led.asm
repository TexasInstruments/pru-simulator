; source/i2c_tca9538_running_led.asm
; PRU0 @ 200 MHz — bit-bang I2C master, 1 Mbit/s, "direct mode" GPIO
; (R30/R31 as plain GPIO — GPCFG.PRU_GP_MUX_SEL = 0, the reset default).
;
; Drives a TCA9538 8-bit IO expander (address 0x23) at 1 Mbit/s:
; configures all 8 ports as outputs, then loops a single bit
; 0x01 -> 0x02 -> ... -> 0x80 -> 0x01 ... into the Output Port register
; forever — a running LED across the expander's 8 physical pins.
;
; Open-drain convention (see pru_io/io_port.py): writing 1 to a bit
; RELEASES the line (pulled high externally / by the slave); writing 0
; DRIVES it low. This is not push-pull GPIO.
;
; Pins (R30/R31):
;   SCL = bit 0
;   SDA = bit 1  (bidirectional: master drives during address/reg/data,
;                 releases for the ACK bit so the slave can pull it low)
;
; Timing: 200 MHz / 1 Mbit/s = 200 cycles/bit, split ~100 cycles
; SCL-low / ~100 cycles SCL-high. DELAY_COUNT below is a starting
; estimate (2 cycles per SUB+QBNE iteration, ~4 cycles fixed overhead
; per phase) — Task 7 single-steps the first byte and tunes it against
; the actual per-edge cycle count before trusting the full run loop.
;
; Call convention: single level of JAL/JMP subroutines (i2c_start,
; i2c_stop, i2c_write_byte), all returning via r1 — none of them call
; each other, so one return-address register is enough (no nesting).
;
; Register map:
;   r0   scratch (error-flag value in run_nack)
;   r1   subroutine return address
;   r2   byte being shifted out by i2c_write_byte / loaded before each call
;   r3   delay-loop counter (i2c_start / i2c_stop / i2c_write_byte)
;   r6   bit counter within i2c_write_byte (8 downto 1)
;   r19  DRAM0 error-flag address scratch
;   r20  ACK/NACK result after i2c_write_byte: 0 = ACK, 2 = NACK (raw
;        bit1 of the sampled R31, not normalized to 0/1)
;   r21  running-LED pattern byte (0x01 .. 0x80, wraps)
;   r30  GPO — SCL=bit0, SDA=bit1
;   r31  GPI — SDA read-back on bit1

SCL_BIT     .set 0
SDA_BIT     .set 1
DELAY_COUNT .set 48          ; measured 100-101c/half-bit at N=48 (Task 7,
                              ; single-stepped against nominal_config/200 MHz);
                              ; within 1% of the 100c target, no change needed
ADDR_W      .set 0x46        ; TCA9538 addr 0x23, R/W=0 (write): (0x23<<1)|0
CFG_REG     .set 0x03
OUT_REG     .set 0x01
ERR_ADDR    .set 0x0FFE

start:
    ldi  r30, 0x0003          ; idle bus: SCL=1, SDA=1 (both released)

    jal  r1, i2c_start
    ldi  r2, ADDR_W
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    ldi  r2, CFG_REG
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    ldi  r2, 0x00              ; all 8 pins = outputs
    jal  r1, i2c_write_byte
    qbne init_fail, r20, 0

    jal  r1, i2c_stop

    ldi  r21, 0x01             ; running-LED pattern, starts at P0

run_loop:
    jal  r1, i2c_start
    ldi  r2, ADDR_W
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    ldi  r2, OUT_REG
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    mov  r2, r21
    jal  r1, i2c_write_byte
    qbne run_nack, r20, 0

    jal  r1, i2c_stop
    jmp  advance_pattern

run_nack:
    ldi  r0, 1
    ldi  r19, ERR_ADDR
    sbco &r0, c24, r19, 1      ; DRAM0[0x0FFE] = 1 (soft error flag)
    jal  r1, i2c_stop          ; release the bus, retry next iteration

advance_pattern:
    lsl  r21, r21, 1
    qbbc run_loop, r21, 8      ; branch back unless bit 8 just got set (wrapped past P7)
    ldi  r21, 0x01
    jmp  run_loop

init_fail:
    halt                       ; nothing useful can happen without CONFIG set

; ---------------------------------------------------------------------
; i2c_start — from idle (SDA=1, SCL=1): generate a START condition.
i2c_start:
    set  r30, r30, SCL_BIT
    set  r30, r30, SDA_BIT
    ldi  r3, DELAY_COUNT
is_d1:
    sub  r3, r3, 1
    qbne is_d1, r3, 0
    clr  r30, r30, SDA_BIT     ; SDA falls while SCL=1 -> START
    ldi  r3, DELAY_COUNT
is_d2:
    sub  r3, r3, 1
    qbne is_d2, r3, 0
    clr  r30, r30, SCL_BIT     ; SCL low, ready to clock the first bit
    jmp  r1

; ---------------------------------------------------------------------
; i2c_stop — from SCL=0: generate a STOP condition, leaves bus idle.
i2c_stop:
    clr  r30, r30, SDA_BIT     ; SDA low while SCL low (setup)
    ldi  r3, DELAY_COUNT
ps_d1:
    sub  r3, r3, 1
    qbne ps_d1, r3, 0
    set  r30, r30, SCL_BIT
    ldi  r3, DELAY_COUNT
ps_d2:
    sub  r3, r3, 1
    qbne ps_d2, r3, 0
    set  r30, r30, SDA_BIT     ; SDA rises while SCL=1 -> STOP
    jmp  r1

; ---------------------------------------------------------------------
; i2c_write_byte — shift r2 out MSB-first, then sample the ACK bit.
; Returns: r20 = 0 (ACK) or 2 (NACK).
i2c_write_byte:
    ldi  r6, 8
wb_bit:
    clr  r30, r30, SCL_BIT
    qbbc wb_clear, r2, 7
    set  r30, r30, SDA_BIT
    jmp  wb_delay1
wb_clear:
    clr  r30, r30, SDA_BIT
wb_delay1:
    ldi  r3, DELAY_COUNT
wb_d1:
    sub  r3, r3, 1
    qbne wb_d1, r3, 0
    set  r30, r30, SCL_BIT     ; slave samples SDA
    ldi  r3, DELAY_COUNT
wb_d2:
    sub  r3, r3, 1
    qbne wb_d2, r3, 0
    lsl  r2, r2, 1
    sub  r6, r6, 1
    qbne wb_bit, r6, 0

    ; 9th clock: release SDA, sample the ACK bit
    clr  r30, r30, SCL_BIT
    set  r30, r30, SDA_BIT
    ldi  r3, DELAY_COUNT
wb_d3:
    sub  r3, r3, 1
    qbne wb_d3, r3, 0
    set  r30, r30, SCL_BIT
    ldi  r3, DELAY_COUNT
wb_d4:
    sub  r3, r3, 1
    qbne wb_d4, r3, 0
    and  r20, r31, 2           ; 0 = ACK (slave drove low), 2 = NACK
    clr  r30, r30, SCL_BIT
    jmp  r1
