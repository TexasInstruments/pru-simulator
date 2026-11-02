"use strict";

// General workspace controls share the existing simulator and device state.
(() => {
  const Chrome = globalThis.PruChrome;
  const buttons = Array.from(document.querySelectorAll('[data-workspace-view]'));
  const subtabs = Array.from(document.querySelectorAll('[data-io-tab]'));
  const views = { simulator: document.getElementById('main'),
    io: document.getElementById('io-view') };
  const panes = { devices: document.getElementById('devices-view'),
    peripherals: document.getElementById('peripherals-view'),
    events: document.getElementById('events-view') };
  const panelBar = document.getElementById('panel-visibility');
  const panelButtons = document.getElementById('panel-visibility-buttons');
  const viewSwitch = document.querySelector('.view-switch');
  const subtabStrip = document.getElementById('io-subtabs');
  const faultBanner = document.getElementById('core-fault-banner');
  const faultBadge = document.getElementById('io-fault-badge');
  // Views saved before the I/O & Devices tab existed.
  const LEGACY_VIEWS = { devices: ['io', 'devices'], events: ['io', 'events'] };
  let currentView = 'simulator';
  let currentTab = 'devices';
  let faultTotal = 0;
  let faultsSeen = 0;
  let bannerText = '';

  function placeGliders() {
    const on = (list, attr) => list.find(item => item.getAttribute(attr) === 'true');
    if (viewSwitch) Chrome.placeGlider(viewSwitch, on(buttons, 'aria-pressed'));
    if (subtabStrip) Chrome.placeGlider(subtabStrip, on(subtabs, 'aria-selected'));
  }

  let shownBadge = -1;
  // A view or tab change always re-places the gliders; a state message only
  // does when the badge itself changed (measuring layout every message is waste).
  function renderFaultBadge(force) {
    const count = currentView === 'io' && currentTab === 'events' ? 0 : Chrome.newFaultCount(faultTotal, faultsSeen);
    if (!force && count === shownBadge) return;
    shownBadge = count;
    faultBadge.hidden = count === 0;
    faultBadge.children[0].textContent = String(count);
    faultBadge.children[1].textContent = ' ' + Chrome.badgeLabel(count);
    placeGliders();   // the badge changes the width of its tab
  }

  function renderFaultBanner() {
    // role="alert": rewrite only on change, or every state update re-announces it.
    if (faultBanner.textContent !== bannerText) faultBanner.textContent = bannerText;
    faultBanner.hidden = !bannerText || currentView !== 'simulator';
  }

  function showIoTab(name, focus) {
    if (!panes[name]) name = 'devices';
    currentTab = name;
    for (const [key, pane] of Object.entries(panes)) pane.hidden = key !== name;
    subtabs.forEach(tab => {
      const active = tab.dataset.ioTab === name;
      tab.setAttribute('aria-selected', String(active));
      tab.setAttribute('tabindex', active ? '0' : '-1');
      if (active && focus) tab.focus?.();
    });
    if (name === 'events' && currentView === 'io') faultsSeen = faultTotal;
    try { localStorage.setItem('pru-io-tab', name); } catch (_) {}
    renderFaultBadge(true);
    document.dispatchEvent(new CustomEvent('pru-workspace-view-changed',
      { detail: { view: currentView, tab: currentTab } }));
  }

  function showView(name, tab) {
    if (LEGACY_VIEWS[name]) [name, tab] = LEGACY_VIEWS[name];
    if (!views[name]) name = 'simulator';
    currentView = name;
    for (const [key, view] of Object.entries(views)) view.hidden = key !== name;
    for (const button of buttons) button.setAttribute('aria-pressed', String(button.dataset.workspaceView === name));
    panelBar.hidden = name !== 'simulator';
    try { localStorage.setItem('pru-workspace-view', name); } catch (_) {}
    renderFaultBanner();
    showIoTab(tab || currentTab);
  }
  for (const button of buttons) button.addEventListener('click', () => showView(button.dataset.workspaceView));
  for (const tab of subtabs) {
    tab.addEventListener('click', () => showIoTab(tab.dataset.ioTab));
    tab.addEventListener('keydown', event => {
      const next = Chrome.nextTabIndex(event.key, subtabs.indexOf(tab), subtabs.length);
      if (next < 0) return;
      event.preventDefault?.();
      showIoTab(subtabs[next].dataset.ioTab, true);
    });
  }
  window.addEventListener('resize', placeGliders);
  let savedView = 'simulator';
  let savedTab = 'devices';
  try {
    savedView = localStorage.getItem('pru-workspace-view') || savedView;
    savedTab = localStorage.getItem('pru-io-tab') || savedTab;
  } catch (_) {}
  currentTab = savedTab;
  showView(savedView);

  // The theme button cycles Dark -> Light -> High contrast. Without a saved
  // choice the theme follows the OS light/dark preference.
  const THEMES = ['dark', 'light', 'contrast'];
  const THEME_NAMES = { dark: 'dark', light: 'light', contrast: 'high contrast' };
  const theme = document.getElementById('workspace-theme');
  let savedTheme = null;
  try { savedTheme = localStorage.getItem('pru-workspace-theme'); } catch (_) {}
  const prefersLight = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-color-scheme: light)').matches;
  theme.value = THEMES.includes(savedTheme) ? savedTheme : (prefersLight ? 'light' : 'dark');
  function applyTheme(save) {
    document.documentElement.dataset.theme = theme.value;
    const next = THEMES[(THEMES.indexOf(theme.value) + 1) % THEMES.length];
    const action = `Theme: ${THEME_NAMES[theme.value]}. Switch to ${THEME_NAMES[next]}`;
    theme.setAttribute('aria-label', action);
    theme.title = action;
    if (save) {
      try { localStorage.setItem('pru-workspace-theme', theme.value); } catch (_) {}
      savedTheme = theme.value;
      if (mq) mq.removeEventListener('change', onOsThemeChange);
    }
    // Canvases read their colours from the theme tokens, so they redraw on a change.
    document.dispatchEvent(new CustomEvent('pru-theme-changed', { detail: { theme: theme.value } }));
  }
  theme.addEventListener('click', () => {
    theme.value = THEMES[(THEMES.indexOf(theme.value) + 1) % THEMES.length];
    applyTheme(true);
  });
  const mq = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: light)') : null;
  function onOsThemeChange(e) {
    if (savedTheme) return;
    theme.value = e.matches ? 'light' : 'dark';
    applyTheme(false);
  }
  if (mq) mq.addEventListener('change', onOsThemeChange);
  applyTheme(false);

  // Icon and short label for each shared panel toggle.
  const PANEL_TOGGLES = {
    source: ['\u25a4', 'Source / Disassembly'], registers: ['\u25a6', 'Registers'],
    io: ['\u2194', 'I/O Pins'], 'signal-graph': ['\u223f', 'Signal Graph'],
    'mem-graph': ['\u2336', 'Memory Graph'], memory1: ['M1', 'Memory 1'],
    memory2: ['M2', 'Memory 2'], editor: ['\u270e', 'Assembly Editor'],
  };
  function panelToggleInfo(id) {
    return PANEL_TOGGLES[id];
  }

  function updatePanels() {
    panelButtons.replaceChildren();
    const order = id => { const at = Object.keys(PANEL_TOGGLES).indexOf(id); return at >= 0 ? at : 99; };
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
    const coreFaults = Chrome.coreFaultLines(state, visibleCores);
    lines.push(...coreFaults);
    document.getElementById('device-event-log').textContent = lines.join('\n') ||
      'No device events. Attach a model and execute firmware to inspect its activity.';

    // Core memory faults also show in the Simulator view; the badge counts
    // every fault the user has not yet looked at in Events.
    bannerText = coreFaults.length ? `Core fault \u00b7 ${coreFaults.join('  |  ')}` : '';
    renderFaultBanner();
    faultTotal = Chrome.faultTotal(state);
    faultsSeen = Math.min(faultsSeen, faultTotal);
    if (currentView === 'io' && currentTab === 'events') faultsSeen = faultTotal;
    renderFaultBadge();
  };
})();
