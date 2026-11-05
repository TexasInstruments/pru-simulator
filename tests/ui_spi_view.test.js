import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

class Element {
  constructor(id) { this.id = id; this.listeners = {}; this.dataset = {}; this.value = ''; this.hidden = false;
    this.innerHTML = ''; this.textContent = ''; this.disabled = false; this.className = ''; this.title = ''; this.checked = false; }
  addEventListener(type, handler) { this.listeners[type] = handler; }
  click() { this.listeners.click?.({ currentTarget: this, target: this }); }
  fire(type) { this.listeners[type]?.({ currentTarget: this, target: this }); }
  setAttribute(name, value) { this[name] = value; }
  classList = { toggle() {}, add() {}, remove() {} };
}
function load() {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  const sent = [];
  const window = {};
  const context = vm.createContext({ document: { getElementById: get }, window, console });
  vm.runInContext(readFileSync(new URL('../ui/static/spi.js', import.meta.url), 'utf8'), context);
  return { SpiView: window.SpiView, get, sent, nodes };
}
const fx = JSON.parse(readFileSync(new URL('./fixtures/spi_view_fixture.json', import.meta.url), 'utf8'));
const plain = value => JSON.parse(JSON.stringify(value));
const SPI = 'SPI multi-core (controller + peripheral)';

test('rateOptions disables unsupported and 50 MHz@200', () => {
  const { SpiView } = load();
  const supported = { 1: [
    { spi_mhz: 10, core_mhz: 200, pass: true },
    { spi_mhz: 25, core_mhz: 200, pass: true },
    { spi_mhz: 40, core_mhz: 250, pass: false },
    { spi_mhz: 50, core_mhz: 200, pass: true },
    { spi_mhz: 50, core_mhz: 300, pass: true },
  ] };
  const options = plain(SpiView.rateOptions(supported, 1));
  assert.equal(options.length, 5);
  assert.deepEqual(options.map(o => o.disabled), [false, false, true, true, false]);
  assert.match(options[1].label, /25 MHz/);
  assert.match(options[1].label, /200 MHz/);
  assert.match(options[3].title, /50 MHz.*200/);
  assert.deepEqual(plain(SpiView.rateOptions(supported, 0)), [], 'no rows for mode 0');
  assert.equal(SpiView.rateOptions({ '1': [{ spi_mhz: 10, core_mhz: 200 }] }, 1).length, 1, 'string mode keys (JSON)');
});

test('decodeBoxes labels CMD ADR IDLE D0..D15 with hex', () => {
  const { SpiView } = load();
  for (const key of ['mode1', 'mode0']) {
    const f = fx[key];
    const boxes = plain(SpiView.decodeBoxes(f.edges, f.config.mode, { miso_delay: f.config.miso_delay, hop_delay: f.hop }));
    const mosi = boxes.filter(b => b.lane === 'mosi' && !b.partial);
    assert.deepEqual(mosi.map(b => b.label), ['CMD 03', 'ADR 02', 'IDLE 00', 'D0 00'], key);
    assert.ok(mosi.every(b => b.endCycle > b.startCycle));
    assert.ok(mosi[1].startCycle >= mosi[0].endCycle - 1);
    const miso = boxes.filter(b => b.lane === 'miso' && !b.partial);
    assert.deepEqual(miso.map(b => b.label), ['CMD 00', 'ADR 00', 'IDLE 00', 'D0 F0'], key + ' miso at the controller sample point');
  }
  // D0..D15 names beyond the fixture
  assert.deepEqual(plain(load().SpiView.fieldName(18)), 'D15');
  assert.equal(load().SpiView.fieldName(3), 'D0');
});

test('compare badge shows mismatch count', () => {
  const { SpiView } = load();
  const state = plain(fx.state);
  let badge = SpiView.compareBadge(state);
  assert.equal(badge.ok, true);
  assert.match(badge.text, /16\/16/);
  state.match = { mosi: 14, miso: 16 };
  state.memory.p_rx[3] = 'EE'; state.memory.p_rx[9] = 'EE';
  badge = SpiView.compareBadge(state);
  assert.equal(badge.ok, false);
  assert.equal(badge.mismatches, 2);
  assert.match(badge.text, /2 mismatch/);
  assert.deepEqual(plain(SpiView.mismatchIndexes(state.memory.c_tx, state.memory.p_rx)), [3, 9]);
  // WRITE frame: MISO is not expected to carry data, so it never counts
  const w = plain(fx.state);
  w.expect = { mosi: true, miso: false }; w.match = { mosi: 16, miso: 0 };
  assert.equal(SpiView.compareBadge(w).ok, true);
});

