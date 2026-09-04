/*
 * GENERATED FILE -- source: schema/foc_abi.json
 *
 * Regenerate with: python tools/gen_foc_abi.py
 */
#ifndef FOC_ABI_H_
#define FOC_ABI_H_

#include <stdint.h>

#define FOC_ICSS_SHARED_BASE                 (0x00010000U)
#define FOC_ABI_VERSION                      (1U)
#define FOC_Q_FRACTION_BITS                  (24U)
#define FOC_Q_ONE                             (16777216U)
#define FOC_IEP_TICK_HZ                      (300000000U)
#define FOC_SINE_LUT_ENTRIES                 (2048U)
#define FOC_SPEED_SCALE                      (2863312U)
#define FOC_SPEED_BASE_RPM                   (1000U)
#define FOC_CONTROL_LOOP_HZ                  (100000U)
#define FOC_CURRENT_BASE_A                   (10U)

#define FOC_CONTROL_BASE                             (0x00010000U)
#define FOC_CONTROL_OFFSET                         (0x0000U)
#define FOC_CONTROL_SIZE                            (36U)
#define FOC_CONTROL_OFF_ABI_VERSION                  (0x00U)
#define FOC_CONTROL_OFF_STRUCT_SIZE                  (0x04U)
#define FOC_CONTROL_OFF_ENABLE                       (0x08U)
#define FOC_CONTROL_OFF_REQUESTED_GENERATION         (0x0CU)
#define FOC_CONTROL_OFF_PRU_ACK_GENERATION           (0x10U)
#define FOC_CONTROL_OFF_SPEED_REF_Q24                (0x14U)
#define FOC_CONTROL_OFF_ID_REF_Q24                   (0x18U)
#define FOC_CONTROL_OFF_IQ_REF_Q24                   (0x1CU)
#define FOC_CONTROL_OFF_RAMP_RATE_Q24                (0x20U)

#define FOC_PWM_OUT_BASE                             (0x00010100U)
#define FOC_PWM_OUT_OFFSET                         (0x0100U)
#define FOC_PWM_OUT_SIZE                            (40U)
#define FOC_PWM_OUT_OFF_SEQ                          (0x00U)
#define FOC_PWM_OUT_OFF_TA_Q24                       (0x04U)
#define FOC_PWM_OUT_OFF_TB_Q24                       (0x08U)
#define FOC_PWM_OUT_OFF_TC_Q24                       (0x0CU)
#define FOC_PWM_OUT_OFF_VALPHA_Q24                   (0x10U)
#define FOC_PWM_OUT_OFF_VBETA_Q24                    (0x14U)
#define FOC_PWM_OUT_OFF_THETA_CMD_U32                (0x18U)
#define FOC_PWM_OUT_OFF_LOOP_COUNTER                 (0x1CU)
#define FOC_PWM_OUT_OFF_TIMESTAMP_CYCLES             (0x20U)

#define FOC_MOTOR_FB_BASE                            (0x00010200U)
#define FOC_MOTOR_FB_OFFSET                        (0x0200U)
#define FOC_MOTOR_FB_SIZE                           (40U)
#define FOC_MOTOR_FB_OFF_SEQ                         (0x00U)
#define FOC_MOTOR_FB_OFF_IA_Q24                      (0x04U)
#define FOC_MOTOR_FB_OFF_IB_Q24                      (0x08U)
#define FOC_MOTOR_FB_OFF_IC_Q24                      (0x0CU)
#define FOC_MOTOR_FB_OFF_ID_MEAS_Q24                 (0x10U)
#define FOC_MOTOR_FB_OFF_IQ_MEAS_Q24                 (0x14U)
#define FOC_MOTOR_FB_OFF_ROTOR_THETA_U32             (0x18U)
#define FOC_MOTOR_FB_OFF_SPEED_RPM_Q24               (0x1CU)
#define FOC_MOTOR_FB_OFF_TIMESTAMP                   (0x20U)

#define FOC_SINE_LUT_BASE                            (0x00011000U)
#define FOC_SINE_LUT_OFFSET                        (0x1000U)
#define FOC_SINE_LUT_SIZE                           (4U)
#define FOC_SINE_LUT_COUNT                           (2048U)
#define FOC_SINE_LUT_OFF_VALUE                       (0x00U)

typedef struct {
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t enable;
    uint32_t requested_generation;
    uint32_t pru_ack_generation;
    int32_t speed_ref_q24;
    int32_t id_ref_q24;
    int32_t iq_ref_q24;
    int32_t ramp_rate_q24;
} foc_control_t;

#endif /* FOC_ABI_H_ */
