/*
 * Simple SSI realtime ABI, version 1.
 *
 * The R5F accesses these offsets through the PRUICSS DRAM pointers. PRU0
 * uses its local C24/DRAM0 window and PRU1/RTU_PRU1 use their slice-1 C24/
 * DRAM1 window. Keep all fields naturally aligned; only the final result
 * words are read after the corresponding DONE flag is set.
 */
#ifndef SSI_TEST_ABI_H_
#define SSI_TEST_ABI_H_

#include <stdint.h>

#define SSI_TEST_ABI_VERSION       1U

#define SSI_PRU0_POSITION_OFF      0x00U
#define SSI_PRU0_READY_OFF         0x04U
#define SSI_PRU0_DONE_OFF          0x08U
#define SSI_PRU0_FRAMES_OFF        0x0CU
#define SSI_PRU0_RESYNCS_OFF       0x10U
#define SSI_PRU0_ABORTS_OFF        0x14U
#define SSI_PRU0_LAST_POSITION_OFF 0x18U
#define SSI_PRU0_REGION_SIZE       0x20U

#define SSI_PRU1_PERIOD_OFF        0x00U
#define SSI_PRU1_FIRST_BOUNDARY_OFF 0x04U
#define SSI_PRU1_GRID_READY_OFF    0x08U
#define SSI_PRU1_ARM_READY_OFF     0x0CU
#define SSI_PRU1_STOP_OFF          0x10U
#define SSI_PRU1_TIMER_READY_OFF   0x14U
#define SSI_PRU1_TIMER_DONE_OFF    0x18U
#define SSI_PRU1_READER_READY_OFF  0x1CU
#define SSI_PRU1_READER_DONE_OFF   0x20U
#define SSI_PRU1_TIMER_MISSED_OFF  0x24U
#define SSI_PRU1_TIMER_MAX_LATE_OFF 0x28U
#define SSI_PRU1_TIMER_EMITTED_OFF 0x2CU
#define SSI_PRU1_READER_FRAMES_OFF 0x40U
#define SSI_PRU1_READER_RAW_LO_OFF 0x44U
#define SSI_PRU1_READER_RAW_HI_OFF 0x48U
#define SSI_PRU1_READER_POSITION_OFF 0x4CU
#define SSI_PRU1_READER_ERRORS_OFF 0x50U
#define SSI_PRU1_READER_ABORTS_OFF 0x54U
#define SSI_PRU1_REGION_SIZE       0x58U

#define SSI_READY_RESET             0U
#define SSI_READY_RUNNING           1U
#define SSI_DONE_RESET              0U
#define SSI_DONE_COMPLETE           1U
#define SSI_STOP_REQUEST            1U

#endif /* SSI_TEST_ABI_H_ */
