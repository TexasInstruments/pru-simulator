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
  }
  theme.addEventListener('change', applyTheme);
  applyTheme();

  function updatePanels() {
    panelButtons.replaceChildren();
    for (const id of modePanelIds[currentMode]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'panel-toggle';
      const title = PANEL_REGISTRY[id]?.querySelector('[data-panel-id]');
      button.textContent = title?.textContent.replace(/[−+]$/, '').trim() || id;
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
