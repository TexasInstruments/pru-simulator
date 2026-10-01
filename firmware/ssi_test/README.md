# Bundled simple SSI realtime firmware

This directory is the self-contained firmware and profile bundle used by the
PRU simulator's **SSI Simple Realtime** panel. A simulator clone does not need
the separate CCS workspace to load or execute the three actual images.

The bundle contains:

- the PRU0 encoder emulator, PRU1 SSI reader, and RTU_PRU1 timing image;
- the generated ABI and profile inputs consumed by those images; and
- the Python profile generator and functional simulator harness.

## Change the SSI profile

Edit only `ssi_test/ssi_hardware_config.h` and select one of these presets:

```c
#define SSI_PRESET SSI_PRESET_AFS_AFM60_MULTITURN_30BIT
```

Available selectors are:

- `SSI_PRESET_CUSTOM_LEGACY_12BIT_4MHZ`
- `SSI_PRESET_AHS_AHM36_SINGLETURN`
- `SSI_PRESET_AHS_AHM36_MULTITURN`
- `SSI_PRESET_AFS_AFM60_SINGLETURN`
- `SSI_PRESET_AFS_AFM60_MULTITURN_30BIT`
- `SSI_PRESET_AFS_AFM60_MULTITURN_27BIT`
- `SSI_PRESET_AFS_AFM60S_PRO_SINGLETURN`
- `SSI_PRESET_AFS_AFM60S_PRO_MULTITURN`
- `SSI_PRESET_ARS60_SHORT`
- `SSI_PRESET_ARS60_LONG`
- `SSI_PRESET_TTK70`
- `SSI_PRESET_KH53`

Then regenerate the derived files from the simulator repository root:

```bash
python3 firmware/ssi_test/tools/generate_config.py \
  --config firmware/ssi_test/ssi_test/ssi_hardware_config.h \
  --output firmware/ssi_test/include
```

The generator rejects profiles that do not fit the fixed 300 MHz / 960 ns
test contract or the measured PRU loop timing.

## Run the bundled firmware

Run the functional harness directly:

```bash
python3 firmware/ssi_test/tools/simulate_ssi.py \
  --iterations 1000 \
  --output /tmp/ssi-simple.json
```

For the dashboard, start the simulator normally, click **Load actual
firmware**, and use the normal **Run** control. The dashboard uses this bundle
by default and reads its generated profile at load time.
