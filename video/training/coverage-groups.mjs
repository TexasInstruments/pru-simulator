// Controls that count as shown when one of their kind was pointed at and the narration
// names the group. Every entry needs a reason; anything not listed here must be pointed
// at in a tour, or `node check.mjs coverage` fails.
//
// Patterns match the descriptors of lib/recorder.mjs: "#id", or
// "#nearest-ancestor-id > tag[label]".
export const GROUPS = [
  { pattern: /^#mem-grid(-2)? > div\[/, reason: 'memory cells of both Memory panels: each is edited by double-click (part 2, mem-edit)' },
  { pattern: /^#pin-gpi-pru\d-\d+$/, reason: 'the 20 R31 input boxes of each core block: a click toggles that input (part 2, io-gpi)' },
  { pattern: /^#gpio-drive-controls-pru\d > button\[/, reason: 'the 20 Drive / Release buttons of each core block (part 2, io-drive)' },
  { pattern: /^#loopback-strip-pru\d > button\[/, reason: 'the five loopback buttons of each core block (part 2, io-loopback)' },
  { pattern: /^#io-mux-sel-pru\d$/, reason: 'the GP Mux selector of each core block (part 2, io-mux)' },
  { pattern: /^#ctable-tbody > td\[/, reason: 'the 32 addresses of the constant table: a click jumps Memory 1 there (part 1, ctable)' },
  { pattern: /^#_src-menu > div\[/, reason: 'the files of the Open list (part 1, open and open-file)' },
  { pattern: /^#editor-tab-bar > div\[/, reason: 'the tabs of the Assembly Editor (part 1, tabs)' },
  { pattern: /^(#[a-z0-9-]+-panel > div\[|#memory-controls(-2)?$)/, reason: 'panel title bars: drag to move, corner sign to collapse (part 1, drag-title and collapse)' },
  { pattern: /^(#tile-root > div\[splitDir=|#ctable-divider$)/, reason: 'dividers between panels and above the constant table: drag to resize (part 1, drag-divider)' },
  { pattern: /^#cores-deck > button\[(PRU|RTU)\d PC /, reason: 'the name on each card of the CORES deck (part 2, cores-name)' },
  { pattern: /^#(core-(source|register)-[a-z0-9]+|(source|register)-focus-pane) > button\[×\]$/, reason: 'the cross on each core pane of Source and Registers (part 2, cores-chips)' },
  { pattern: /^#(source|register)-core-chips > button\[/, reason: 'the core chips in the Source and Registers titles (part 2, cores-chips)' },
  { pattern: /^#sd-pattern-gen > (select|input)\[ch=\d/, reason: 'the Pattern Generator fields of the three SD channels (part 4, sd-pattern and sd-sine)' },
  { pattern: /^#sd-config-chips > button\[/, reason: 'the OSR chips of the three SD channels (part 4, sd-osr)' },
  { pattern: /^#perif-loopback > input\[ch=\d/, reason: 'the loopback rows of the three Peripheral Interface channels (part 4, pi-loopback)' },
];