test('timingRows flags exactly one worst row and keeps derived rows unflagged', () => {
  const { SpiView } = load();
  const rows = plain(fx.state.timing);
  rows.forEach(r => { r.worst = false; });
  const out = plain(SpiView.timingRows(rows));
  assert.equal(out.filter(r => r.worst).length, 1);
  assert.ok(out.find(r => r.worst).derived !== true);
  const already = plain(SpiView.timingRows(plain(fx.state.timing)));
  assert.equal(already.filter(r => r.worst).length, 1);
  assert.equal(already.find(r => r.worst).symbol, 'tSU,MISO');
});

test('spi view rehydrates from spi_state on connect', () => {
  const { SpiView, get } = load();
  const sent = [];
  SpiView.init({ send: action => sent.push(action) });
  SpiView.render(plain(fx.state));
  const svg = get('spi-scope').innerHTML;
  assert.match(svg, /<svg/);
  assert.match(svg, /CMD 03/);
  assert.match(svg, /D0 F0/);
  assert.match(get('spi-mem-c-tx').innerHTML, />0F</);
  assert.match(get('spi-mem-c-rx').innerHTML, />FF</);
  assert.match(get('spi-frame-strip').innerHTML, /ADR/);
  assert.match(get('spi-compare').textContent, /16\/16/);
  assert.match(get('spi-timing').innerHTML, /SU,MISO/);
  assert.match(get('spi-gpi-delay').textContent, /^GPI delay 5 cycles \(calibrated 18 ns @ 333 MHz\)/);
  assert.match(get('spi-core-note').textContent, /200 MHz.*session only/);
  assert.equal(get('spi-result').hidden, false);
  assert.deepEqual(plain(sent), [{ action: 'spi_supported' }], 'asks once for the rate table it has not cached');
  SpiView.render(plain(fx.state));
  assert.equal(sent.length, 1);
  SpiView.render(null);
  assert.equal(get('spi-result').hidden, true);
});

test('setup, send, sequence and rate controls send the backend actions', () => {
  const { SpiView, get } = load();
  const sent = [];
  SpiView.init({ send: action => sent.push(plain(action)), stop() {} });
  SpiView.setSupported({ 0: [{ spi_mhz: 25, core_mhz: 200, pass: true }],
    1: [{ spi_mhz: 10, core_mhz: 200, pass: true }, { spi_mhz: 25, core_mhz: 200, pass: true }] });
  get('spi-setup-button').click();
  assert.deepEqual(sent.at(-1), { action: 'scenario_load', name: SPI });
  SpiView.render(plain(fx.state));
  get('spi-cmd').value = '2'; get('spi-addr').value = '2'; get('spi-pattern').value = 'walking1';
  get('spi-send').click();
  assert.deepEqual(sent.at(-1), { action: 'spi_send', cmd: 2, addr: 2, pattern: 'walking1' });
  assert.equal(get('spi-send').disabled, true, 'busy until the state returns');
  SpiView.render(plain(fx.state));
  assert.equal(get('spi-send').disabled, false);
  get('spi-sequence').click();
  assert.deepEqual(sent.at(-1), { action: 'spi_sequence' });
  SpiView.render(plain(fx.state));
  get('spi-rate').value = '10';
  get('spi-rate').fire('change');
  assert.deepEqual(sent.at(-1), { action: 'spi_setup', mode: 1, spi_mhz: 10 });
  SpiView.render(plain(fx.state));
  get('spi-mode').value = '0';
  get('spi-mode').fire('change');
  assert.deepEqual(sent.at(-1), { action: 'spi_setup', mode: 0, spi_mhz: 25 });
  get('spi-addr').value = '5';
  get('spi-addr').fire('input');
  assert.match(get('spi-addr-byte').textContent, /= byte 80/);
  SpiView.showError(['bad rate']);
  assert.equal(get('spi-error').hidden, false);
  assert.equal(get('spi-send').disabled, false);
});

test('index.html wires the SPI subtab, pane, assets and app dispatch', () => {
  const html = readFileSync(new URL('../ui/static/index.html', import.meta.url), 'utf8');
  assert.match(html, /id="io-subtab-spi" data-io-tab="spi"/);
  assert.match(html, /id="spi-view"/);
  assert.match(html, /\/static\/spi\.js/);
  assert.match(html, /\/static\/spi\.css/);
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));
  const js = readFileSync(new URL('../ui/static/spi.js', import.meta.url), 'utf8');
  for (const m of js.matchAll(/(?:byId|setHtml|setText)\('([^']+)'/g)) assert.ok(ids.has(m[1]), `index.html has ${m[1]}`);
  const app = readFileSync(new URL('../ui/static/app.js', import.meta.url), 'utf8');
  assert.match(app, /msg\.io\?\.spi_demo/);
  assert.match(app, /msg\.tag === "spi"/);
  assert.match(app, /spi_supported/);
});
