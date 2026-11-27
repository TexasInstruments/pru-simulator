// Part 2: Memory, pins, cores and clocks. One r.cue() per narration key, in file order.
import fs from 'node:fs';
import { openFile, reg } from '../lib/dashboard.mjs';

const fmt = (group, name) => `#${group} [data-fmt="${name}"]`;
const end = (group, name) => `#${group} [data-end="${name}"]`;
const ioSection = (n) => `#io-block-pru0 > :nth-child(${n})`;      // 3 GPO, 4 GPI, 5 loopback, 6 Drive / Release
const lb = (group) => `#loopback-strip-pru0 .lb-btn[data-group="${group}"]`;
const drive = (pin) => `#gpio-drive-controls-pru0 button >> nth=${pin}`;
const tele = (core) => `#telemetry-cores button[data-core="${core}"]`;
const IO_BODY = '#io-panel .panel-body';

export default async function tour(r, page) {
  const js = (expression) => page.evaluate(expression);
  const must = (ok, message) => { if (!ok) throw r.fail(message); };
  const regValue = async (n) => parseInt(await reg(page, n), 16);
  // Memory panel cells as shown on screen: panel 1 is #mem-grid, panel 2 is #mem-grid-2.
  const cells = (grid, from, to) => js(`[...document.querySelectorAll('#${grid} .mem-byte')].slice(${from}, ${to}).map((e) => e.textContent.trim())`);
  const cell = (grid, offset) => js(`(document.querySelector('#${grid} .mem-byte[data-offset="${offset}"]') || {}).textContent`);
  const halted = `document.getElementById('status-badge').textContent.includes('HALTED') && document.querySelector('#btn-run .text').textContent === 'Run'`;
  const modal = (id) => `getComputedStyle(document.getElementById('${id}')).display`;
  const cores = () => js(`({ used: coreSelection.used.length, shown: coreSelection.shown.join(), focus: coreSelection.focus,
    source: document.querySelectorAll('#source-panes .core-pane').length, registers: document.querySelectorAll('#reg-body .core-pane').length,
    tele: document.querySelectorAll('#telemetry-cores button').length, mode: document.querySelector('.cores-mode').textContent,
    cards: [...document.querySelectorAll('.core-card')].filter((c) => !c.hidden).length,
    open: document.getElementById('cores-control').classList.contains('is-open') })`);
  const coresAre = (want, what) => r.until(`(() => { const s = { used: coreSelection.used.length, shown: coreSelection.shown.join(), focus: coreSelection.focus,
      source: document.querySelectorAll('#source-panes .core-pane').length, registers: document.querySelectorAll('#reg-body .core-pane').length,
      tele: document.querySelectorAll('#telemetry-cores button').length };
    const want = ${JSON.stringify(want)}; return Object.keys(want).every((k) => s[k] === want[k]); })()`, { max: 5, what });

  // ---- memory -------------------------------------------------------------------
  await r.cue('part-intro', async () => { await r.moveTo([960, 560], 24); });
  await r.cue('mem-run', async () => {
    await openFile(r, 'mem_copy.asm');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-panel').innerText.includes('sbco')`, { max: 5, what: 'mem_copy listing' });
    await r.click('#btn-reset');
    await r.click('#btn-run');
    await r.until(`document.getElementById('cnt-instrs').textContent.trim() === '282' && ${halted}`, { max: 8, what: 'mem_copy halted after 282 instructions' });
  });
  await r.cue('mem-refresh', async () => {
    await r.outline('#memory-panel');
    await r.show('#mem-addr-input', 0.9);
    await r.outline('#memory-panel-2');
    await r.show('#mem-addr-input-2', 0.7);
    must((await cells('mem-grid', 0, 1))[0] === '00000000', 'Memory 1 already shows data before Refresh');
    await r.click('#btn-mem-refresh-2');
    await r.until(`document.querySelector('#mem-grid-2 .mem-byte').textContent === '03020100'`, { max: 4, what: 'Memory 2 shows the shared RAM after Refresh' });
    await r.outline('#memory-panel');
    await r.click('#btn-mem-refresh');
    await r.until(`document.querySelector('#mem-grid .mem-byte').textContent === '03020100'`, { max: 4, what: 'Memory 1 shows data RAM 0 after Refresh' });
    r.clearOutline();
  });
  await r.cue('mem-address', async () => {
    await r.type('#mem-addr-input', 'DRAM0', { clear: true });
    await r.expect('#mem-addr-input', /^DRAM0$/);
    await r.press('Enter', 14);
    await r.until(`document.getElementById('mem-addr-input').value === '0x00000000'`, { max: 3, what: 'region name DRAM0 replaced by its address' });
    await r.type('#mem-len-input', '128', { clear: true });
    await r.click('#btn-mem-refresh');
    await r.until(`document.querySelectorAll('#mem-grid .mem-addr').length === 8`, { max: 3, what: '128 bytes: eight rows of 16 bytes' });
  });
  await r.cue('mem-format', async () => {
    const seen = [];
    for (const [name, shape] of [['bin', /^[01]{8}$/], ['byte', /^[0-9A-F]{2}$/], ['16b', /^[0-9A-F]{4}$/], ['24b', /^[0-9A-F]{6}$/], ['32b', /^[0-9A-F]{8}$/]]) {
      await r.click(fmt('mem-fmt-group', name));
      await r.hold(0.45);
      const dump = await cells('mem-grid', 0, 4);
      must(dump.every((c) => shape.test(c)), `${name} view shows ${dump}`);
      must(!seen.includes(dump.join()), `${name} view did not change the dump`);
      seen.push(dump.join());
    }
  });
  await r.cue('mem-endian', async () => {
    must((await cells('mem-grid', 0, 1))[0] === '03020100', 'LSB view of the first word');
    await r.click(end('mem-end-group', 'msb'));
    must((await cells('mem-grid', 0, 1))[0] === '00010203', 'MSB view of the first word is ' + await cells('mem-grid', 0, 1));
    await r.show('#mem-grid .mem-byte >> nth=0', 1.3);
    await r.click(end('mem-end-group', 'lsb'));
    must((await cells('mem-grid', 0, 1))[0] === '03020100', 'back to LSB');
  });
  await r.cue('mem-compare', async () => {
    await r.click(fmt('mem-fmt-group', 'byte'));
    await r.click(fmt('mem-fmt-group-2', 'byte'));
    const dst = await cells('mem-grid', 0, 32), src = await cells('mem-grid-2', 0, 32);
    must(dst.slice(1, 26).join() === src.slice(1, 26).join() && dst[25] === '19', `copy differs: ${dst.slice(0, 27)} / ${src.slice(0, 27)}`);
    must(dst[26] === '00' && src[26] === '1A', 'byte 0x1A should be outside the copy');
    await r.show('#mem-grid .mem-byte[data-offset="25"]', 0.8);
  });
  await r.cue('mem-edit', async () => {
    await r.dblclick('#mem-grid .mem-byte[data-offset="5"]');
    await r.hold(0.3);
    for (const ch of 'FF') { await page.keyboard.type(ch); r.force = 3; await r.frame(); await r.frame(); await r.frame(); }
    await r.press('Enter', 10);
    // the panel reads the range again 100 ms after an edit: the value must come back from the simulator
    await r.until(`prevMemData[5] === 255 && document.querySelector('#mem-grid .mem-byte[data-offset="5"]').textContent === 'FF'`, { max: 3, what: 'edited cell keeps FF' });
  });
  await r.cue('mem-auto', async () => {
    await r.click('#mem-auto-refresh');
    must(await js(`document.getElementById('mem-auto-refresh').checked`), 'Auto is not ticked');
    await r.click('#btn-reset');
    await r.click('#btn-run');
    await r.until(`prevMemData[5] === 5 && document.querySelector('#mem-grid .mem-byte[data-offset="5"]').textContent === '05'`, { max: 6, what: 'Memory 1 follows the second run without Refresh' });
    await r.show('#mem-grid .mem-byte[data-offset="5"]', 0.8);
  });
  await r.cue('mem-second', async () => {
    await r.outline('#memory-panel-2');
    for (const sel of ['#mem-addr-input-2', '#mem-len-input-2', '#btn-mem-refresh-2', '#mem-auto-refresh-2']) await r.show(sel, 0.2, 7);
    for (const name of ['bin', 'byte', '16b', '24b', '32b']) await r.show(fmt('mem-fmt-group-2', name), 0.1, 5);
    for (const name of ['lsb', 'msb']) await r.show(end('mem-end-group-2', name), 0.1, 5);
    r.clearOutline();
  });

  // ---- fill and memory graph --------------------------------------------------------
  await r.cue('fill-open', async () => {
    await r.show('#btn-mem-fill', 0.4);
    await r.click('#btn-mem-fill-2');
    await r.until(`${modal('fill-modal')} === 'flex'`, { max: 3, what: 'Memory Fill dialog' });
    await r.expect('#fill-addr', /^0x00010000$/i);
    await r.expect('#fill-len', /^1024$/);
    await r.show('#fill-addr', 0.5);
    await r.show('#fill-len', 0.5);
  });
  await r.cue('fill-format', async () => {
    await r.show(fmt('fill-fmt-group', 'byte'), 0.5);
    await r.show(fmt('fill-fmt-group', '16b'), 0.5);
    await r.click(fmt('fill-fmt-group', '32b'));
    must(await js(`document.querySelector('#fill-fmt-group [data-fmt="32b"]').classList.contains('active') && fillFmt === '32b'`), '32-bit not selected');
  });
  await r.cue('fill-pattern', async () => {
    await r.show('.fill-tab-btn[data-tab="pattern"]', 0.3);
    await r.show('#fill-pattern-val', 0.5);
  });
  await r.cue('fill-sequence', async () => {
    await r.click('.fill-tab-btn[data-tab="sequence"]');
    await r.show('#fill-seq-start', 0.4);
    await r.show('#fill-seq-step', 0.4);
  });
  r.live = true;                                    // the wave preview is a canvas
  await r.cue('fill-waveform', async () => {
    await r.click('.fill-tab-btn[data-tab="waveform"]');
    for (const type of ['square', 'triangle', 'sawtooth', 'sine']) {
      await r.click(`#fill-wave-type-group [data-wtype="${type}"]`);
      must(await js(`fillWtype === '${type}'`), `wave type ${type} not selected`);
      await r.hold(0.3);
    }
  });
  await r.cue('fill-wave-fields', async () => {
    await r.show('#fill-wave-amp', 0.4);
    await r.type('#fill-wave-cycles', '2', { clear: true });
    await r.show('#fill-wave-offset', 0.4);
    await r.click('#fill-wave-signed');
    await r.hold(0.6);
    await r.click();
    must(await js(`document.getElementById('fill-wave-signed').checked`), 'Signed is not ticked');
  });
  await r.cue('fill-buttons', async () => {
    await r.click('#btn-fill-cancel');
    await r.until(`${modal('fill-modal')} === 'none'`, { max: 3, what: 'Cancel closes the Fill dialog' });
    must(await cell('mem-grid-2', 4) === '04', 'Cancel changed the memory');
    await r.click('#btn-mem-fill-2');
    await r.until(`${modal('fill-modal')} === 'flex' && document.querySelector('.fill-tab-btn.active').dataset.tab === 'waveform'`, { max: 3, what: 'Fill dialog again, still on Waveform' });
    await r.click('#btn-fill-apply');
    // 32-bit sine, amplitude 0x7FFF, 2 cycles over 256 values: value 1 is 1608 = 0x0648
    await r.until(`${modal('fill-modal')} === 'none' && prevMemData2[4] === 0x48 && prevMemData2[5] === 0x06`, { max: 4, what: 'the sine in the shared RAM' });
    must(await cell('mem-grid-2', 4) === '48', 'Memory 2 does not show the sine');
  });
  await r.cue('mgraph-add', async () => {
    await r.outline('#mem-graph-panel');
    await r.click('#graph-add-ch-btn');
    await r.until(`document.querySelectorAll('#graph-mem-channels .graph-mem-row').length === 1`, { max: 3, what: 'one channel row' });
    await r.show('.graph-ch-addr', 0.6);
    await r.type('.graph-ch-len', '256', { clear: true });
    await r.press('Enter', 8);
    await r.select('.graph-ch-fmt', 'int32');
    await r.until(`(() => { const c = signalGraph.memChannels[0]; return c.format === 'int32' && c.snapshot && c.snapshot.length === 256
      && Math.max(...c.snapshot) === 32767 && Math.min(...c.snapshot) === -32767; })()`, { max: 4, what: 'channel M1 holds the sine, 256 values' });
    await r.expect('#mgraph-legend', /M1/);
    r.clearOutline();
  });
  await r.cue('mgraph-tools', async () => {
    await r.click('#graph-add-ch-btn');
    await r.until(`document.querySelectorAll('#graph-mem-channels .graph-mem-row').length === 2`, { max: 3, what: 'second channel row' });
    await r.hold(0.3);
    await r.click('.graph-ch-remove >> nth=1');
    await r.until(`document.querySelectorAll('#graph-mem-channels .graph-mem-row').length === 1 && signalGraph.memChannels.length === 1`, { max: 3, what: 'second channel removed' });
    await js(`window.__snapshotBefore = signalGraph.memChannels[0].snapshot`);
    await r.click('#graph-mem-refresh-btn');
    await r.until(`signalGraph.memChannels[0].snapshot && signalGraph.memChannels[0].snapshot !== window.__snapshotBefore`, { max: 3, what: 'channel read again' });
  });
  await r.cue('mgraph-height', async () => {
    const height = `document.getElementById('mgraph-canvas-wrap').offsetHeight`;
    await r.click('#mgraph-shorter-btn');
    must(await js(height) === 80, 'shorter: ' + await js(height));
    await r.click('#mgraph-taller-btn');
    must(await js(height) === 140, 'taller: ' + await js(height));
    await r.click();
    must(await js(height) === 200, 'taller again: ' + await js(height));
    await r.drag('#mgraph-resize-handle', 0, -47, 16);
    must(Math.abs(await js(height) - 150) <= 6, 'dragged edge: ' + await js(height));
  });
  r.live = false;

  // ---- I/O pins ----------------------------------------------------------------------
  await r.cue('io-blocks', async () => {
    await openFile(r, 'mvi_gpio_loopback.asm');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-panel').innerText.includes('mvib')`, { max: 5, what: 'mvi_gpio_loopback listing' });
    await r.click('#btn-reset');
    await r.outline('#io-panel');
    await r.moveTo('#io-block-pru0 .io-block-title', 16);
    await r.reveal('#io-block-pru1 .io-block-title', IO_BODY, 20);
    await r.show('#io-block-pru1 .io-block-title', 0.6);
    await r.scrollTo(IO_BODY, 0, 20);
    r.clearOutline();
  });
  await r.cue('io-mux', async () => {
    const note = (word) => r.until(`document.getElementById('io-mux-note-pru0').textContent.includes('${word}')`, { max: 3, what: `GP Mux note shows ${word}` });
    await r.show('#io-mux-sel-pru0', 0.8);
    await r.select('#io-mux-sel-pru0', '1');
    await note('mode: perif');
    await r.hold(1.3);
    await r.select('#io-mux-sel-pru0', '3');
    await note('mode: sd');
    await r.hold(1.3);
    await r.select('#io-mux-sel-pru0', '0');
    await note('mode: gpio');
  });
  await r.cue('io-gpo', async () => {
    await r.outline(ioSection(3));
    await r.moveTo('#pin-gpo-pru0-9', 16);
    await r.click('#btn-run');
    r.live = true;
    await r.until(`(parseInt(document.getElementById('reg-val-30').textContent, 16) & 0xFF) !== 0`, { max: 5, what: 'R30 driven by the program' });
    await r.hold(0.6);
    await r.click('#btn-run');
    await r.until(halted, { max: 5, what: 'stopped' });
    r.live = false;
    const r30 = await regValue(30);
    const high = await js(`Array.from({ length: 20 }, (_, i) => document.getElementById('pin-gpo-pru0-' + i).classList.contains('high') ? 1 << i : 0).reduce((a, b) => a | b, 0)`);
    must(r30 !== 0 && high === (r30 & 0xFFFFF), `R30 grid shows ${high.toString(16)}, R30 is ${r30.toString(16)}`);
    await r.show(`#pin-gpo-pru0-${Math.log2(r30 & 0xFF)}`, 0.8);
    r.clearOutline();
  });
  await r.cue('io-gpi', async () => {
    await r.reveal('#reg-val-31', '#register-focus-pane .core-scroll');
    await r.outline(ioSection(4));
    const before = await regValue(31);
    must((before & 0x20) === 0, 'input 5 is already high');
    await r.click('#pin-gpi-pru0-5');
    await r.until(`(parseInt(document.getElementById('reg-val-31').textContent, 16) & 0x20) !== 0 && document.getElementById('pin-gpi-pru0-5').classList.contains('high')`, { max: 3, what: 'bit 5 of R31 set' });
    must(await regValue(31) === (before | 0x20), 'R31 is ' + await reg(page, 31));
    await r.show('#reg-val-31', 1.1);
    await r.click('#pin-gpi-pru0-5');
    await r.until(`(parseInt(document.getElementById('reg-val-31').textContent, 16) & 0x20) === 0`, { max: 3, what: 'bit 5 of R31 clear again' });
    r.clearOutline();
  });
  await r.cue('io-loopback', async () => {
    await r.outline(ioSection(5));
    for (const group of [4, 3, 2]) await r.show(lb(group), 0.3, 8);
    for (const group of [1, 0]) {
      await r.click(lb(group));
      await r.hold(0.4);
    }
    await r.until(`[0, 1].every((g) => document.querySelector('#loopback-strip-pru0 .lb-btn[data-group="' + g + '"]').classList.contains('active'))
      && [2, 3, 4].every((g) => !document.querySelector('#loopback-strip-pru0 .lb-btn[data-group="' + g + '"]').classList.contains('active'))`, { max: 3, what: 'loopback 3:0 and 7:4 on' });
    r.clearOutline();
  });
  const followed = async () => {                      // R31 bits 7:0 equal R30 bits 7:0, on the pins and in the registers
    const [r30, r31] = await js(`[30, 31].map((n) => parseInt(document.getElementById('reg-val-' + n).textContent, 16))`);
    must((r31 & 0xFF) === (r30 & 0xFF), `R31 ${r31.toString(16)} does not follow R30 ${r30.toString(16)}`);
    return r30 & 0xFF;
  };
  await r.cue('io-loopback-run', async () => {
    await r.type('#sim-interval', '0.02', { clear: true });
    await r.click('#btn-sim');
    r.live = true;
    await r.moveTo('#pin-gpi-pru0-9', 20);
    const values = new Set();
    for (let i = 0; i < 10; i++) { await r.hold(0.3); values.add(await followed()); }     // one loop pass is 0.1 s: 0.3 s visits every bit
    must(values.size >= 3 && !values.has(0), 'the walking bit did not move: ' + [...values]);
    await r.expect('#btn-sim', /Stop SIM/i);
    await r.click('#btn-sim');
    await r.until(halted, { max: 4, what: 'SIM stopped' });
    r.live = false;
    await followed();
    must(await reg(page, 10) === await reg(page, 2), 'R10 does not mirror R2');
  });
  await r.cue('io-drive', async () => {
    await r.reveal('#gpio-drive-controls-pru0', IO_BODY, 20);
    await r.outline(ioSection(6));
    const r30 = await reg(page, 30);
    await r.expect(drive(12), /^12: Release$/);
    await r.click(drive(12));
    await r.until(`document.querySelectorAll('#gpio-drive-controls-pru0 button')[12].textContent === '12: Drive'`, { max: 3, what: 'pin 12 shows Drive' });
    must(await reg(page, 30) === r30, 'Drive changed R30');
    await r.hold(1.6);
    await r.click();
    await r.until(`document.querySelectorAll('#gpio-drive-controls-pru0 button')[12].textContent === '12: Release'`, { max: 3, what: 'pin 12 shows Release' });
    must(await reg(page, 30) === r30, 'Release changed R30');
    await r.hold(0.8);
    r.clearOutline();
  });

  // ---- signal graph ---------------------------------------------------------------------
  const graphHeight = `document.getElementById('signal-graph-canvas').closest('.graph-canvas-wrap').offsetHeight`;
  await r.cue('sg-room', async () => {
    await r.scrollTo(IO_BODY, 0, 16);
    await r.outline('#signal-graph-panel');
    await r.hold(0.8);
    const height = (id) => js(`Math.round(document.getElementById('${id}').getBoundingClientRect().height)`);
    const before = await height('signal-graph-panel');
    await r.click('#io-panel .panel-collapse-btn');
    await r.until(`document.getElementById('io-panel').classList.contains('panel-collapsed')`, { max: 3, what: 'I/O Pins collapsed' });
    must(await height('signal-graph-panel') >= before + 150, 'the Signal Graph panel did not grow: ' + await height('signal-graph-panel'));
    await r.hold(0.6);
    r.clearOutline();
  });
  await r.cue('sg-rec', async () => {
    await r.click('#graph-rec-btn');
    must(await js(`signalGraph.recording && document.getElementById('graph-rec-btn').classList.contains('rec-on') && document.getElementById('graph-rec-dot').classList.contains('active')`), 'REC is not on');
    await r.click('#btn-sim');
    r.live = true;
    await r.until(`document.querySelectorAll('#graph-legend .graph-legend-item').length === 16`, { max: 8, what: 'lanes GPO 0-7 and GPI 0-7' });
    await r.moveTo('#signal-graph-canvas', 20);
  });
  await r.cue('sg-rate', async () => {
    await r.show('#graph-sample-count', 2.4);
    await r.moveTo('#signal-graph-canvas', 20);
  });
  await r.cue('sg-window', async () => {
    await r.show('#graph-win-sel', 0.8);
    await r.select('#graph-win-sel', '128');
    await r.until(`signalGraph.windowSize === 128 && signalGraph.fill <= 128 && document.getElementById('graph-step-label').textContent.endsWith('/ 128')`, { max: 3, what: 'window of 128 samples' });
    await r.until(`signalGraph.fill === 128`, { max: 6, what: 'window full' });
    await r.moveTo('#signal-graph-canvas', 16);
  });
  await r.cue('sg-height', async () => {
    await r.click('#graph-taller-btn');
    must(await js(graphHeight) === 200, 'taller: ' + await js(graphHeight));
    await r.hold(0.3);
    await r.click('#graph-shorter-btn');
    must(await js(graphHeight) === 140, 'shorter: ' + await js(graphHeight));
    await r.drag('#graph-resize-handle', 0, 100, 20);
    must(Math.abs(await js(graphHeight) - 240) <= 6, 'dragged edge: ' + await js(graphHeight));
  });
  await r.cue('sg-export', async () => {
    await r.click('#btn-sim');
    await r.until(halted, { max: 4, what: 'SIM stopped' });
    r.live = false;
    const visible = await js(`(() => { const b = document.getElementById('graph-export-btn').getBoundingClientRect(), p = document.getElementById('signal-graph-panel').getBoundingClientRect(); return b.height > 0 && b.bottom <= p.bottom; })()`);
    if (!visible) await r.reveal('#graph-export-btn', '#signal-graph-panel .panel-body');
    const samples = await js(`signalGraph.fill`);
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 30000 }), r.click('#graph-export-btn')]);
    must(/^pru-trace-\d{8}-\d{6}\.csv$/.test(download.suggestedFilename()), 'download is called ' + download.suggestedFilename());
    const lines = fs.readFileSync(await download.path(), 'utf8').split('\n');
    // header, one row per sample, then one block per Memory Graph channel ("# M1 ...", "index,value", the values)
    must(lines[0].startsWith('step,mode,gpo0,') && lines[samples + 1] === '# M1 0x00010000 256×int32' && lines.length === samples + 1 + 2 + 256,
      `CSV has ${lines.length} lines for ${samples} samples; line ${samples + 2} is ${lines[samples + 1]}`);
    await r.show('#graph-sample-count', 0.6);
  });
  await r.cue('sg-clear', async () => {
    await r.click('#graph-clear-btn');
    await r.until(`signalGraph.fill === 0 && document.querySelectorAll('#graph-legend .graph-legend-item').length === 0 && getComputedStyle(document.getElementById('graph-export-row')).display === 'none'`, { max: 3, what: 'graph empty' });
    must(await js(`signalGraph.recording && document.getElementById('graph-rec-btn').classList.contains('rec-on')`), 'Clear switched REC off');
    await r.click('#graph-rec-btn');
    must(await js(`!signalGraph.recording && !document.getElementById('graph-rec-btn').classList.contains('rec-on')`), 'REC is still on');
  });

  // ---- cores ----------------------------------------------------------------------------
  await r.cue('cores-open', async () => {
    await r.click('#cores-toggle');
    await r.until(`document.getElementById('cores-control').classList.contains('is-open')`, { max: 3, what: 'CORES deck open' });
    await r.show('[data-solo="pru0"]', 0.5);
    await r.moveTo([1000, 330], 24);                 // the pointer leaves the deck: it stays open
    await r.hold(0.6);
    must((await cores()).open, 'the deck closed when the pointer left it');
  });
  await r.cue('cores-use', async () => {
    await r.click('[data-use="pru1"]');
    await coresAre({ used: 2, shown: 'pru0,pru1', source: 2, registers: 2, tele: 2 }, 'PRU1 used and shown');
    must((await cores()).mode === '×2', 'the pill does not show two cores');
    await r.show('#core-register-pru1 .core-pane-heading b', 1.0);
    await r.show(tele('pru1'), 1.0);
  });
  await r.cue('cores-show', async () => {
    await r.click('[data-show="pru1"]');
    await coresAre({ used: 2, shown: 'pru0', source: 1, registers: 1 }, 'PRU1 hidden, still used');
    await r.hold(0.9);
    await r.click();
    await coresAre({ used: 2, shown: 'pru0,pru1', source: 2, registers: 2 }, 'PRU1 shown again');
  });
  await r.cue('cores-name', async () => {
    await r.click('[data-solo="pru1"]');
    await coresAre({ used: 2, shown: 'pru1', focus: 'pru1', source: 1, registers: 1 }, 'only PRU1 shown, PRU1 in focus');
    await r.expect('#bp-focus-core', /^PRU1$/);
  });
  await r.cue('cores-all', async () => {
    await r.click('[data-action="use-all"]');
    await coresAre({ used: 4, shown: 'pru1', tele: 4 }, 'four cores used');
    await r.click('[data-action="show-all"]');
    await coresAre({ used: 4, source: 4, registers: 4 }, 'four panes');
    await r.hold(0.5);
    await r.click('[data-use="rtu0"]');
    await r.click('[data-use="rtu1"]');
    await coresAre({ used: 2, shown: 'pru0,pru1', source: 2, registers: 2, tele: 2 }, 'PRU0 and PRU1 used and shown');
  });
  await r.cue('cores-clock', async () => {
    const rate = `document.querySelector('.cores-clock-head [data-core-rate]').textContent`;
    must(await js(rate) === '250 MHz', 'core frequency starts at ' + await js(rate));
    must(await js(`document.querySelector('.cores-clock-slider')._choices.join()`) === '200,225,250,300,333', 'presets differ');
    await r.show('.cores-clock-slider', 1.2);
    await r.drag('.cores-clock-slider', 60, 0, 30);
    await r.until(`${rate} === '300 MHz' && !clockChanging && clockState.mhz === 300 && document.querySelector('[data-card="pru0"] [data-core-rate]').textContent === '300 MHz'`, { max: 8, what: 'core frequency 300 MHz' });
    await r.until(`document.querySelectorAll('#source-panes li').length === 0`, { max: 4, what: 'programs cleared by the reload' });
    await r.show('.cores-clock-notice', 1.0);
  });
  await r.cue('cores-chips', async () => {
    // a click outside the deck closes it; this one is on the cross of the PRU0 pane in Registers
    await r.click('#core-register-pru0 .core-pane-heading button');
    await coresAre({ source: 2, registers: 1, used: 2, shown: 'pru0,pru1', focus: 'pru1' }, 'PRU0 hidden in Registers only');
    must(!(await cores()).open, 'the deck is still open');
    await r.hold(0.7);
    await r.show('#source-core-chips .core-chip >> nth=0', 0.4);
    await r.click('#register-core-chips .core-chip >> nth=0');
    await coresAre({ source: 2, registers: 2 }, 'PRU0 back in Registers');
  });
  await r.cue('cores-telemetry', async () => {
    await r.click(tele('pru0'));
    await coresAre({ focus: 'pru0', used: 2 }, 'PRU0 in focus');
    must(await js(`document.getElementById('bp-focus-core').textContent + document.getElementById('assembly-core-target').value`) === 'PRU0pru0', 'breakpoint row and Load into did not follow the focus');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-focus-pane').innerText.includes('mvib')`, { max: 5, what: 'mvi_gpio_loopback in PRU0' });
    await r.click(tele('pru1'));
    await coresAre({ focus: 'pru1', used: 2 }, 'PRU1 in focus');
    must(await js(`document.getElementById('bp-focus-core').textContent + document.getElementById('assembly-core-target').value`) === 'PRU1pru1', 'breakpoint row and Load into did not follow the focus');
    await openFile(r, 'running_led.asm');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-focus-pane').innerText.includes('lsl') && document.getElementById('core-source-pru0').innerText.includes('mvib')`, { max: 5, what: 'running_led in PRU1, mvi_gpio_loopback in PRU0' });
  });
  const pcs = `[...document.querySelectorAll('#source-panes .core-pane [data-pane-readout="pc"]')].map((e) => e.textContent).join()`;
  await r.cue('cores-step', async () => {
    await r.click('#btn-reset');
    await r.until(`${pcs} === 'PC 0,PC 0'`, { max: 3, what: 'both cores at PC 0' });
    await r.click('#btn-step');
    await r.until(`${pcs} === 'PC 1,PC 1'`, { max: 4, what: 'both cores at PC 1' });
    await r.show('#core-source-pru0 [data-pane-readout="pc"]', 0.8);
    await r.show('#source-focus-pane [data-pane-readout="pc"]', 0.8);
  });

  // ---- processor, clocks and config -------------------------------------------------------
  const processor = `document.getElementById('processor-select').dataset.current`;
  const switchTo = async (name) => {
    const asked = r.answerDialog(true);
    await r.select('#processor-select', name);
    const message = await asked;
    must(message === `Switching to ${name} reloads the simulator and clears loaded programs. Continue?`, 'the dashboard asked: ' + message);
    await r.until(`${processor} === '${name}' && document.getElementById('processor-select').value === '${name}' && !processorChanging`, { max: 10, what: `processor ${name}` });
  };
  await r.cue('proc-select', async () => {
    must(await js(`[...document.getElementById('processor-select').options].map((o) => o.value).join()`) === 'AM243x,AM261x,AM263x,AM62x,AM64x', 'the processor list differs');
    must(await js(processor) === 'AM243x', 'not on AM243x');
    await r.show('#processor-select', 2.5);
  });
  await r.cue('proc-confirm', async () => {
    await r.hold(1.4);
    await switchTo('AM261x');
    await r.until(`document.querySelectorAll('#source-panes li').length === 0 && [...document.querySelectorAll('.core-card')].filter((c) => !c.hidden).length === 2`, { max: 5, what: 'AM261x: programs cleared, two cores' });
  });
  await r.cue('proc-back', async () => {
    await r.click('#cores-toggle');
    await r.until(`document.getElementById('cores-control').classList.contains('is-open')`, { max: 3, what: 'CORES deck open' });
    must((await cores()).cards === 2, 'AM261x should offer two cores');
    await r.hold(1.2);
    await switchTo('AM243x');
    await r.until(`[...document.querySelectorAll('.core-card')].filter((c) => !c.hidden).length === 4`, { max: 5, what: 'AM243x: four cores' });
    await r.hold(0.8);
    await r.click('.logo');                          // a click outside closes the deck
    must(!(await cores()).open, 'the deck is still open');
  });
  const iep = (active, value) => r.until(`document.getElementById('iep-clock-select').title.startsWith('Active ${active} MHz (external source)') && document.getElementById('iep-clock-select').value === '${value}'`,
    { max: 4, what: `IEP clock ${active} MHz` });
  await r.cue('iep-select', async () => {
    await iep(200, 'configured');
    must(await js(`[...document.getElementById('iep-clock-select').options].map((o) => o.value).join()`) === 'configured,200,225,250,300,333,custom', 'the IEP rate list differs');
    await r.show('#iep-clock-select', 1.6);
    await r.select('#iep-clock-select', '250');
    await iep(250, '250');
  });
  await r.cue('iep-custom', async () => {
    await r.select('#iep-clock-select', 'custom');
    await r.until(`!document.getElementById('iep-clock-custom-form').hidden`, { max: 3, what: 'custom rate field' });
    await r.type('#iep-clock-custom', '312.5', { clear: true });
    await r.click('#iep-clock-custom-form button');
    await iep(312.5, 'custom');
  });
  await r.cue('iep-configured', async () => {
    await r.select('#iep-clock-select', 'configured');
    await iep(200, 'configured');
    must(await js(`document.getElementById('iep-clock-custom-form').hidden`), 'the custom rate field is still shown');
    must(/Configured 200 MHz · External 200 MHz/.test(await js(`document.getElementById('iep-clock-select').title`)), 'tooltip: ' + await js(`document.getElementById('iep-clock-select').title`));
  });
  await r.cue('config-open', async () => {
    await r.click('#btn-config');
    await r.until(`${modal('config-modal')} === 'flex' && document.getElementById('config-textarea').value.includes('[device]')`, { max: 4, what: 'Config dialog with memory.cfg' });
    await r.expect('#config-modal-title', /^memory\.cfg$/);
    await r.expect('#config-textarea', /\[device\]\s+target = AM243x[\s\S]*pru_clock_mhz = 300[\s\S]*\[DRAM0\][\s\S]*read_latency/);
    await r.moveTo('#config-textarea', 20);
    await r.hold(0.8);
    // the dialog opens with the text scrolled to its end: go to the top, where [device] is
    await r.scrollTo('#config-textarea', 0, 24);
    const line = await js(`(() => { const t = document.getElementById('config-textarea'), b = t.getBoundingClientRect(), s = getComputedStyle(t);
      const row = t.value.slice(0, t.value.indexOf('pru_clock_mhz')).split('\\n').length - 1;
      return [b.left + parseFloat(s.paddingLeft) + 150, b.top + parseFloat(s.paddingTop) + (row + 0.5) * parseFloat(s.lineHeight)]; })()`);
    must(line.every(Number.isFinite), 'cannot place the pointer on pru_clock_mhz: ' + line);
    await r.moveTo(line, 16);
  });
  await r.cue('config-buttons', async () => {
    await r.show('#btn-config-save', 2.2);
    await r.click('#btn-config-cancel');
    await r.until(`${modal('config-modal')} === 'none'`, { max: 3, what: 'Config dialog closed' });
  });
  await r.cue('part-end', async () => { await r.moveTo([960, 560], 24); });
}
