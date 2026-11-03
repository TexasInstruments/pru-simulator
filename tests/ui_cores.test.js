import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const context = vm.createContext({});
const source = new URL('../ui/static/cores.js', import.meta.url);
if (existsSync(source)) vm.runInContext(readFileSync(source, 'utf8'), context);
const available = ['pru0', 'pru1', 'rtu0', 'rtu1'];
const initial = { used: ['pru0'], shown: ['pru0'], focus: 'pru0', pinned: false };
function normalize(saved, cores = available) {
  assert.ok(context.PruCores, 'PruCores global API exists');
  return JSON.parse(JSON.stringify(context.PruCores.normalize(saved, cores)));
}
function transition(state, action, cores = available) {
  assert.ok(context.PruCores, 'PruCores global API exists');
  return JSON.parse(JSON.stringify(context.PruCores.transition(state, action, cores)));
}

test('fresh sessions use and show PRU0, or the first available core', () => {
  assert.deepEqual(normalize(), initial);
  assert.deepEqual(normalize(null, ['rtu1', 'pru1']), {
    used: ['pru1'], shown: ['pru1'], focus: 'pru1', pinned: false,
  });
});

test('normalization orders, deduplicates, and prunes target-unavailable cores', () => {
  assert.deepEqual(normalize({
    used: ['rtu1', 'rtu0', 'pru1', 'pru0', 'pru1', 'unknown'],
    shown: ['rtu1', 'rtu0', 'pru0', 'pru0'], focus: 'rtu1', pinned: true,
  }, ['rtu0', 'pru0', 'pru1']), {
    used: ['pru0', 'pru1', 'rtu0'], shown: ['pru0', 'rtu0'], focus: 'pru0', pinned: true,
  });
});

test('corrupt saved selections recover nonempty defaults without enabling extra cores', () => {
  for (const saved of [false, 'bad', [], {}, { used: [], shown: [] },
    { used: 'pru1', shown: ['rtu0'] }, { used: ['unknown'], shown: ['rtu0'] }]) {
    assert.deepEqual(normalize(saved), initial);
  }
  assert.deepEqual(normalize({ used: ['rtu0'], shown: 'rtu0', focus: 'pru0', pinned: 'yes' }), {
    used: ['rtu0'], shown: ['rtu0'], focus: 'rtu0', pinned: false,
  });
});

test('normalization enforces shown subset and focus while preserving valid focus', () => {
  assert.deepEqual(normalize({ used: ['pru1', 'rtu0'], shown: ['pru0'], focus: 'pru0' }), {
    used: ['pru1', 'rtu0'], shown: ['pru1'], focus: 'pru1', pinned: false,
  });
  assert.deepEqual(normalize({ used: ['pru0', 'rtu0'], shown: ['rtu0', 'pru0'], focus: 'rtu0' }), {
    used: ['pru0', 'rtu0'], shown: ['pru0', 'rtu0'], focus: 'rtu0', pinned: false,
  });
});

test('explicit legacy multicore selections migrate used and shown together', () => {
  assert.deepEqual(normalize({ multiCoreMode: true, mcPartner: 'rtu0',
    mcExtras: ['rtu1', 'pru1', 'rtu0'], currentCore: 'pru0' }), {
    used: ['pru0', 'pru1', 'rtu0', 'rtu1'], shown: ['pru0', 'pru1', 'rtu0', 'rtu1'],
    focus: 'pru0', pinned: false,
  });
  assert.deepEqual(normalize({ multiCoreMode: true, mcPartner: 'rtu1',
    mcExtras: ['pru1'], currentCore: 'pru0' }, ['pru0', 'pru1', 'rtu0']), {
    used: ['pru0', 'pru1'], shown: ['pru0', 'pru1'], focus: 'pru0', pinned: false,
  });
});

test('explicit legacy single core migrates only a valid current core', () => {
  assert.deepEqual(normalize({ multiCoreMode: false, currentCore: 'rtu0',
    mcPartner: 'pru1', mcExtras: ['pru1'] }), {
    used: ['rtu0'], shown: ['rtu0'], focus: 'rtu0', pinned: false,
  });
  assert.deepEqual(normalize({ multiCoreMode: false, currentCore: 'rtu1' }, ['pru0', 'rtu0']), initial);
});

test('abandoned legacy layout trees do not enable multicore', () => {
  assert.deepEqual(normalize({ mcTree: { core: 'rtu0' }, mcPartner: 'rtu0',
    mcExtras: ['pru1'], currentCore: 'pru1' }), initial);
});

test('modern selections take precedence over legacy fields', () => {
  assert.deepEqual(normalize({ used: ['pru1'], shown: ['pru1'], focus: 'pru1',
    multiCoreMode: true, mcPartner: 'rtu0' }), {
    used: ['pru1'], shown: ['pru1'], focus: 'pru1', pinned: false,
  });
});

test('use enables a core without showing it or moving the current focus', () => {
  assert.deepEqual(transition(initial, { type: 'use', core: 'rtu0' }), {
    used: ['pru0', 'rtu0'], shown: ['pru0'], focus: 'pru0', pinned: false,
  });
});

