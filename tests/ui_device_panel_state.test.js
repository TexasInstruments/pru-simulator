import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const appPath = fileURLToPath(new URL('../ui/static/app.js', import.meta.url));
const app = readFileSync(appPath, 'utf8');
const start = app.indexOf('function updateDevicePanel(');
const end = app.indexOf('// ---- Signal graph', start);
assert.ok(start >= 0 && end > start, 'updateDevicePanel should be present in app.js');

const fields = [
  ['foc-alpha-q15', 'alpha_q15'],
  ['foc-beta-q15', 'beta_q15'],
  ['foc-modulation-q15', 'modulation_q15'],
  ['foc-phase-step', 'phase_increment_q32'],
  ['foc-initial-phase', 'initial_phase_q32'],
];
const routeIds = ['foc-sd-route-0', 'foc-sd-route-1'];
const initialConfig = {
  alpha_q15: 1,
  beta_q15: 2,
  modulation_q15: 3,
  phase_increment_q32: 4,
  initial_phase_q32: 5,
};
const draftConfig = { ...initialConfig, alpha_q15: 10 };
const nodes = new Map();
for (const [id] of fields) nodes.set(id, { value: 'draft' });
for (const id of routeIds) nodes.set(id, { options: [{}], value: 'draft' });
for (const id of [
  'device-runtime-panel', 'foc-device-status', 'foc-attach-button', 'foc-controls',
  'foc-apply-button', 'foc-detach-button', 'foc-live-stats',
]) nodes.set(id, {});

const document = {
  activeElement: null,
  getElementById: id => nodes.get(id) || null,
  createElement: () => ({ dataset: {}, options: [], appendChild() {}, addEventListener() {} }),
};
const harness = new Function('document', `
  let currentCore = 'pru0';
  let devicePanelCore = 'pru0';
  let focMotorName = null;
  let multiCoreMode = false;
  const focConfigFields = ${JSON.stringify(fields)};
  const focRouteIds = ${JSON.stringify(routeIds)};
  const focDirtyFields = new Set(${JSON.stringify(fields.map(([id]) => id).concat(routeIds))});
  let focApplyDraft = { config: ${JSON.stringify(draftConfig)}, routes: [3, 4] };
  ${app.slice(start, end)}
  return { updateDevicePanel, focDirtyFields, hasPendingApply: () => focApplyDraft !== null };
`)(document);

const motor = { model: 'three_phase_rl', name: 'foc_motor', phase_currents_a: [] };
const state = config => ({
  device_bus: { devices: [motor] },
  foc_config: config,
  sd: { input_routes: [3, 4, null] },
});

harness.updateDevicePanel(state(draftConfig), 'pru0');
assert.equal(harness.hasPendingApply(), false, 'matching backend state acknowledges Apply');
assert.equal(harness.focDirtyFields.size, 0, 'acknowledged fields become eligible for backend updates');

const resetConfig = { ...initialConfig, alpha_q15: 999 };
harness.updateDevicePanel(state(resetConfig), 'pru0');
assert.equal(nodes.get('foc-alpha-q15').value, '999', 'later backend reset state replaces the applied value');

console.log('FOC device panel acknowledges Apply and follows later backend state');
