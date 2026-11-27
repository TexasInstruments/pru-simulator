// Part 4: SSI encoder, peripherals and events. One r.cue() per narration key, in file order.
import { text } from '../lib/dashboard.mjs';

const SIM = '[data-workspace-view="simulator"]';
const IO = '#io-nav-button';
const MODEL = '[data-scenario="SSI encoder (single core)"]';
const PRU = '[data-scenario="SSI (encoder PRU + reader)"]';
const status = (word) => `document.getElementById('status-badge').textContent.includes('${word}')`;
const FAULT_PROGRAM = '    ldi32 r1, 0x90000000\n    lbbo  &r0, r1, 0, 4\n    halt';

export default async function tour(r, page) {
  const value = (sel) => page.locator(sel).first().inputValue();
  const must = (ok, message) => { if (!ok) throw new Error(message); };
  /** A point near the left end of a wide element, where its text is. */
  const near = async (sel, dx = 60) => {
    const box = await page.locator(sel).first().boundingBox();
    if (!box) throw new Error('not visible: ' + sel);
    return [box.x + Math.min(dx, box.width / 2), box.y + box.height / 2];
  };
  const frames = () => text(page, '#ssi-received-count').then(Number);
  /** Run is on: Received position must show `position` and Received frames must keep rising. */
  const receiving = async (position) => {
    await r.until(`document.getElementById('ssi-received-position').textContent.startsWith('${position} (') && Number(document.getElementById('ssi-received-count').textContent) >= 2`,
      { max: 30, what: `Received position ${position}` });
    const seen = await frames();
    await r.until(`Number(document.getElementById('ssi-received-count').textContent) > ${seen}`, { max: 20, what: 'Received frames rising' });
  };
  const stop = async () => {
    await r.click('#btn-run');
    await r.until(status('HALTED'), { max: 4, what: 'status HALTED' });
  };

  // ---- SSI encoder ------------------------------------------------------------------
  // Number of different colours on the Signal Graph canvas: the empty graph has only its background and grid.
  const graphColours = `(() => { const c = document.getElementById('signal-graph-canvas');
    if (!c.width || !c.height) return 0;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, seen = new Set();
    for (let i = 0; i < d.length; i += 4) seen.add((d[i] << 16) | (d[i + 1] << 8) | d[i + 2]);
    return seen.size; })()`;
  await r.cue('part-intro', async () => { await r.moveTo([960, 560], 30); });
  const emptyGraph = await page.evaluate(graphColours);   // the Simulator view is on screen, nothing is recorded yet

  await r.cue('ssi-open', async () => {
    await r.click(IO);
    await r.until(`!document.getElementById('io-view').hidden && !document.getElementById('devices-view').hidden`, { max: 3, what: 'the Devices sub-tab' });
    await r.show('#io-subtab-devices', 0.8);
    await r.outline('section[aria-labelledby="ssi-runtime-title"]');
    await r.moveTo('#ssi-runtime-title', 20);
  });
  await r.cue('ssi-preset', async () => {
    r.clearOutline();
    must(await page.locator('#ssi-preset option').count() === 13, 'Preset does not offer Custom plus twelve frames');
    await r.show('#ssi-preset', 3.6);
    await r.select('#ssi-preset', 'TTK70');
    must(await value('#ssi-resolution') === '26' && await value('#ssi-error-bits') === '2' && await value('#ssi-encoding') === 'gray',
      'TTK70 did not fill 26 bits, 2 error bits, Gray code');
    await r.hold(0.8);
    await r.show('#ssi-resolution', 1.2);
    await r.show('#ssi-error-bits', 1.0);
    await r.show('#ssi-encoding', 1.0);
  });
  await r.cue('ssi-position', async () => {
    must(await value('#ssi-position') === '2748', 'Position is not 2748');
    await r.show('#ssi-position', 3.0);
    await r.show('#ssi-error-value', 2.4);
  });
  await r.cue('ssi-frame', async () => {
    must((await page.locator('#ssi-encoding option').allInnerTexts()).join() === 'Binary,Gray code', 'Encoding is not Binary / Gray code');
    await r.show('#ssi-resolution', 2.0);
    await r.show('#ssi-error-bits', 1.6);
    await r.show('#ssi-encoding', 2.4);
  });
  await r.cue('ssi-model', async () => {
    await r.click(MODEL);
    await r.until(`!document.getElementById('ssi-demo-result').hidden && document.getElementById('ssi-demo-status').textContent.includes('Press Run')`,
      { max: 10, what: 'the reader and the encoder model set up' });
    await r.expect('#ssi-demo-wiring', /PRU0 reader → encoder model: 26-bit frame, clock GPO0, data GPI16/);
    await r.expect('#ssi-empty-state', /1 SSI encoder attached to pru0/);
    must(await page.evaluate(`JSON.stringify(coreSelection.used)`) === '["pru0"]', 'the model setup does not use PRU0 alone');
    await r.show(await near('#ssi-empty-state', 90), 2.2);
    await r.show(await near('#ssi-demo-wiring', 120), 3.0);
    await r.show(await near('#ssi-demo-status', 80), 1.6);
  });
  await r.cue('ssi-run', async () => {
    await r.click('#btn-run');
    await receiving(2748);
    await r.expect('#ssi-demo-requested', /^2748$/);
    await r.show('#ssi-received-position', 2.2);
    await r.show('#ssi-received-count', 2.0);
  });
  await r.cue('ssi-next', async () => {
    await r.type('#ssi-demo-position', '1234', { clear: true });
    await r.click('#ssi-demo-set-position');
    await r.until(`document.getElementById('ssi-received-position').textContent.startsWith('1234 (')`, { max: 20, what: 'Received position 1234' });
    await r.expect('#ssi-demo-requested', /^1234$/);
    await r.show('#ssi-received-position', 2.0);
  });
  const row = '#ssi-device-list .runtime-device-row';
  await r.cue('ssi-list', async () => {
    await r.reveal('#ssi-device-list', '#io-view');
    await r.outline('#ssi-device-list');
    await r.expect(row, /ssi_encoder\s+CLK GPO 0 · DATA GPI 16 · TTK70 · 26-bit gray · position 1234[\s\S]*Latest frame: 1234 \(error 0\) at cycle \d+/);
    await r.show(await near('#device-list-title'), 1.0);
    await r.show(await near(`${row} [data-role="frame"]`, 150), 1.4);
    must(await value(`${row} input[data-position]`) === '1234', 'the list row does not show the next position 1234');
    await r.show(`${row} input[data-position]`, 1.0);
    await r.show(`${row} input[data-error]`, 1.0);
    await r.show(`${row} button[data-device-action="set-position"]`, 0.8);
    await r.show(`${row} button[data-device-action="set-error"]`, 0.8);
    await r.show(`${row} button[data-device-action="detach"]`, 0.8);
    r.clearOutline();
  });
  await r.cue('ssi-pru', async () => {
    await stop();
    await r.scrollTo('#io-view', 0);
    await r.click(PRU);
    await r.until(`document.getElementById('ssi-demo-wiring').textContent.includes('PRU1 encoder firmware') && document.getElementById('ssi-demo-status').textContent.includes('Press Run')`,
      { max: 10, what: 'the reader and the encoder firmware set up' });
    await r.expect('#ssi-demo-wiring', /PRU0 reader → PRU1 encoder firmware: 26-bit frame, clock pin 0, data pin 16/);
    await r.expect('#ssi-empty-state', /No SSI encoder attached to pru0/);          // no model in this mode
    must(await page.evaluate(`JSON.stringify(coreSelection.used)`) === '["pru0","pru1"]', 'PRU0 and PRU1 are not both in use');
    await r.show(await near('#ssi-demo-wiring', 120), 3.4);
    await r.expect('#cores-toggle', /PRU0\s*PRU1[\s\S]*×2/);
    await r.show('#cores-toggle', 2.6);                                              // hovering opens the deck: Use is on for PRU0 and PRU1
  });
  await r.cue('ssi-pru-run', async () => {
    await r.click('#btn-run');
    await receiving(2748);
    await r.expect('#ssi-firmware-status', /Reader 0 \/ encoder 0/);
    await r.show('#ssi-received-position', 1.4);
    await r.show('#ssi-received-count', 1.6);
  });
  await r.cue('ssi-waveforms', async () => {
    // Run stays on: the Signal Graph is only drawn while it is visible, so a stopped program would show an empty graph here
    await r.reveal('#ssi-demo-waveforms', '#io-view');
    await r.click('#ssi-demo-waveforms');
    await r.until(`!document.getElementById('main').hidden && document.getElementById('io-view').hidden`, { max: 3, what: 'the Simulator view' });
    r.live = true;
    await r.until(`document.querySelectorAll('#graph-legend .graph-legend-item').length >= 4 && ${graphColours} > ${emptyGraph} + 8`,
      { max: 10, what: 'lanes drawn in the Signal Graph' });
    await r.expect('#graph-legend', /PRU0 GPO 0[\s\S]*PRU1 GPO 16/);
    // the clock (PRU0 GPO0) and the data line (PRU0 GPI16) must both toggle in the recording
    must(await page.evaluate(`(() => { const s = graphGetSamples(); const both = (f) => s.some(f) && !s.every(f);
      return s.length > 100 && both((x) => x.gpo[0]) && both((x) => x.gpi[16]); })()`), 'the Signal Graph holds no SSI clock and data trace');
    await r.outline('#signal-graph-panel');
    await r.moveTo('#signal-graph-canvas', 26);
    await r.hold(3.2);
    await stop();
    await r.hold(0.5);
    must(await page.evaluate(graphColours) > emptyGraph + 8, 'the Signal Graph is empty after Stop');
    await r.moveTo('#signal-graph-canvas', 20);
    r.live = false;
    r.clearOutline();
  });
  await r.cue('ssi-advanced', async () => {
    await r.click(IO);
    await r.until(`!document.getElementById('devices-view').hidden`, { max: 3, what: 'the Devices sub-tab' });
    await r.reveal('#devices-view details > summary', '#io-view');
    await r.click('#devices-view details > summary');
    await r.until(`document.querySelector('#devices-view details').open`, { max: 3, what: 'the Advanced section open' });
    await r.reveal('#ssi-attach-button', '#io-view');
    await r.show('#ssi-device-name', 1.2);
    await r.show('#ssi-clock-pin', 1.0);
    await r.show('#ssi-data-pin', 1.0);
    await r.click('#ssi-attach-button');
    await r.until(`document.querySelectorAll('${row}[data-kind="ssi"]').length === 1`, { max: 5, what: 'the encoder model attached' });
    await r.expect('#ssi-demo-wiring', /PRU1 encoder firmware/);                     // the loaded programs are untouched
    await r.reveal('#ssi-device-list', '#io-view');
    await r.outline('#ssi-device-list');
    await r.expect(row, /ssi_encoder\s+CLK GPO 0 · DATA GPI 8 · TTK70 · 26-bit gray · position 2748/);
    await r.show(await near(`${row} .runtime-device-row-detail`, 260), 1.6);
    await r.show(`${row} input[data-position]`, 0.6);
    await r.click(`${row} button[data-device-action="detach"]`);
    await r.until(`document.querySelectorAll('${row}').length === 0`, { max: 4, what: 'the encoder model detached' });
    r.clearOutline();
  });

  // ---- Peripherals ----------------------------------------------------------------
  const active = (id) => `(document.getElementById('${id}').dataset.active === 'true')`;
  await r.cue('periph-tab', async () => {
    await r.scrollTo('#io-view', 0);
    await r.click('#io-subtab-peripherals');
    await r.until(`!document.getElementById('peripherals-view').hidden`, { max: 3, what: 'the Peripherals sub-tab' });
    await r.expect('#periph-sd-card-inactive', /Inactive in this mux mode: GP mux is GPIO/);
    await r.expect('#periph-pi-card-inactive', /Inactive in this mux mode: GP mux is GPIO/);
    await r.show(await near('#periph-sd-card-title', 90), 1.8);
    await r.show(await near('#periph-pi-card-title', 90), 1.8);
    await r.show(await near('#periph-sd-card-inactive', 200), 1.6);
    await r.show(await near('#periph-pi-card-inactive', 200), 1.6);
  });
  await r.cue('pi-switch', async () => {
    await r.click('#pi-switch-pi');
    await r.until(`${active('periph-pi-card')} && getComputedStyle(document.getElementById('perif-interface')).display !== 'none' && document.querySelectorAll('#perif-loopback .perif-lb-row').length === 3`,
      { max: 4, what: 'the Peripheral Interface card live' });
    await r.expect('#periph-sd-card-inactive', /GP mux is Peripheral Interface/);
    await r.show(await near('#perif-mode-info', 40), 1.6);
    await r.show(await near('#periph-sd-card-inactive', 260), 2.0);
  });
  const cfg = (n, what) => `#perif-cfg-regs .perif-cfgreg-row >> nth=${n} >> ${what}`;
  await r.cue('pi-cfg', async () => {
    await r.show(cfg(0, 'input'), 1.2);
    await r.show(cfg(1, '.perif-cfgreg-name'), 0.6);
    await r.type(cfg(1, 'input'), '0x00070010', { clear: true });
    await r.click(cfg(1, 'button'));
    await r.until(`document.getElementById('perif-r30-decode').textContent.includes('tx_div=7')`, { max: 4, what: 'tx_div=7 in the decoded line' });
    must(await value(cfg(1, 'input')) === '0x00070010', 'TXCFG does not read back 0x00070010');
    await r.show(cfg(1, 'input'), 0.6);
    await r.show(await near('#perif-r30-decode', 170), 2.0);
  });
  const lb = (what) => `#perif-loopback .perif-lb-row >> nth=0 >> ${what}`;
  await r.cue('pi-loopback', async () => {
    await r.outline('#perif-channels');
    await r.show('#perif-channels .perif-channel-card >> nth=0', 1.8);
    await r.outline('#perif-loopback');
    await r.click(lb('input[data-f="enabled"]'));
    await r.show(lb('input[data-f="latency_ns"]'), 0.8);
    await r.show(lb('input[data-f="jitter_ns"]'), 0.8);
    await r.show(lb('input[data-f="drift_ppm"]'), 0.8);
    await r.click(lb('button'));
    await r.until(`document.getElementById('perif-lb-status').textContent.includes('Loopback ch0 enabled')`, { max: 4, what: '"Loopback ch0 enabled"' });
    must(await page.locator(lb('input[data-f="enabled"]')).isChecked(), 'the ch0 loopback box is not ticked after Apply');
    r.clearOutline();
    await r.show(await near('#perif-lb-status', 70), 1.0);
  });
  const mod = (what) => `#sd-pattern-gen .sd-mod-card >> nth=0 >> ${what}`;
  await r.cue('sd-switch', async () => {
    await r.click('#sd-switch-sd');
    await r.until(`${active('periph-sd-card')} && !${active('periph-pi-card')} && document.querySelectorAll('#sd-channels .sd-channel-card').length === 3`,
      { max: 4, what: 'the SD filter card live with three channels' });
    await r.expect('#periph-pi-card-inactive', /GP mux is SD \(Sigma-Delta\)/);
    await r.outline('#sd-channels');
    await r.show('#sd-channels .sd-channel-card >> nth=1', 1.6);
    r.clearOutline();
  });
  await r.cue('sd-pattern', async () => {
    await r.outline('#sd-pattern-gen');
    await r.moveTo(mod('.sd-mod-title'), 20);
    await r.hold(2.0);
    r.clearOutline();
    must((await page.locator(mod('select[data-param="signal"] option')).allInnerTexts()).join() === 'DC,Sine', 'Signal is not DC / Sine');
    await r.show(mod('select[data-param="signal"]'), 1.8);
    await r.show(mod('input[data-param="sd_clock_mhz"]'), 2.0);
    await r.show(mod('input[data-param="dc_level"]'), 1.6);
  });
  await r.cue('sd-sine', async () => {
    await r.select(mod('select[data-param="signal"]'), 'sine');
    await r.until(`['amplitude', 'period', 'phase_deg'].every((p) => document.querySelector('#sd-pattern-gen input[data-ch="0"][data-param="' + p + '"]')) && !document.querySelector('#sd-pattern-gen input[data-ch="0"][data-param="dc_level"]')`,
      { max: 4, what: 'Amp, Period and Phase instead of DC Level' });
    await r.show(mod('input[data-param="amplitude"]'), 0.7);
    await r.show(mod('input[data-param="period"]'), 0.7);
    await r.show(mod('input[data-param="phase_deg"]'), 0.7);
  });
  const chip = '#sd-config-chips button >> nth=0';
  await r.cue('sd-osr', async () => {
    await r.outline('#sd-config-chips');
    await r.show(chip, 2.6);
    const asked = r.answerDialog('64');                          // the prompt() is drawn by the browser, not by the page
    await r.click();
    must(await asked === 'Channel 0 OSR (4-256):', 'the OSR prompt has another text');
    await r.until(`document.querySelector('#sd-config-chips button').textContent.includes('Ch0: OSR=64') && document.querySelector('#sd-channels .sd-channel-card').textContent.includes('OSR:64')`,
      { max: 4, what: 'channel 0 at OSR 64' });
    r.clearOutline();
    await r.hold(3.4);
    await r.show(chip, 1.0);
    await r.show('#sd-channels .sd-channel-card >> nth=0 >> .sd-channel-config', 1.6);
  });
  await r.cue('sd-gpio', async () => {
    await r.click('#sd-switch-gpio');
    await r.until(`!${active('periph-sd-card')} && !${active('periph-pi-card')}`, { max: 4, what: 'both cards inactive' });
    await r.expect('#periph-sd-card-inactive', /Inactive in this mux mode: GP mux is GPIO/);
    await r.expect('#periph-pi-card-inactive', /Inactive in this mux mode: GP mux is GPIO/);
    await r.show(await near('#periph-pi-card-inactive', 200), 1.2);
  });

  // ---- Events and faults ------------------------------------------------------------
  const badge = `!document.getElementById('io-fault-badge').hidden`;
  await r.cue('events-program', async () => {
    await r.show('#io-subtab-events', 1.6);
    await r.click(SIM);
    await r.click('#editor-tab-bar .editor-tab:has-text("program.asm")');
    await r.hold(0.4);
    await r.type('#asm-source', FAULT_PROGRAM, { clear: true });
    must((await value('#asm-source')).trim() === FAULT_PROGRAM.trim(), 'the editor does not hold the typed program');
  });
  await r.cue('events-run', async () => {
    must(await value('#assembly-core-target') === 'pru0', 'Load into is not PRU0');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-panel').innerText.includes('lbbo')`, { max: 6, what: 'the program listed in Source / Disassembly' });
    await r.click('#btn-reset');
    await r.click('#btn-run');
    await r.until(`!document.getElementById('core-fault-banner').hidden && ${status('HALTED')}`, { max: 10, what: 'the Core fault band' });
    await r.expect('#core-fault-banner', /Core fault · pru0 · LBBO at \d+: No memory region mapped at address 0x90000000/);
    await r.show(await near('#core-fault-banner', 330), 3.0);
  });
  await r.cue('events-badge', async () => {
    await r.until(badge, { max: 3, what: 'the fault badge' });
    await r.expect('#io-fault-badge', /^\s*1\s+1 new fault\s*$/);
    await r.show('#io-fault-badge', 3.0);
  });
  await r.cue('events-tab', async () => {
    await r.click(IO);
    await r.until(`!document.getElementById('io-view').hidden`, { max: 3, what: 'the I/O & Devices view' });
    must(await page.evaluate(badge), 'the badge was cleared before Events was opened');
    await r.hold(0.6);
    await r.click('#io-subtab-events');
    await r.until(`!document.getElementById('events-view').hidden && !(${badge})`, { max: 3, what: 'Events open and the badge cleared' });
    await r.expect('#device-event-log', /pru0 · LBBO at \d+: No memory region mapped at address 0x90000000/);
    await r.show(await near('#device-event-log', 300), 2.0);
    await r.show(IO, 1.4);
  });

  // ---- where to go next -----------------------------------------------------------
  await r.cue('next-examples', async () => { await r.moveTo([960, 620], 30); });
  await r.cue('next-tools');
}
