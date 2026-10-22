# UI branch issue status

Checked against the repository's open issues on 22 October 2026. These are
UI branch changes; no GitHub issue has been closed or updated.

| Issue | Status in this branch | Evidence or remaining work |
| --- | --- | --- |
| [#53](https://github.com/TexasInstruments/pru-simulator/issues/53) — repeated Load and Assemble loses Source | Fixed | Source-cache invalidation on reload and core changes; `tests/ui_issue_regressions.test.js`. |
| [#55](https://github.com/TexasInstruments/pru-simulator/issues/55) — missing UART Record button | Fixed | UART Record and Signal Graph REC share one recording buffer and state; frontend regression tests. |
| [#56](https://github.com/TexasInstruments/pru-simulator/issues/56) — reset UART inputs/feedback | Fixed | Reset inputs restores the form and clears feedback without restarting Python. It does not cancel previously injected UART traffic. |
| [#61](https://github.com/TexasInstruments/pru-simulator/issues/61) — Reset Layout leaves minimized panels collapsed | Fixed | Restores visible, expanded panels and default sizes; layout recovery tests. |
| [#63](https://github.com/TexasInstruments/pru-simulator/issues/63) — CRC follow-up | UI item fixed; issue remains open | Uses `CRCAccelerator.DEVICE_ID`; missing CRC shows “—”. Burst transfers and short seed writes remain accelerator-model work. The independent IEP clock change is already in main. |
| [#62](https://github.com/TexasInstruments/pru-simulator/issues/62) — program removal affects other cores | Related; do not claim fixed | The redesign has one shared file editor and separate loaded programs per core. Closing an editor file does not unload firmware from any core. Explicit per-core unloading and its UI semantics still need a focused reproduction and test. |
| [#66](https://github.com/TexasInstruments/pru-simulator/issues/66) — breakpoints survive program loads | Related; still open | Clearing program-specific breakpoints and checking per-core isolation would be a suitable separate fix. The earlier instruction to leave this issue unchanged is preserved. |
| [#54](https://github.com/TexasInstruments/pru-simulator/issues/54) — example instructions | Partly related | SSI demos now have dashboard instructions. The issue's UART string initialization and `mem_copy` documentation are not fully addressed. |
| [#74](https://github.com/TexasInstruments/pru-simulator/issues/74) — IEP clock default | Already addressed by merged main | IEP defaults independently to 200 MHz and can be selected independently in the UI. This is a simulator default, not a universal hardware frequency. |
| [#57](https://github.com/TexasInstruments/pru-simulator/issues/57) — multicore instruction timing | Separate backend work | Linked browser views do not change the scheduler. Existing chronological shared-effect limitations still apply. |

Issues #72/#73 require hardware PWM/IEP model work. Issue #79 needs AM263x
silicon validation. Those cannot be resolved by a UI redesign. Target timing,
parser and processor-feature issues also remain outside this UI task.

The linked-tab feature does not correspond to an existing open issue found
in this check. It is covered by real two-connection tests in
`tests/test_dashboard_tabs.py`, ordered-output tests in
`tests/test_dashboard_live_sessions.py`, and frontend Run/SIM flow tests.
