// Part 3: UART and I2C devices. One r.cue() per narration key, in file order.
import { openFile } from '../lib/dashboard.mjs';

const SIM = '[data-workspace-view="simulator"]';
const IO = '#io-nav-button';
const card = (name) => `section[aria-labelledby="${name}-runtime-title"]`;
const status = (word) => `document.getElementById('status-badge').textContent.includes('${word}')`;

export default async function tour(r, page) {
  const value = (sel) => page.locator(sel).first().inputValue();
  const must = (ok, message) => { if (!ok) throw new Error(message); };
  /** A point near the left end of a wide element, where its text is. */
  const near = async (sel, dx = 60) => {
    const box = await page.locator(sel).first().boundingBox();
    if (!box) throw new Error('not visible: ' + sel);
    return [box.x + Math.min(dx, box.width / 2), box.y + box.height / 2];
  };
  /** Open a source file, Load & Assemble, Reset. `mnemonic` must appear in the listing. */
  const load = async (name, mnemonic) => {
    await openFile(r, name);
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-panel').innerText.includes('${mnemonic}')`, { max: 6, what: `${name} listed in Source / Disassembly` });
    await r.click('#btn-reset');
  };
  const devices = async () => {
    await r.click(IO);
    await r.until(`!document.getElementById('devices-view').hidden && !document.getElementById('io-view').hidden`, { max: 3, what: 'the Devices sub-tab' });
  };

  // ---- the I/O & Devices view ---------------------------------------------------
  await r.cue('part-intro', async () => { await r.moveTo([960, 560], 30); });
  await r.cue('io-view', async () => {
    await r.click(IO);
    await r.until(`!document.getElementById('io-view').hidden`, { max: 3, what: 'the I/O & Devices view' });
    await r.outline('#io-subtabs');
    await r.show('#io-subtab-devices', 0.5);
    await r.click('#io-subtab-peripherals'); await r.hold(0.7);
    await r.click('#io-subtab-events'); await r.hold(0.7);
    await r.click('#io-subtab-devices');
    r.clearOutline();
  });
  await r.cue('device-core', async () => {
    await r.outline('#device-runtime-panel');
    await r.moveTo('#ssi-runtime-title', 20);
    await r.hold(1.6);
    r.clearOutline();
    must((await page.locator('#device-core-select option').allInnerTexts()).join() === 'PRU0,PRU1,RTU0,RTU1', 'the Core selector does not list PRU0, PRU1, RTU0, RTU1');
    await r.show('#device-core-select', 2.4);
    await r.show(`${card('ssi')} strong`, 0.8);
    await r.show(`${card('tca')} strong`, 0.8);
    await r.show(await near('#device-list-title'), 0.8);
  });

  await r.cue('core-clock', async () => {
    await r.hold(2.2);
    await r.moveTo('#cores-toggle', 24);                       // the deck opens on hover
    await r.until(`document.getElementById('cores-toggle').getAttribute('aria-expanded') === 'true'`, { max: 3, what: 'the CORES deck open' });
    await r.expect('.cores-clock-head [data-core-rate]', /^250 MHz$/);
    await r.show('.cores-clock-head', 1.6);
    const slider = await page.locator('.cores-clock-slider').boundingBox();
    await r.drag('.cores-clock-slider', -slider.width / 2, 0, 30);   // thumb from the middle (250) to the left end (200)
    await r.until(`clockState && clockState.mhz === 200 && !clockChanging`, { max: 10, what: 'a PRU core frequency of 200 MHz' });
    await r.expect('.cores-clock-head [data-core-rate]', /^200 MHz$/);
    await r.show('.cores-clock-notice', 2.0);
  });

  // ---- UART decoder ---------------------------------------------------------------
  await r.cue('uart-load', async () => {
    await r.click(SIM);                                         // also takes the focus out of the deck, which closes it
    await r.until(`document.getElementById('cores-toggle').getAttribute('aria-expanded') === 'false'`, { max: 3, what: 'the CORES deck closed' });
    await load('uart_print.asm', 'sbco');
  });
  await r.cue('uart-window', async () => {
    await r.outline('#signal-graph-panel');
    await r.select('#graph-win-sel', '2048');
    await r.until(`signalGraph.windowSize === 2048`, { max: 3, what: 'a Signal Graph window of 2048 samples' });
    await r.hold(1.5);
    r.clearOutline();
  });
  await r.cue('uart-record', async () => {
    await devices();
    await r.outline(card('uart'));
    await r.moveTo('#uart-runtime-title', 22);
    await r.hold(1.2);
    r.clearOutline();
    await r.click('#uart-record-btn');
    await r.until(`signalGraph.recording && document.getElementById('graph-rec-btn').classList.contains('rec-on')`, { max: 3, what: 'the Signal Graph recording' });
  });
  await r.cue('uart-run', async () => {
    await r.click('#btn-run');
    // the firmware halts by itself after the tenth character (about 174,000 instructions)
    await r.until(`${status('HALTED')} && parseInt(document.getElementById('cnt-cycles').textContent.replace(/\\D/g, ''), 10) > 170000`,
      { max: 60, what: 'uart_print.asm halted after sending the string' });
    await r.show('#cnt-cycles', 1.0);
  });
  await r.cue('uart-stop', async () => {
    await r.expect('#uart-record-btn', /Stop recording/);
    await r.show('#uart-record-btn', 1.0);
    await r.click();
    await r.until(`!signalGraph.recording`, { max: 3, what: 'recording stopped' });
    await r.expect('#uart-record-btn', /^\s*Record\s*$/);
  });
  await r.cue('uart-decode', async () => {
    await r.click('#uart-decode-btn');
    await r.until(`getComputedStyle(document.getElementById('uart-output')).display !== 'none'`, { max: 3, what: 'decoder output' });
    await r.expect('#uart-output', /^Hello 2026$/);
    await r.expect('#uart-status', /Auto-detected: \d+ steps\/bit · 10 bytes decoded/);
    await r.show(await near('#uart-status', 110), 1.4);
    await r.show(await near('#uart-output', 50), 1.6);
  });
  await r.cue('uart-clear', async () => {
    await r.click('#uart-clear-btn');
    await r.until(`getComputedStyle(document.getElementById('uart-output')).display === 'none' && getComputedStyle(document.getElementById('uart-status')).display === 'none'`,
      { max: 3, what: 'decoder output cleared' });
  });

  // ---- UART RX Inject -----------------------------------------------------------
  await r.cue('inject-load', async () => {
    await r.click(SIM);
    await load('uart_rx_11frame.asm', 'wait_start');
  });
  await r.cue('inject-mode', async () => {
    await devices();
    await r.outline('#uart-rx-inject');
    await r.show('#uart-inj-mode-hex', 1.4);
    await r.expect('#uart-inj-payload', /^48 65 6C 6C 6F 57 6F 72 6C 64 21$/);
    await r.click('#uart-inj-mode-ascii');
    await r.expect('#uart-inj-payload', /^HelloWorld!$/);
    await r.hold(0.8);
    r.clearOutline();
  });
  await r.cue('inject-payload', async () => {
    await r.type('#uart-inj-payload', 'HelloWorld!', { clear: true });
    await r.expect('#uart-inj-payload', /^HelloWorld!$/);
    await r.expect('#uart-inj-byte-count', /\(11 bytes\)/);
    await r.show('#uart-inj-byte-count', 1.2);
  });
  await r.cue('inject-settings', async () => {
    must(await value('#uart-inj-pin') === '0' && await value('#uart-inj-baud') === '4.00' && await value('#uart-inj-frames') === '1',
      'RX Inject is not at GPI0, 4.00 Mb, 1 frame');
    must((await page.locator('#uart-inj-pin option').allInnerTexts()).join() === 'GPI0,GPI1,GPI2,GPI3', 'the Pin list is not GPI0 to GPI3');
    await r.show('#uart-inj-pin', 2.2);
    await r.show('#uart-inj-baud', 1.6);
    await r.show('#uart-inj-frames', 2.4);
  });
  await r.cue('inject-arm', async () => {
    await r.click('#uart-inj-btn');
    await r.until(`document.getElementById('uart-inj-status').textContent.includes('Armed: 11 bytes × 1 frame')`, { max: 4, what: 'the status line "Armed: 11 bytes x 1 frame"' });
    await r.expect('#uart-inj-status', /run to receive/);
    await r.show(await near('#uart-inj-status', 130), 2.0);
  });
  await r.cue('inject-run', async () => {
    await r.click('#btn-run');
    // R20 is the firmware's frame counter
    await r.until(`document.getElementById('reg-val-20').textContent.trim() === '0x00000001'`, { max: 20, what: 'one frame received (R20 = 1)' });
    await r.hold(1.6);
    await r.click();
    await r.until(status('HALTED'), { max: 4, what: 'status HALTED' });
  });
  await r.cue('inject-memory', async () => {
    await r.click(SIM);
    await r.expect('#mem-addr-input', /^0x00000000$/);
    await r.click('#btn-mem-refresh');
    await r.click('#mem-fmt-group button[data-fmt="byte"]');
    await r.until(`/^0x00000000\\s+48\\s+65\\s+6C\\s+6C\\s+6F\\s+57\\s+6F\\s+72\\s+6C\\s+64\\s+21\\s/.test(document.getElementById('mem-grid').innerText)`,
      { max: 4, what: 'the bytes 48 65 6C 6C 6F ... at address 0 in Memory 1' });
    await r.show('#mem-grid .mem-byte >> nth=0', 0.5);
    await r.moveTo('#mem-grid .mem-byte >> nth=4', 40);
    await r.hold(1.2);
  });
  await r.cue('inject-reset', async () => {
    await devices();
    await r.click('#uart-inj-reset-btn');
    await r.expect('#uart-inj-payload', /^48 65 6C 6C 6F 57 6F 72 6C 64 21$/);
    must(await page.locator('#uart-inj-mode-hex').evaluate((el) => el.classList.contains('active')), 'Reset inputs did not return to Hex');
    must(await value('#uart-inj-pin') === '0' && await value('#uart-inj-baud') === '4.00' && await value('#uart-inj-frames') === '1', 'Reset inputs did not restore pin, baud and frames');
    must(!(await page.locator('#uart-inj-status').isVisible()), 'Reset inputs left the status line');
    await r.show('#uart-inj-payload', 1.4);
  });

  // ---- I2C expander -----------------------------------------------------------------
  await r.cue('i2c-load', async () => {
    await r.click(SIM);
    await load('i2c_tca9538_running_led.asm', 'i2c_write_byte');
  });
  await r.cue('i2c-name', async () => {
    await devices();
    await r.outline(card('tca'));
    await r.moveTo('#tca-runtime-title', 20);
    await r.hold(1.6);
    r.clearOutline();
    await r.show('#tca-device-name', 2.6);
    must(await value('#tca-address') === '0x23', 'the I2C address field is not 0x23');
    await r.show('#tca-address', 2.6);
  });
  await r.cue('i2c-pins', async () => {
    must(await value('#tca-scl-pin') === '0' && await value('#tca-sda-pin') === '1', 'SCL and SDA are not 0 and 1');
    await r.show('#tca-scl-pin', 1.4);
    await r.show('#tca-sda-pin', 1.4);
  });
  await r.cue('i2c-attach', async () => {
    await r.click('#tca-attach-button');
    await r.until(`!document.getElementById('tca-state').hidden && document.querySelectorAll('#tca-leds .i2c-led').length === 8`, { max: 4, what: 'the eight pin boxes' });
    await r.expect('#tca-empty-state', /tca9538 attached to pru0 at 0x23 \(SCL pin 0, SDA pin 1\)/);
    await r.expect('#tca-registers', /CONFIG=0xFF · all pins are inputs/);
    await r.outline('#tca-leds');
    await r.show('#tca-leds .i2c-led >> nth=0', 2.0);
    await r.show('#tca-registers', 1.6);
    r.clearOutline();
  });
  const lit = `[...document.querySelectorAll('#tca-leds .i2c-led')].findIndex((el) => el.classList.contains('on'))`;
  await r.cue('i2c-run', async () => {
    await r.click('#btn-run');
    await r.until(`document.getElementById('tca-registers').textContent.includes('CONFIG=0x00')`, { max: 20, what: 'CONFIG=0x00 on the expander card' });
    await r.until(`${lit} >= 0`, { max: 10, what: 'a lit pin box' });
    await r.moveTo('#tca-leds', 20);
    for (let i = 0; i < 2; i++) {                               // the lit box has to move, twice
      const at = await page.evaluate(lit);
      await r.until(`${lit} >= 0 && ${lit} !== ${at}`, { max: 10, what: 'the lit pin box moving on' });
    }
    await r.outline('#tca-leds');
    await r.hold(3.0);
    r.clearOutline();
  });
  await r.cue('i2c-status', async () => {
    await r.expect('#tca-registers', /OUTPUT=0x[0-9A-F]{2}\s+POLARITY=0x00\s+CONFIG=0x00[\s\S]*bus events · 0 faults · state/);
    await r.expect('#tca-log', /addr=0x23 reg=0x01 data=0x[0-9A-F]{2} ACK/);
    await r.show('#tca-registers', 4.2);
    await r.show('#tca-log', 1.6);
  });
  await r.cue('i2c-list', async () => {
    await r.reveal('#ssi-device-list', '#io-view');
    await r.outline('#ssi-device-list');
    await r.expect('#ssi-device-list', /tca9538\s+I2C expander \(tca9538\) · pru0 · address 0x23 · SCL pin 0 · SDA pin 1/);
    await r.show(await near('#device-list-title'), 1.2);
    await r.show(await near('#ssi-device-list .runtime-device-row-detail', 260), 2.0);
    await r.show('#ssi-device-list button[data-device-action="detach"]', 1.2);
    r.clearOutline();
  });
  await r.cue('i2c-detach', async () => {
    await r.click('#btn-run');
    await r.until(status('HALTED'), { max: 4, what: 'status HALTED' });
    await r.click('#tca-detach-button');
    await r.until(`document.querySelectorAll('#ssi-device-list .runtime-device-row').length === 0 && document.getElementById('tca-state').hidden`,
      { max: 4, what: 'the expander removed from the card and the list' });
    await r.expect('#tca-empty-state', /No TCA9538 attached to pru0/);
    await r.show(await near('#tca-empty-state', 80), 1.0);
  });
  await r.cue('part-end', async () => { await r.moveTo([960, 620], 24); });
}
