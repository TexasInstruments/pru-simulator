; rtu_pru1_main.asm - fixed-grid SSI publication timer (RTU_PRU1)
; ---------------------------------------------------------------------------
; RTU_PRU1 owns IEP0 setup and publishes one monotonically increasing PERIOD
; opportunity on the slice-1 DRAM mailbox. The deadline remains an absolute
; 288-tick grid; a late host does not move the grid to "now". The timer never
; touches the SSI wire or the position scalar.

    .include "ssi_test_abi.inc"
    .include "ssi_build_config.inc"

    .retain
    .retainrefs
    .global main
    .sect ".text"

    ; r0 scratch, r1 current IEP count, r2 next absolute deadline
    ; r3 arithmetic/late value, r4 opportunity number, r5 stop value
    ; r6 emitted opportunities, r7 missed opportunities, r8 max lateness
    ; r9 fixed period in a register (the PRU ADD immediate is only 8-bit)
    ; r10 iteration limit (branch immediates are only 8-bit)

main:
    ; RTU_PRU1 is the only firmware owner of IEP startup.
    ldi     r0, 1
    sbco    &r0, c4, 0x30, 4
    ldi     r0, 0x11                 ; CNT_ENABLE | DEFAULT_INC=1
    sbco    &r0, c26, 0x00, 4
    ldi     r9, SSI_PERIOD_TICKS
    ldi32   r10, SSI_ITERATIONS

    ; Clear the slice-1 control/result words before announcing readiness.
    ldi     r0, 0
    sbco    &r0, c24, SSI_PRU1_PERIOD_OFF, 4
    sbco    &r0, c24, SSI_PRU1_GRID_READY_OFF, 4
    sbco    &r0, c24, SSI_PRU1_TIMER_DONE_OFF, 4
    sbco    &r0, c24, SSI_PRU1_TIMER_MISSED_OFF, 4
    sbco    &r0, c24, SSI_PRU1_TIMER_MAX_LATE_OFF, 4
    sbco    &r0, c24, SSI_PRU1_TIMER_EMITTED_OFF, 4

    ; The R5F owns ARM_READY and STOP. Do not overwrite either control word:
    ; the host seeds them while all three firmware images are stopped.
    ldi     r0, SSI_READY_RUNNING
    sbco    &r0, c24, SSI_PRU1_TIMER_READY_OFF, 4

l_wait_arm:
    lbco    &r5, c24, SSI_PRU1_STOP_OFF, 4
    qbne    l_stop, r5, 0
    lbco    &r5, c24, SSI_PRU1_ARM_READY_OFF, 4
    qbeq    l_wait_arm, r5, 0

    ; Give the R5F a one-time lead so it can finish startup before the first
    ; publication. The low-word comparison below is valid for intervals less
    ; than 2^31 ticks and therefore survives a 32-bit rollover.
    lbco    &r1, c26, 0x10, 4
    ldi32   r3, SSI_FIRST_LEAD_TICKS
    add     r2, r1, r3
    sbco    &r2, c24, SSI_PRU1_FIRST_BOUNDARY_OFF, 4
    ldi     r0, 1
    sbco    &r0, c24, SSI_PRU1_GRID_READY_OFF, 4

    ldi     r4, 0
    ldi     r6, 0
    ldi     r7, 0
    ldi     r8, 0

l_wait_deadline:
    lbco    &r5, c24, SSI_PRU1_STOP_OFF, 4
    qbne    l_stop, r5, 0
    lbco    &r1, c26, 0x10, 4
    sub     r3, r1, r2
    ; Sign bit set means current time is before the absolute deadline.
    qbbs    l_wait_deadline, r3, 31

l_emit:
    add     r4, r4, 1
    add     r6, r6, 1
l_period_store:
    sbco    &r4, c24, SSI_PRU1_PERIOD_OFF, 4

    ; Record lateness against this deadline, then advance the absolute grid.
    sub     r3, r1, r2
    qbbs    l_not_late, r3, 31
    qble    l_late_counted, r8, r3
    mov     r8, r3
l_late_counted:
    add     r2, r2, r9

    qble    l_stop, r4, r10

    ; If the host or this core was late by one or more whole periods, expose
    ; the current opportunity number and count the missing slots without
    ; moving the deadline origin. R5 observes a delta and reports skipped.
l_catch_up:
    lbco    &r1, c26, 0x10, 4
    sub     r3, r1, r2
    qbbs    l_wait_deadline, r3, 31
    add     r4, r4, 1
    add     r7, r7, 1
    add     r2, r2, r9
    qble    l_stop, r4, r10
    qba     l_catch_up

l_not_late:
    qba     l_wait_deadline

l_stop:
    sbco    &r7, c24, SSI_PRU1_TIMER_MISSED_OFF, 4
    sbco    &r8, c24, SSI_PRU1_TIMER_MAX_LATE_OFF, 4
    sbco    &r6, c24, SSI_PRU1_TIMER_EMITTED_OFF, 4
    ldi     r0, SSI_DONE_COMPLETE
    sbco    &r0, c24, SSI_PRU1_TIMER_DONE_OFF, 4
    halt