test('unusing a core hides it and repairs focus to the first shown core', () => {
  assert.deepEqual(transition({ used: ['pru0', 'pru1', 'rtu0'],
    shown: ['pru0', 'rtu0'], focus: 'pru0', pinned: true }, { type: 'use', core: 'pru0' }), {
    used: ['pru1', 'rtu0'], shown: ['rtu0'], focus: 'rtu0', pinned: true,
  });
});

test('unusing the only shown core shows the first remaining used core', () => {
  assert.deepEqual(transition({ used: ['pru0', 'pru1', 'rtu0'],
    shown: ['rtu0'], focus: 'rtu0', pinned: false }, { type: 'use', core: 'rtu0' }), {
    used: ['pru0', 'pru1'], shown: ['pru0'], focus: 'pru0', pinned: false,
  });
});

test('the final used core and the final shown core cannot be removed', () => {
  assert.deepEqual(transition(initial, { type: 'use', core: 'pru0' }), initial);
  assert.deepEqual(transition(initial, { type: 'show', core: 'pru0' }), initial);
  const state = { used: ['pru0', 'pru1'], shown: ['pru1'], focus: 'pru1', pinned: false };
  assert.deepEqual(transition(state, { type: 'show', core: 'pru1' }), state);
});

test('show toggles only visibility and repairs hidden focus', () => {
  const state = { used: ['pru0', 'pru1'], shown: ['pru0'], focus: 'pru0', pinned: false };
  const both = transition(state, { type: 'show', core: 'pru1' });
  assert.deepEqual(both, { used: ['pru0', 'pru1'], shown: ['pru0', 'pru1'], focus: 'pru0', pinned: false });
  assert.deepEqual(transition(both, { type: 'show', core: 'pru0' }), {
    used: ['pru0', 'pru1'], shown: ['pru1'], focus: 'pru1', pinned: false,
  });
});

test('focus reveals a hidden used core and solo preserves execution membership', () => {
  const state = { used: ['pru0', 'pru1', 'rtu0'], shown: ['pru0'], focus: 'pru0', pinned: false };
  assert.deepEqual(transition(state, { type: 'focus', core: 'rtu0' }), {
    used: ['pru0', 'pru1', 'rtu0'], shown: ['pru0', 'rtu0'], focus: 'rtu0', pinned: false,
  });
  assert.deepEqual(transition(state, { type: 'solo', core: 'pru1' }), {
    used: ['pru0', 'pru1', 'rtu0'], shown: ['pru1'], focus: 'pru1', pinned: false,
  });
});

test('unused or target-unavailable cores cannot be shown, focused, or soloed', () => {
  for (const type of ['show', 'focus', 'solo']) {
    assert.deepEqual(transition(initial, { type, core: 'rtu0' }), initial);
  }
  for (const type of ['use', 'show', 'focus', 'solo']) {
    assert.deepEqual(transition(initial, { type, core: 'rtu1' }, ['pru0', 'pru1', 'rtu0']), initial);
    assert.deepEqual(transition(initial, { type, core: 'unknown' }), initial);
  }
});

test('use-all enables cores without adding panes and show-all explicitly reveals them', () => {
  assert.deepEqual(transition(initial, { type: 'use-all' }, ['rtu0', 'pru1', 'pru0']), {
    used: ['pru0', 'pru1', 'rtu0'], shown: ['pru0'], focus: 'pru0', pinned: false,
  });
  assert.deepEqual(transition({ used: ['pru1', 'rtu0'], shown: ['rtu0'], focus: 'rtu0', pinned: true },
    { type: 'show-all' }), {
    used: ['pru1', 'rtu0'], shown: ['pru1', 'rtu0'], focus: 'rtu0', pinned: true,
  });
});

test('pin toggles and reset restores target defaults', () => {
  const pinned = transition(initial, { type: 'pin' });
  assert.deepEqual(pinned, { ...initial, pinned: true });
  assert.deepEqual(transition(pinned, { type: 'pin' }), initial);
  assert.deepEqual(transition({ used: available, shown: ['rtu0'], focus: 'rtu0', pinned: true },
    { type: 'reset' }), initial);
});

test('normalization and every action leave frozen inputs unchanged', () => {
  const state = Object.freeze({ used: Object.freeze(['pru1', 'pru0']),
    shown: Object.freeze(['pru0']), focus: 'pru0', pinned: false });
  const cores = Object.freeze(['rtu0', 'pru1', 'pru0']);
  normalize(state, cores);
  for (const type of ['use', 'show', 'solo', 'focus', 'use-all', 'show-all', 'pin', 'reset', 'unknown']) {
    transition(state, Object.freeze({ type, core: 'pru1' }), cores);
  }
  assert.deepEqual(state, { used: ['pru1', 'pru0'], shown: ['pru0'], focus: 'pru0', pinned: false });
  assert.deepEqual(cores, ['rtu0', 'pru1', 'pru0']);
});
