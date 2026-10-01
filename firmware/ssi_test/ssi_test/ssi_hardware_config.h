/*
 * Sole user-edited build-time profile for the simple SSI realtime image.
 *
 * Set SSI_PRESET to the encoder profile to emulate, then regenerate the
 * generated files with firmware/ssi_test/tools/generate_config.py.
 */
#ifndef SSI_HARDWARE_CONFIG_H_
#define SSI_HARDWARE_CONFIG_H_

#define SSI_PRESET_CUSTOM_LEGACY_12BIT_4MHZ 0
#define SSI_PRESET_AHS_AHM36_SINGLETURN    1
#define SSI_PRESET_AHS_AHM36_MULTITURN     2
#define SSI_PRESET_AFS_AFM60_SINGLETURN    3
#define SSI_PRESET_AFS_AFM60_MULTITURN_30BIT 4
#define SSI_PRESET_AFS_AFM60_MULTITURN_27BIT 5
#define SSI_PRESET_AFS_AFM60S_PRO_SINGLETURN 6
#define SSI_PRESET_AFS_AFM60S_PRO_MULTITURN  7
#define SSI_PRESET_ARS60_SHORT              8
#define SSI_PRESET_ARS60_LONG               9
#define SSI_PRESET_TTK70                    10
#define SSI_PRESET_KH53                     11

#define SSI_PRESET SSI_PRESET_CUSTOM_LEGACY_12BIT_4MHZ

#define SSI_CORE_HZ          300000000
#define SSI_IEP_HZ           300000000
#define SSI_PERIOD_NS        960
#define SSI_ITERATIONS       100000

#define SSI_INITIAL_POSITION 256
#define SSI_POSITION_STEP    1

#if SSI_PRESET == SSI_PRESET_CUSTOM_LEGACY_12BIT_4MHZ
#define SSI_FRAME_BITS       12
#define SSI_POSITION_BITS    12
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       0
#define SSI_ERROR_OFFSET     0
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0

#define SSI_CLOCK_HZ         4000000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AHS_AHM36_SINGLETURN
#define SSI_FRAME_BITS       15
#define SSI_POSITION_BITS    14
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       1
#define SSI_ERROR_OFFSET     14
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AHS_AHM36_MULTITURN
#define SSI_FRAME_BITS       27
#define SSI_POSITION_BITS    26
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       1
#define SSI_ERROR_OFFSET     26
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AFS_AFM60_SINGLETURN
#define SSI_FRAME_BITS       21
#define SSI_POSITION_BITS    18
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       3
#define SSI_ERROR_OFFSET     18
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AFS_AFM60_MULTITURN_30BIT
#define SSI_FRAME_BITS       33
#define SSI_POSITION_BITS    30
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       3
#define SSI_ERROR_OFFSET     30
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AFS_AFM60_MULTITURN_27BIT
#define SSI_FRAME_BITS       30
#define SSI_POSITION_BITS    27
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       3
#define SSI_ERROR_OFFSET     27
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AFS_AFM60S_PRO_SINGLETURN
#define SSI_FRAME_BITS       21
#define SSI_POSITION_BITS    18
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       3
#define SSI_ERROR_OFFSET     18
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_AFS_AFM60S_PRO_MULTITURN
#define SSI_FRAME_BITS       28
#define SSI_POSITION_BITS    25
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       3
#define SSI_ERROR_OFFSET     25
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_ARS60_SHORT
#define SSI_FRAME_BITS       13
#define SSI_POSITION_BITS    13
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       0
#define SSI_ERROR_OFFSET     0
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_ARS60_LONG
#define SSI_FRAME_BITS       17
#define SSI_POSITION_BITS    15
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       2
#define SSI_ERROR_OFFSET     15
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_TTK70
#define SSI_FRAME_BITS       26
#define SSI_POSITION_BITS    24
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       2
#define SSI_ERROR_OFFSET     24
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#elif SSI_PRESET == SSI_PRESET_KH53
#define SSI_FRAME_BITS       24
#define SSI_POSITION_BITS    24
#define SSI_POSITION_OFFSET  0
#define SSI_ERROR_BITS       0
#define SSI_ERROR_OFFSET     0
#define SSI_ERROR_VALUE      0
#define SSI_ENCODING_GRAY    0
#define SSI_CLOCK_HZ         1500000
#define SSI_SAMPLE_NS        80
#define SSI_TV_NS            40
#define SSI_TM_NS            12500
#define SSI_TP_NS            13500

#endif

#endif /* SSI_HARDWARE_CONFIG_H_ */
