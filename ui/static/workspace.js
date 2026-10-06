"use strict";

// General workspace controls share the existing simulator and device state.
(() => {
  const buttons = Array.from(document.querySelectorAll('[data-workspace-view]'));
  const views = { simulator: document.getElementById('main'),
    devices: document.getElementById('devices-view'),
    motor: document.getElementById('motor-view'), events: document.getElementById('events-view') };
  const panelBar = document.getElementById('panel-visibility');
  const panelButtons = document.getElementById('panel-visibility-buttons');
  views.devices.appendChild(document.getElementById('device-runtime-panel'));

  function showView(name) {
    if (!views[name]) name = 'simulator';
    for (const [key, view] of Object.entries(views)) view.hidden = key !== name;
    for (const button of buttons) button.setAttribute('aria-pressed', String(button.dataset.workspaceView === name));
    panelBar.hidden = name !== 'simulator';
    try { localStorage.setItem('pru-workspace-view', name); } catch (_) {}
    document.dispatchEvent(new CustomEvent('pru-workspace-view-changed', { detail: { view: name } }));
  }
  for (const button of buttons) button.addEventListener('click', () => showView(button.dataset.workspaceView));
  let savedView = 'simulator';
  try { savedView = localStorage.getItem('pru-workspace-view') || savedView; } catch (_) {}
  showView(savedView);

  const theme = document.getElementById('workspace-theme');
  try { theme.value = localStorage.getItem('pru-workspace-theme') === 'contrast' ? 'contrast' : 'dark'; } catch (_) {}
  function applyTheme() {
    document.documentElement.dataset.theme = theme.value;
    try { localStorage.setItem('pru-workspace-theme', theme.value); } catch (_) {}
    // Canvases read their colours from the theme tokens, so they redraw on a change.
    document.dispatchEvent(new CustomEvent('pru-theme-changed', { detail: { theme: theme.value } }));
  }
  theme.addEventListener('change', applyTheme);
  applyTheme();

  // Icon and short label for each panel toggle; the multi-core panels derive
  // theirs from the partner core name shown in the panel title.
  const PANEL_TOGGLES = {
    source: ['\u25a4', 'Source / Disassembly'], registers: ['\u25a6', 'Registers'],
    io: ['\u2194', 'I/O Pins'], 'signal-graph': ['\u223f', 'Signal Graph'],
    'mem-graph': ['\u2336', 'Memory Graph'], memory1: ['M1', 'Memory 1'],
    memory2: ['M2', 'Memory 2'], editor: ['\u270e', 'Assembly Editor'],
  };
  function panelToggleInfo(id) {
    if (PANEL_TOGGLES[id]) return PANEL_TOGGLES[id];
    const title = PANEL_REGISTRY[id]?.querySelector('[data-panel-id] span');
    return [id.endsWith('registers') ? '\u25a6' : '\u25a4', title?.textContent.trim() || id];
  }

  function updatePanels() {
    panelButtons.replaceChildren();
    const order = id => { const at = Object.keys(PANEL_TOGGLES).indexOf(id); return at >= 0 ? at : (id.startsWith('mc-') ? -1 : 99); };
    for (const id of [...modePanelIds[currentMode]].sort((a, b) => order(a) - order(b))) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'panel-toggle';
      const [glyph, name] = panelToggleInfo(id);
      const icon = document.createElement('span');
      icon.className = 'panel-toggle-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = glyph;
      const label = document.createElement('span');
      label.className = 'panel-toggle-label';
      label.textContent = name;
      button.appendChild(icon);
      button.appendChild(label);
      button.setAttribute('aria-pressed', String(getPanelVisibility(id)));
      button.addEventListener('click', () => setPanelsVisibility([id], !getPanelVisibility(id)));
      panelButtons.appendChild(button);
    }
  }
  document.addEventListener('pru-layout-changed', updatePanels);
  updatePanels();

  window.updateWorkspaceEvents = (state, visibleCores = [state.core]) => {
    const bus = state.io?.device_bus || {};
    const owners = new Map((bus.devices || []).map(device => [device.name, device.core]));
    const lines = (bus.events || []).slice(-500).map(event =>
      `${owners.get(event.device) || 'device'} · cycle ${event.cycle} · ${event.device} · ${event.kind}: ` +
      (event.message || JSON.stringify(event)));
    lines.push(...(bus.faults || []).slice(-100).map(fault => `FAULT · ${fault}`));
    const faults = state.core_faults || (state.fault ? { [state.core]: state.fault } : {});
    for (const core of visibleCores) {
      const fault = faults[core];
      if (fault) lines.push(`${core} · ${fault.opcode} at ${fault.address}: ${fault.error}`);
    }
    document.getElementById('device-event-log').textContent = lines.join('\n') ||
      'No device events. Attach a model and execute firmware to inspect its activity.';
  };
})();
