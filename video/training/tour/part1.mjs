// Part 1: First steps. One r.cue() per narration key, in file order.
import { openFile, reg, cycles, text } from '../lib/dashboard.mjs';

export const startPage = 'terminal.html';

const toggle = (label) => `.panel-toggle:has-text("${label}")`;
const title = (id) => `.panel-title[data-panel-id="${id}"]`;
const num = (page, sel) => text(page, sel).then((t) => parseInt(t.replace(/\D/g, ''), 10));

export default async function tour(r, page, { url }) {
  // Waits for results of the simulator: never a fixed pause followed by one look.
  const regIs = (n, hex, what) => r.until(`document.getElementById('reg-val-${n}').textContent.trim().toUpperCase() === '${hex.toUpperCase()}'`, { max: 5, what: `R${n} = ${hex} ${what}` });
  const pcIs = (pc, what) => r.until(`document.getElementById('cnt-pc').textContent.trim() === '${pc}'`, { max: 5, what: `PC ${pc} ${what}` });
  const step = async (target) => {            // click Step (or click again where the pointer is) and wait for the instruction
    const before = await cycles(page);
    await r.click(target);
    await r.until(`parseInt(document.getElementById('cnt-cycles').textContent.replace(/\\D/g, ''), 10) > ${before}`, { max: 5, what: 'the step executed' });
  };
  const themeIs = (name) => r.until(`document.documentElement.dataset.theme === '${name}'`, { max: 3, what: `theme ${name}` });

  // ---- start ------------------------------------------------------------------
  const typeLine = async (i) => {
    await page.evaluate((n) => { window.typeLine(n); }, i);
    await r.until(`window.typed(${i})`, { max: 6, what: `terminal line ${i} typed` });
  };
  await r.cue('part-intro', async () => { await r.moveTo([1180, 620], 30); });
  await r.cue('install', async () => {
    await typeLine(0); await r.hold(0.4);
    await typeLine(1); await r.hold(0.3);
    await typeLine(2);
  });
  await r.cue('start-server', async () => { await typeLine(3); await r.hold(0.5); await page.evaluate(() => window.showLine(4)); });
  await r.cue('open-browser', async () => { await r.moveTo([1010, 558], 24); });

  // ---- the screen -------------------------------------------------------------
  await r.goto(url);
  await r.cue('dashboard', async () => { await r.moveTo([960, 420], 24); });
  await r.cue('views', async () => {
    await r.show('[data-workspace-view="simulator"]', 1.6);
    await r.click('#io-nav-button');
    await r.hold(2.2);
    await r.click('[data-workspace-view="simulator"]');
  });
  await r.cue('header-target', async () => {
    await r.show('#processor-select', 0.9);
    await r.show('#cores-toggle', 0.9);
    await r.show('#iep-clock-select', 0.9);
  });
  await r.cue('header-execute', async () => {
    await r.show('#btn-step', 0.35); await r.show('#btn-run', 0.35); await r.show('#btn-sim', 0.35);
    await r.show('#sim-interval', 0.6);
  });
  await r.cue('header-session', async () => {
    for (const id of ['#btn-reset', '#btn-hard-reset', '#btn-config', '#btn-reset-layout', '#btn-help']) await r.show(id, 0.3, 8);
  });
  await r.cue('header-status', async () => { await r.show('#status-badge', 1.4); await r.show('#ws-status', 1.2); });
  await r.cue('telemetry', async () => {
    await r.outline('#counters');
    for (const id of ['#cnt-cycles', '#cnt-stalls', '#cnt-instrs', '#cnt-ipc', '#cnt-pc']) await r.show(id, 0.55, 8);
    r.clearOutline();
  });
  await r.cue('panels', async () => {
    for (const label of ['Source / Disassembly', 'Registers', 'I/O Pins', 'Signal Graph', 'Memory Graph', 'Memory 1', 'Memory 2', 'Assembly Editor'])
      await r.show(toggle(label), 0.62, 8);
  });

  // ---- first program ------------------------------------------------------------
  await r.cue('editor', async () => { await r.outline('#editor-panel'); await r.moveTo('#asm-source', 22); });
  await r.cue('load-into', async () => { r.clearOutline(); await r.show('#assembly-core-target', 2.4); });
  await r.cue('open', async () => {
    await r.click('#btn-file');
    await r.hold(2.8);
    await r.show('#_src-menu div:text-is("Browse file system...")', 2.0);
  });
  await r.cue('open-file', async () => {
    const item = '#_src-menu div:text-is("running_led.asm")';
    await r.reveal(item, '#_src-menu');
    await r.click(item);
    await r.until(`document.querySelector('#asm-source').value.includes('lsl')`, { max: 5, what: 'running_led.asm in the editor' });
    await r.show('#editor-tab-bar .editor-tab.active', 1.0);
    await r.click('#asm-source');
  });
  await r.cue('tabs', async () => {
    const tab = (name) => `#editor-tab-bar .editor-tab:has-text("${name}")`;
    await r.click(tab('program.asm'));
    await r.until(`!document.querySelector('#asm-source').value.includes('lsl')`, { max: 3, what: 'program.asm shown' });
    await r.hold(0.5);
    await r.click(tab('running_led.asm'));
    await r.until(`document.querySelector('#asm-source').value.includes('lsl')`, { max: 3, what: 'running_led.asm shown again' });
    await r.click(tab('program.asm') + ' .tab-close');
    await r.until(`document.querySelectorAll('#editor-tab-bar .editor-tab').length === 1`, { max: 3, what: 'program.asm closed' });
  });
  await r.cue('project-save', async () => {
    await r.click('#btn-open-project');
    await r.hold(1.8);
    await page.keyboard.press('Escape');
    await r.click([300, 470]);                       // a click on empty space closes the list
    await r.show('#btn-save-asm', 1.2);
  });
  await r.cue('load-assemble', async () => {
    await r.click('#btn-load');
    await r.until(`!!document.getElementById('src-line-2')`, { max: 5, what: 'the listing in Source / Disassembly' });
    await r.expect('#source-panel', /ldi\s+r30, 1/);
    await r.outline('#source-panel');
    await r.moveTo('#src-line-0', 20);
  });
  await r.cue('reset-first', async () => { r.clearOutline(); await r.click('#btn-reset'); });
  await r.cue('step', async () => {
    await r.reveal('#reg-val-30', '#register-focus-pane .core-scroll');
    await step('#btn-step');
    await regIs(30, '0x00000001', 'after the first step');
    await r.show('#reg-val-30', 1.3);
    await r.show('#pin-gpo-pru0-0', 1.3);
  });
  await r.cue('step-loop', async () => {
    await step('#btn-step'); await r.hold(0.5);
    await step(); await r.hold(0.3);
    await regIs(30, '0x00000002', 'after three steps');
    await r.show('#pin-gpo-pru0-1', 1.0);
  });
  await r.cue('arrow-keys', async () => {
    await r.click('#src-line-2');          // shortcuts are ignored while a button or field has the focus
    await r.hold(0.6);
    await r.press('ArrowRight', 12);
    await regIs(30, '0x00000004', 'after Arrow Right');
    await r.hold(1.0);
    await r.press('ArrowLeft', 12);
    await regIs(30, '0x00000002', 'after Arrow Left');
  });
  await r.cue('run', async () => {
    await r.click('#btn-run');
    await r.until(`document.getElementById('status-badge').textContent.includes('RUNNING')`, { max: 5, what: 'status RUNNING' });
    r.live = true;
    await r.show('#status-badge', 1.5);
    await r.moveTo('#btn-run', 16);
  });
  await r.cue('stop', async () => {
    await r.click('#btn-run');
    await r.until(`document.getElementById('status-badge').textContent.includes('HALTED')`, { max: 5, what: 'status HALTED after Stop' });
    r.live = false;
    await r.show('#cnt-cycles', 0.6);
    const a = await cycles(page);
    await r.hold(0.8);
    if (await cycles(page) !== a) throw new Error('cycle counter still moving after Stop');
  });
  await r.cue('sim', async () => {
    await r.type('#sim-interval', '0.1', { clear: true });
    await r.click('#btn-sim');
    r.live = true;
    await r.moveTo('#pin-gpo-pru0-9', 30);
    await r.hold(4.5);
  });
  await r.cue('sim-stop', async () => {
    await r.expect('#btn-sim', /Stop SIM/i);
    await r.click('#btn-sim');
    r.live = false;
  });
  await r.cue('reset', async () => {
    await r.click('#btn-reset');
    await pcIs(0, 'after Reset');
    await regIs(30, '0x00000000', 'after Reset');
    await r.show('#cnt-pc', 1.2);
    await r.show('#src-line-0', 1.0);
  });
  await r.cue('hw-reset', async () => {
    await step('#btn-step');                          // something to reset
    await r.click('#btn-hard-reset');
    await pcIs(0, 'after HW Reset');
    await r.until(`document.getElementById('cnt-cycles').textContent.trim() === '0'`, { max: 5, what: 'cycle counter 0 after HW Reset' });
    await r.expect('#source-panel', /ldi\s+r30, 1/);    // the program stays loaded
  });

  // ---- breakpoints -----------------------------------------------------------------
  const chips = () => text(page, '#bp-chip-list');
  await r.cue('bp-dblclick', async () => {
    await r.dblclick('#src-line-3');
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 1`, { max: 3, what: 'one breakpoint listed' });
    await r.show('#bp-chip-list', 1.6);
  });
  await r.cue('bp-run', async () => {
    await r.click('#btn-run');
    await r.until(`document.getElementById('status-badge').textContent.includes('BREAK') && document.getElementById('cnt-pc').textContent.trim() === '3'`,
      { max: 6, what: 'status BREAK at PC 3' });
    await r.show('#status-badge', 1.2);
    await r.show('#src-line-3', 0.8);
  });
  await r.cue('bp-space', async () => {
    await r.click('#src-line-3');                // focus away from the Run button
    await r.hold(0.8);
    await r.press(' ', 20);
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 0`, { max: 3, what: 'breakpoint removed by Space' });
  });
  await r.cue('bp-address', async () => {
    await r.type('#bp-addr-input', '2');
    await r.click('#bp-add-btn');
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 1`, { max: 3, what: 'breakpoint at address 2' });
    await r.type('#bp-addr-input', '1', { clear: true });
    await r.click('#bp-add-btn');
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 2`, { max: 3, what: 'a second breakpoint at address 1' });
  });
  await r.cue('bp-remove', async () => {
    await r.click('#bp-chip-list .bp-chip button >> nth=0');
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 1`, { max: 3, what: 'one breakpoint removed by its cross' });
    await r.hold(0.8);
    await r.click('#bp-clear-btn');
    await r.until(`document.querySelectorAll('#bp-chip-list .bp-chip').length === 0`, { max: 3, what: 'Clear all removed the rest' });
  });

  // ---- registers -------------------------------------------------------------------
  const regScroll = '#register-focus-pane .core-scroll';
  await r.cue('reg-panel', async () => {
    await r.outline('#registers-panel');
    await r.scrollTo(regScroll, 0, 24);
    await r.show('#reg-val-0', 1.0);
    await r.show('#reg-carry', 0.7); await r.show('#mac-mode-label', 0.7); await r.show('#crc-value-label', 0.7);
    r.clearOutline();
  });
  await r.cue('reg-edit', async () => {
    await r.dblclick('#reg-val-1');
    await r.hold(0.4);
    await page.keyboard.press('Control+A');
    for (const ch of '12345678') { await page.keyboard.type(ch); r.force = 3; await r.frame(); await r.frame(); }
    await r.press('Enter', 12);
    await r.until(`document.getElementById('reg-val-1').textContent.trim().toLowerCase() === '0x12345678'`, { max: 3, what: 'R1 = 0x12345678' });
  });
  await r.cue('ctable', async () => {
    await r.click('#ctable-toggle');
    await r.until(`document.getElementById('ctable-tbody').children.length >= 32 && document.getElementById('ctable-section').getBoundingClientRect().height > 0`,
      { max: 3, what: 'constant table shown' });
    await r.show('#ctable-table', 2.2);
    await r.reveal('#ctable-tbody tr:nth-child(26) .ct-addr', '#ctable-section');
    const before = await page.locator('#mem-addr-input').inputValue();
    await r.click('#ctable-tbody tr:nth-child(26) .ct-addr');
    await r.until(`document.getElementById('mem-addr-input').value !== ${JSON.stringify(before)}`, { max: 3, what: 'Memory 1 jumped to the constant address' });
    await r.show('#mem-addr-input', 1.0);
  });
  await r.cue('xfr-load', async () => {
    await r.click('#ctable-toggle');                 // hide the constant table again
    await openFile(r, 'xfr_test.asm');
    await r.click('#btn-load');
    await r.until(`document.getElementById('source-panel').innerText.includes('xout')`, { max: 5, what: 'xfr_test listing' });
    await r.click('#btn-reset');
  });
  await r.cue('spad', async () => {
    for (const bank of ['bank0', 'bank1', 'bank2']) { await r.click(`.spad-btn[data-bank="${bank}"]`); await r.hold(0.5); }
    await r.hold(0.6);
    await r.click('.spad-btn[data-bank="ipc"]');
  });
  await r.cue('spad-step', async () => {
    await r.scrollTo(regScroll, 0, 10);
    await step('#btn-step'); await r.hold(0.6);                                           // fill
    await step(); await r.hold(0.6);                                                       // ldi
    await regIs(2, '0x0000CAFE', 'after ldi');
    await step(); await r.hold(0.4);                                                       // xout 10
    await r.until(`/0000CAFE.*0000CAFE/i.test(document.getElementById('reg-row-2').innerText.replace(/\\s+/g, ' '))`,
      { max: 5, what: 'R2 and its bank 0 column both 0x0000CAFE' });
    await r.show('#reg-row-2', 1.5);
  });
  await r.cue('spad-xin', async () => {
    await step('#btn-step');                                                               // zero
    await regIs(2, '0x00000000', 'after zero');
    await r.hold(0.9);
    await step();                                                                          // xin 10
    await regIs(2, '0x0000CAFE', 'after xin');
    await r.show('#reg-val-2', 1.2);
  });
  await r.cue('shf', async () => {
    await r.click('#btn-xfr-shift');
    await r.until(`document.getElementById('btn-xfr-shift').classList.contains('active')`, { max: 3, what: 'SHF shown as set' });
    await r.hold(1.4);
    await r.click();
    await r.until(`!document.getElementById('btn-xfr-shift').classList.contains('active')`, { max: 3, what: 'SHF shown as cleared' });
    for (const bank of ['bank0', 'bank1', 'bank2', 'ipc']) await page.locator(`.spad-btn[data-bank="${bank}"]`).click();   // columns off again
    r.force = 6;
  });

  // ---- layout, theme and help ------------------------------------------------------
  await r.cue('panel-toggles', async () => {
    await r.outline('#panel-visibility');
    await r.click(toggle('Memory Graph'));
    await r.until(`document.getElementById('mem-graph-panel').getBoundingClientRect().width === 0`, { max: 3, what: 'Memory Graph hidden' });
    r.clearOutline();
  });
  await r.cue('collapse', async () => {
    await r.click('#registers-panel .panel-collapse-btn');
    await r.until(`document.getElementById('registers-panel').classList.contains('panel-collapsed')`, { max: 3, what: 'Registers collapsed' });
    await r.hold(1.2);
  });
  await r.cue('drag-title', async () => {
    const from = await r.point(title('io'));
    const to = await page.locator('#source-panel').boundingBox();
    const before = await page.locator('#io-panel').boundingBox();
    await r.drag(title('io'), to.x + to.width * 0.5 - from[0], to.y + to.height * 0.8 - from[1], 40);
    await r.until(`Math.abs(document.getElementById('io-panel').getBoundingClientRect().x - ${before.x}) > 100`, { max: 3, what: 'I/O Pins moved to the left column' });
    await r.hold(0.8);
  });
  await r.cue('drag-divider', async () => {
    const width = (await page.locator('#source-panel').boundingBox()).width;
    await r.drag('.tile-divider:not(.tile-divider-v) >> nth=0', 110, 0, 30);
    await r.until(`Math.abs(document.getElementById('source-panel').getBoundingClientRect().width - ${width}) > 60`, { max: 3, what: 'the left column resized' });
    await r.hold(0.5);
  });
  await r.cue('reset-layout', async () => {
    await r.click('#btn-reset-layout');
    await r.until(`['source-panel','registers-panel','editor-panel','memory-panel','memory-panel-2','io-panel','signal-graph-panel','mem-graph-panel'].every((id) => document.getElementById(id).getBoundingClientRect().width > 0) && !document.querySelector('.panel-collapsed')`,
      { max: 4, what: 'all eight panels visible and expanded' });
  });
  await r.cue('theme', async () => {
    await themeIs('light');
    await r.click('#workspace-theme'); await themeIs('contrast'); await r.hold(1.4);
    await r.click(); await themeIs('dark'); await r.hold(1.4);
    await r.click(); await themeIs('light'); await r.hold(0.4);
  });
  await r.cue('help', async () => {
    await r.click('#btn-help');
    await r.until(`getComputedStyle(document.getElementById('help-modal')).display !== 'none'`, { max: 3, what: 'Help open' });
    await r.moveTo('#help-card', 20);
    await r.hold(2.2);
    await r.scrollTo('#help-card', 420, 40);
    await r.hold(2.0);
    await r.scrollTo('#help-card', 0, 20);          // the close button scrolls with the text
    await r.click('#btn-help-close');
    await r.until(`getComputedStyle(document.getElementById('help-modal')).display === 'none'`, { max: 3, what: 'Help closed' });
  });
  await r.cue('part-end', async () => { await r.moveTo([960, 560], 24); });
}
