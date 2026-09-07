/**
 * PRU Simulator Dashboard — WebSocket client
 */

"use strict";

// ---- State ----------------------------------------------------------------
let ws = null;
let prevRegisters = new Array(32).fill("0x00000000");
let currentCore = "pru0";
let running = false;
let runInterval = null;
let runRequestInFlight = false;
let nextRunRequestId = 1;
const pendingRunRequestIds = new Set();
const abandonedRunRequestIds = new Set();
let activeToolbarRequestId = null;
let activeSimRequestId = null;
let genericSsiLoaded = false;
let genericSsiRunInFlight = false;
let genericSsiRequestId = null;
const SSI_RUNTIME_READ_THROTTLE_MS = 250;
let ssiRuntimeReadTimer = null;
let ssiRuntimeReadQueued = false;
let ssiRuntimeLastReadAt = 0;
let simRunning = false;
let simTimer = null;
let _flashTimer = null;
let clientBreakpoints = new Set();
let _lastSourceBreakpointKey = null;
let _currentSourceLine = null;
let _sourceInstructions = [];
let _sourceLabels = {};
let _renderedSourceInstructions = null;
let _renderedSourceLabels = null;
let panelVisibilityButtons = null;
let protocolPanelButtons = null;
let protocolPanelVisibleKeys = new Set(["i2c"]);
let focLatestState = null;
let focSamples = [];
let focDialAnimationFrame = null;

// ---- Multi-core state ------------------------------------------------------
let multiCoreMode = false;
let mcPartner = "rtu0";            // second core shown in multi-core view
let mcPrevRegs = {
  pru0: new Array(32).fill("0x00000000"),
  rtu0: new Array(32).fill("0x00000000"),
  pru1: new Array(32).fill("0x00000000"),
};
let mcLastSourceBreakpointKey = { pru0: null, rtu0: null, pru1: null };
let mcCurrentSourceLine = { pru0: null, rtu0: null, pru1: null };
let mcSourceInstructions = { pru0: [], rtu0: [], pru1: [] };
let mcSourceLabels = { pru0: {}, rtu0: {}, pru1: {} };
let mcRenderedSourceInstructions = { pru0: null, rtu0: null, pru1: null };
let mcRenderedSourceLabels = { pru0: null, rtu0: null, pru1: null };
let mcBreakpoints   = { pru0: new Set(), rtu0: new Set(), pru1: new Set() };
let mcHaltedState   = { pru0: false, rtu0: false, pru1: false };
let mcBreakState    = { pru0: false, rtu0: false, pru1: false };
let mcSpadVisible   = new Set();   // SPAD banks visible in PRU0 MC reg panel
let mcPrevSpad      = {};          // key -> Array for change detection

// ---- Editor tab state ------------------------------------------------------
let tabs = [];              // [{path: string, content: string, dirty: boolean}]
let activeTab = -1;         // index into tabs[], -1 = no open tabs
let activeProjectManifest = null;  // {project: string, files: string[]} or null

// ---- Signal graph state ----------------------------------------------------

const GRAPH_GPO_COLORS = [
  '#4ec9b0','#9cdcfe','#ce9178','#d7ba7d','#b5cea8',
  '#73c991','#50b8a8','#4ec994','#9dcfe0','#7ecfc4',
  '#4db8a8','#66c19c','#5cc9bc','#8ed4b8','#6bc4a0',
  '#45b89c','#5dc9a0','#7ecfb0','#4cc4a4','#65b8a0',
];
const GRAPH_GPI_COLORS = [
  '#569cd6','#c586c0','#9b9bff','#7b99ee','#82aaff',
  '#89b8e0','#7fa8d0','#a080c0','#8090d0','#6880c8',
  '#7090d8','#6878c8','#7888d8','#8898e8','#7898d0',
  '#5878c8','#6888d8','#6898d8','#6888c0','#5868b8',
];
const GRAPH_MEM_COLORS = [
  '#c586c0','#dcdcaa','#4fc1ff','#f44747',
  '#d7ba7d','#b5cea8','#ce9178','#f48771',
];
const GRAPH_PERIF_COLORS = ['#ffb74d', '#ff8a65', '#ffd54f'];
// Muted same-hue variants: each perif clock lane pairs with its data lane.
const GRAPH_PERIF_CLK_COLORS = ['#c9924a', '#c97a5c', '#c9a84a'];
// Darkest of the three: out_en frames the data/clock pair it belongs to.
const GRAPH_PERIF_OE_COLORS = ['#8f6a36', '#8f5844', '#8f7736'];

const GRAPH_HEIGHT_STEPS = [80, 140, 200, 280, 400, 560, 720, 960, 1200];

const signalGraph = {
  recording: false,
  windowSize: 1024,
  buf: [],        // circular buffer array, length === windowSize
  head: 0,        // next write index
  fill: 0,        // number of valid samples (0..windowSize)
  memChannels: [], // [{addr, length, color, label}], up to 8
  heightIdx: 1,   // index into GRAPH_HEIGHT_STEPS (default 140px)
  memHeightIdx: 1, // separate height index for memory graph canvas
  view: null,      // null = full range; { minStep, maxStep } = zoomed
  _dragStart: null, // { clientX, fracX, view } for pan tracking
};
// Paired multicore captures arrive as one message per core. Hold the two
// batches briefly so they can be merged by run step before entering the one
// circular graph buffer; otherwise the second batch evicts the first core.
const pendingGraphCaptures = new Map();

// ---- DOM references -------------------------------------------------------
const coreSelect    = document.getElementById("core-select");
const btnStep       = document.getElementById("btn-step");
const btnRun        = document.getElementById("btn-run");
const btnReset      = document.getElementById("btn-reset");
const btnHardReset  = document.getElementById("btn-hard-reset");
const btnLoad       = document.getElementById("btn-load");
const asmSource     = document.getElementById("asm-source");
const sourceList    = document.getElementById("source-list");
const regTbody      = document.getElementById("reg-tbody");
const regCarry      = document.getElementById("reg-carry");
const gpoGrid       = document.getElementById("gpo-grid");
const gpiGrid       = document.getElementById("gpi-grid");
const statusBadge   = document.getElementById("status-badge");
const wsStatus      = document.getElementById("ws-status");
const cntCycles     = document.getElementById("cnt-cycles");
const cntStalls     = document.getElementById("cnt-stalls");
const cntInstrs     = document.getElementById("cnt-instrs");
const cntIpc        = document.getElementById("cnt-ipc");
const cntPc         = document.getElementById("cnt-pc");

const btnSim          = document.getElementById("btn-sim");
const simIntervalInput = document.getElementById("sim-interval");
const btnSaveAsm      = document.getElementById("btn-save-asm");

const btnMulticore    = document.getElementById("btn-multicore");
const editorPanel     = document.getElementById("editor-panel");
const mcLoadCore      = document.getElementById("mc-load-core");

const btnOpenProject  = document.getElementById("btn-open-project");

const btnConfig       = document.getElementById("btn-config");
const configModal     = document.getElementById("config-modal");
const configTextarea  = document.getElementById("config-textarea");
const pruSpeedSelect  = document.getElementById("pru-speed-select");
const configError     = document.getElementById("config-error");
const btnConfigSave   = document.getElementById("btn-config-save");
const btnConfigCancel = document.getElementById("btn-config-cancel");

function setButtonLabel(button, label) {
  const text = button && button.querySelector(".text");
  if (text) {
    text.textContent = label;
  } else if (button) {
    button.textContent = label;
  }
}

// ---- Editor dirty tracking -----------------------------------------------
asmSource.addEventListener('input', () => {
  if (activeTab >= 0 && tabs[activeTab] && !tabs[activeTab].dirty) {
    tabs[activeTab].dirty = true;
    renderTabs();
  }
});

// ---- SPAD display --------------------------------------------------------

// Banks visible in register table columns (ordered for display)
// regStart: first register index that has SPAD data
// count:    number of words in this bank
const SPAD_BANKS = [
  { key: "bank0", label: "BANK0",  regStart: 0, count: 30 },
  { key: "bank1", label: "BANK1",  regStart: 0, count: 30 },
  { key: "bank2", label: "BANK2",  regStart: 0, count: 30 },
  { key: "ipc",   label: "IPC_SP", regStart: 2, count: 8  },
];
let spadVisible = new Set();   // which bank keys are currently shown
let prevSpad = {};             // key -> Array(count) of hex strings for change detection

function initSpadState() {
  for (const b of SPAD_BANKS) {
    prevSpad[b.key] = new Array(b.count).fill("0x00000000");
  }
}

// ---- Tab management --------------------------------------------------------

function renderTabs() {
  const bar = document.getElementById('editor-tab-bar');
  if (!bar) return;
  bar.innerHTML = '';
  tabs.forEach((tab, i) => {
    const name = tab.path.split('/').pop();
    const el = document.createElement('div');
    el.className = 'editor-tab' + (i === activeTab ? ' active' : '');

    const label = document.createElement('span');
    label.className = 'tab-label';
    label.textContent = (i === activeTab ? '▶ ' : '') + name + (tab.dirty ? '*' : '');

    const close = document.createElement('span');
    close.className = 'tab-close';
    close.textContent = '×';
    close.addEventListener('click', e => { e.stopPropagation(); closeTab(i); });

    el.appendChild(label);
    el.appendChild(close);
    el.addEventListener('click', () => switchTab(i));
    bar.appendChild(el);
  });
}

function switchTab(i) {
  if (i === activeTab) return;
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }
  activeTab = i;
  asmSource.value = tabs[i] ? tabs[i].content : '';
  renderTabs();
}

function closeTab(i) {
  if (tabs[i] && tabs[i].dirty) {
    if (!confirm(`Close ${tabs[i].path.split('/').pop()} without saving?`)) return;
  }
  // If this was the only tab in a project, clear manifest
  if (activeProjectManifest) {
    const proj = activeProjectManifest.project;
    const remaining = tabs.filter((t, idx) => idx !== i && t.path.startsWith(proj + '/'));
    if (remaining.length === 0) activeProjectManifest = null;
  }
  tabs.splice(i, 1);
  if (i < activeTab) activeTab--;
  if (activeTab >= tabs.length) activeTab = tabs.length - 1;
  asmSource.value = activeTab >= 0 && tabs[activeTab] ? tabs[activeTab].content : '';
  renderTabs();
}

function openFileAsTab(path, content) {
  const existing = tabs.findIndex(t => t.path === path);
  if (existing >= 0) {
    switchTab(existing);
    return;
  }
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }
  tabs.push({ path, content, dirty: false });
  activeTab = tabs.length - 1;
  asmSource.value = content;
  renderTabs();
}

async function initEditorTabs() {
  try {
    const res = await fetch('/source/program.asm');
    const text = res.ok ? await res.text() : '';
    openFileAsTab('program.asm', text);
  } catch (_) {
    tabs.push({ path: 'program.asm', content: '', dirty: false });
    activeTab = 0;
    renderTabs();
  }
}

// ---- Build static UI elements --------------------------------------------

function buildRegTable() {
  regTbody.innerHTML = "";
  // Header row for SPAD columns (if any visible)
  const visibleBanks = SPAD_BANKS.filter(b => spadVisible.has(b.key));
  if (visibleBanks.length > 0) {
    const hdrRow = document.createElement("tr");
    hdrRow.id = "spad-hdr-row";
    hdrRow.innerHTML = `<td></td><td></td>`;
    for (const bank of visibleBanks) {
      hdrRow.innerHTML += `<td class="spad-hdr">${bank.label}</td>`;
    }
    regTbody.appendChild(hdrRow);
  }

  for (let i = 0; i < 32; i++) {
    const tr = document.createElement("tr");
    tr.id = `reg-row-${i}`;
    let html = `<td class="reg-name">R${i}</td><td class="reg-val" id="reg-val-${i}">0x00000000</td>`;
    for (const bank of visibleBanks) {
      const wordIdx = i - bank.regStart;
      if (wordIdx >= 0 && wordIdx < bank.count) {
        html += `<td class="spad-val" id="spad-${bank.key}-${i}">0x00000000</td>`;
      } else {
        html += `<td class="spad-val empty">--</td>`;
      }
    }
    tr.innerHTML = html;
    const valCell = tr.querySelector(".reg-val");
    const regIndex = i;
    valCell.addEventListener("dblclick", () => editRegister(valCell, regIndex));
    regTbody.appendChild(tr);
  }
}

// XFR shift enable toggle
const btnXfrShift = document.getElementById("btn-xfr-shift");
btnXfrShift.addEventListener("click", () => {
  const nowEnabled = !btnXfrShift.classList.contains("active");
  sendAction({ action: "set_xfr_shift", core: currentCore, enabled: nowEnabled });
});

// Toggle a SPAD bank column on/off (single-core panel)
document.querySelectorAll(".spad-btn:not(.mc-spad-btn)").forEach(btn => {
  btn.addEventListener("click", () => {
    const key = btn.dataset.bank;
    if (spadVisible.has(key)) {
      spadVisible.delete(key);
      btn.classList.remove("active");
    } else {
      spadVisible.add(key);
      btn.classList.add("active");
    }
    buildRegTable();
    // Re-apply last known values so the rebuilt table isn't all zeros
    updateRegisters(prevRegisters, null);
  });
});

// Toggle a SPAD bank column in the PRU0 MC register panel
document.querySelectorAll(".mc-spad-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    const key = btn.dataset.bank;
    if (mcSpadVisible.has(key)) {
      mcSpadVisible.delete(key);
      btn.classList.remove("active");
    } else {
      mcSpadVisible.add(key);
      btn.classList.add("active");
    }
    buildMCRegTable("pru0");
    updateMCRegisters("pru0", mcPrevRegs.pru0);
  });
});

function buildPinGrid(container, count, cssClass, clickHandler) {
  container.innerHTML = "";
  for (let i = 0; i < count; i++) {
    const pin = document.createElement("div");
    pin.className = `pin ${cssClass}`;
    pin.id = `pin-${cssClass}-${i}`;
    pin.title = `${cssClass.toUpperCase()} ${i}`;
    pin.textContent = i;
    if (clickHandler) {
      pin.addEventListener("click", () => clickHandler(i, pin));
    }
    container.appendChild(pin);
  }
}

function initTabList(tabListId, initialTabId, onActivate = null) {
  const tabList = document.getElementById(tabListId);
  if (!tabList) return null;

  const tabs = Array.from(tabList.querySelectorAll('[role="tab"]'));

  function activateTab(tab, focus = false) {
    if (!tab) return;
    tabs.forEach(candidate => {
      const selected = candidate === tab;
      candidate.setAttribute("aria-selected", selected ? "true" : "false");
      candidate.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(candidate.getAttribute("aria-controls"));
      if (panel) {
        panel.hidden = !selected;
        panel.setAttribute("aria-hidden", selected ? "false" : "true");
      }
    });
    if (onActivate) onActivate(tab);
    if (focus) tab.focus();
  }

  tabList.addEventListener("click", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (tab && tabs.includes(tab)) activateTab(tab);
  });

  tabList.addEventListener("keydown", (event) => {
    const tab = event.target.closest('[role="tab"]');
    if (!tab || !tabs.includes(tab)) return;

    // Keep the simulator's document-level Space/arrow shortcuts from seeing
    // keyboard interaction that belongs to an application tab.
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      event.stopPropagation();
      activateTab(tab);
      return;
    }

    let nextIndex = -1;
    const currentIndex = tabs.indexOf(tab);
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % tabs.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = tabs.length - 1;
    }

    if (nextIndex >= 0) {
      event.preventDefault();
      event.stopPropagation();
      activateTab(tabs[nextIndex], true);
    }
  });

  activateTab(document.getElementById(initialTabId) || tabs[0]);
  return activateTab;
}

function syncWorkspaceSubnav(tab) {
  const isSimulator = tab?.getAttribute("aria-controls") === "simulator-view";
  const isProtocolTools = tab?.getAttribute("aria-controls") === "protocol-tools-view";
  const isFoc = tab?.getAttribute("aria-controls") === "foc-view";
  const panelVisibility = document.getElementById("panel-visibility");
  const protocolToolTabs = document.getElementById("protocol-tool-tabs");

  if (panelVisibility) {
    panelVisibility.hidden = !isSimulator;
    panelVisibility.setAttribute("aria-hidden", isSimulator ? "false" : "true");
  }
  if (protocolToolTabs) {
    protocolToolTabs.hidden = !isProtocolTools;
    protocolToolTabs.setAttribute("aria-hidden", isProtocolTools ? "false" : "true");
  }
  if (isFoc) requestGraphDraw();
}

const PROTOCOL_PANEL_VISIBILITY_KEY = "pru-protocol-panel-visibility";
const PROTOCOL_PANEL_DEFS = [
  { key: "i2c", label: "I2C", panelId: "protocol-i2c-panel", buttonId: "protocol-tab-i2c" },
  { key: "uart", label: "UART", panelId: "protocol-uart-panel", buttonId: "protocol-tab-uart" },
  { key: "ssi", label: "SSI", panelId: "protocol-ssi-panel", buttonId: "protocol-tab-ssi" },
];

function loadProtocolPanelVisibility() {
  try {
    const raw = localStorage.getItem(PROTOCOL_PANEL_VISIBILITY_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    const allowed = new Set(PROTOCOL_PANEL_DEFS.map(definition => definition.key));
    const visible = Array.isArray(saved)
      ? saved.filter(key => allowed.has(key))
      : [];
    return new Set(visible.length ? visible : ["i2c"]);
  } catch (e) {
    return new Set(["i2c"]);
  }
}

function saveProtocolPanelVisibility() {
  try {
    localStorage.setItem(
      PROTOCOL_PANEL_VISIBILITY_KEY,
      JSON.stringify(Array.from(protocolPanelVisibleKeys)),
    );
  } catch (e) { /* quota exceeded — ignore */ }
}

function applyProtocolPanelVisibility() {
  for (const definition of PROTOCOL_PANEL_DEFS) {
    const visible = protocolPanelVisibleKeys.has(definition.key);
    const panel = document.getElementById(definition.panelId);
    const button = document.getElementById(definition.buttonId);
    if (panel) {
      panel.hidden = !visible;
      panel.setAttribute("aria-hidden", visible ? "false" : "true");
    }
    if (button) {
      button.setAttribute("aria-pressed", visible ? "true" : "false");
      button.title = visible
        ? `Hide ${definition.label} panel`
        : `Show ${definition.label} panel`;
    }
  }
}

function initProtocolPanelControls() {
  protocolPanelButtons = document.getElementById("protocol-tool-tabs");
  if (!protocolPanelButtons) return;

  protocolPanelVisibleKeys = loadProtocolPanelVisibility();
  protocolPanelButtons.querySelectorAll("[data-protocol-panel]").forEach(button => {
    button.addEventListener("click", () => {
      const key = button.dataset.protocolPanel;
      if (!PROTOCOL_PANEL_DEFS.some(definition => definition.key === key)) return;

      if (protocolPanelVisibleKeys.has(key)) {
        if (protocolPanelVisibleKeys.size === 1) {
          flashStatus("Keep one protocol panel visible", "halted");
          return;
        }
        protocolPanelVisibleKeys.delete(key);
      } else {
        protocolPanelVisibleKeys.add(key);
      }

      saveProtocolPanelVisibility();
      applyProtocolPanelVisibility();
    });
  });

  applyProtocolPanelVisibility();
}

function initProtocolTools() {
  initTabList("view-tabs", "tab-simulator", syncWorkspaceSubnav);

  const protocolSlots = {
    i2c: ["i2c-attach-strip", "i2c-interface"],
    uart: ["uart-decoder", "uart-rx-inject"],
    ssi: ["ssi-inject", "ssi-runtime"],
  };

  for (const [slotName, elementIds] of Object.entries(protocolSlots)) {
    const slot = document.querySelector(`[data-protocol-slot="${slotName}"]`);
    if (!slot) continue;
    for (const elementId of elementIds) {
      const element = document.getElementById(elementId);
      if (element && !slot.contains(element)) slot.appendChild(element);
    }
  }

  // The protocol sections are now owned by Protocol Tools. Remove only the
  // separators left behind in the simulator's I/O observation panel.
  const ioBody = document.querySelector("#io-panel .panel-body");
  ioBody?.querySelectorAll(".uart-divider").forEach(separator => separator.remove());

  initProtocolPanelControls();
}

const PANEL_TOGGLE_DEFS = [
  {
    key: "source",
    label: "Source / Disassembly",
    icon: "\u25a4",
    ids: () => multiCoreMode ? ["mc-pru0-source", "mc-rtu0-source"] : ["source"],
  },
  {
    key: "registers",
    label: "Registers",
    icon: "\u25a6",
    ids: () => multiCoreMode ? ["mc-pru0-registers", "mc-rtu0-registers"] : ["registers"],
  },
  { key: "io", label: "I/O Pins", icon: "\u2194", ids: () => ["io"] },
  { key: "signal-graph", label: "Signal Graph", icon: "\u223f", ids: () => ["signal-graph"] },
  { key: "mem-graph", label: "Memory Graph", icon: "\u2336", ids: () => ["mem-graph"] },
  { key: "memory1", label: "Memory 1", icon: "M1", ids: () => ["memory1"] },
  { key: "memory2", label: "Memory 2", icon: "M2", ids: () => ["memory2"] },
  { key: "editor", label: "Assembly Editor", icon: "\u270e", ids: () => ["editor"] },
];

function panelToggleState(panelIds) {
  const visibleCount = panelIds.filter(panelId => getPanelVisibility(panelId)).length;
  if (visibleCount === 0) return "hidden";
  if (visibleCount === panelIds.length) return "visible";
  return "partial";
}

function updatePanelVisibilityButtons() {
  if (!panelVisibilityButtons) return;

  panelVisibilityButtons.querySelectorAll("[data-panel-toggle]").forEach(button => {
    const definition = PANEL_TOGGLE_DEFS.find(
      candidate => candidate.key === button.dataset.panelToggle,
    );
    if (!definition) return;
    const state = panelToggleState(definition.ids());
    const selected = state !== "hidden";
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.title = selected ? `Hide ${definition.label} panel` : `Show ${definition.label} panel`;
    if (state === "partial") button.dataset.partial = "true";
    else delete button.dataset.partial;
  });
}

function initPanelVisibilityControls() {
  panelVisibilityButtons = document.getElementById("panel-visibility-buttons");
  if (!panelVisibilityButtons) return;

  for (const definition of PANEL_TOGGLE_DEFS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "panel-toggle";
    button.dataset.panelToggle = definition.key;
    button.setAttribute("aria-pressed", "true");

    const icon = document.createElement("span");
    icon.className = "panel-toggle-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = definition.icon;

    const label = document.createElement("span");
    label.className = "panel-toggle-label";
    label.textContent = definition.label;

    button.append(icon, label);
    button.addEventListener("click", () => {
      const panelIds = definition.ids();
      const show = panelToggleState(panelIds) === "hidden";
      const changed = setPanelsVisibility(panelIds, show);
      if (!changed && !show) flashStatus("Keep one panel visible", "halted");
      updatePanelVisibilityButtons();
    });
    panelVisibilityButtons.appendChild(button);
  }

  document.addEventListener("pru-layout-changed", updatePanelVisibilityButtons);
  updatePanelVisibilityButtons();
}

function initUI() {
  initSpadState();
  buildRegTable();
  buildPinGrid(gpoGrid, 20, "gpo", null);
  buildPinGrid(gpiGrid, 20, "gpi", handleGpiClick);
  initProtocolTools();
  initFocControls();
  initPanelVisibilityControls();
  initLayout('sc');
  updatePanelVisibilityButtons();
  initEditorTabs();
}


// ---- WebSocket setup ------------------------------------------------------

function connect() {
  const url = `ws://${location.host}/ws`;
  ws = new WebSocket(url);

  ws.onopen = () => {
    wsStatus.textContent = "Connected";
    wsStatus.className = "connected";
    if (multiCoreMode) {
      sendAction({ action: "get_state", core: "pru0" });
      sendAction({ action: "get_state", core: mcPartner });
    } else {
      sendAction({ action: "get_state", core: currentCore });
    }
    sendAction({ action: "foc_state" });
    refreshMemory();
    refreshMemory2();
    loadRegions();
    sendAction({ action: "get_wires" });
  };

  ws.onclose = () => {
    wsStatus.textContent = "Disconnected";
    wsStatus.className = "error";
    cancelAllRunRequests();
    pendingGraphCaptures.clear();
    memReadInFlight = false;
    memReadInFlight2 = false;
    memReadPending = false;
    memReadPending2 = false;
    cancelQueuedSsiRuntimeRead();
    stopRun();
    // Attempt reconnect after 2 s
    setTimeout(connect, 2000);
  };

  ws.onerror = () => {
    wsStatus.textContent = "Error";
    wsStatus.className = "error";
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "state") {
        if (msg.wires !== undefined) renderWires(msg.wires);
        if (multiCoreMode) {
          updateMCUI(msg);
        } else if (!msg.core || msg.core === currentCore) {
          updateUI(msg);
        }
      } else if (msg.type === "capture") {
        graphHandleCapture(msg);
        requestGraphDraw();
      } else if (msg.type === "memory") {
        if (msg.tag === "mem2") renderMemory2(msg);
        else if (msg.tag && msg.tag.startsWith("graph-")) { graphHandleMemory(msg); requestGraphDraw(); }
        else renderMemory(msg);
      } else if (msg.type === "run_done") {
        const requestId = String(msg.request_id ?? "");
        const currentRequest = completeRunRequest(requestId);
        if (!currentRequest) return;
        if (requestId === genericSsiRequestId) {
          genericSsiRunInFlight = false;
          genericSsiRequestId = null;
          // A toolbar Run/SIM request can complete every few milliseconds.
          // Do not put a full SSI status/render pass behind every one.  A
          // standalone SSI run still gets an immediate final read.
          queueSsiRuntimeRead(!(running || simRunning));
        }
      } else if (msg.type === "uart_inject_ok") {
        const st = document.getElementById("uart-inj-status");
        if (st) {
          st.textContent = "\u2713 Armed: " + msg.payload_len + " bytes \u00D7 " +
            msg.frames + " frame" + (msg.frames > 1 ? "s" : "") +
            " \u00B7 trigger cycle " + msg.trigger_cycle + " \u00B7 run to receive";
          st.style.color = "#6a9955";
          st.style.display = "";
        }
      } else if (msg.type === "ssi_inject_ok") {
        const st = document.getElementById("ssi-inj-status");
        if (st) {
          st.textContent = "\u2713 Armed: position " + msg.value_hex +
            " (" + msg.bits + "-bit) \u00B7 CLK=GPO" + msg.clk_pin +
            " DATA=GPI" + msg.data_pin + " \u00B7 run to capture";
          st.style.color = "#6a9955";
          st.style.display = "";
        }
      } else if (msg.type === "ssi_runtime_state") {
        renderSsiRuntimeState(msg);
      } else if (msg.type === "ssi_runtime_error") {
        const st = document.getElementById("ssi-runtime-status");
        if (st) {
          st.textContent = "✗ " + msg.error;
          st.style.color = "#f38ba8";
          st.style.display = "";
        }
      } else if (msg.type === "foc_state") {
        renderFocState(msg);
        requestGraphDraw();
      } else if (msg.type === "foc_error") {
        const status = document.getElementById("foc-runtime-status");
        const inline = document.getElementById("foc-control-status");
        if (status) status.textContent = "FOC error · " + msg.error;
        if (inline) inline.textContent = msg.error;
      } else if (msg.type === "perif_ok") {
        const st = document.getElementById("perif-lb-status");
        if (st) {
          st.textContent = "✓ Loopback ch" + msg.channel + " " +
            (msg.enabled ? "enabled" : "disabled");
          st.style.display = "";
        }
      } else if (msg.type === "wires") {
        renderWires(msg.wires);
      } else if (msg.type === "error") {
        if (msg.tag && msg.tag.startsWith("graph-")) graphMarkChannelError(msg.tag);
        else {
          if (msg.tag === "mem1") finishMemoryRequest(1);
          if (msg.tag === "mem2") finishMemoryRequest(2);
          if (msg.request_id !== undefined) {
            const requestId = String(msg.request_id);
            const currentRequest = completeRunRequest(requestId);
            if (currentRequest && requestId === genericSsiRequestId) {
              genericSsiRunInFlight = false;
              genericSsiRequestId = null;
            }
          }
          if (msg.code === "multicore_sync") {
            stopRun();
            graphSetRecording(false);
          }
          showErrors(msg.errors);
        }
      }
    } catch (e) {
      console.error("Failed to parse message", e);
    }
  };
}

function sendAction(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
    return true;
  }
  return false;
}

function queueSsiRuntimeRead(force = false) {
  ssiRuntimeReadQueued = true;
  if (force && ssiRuntimeReadTimer !== null) {
    clearTimeout(ssiRuntimeReadTimer);
    ssiRuntimeReadTimer = null;
  }
  if (ssiRuntimeReadTimer !== null) return;

  const elapsed = Date.now() - ssiRuntimeLastReadAt;
  const delay = force
    ? 0
    : Math.max(0, SSI_RUNTIME_READ_THROTTLE_MS - elapsed);
  ssiRuntimeReadTimer = setTimeout(() => {
    ssiRuntimeReadTimer = null;
    if (!ssiRuntimeReadQueued) return;
    ssiRuntimeReadQueued = false;
    if (sendAction({ action: "ssi_runtime_read" })) {
      ssiRuntimeLastReadAt = Date.now();
    }
  }, delay);
}

function cancelQueuedSsiRuntimeRead() {
  if (ssiRuntimeReadTimer !== null) {
    clearTimeout(ssiRuntimeReadTimer);
    ssiRuntimeReadTimer = null;
  }
  ssiRuntimeReadQueued = false;
}

function trackRunRequest(requestId, owner = null) {
  if (requestId === undefined || requestId === null) return;
  const key = String(requestId);
  pendingRunRequestIds.add(key);
  if (owner === "toolbar") activeToolbarRequestId = key;
  if (owner === "sim") activeSimRequestId = key;
  runRequestInFlight = true;
}

function completeRunRequest(requestId) {
  const key = String(requestId ?? "");
  const abandoned = abandonedRunRequestIds.delete(key);
  pendingRunRequestIds.delete(key);
  if (activeToolbarRequestId === key) activeToolbarRequestId = null;
  if (activeSimRequestId === key) activeSimRequestId = null;
  runRequestInFlight = pendingRunRequestIds.size > 0;
  return !abandoned;
}

function abandonRunRequest(requestId) {
  if (requestId === undefined || requestId === null) return;
  const key = String(requestId);
  if (pendingRunRequestIds.delete(key)) abandonedRunRequestIds.add(key);
  if (activeToolbarRequestId === key) activeToolbarRequestId = null;
  if (activeSimRequestId === key) activeSimRequestId = null;
  runRequestInFlight = pendingRunRequestIds.size > 0;
}

function cancelAllRunRequests() {
  for (const requestId of pendingRunRequestIds) abandonedRunRequestIds.add(requestId);
  pendingRunRequestIds.clear();
  activeToolbarRequestId = null;
  activeSimRequestId = null;
  genericSsiRequestId = null;
  genericSsiRunInFlight = false;
  runRequestInFlight = false;
}

function canStartRunRequest(inFlight) {
  return !inFlight;
}

// ---- UI update ------------------------------------------------------------

function updateUI(state) {
  // Counters / header
  cntCycles.textContent   = state.cycles;
  cntStalls.textContent   = state.stall_cycles;
  cntInstrs.textContent   = state.instruction_count;
  cntIpc.textContent      = state.ipc.toFixed(3);
  cntPc.textContent       = state.pc;

  // Sync breakpoints from server
  if (state.breakpoints) clientBreakpoints = new Set(state.breakpoints);

  // Status badge
  if (state.halted) {
    statusBadge.textContent = "HALTED";
    statusBadge.className   = "halted";
    stopRun(); stopSim();
  } else if (state.at_breakpoint) {
    statusBadge.textContent = "BREAK";
    statusBadge.className   = "halted";
    stopRun(); stopSim();
  } else {
    statusBadge.textContent = "RUNNING";
    statusBadge.className   = "";
  }

  // XFR shift button state
  if (state.xfr_shift_en !== undefined) {
    btnXfrShift.classList.toggle("active", state.xfr_shift_en);
  }

  // MAC mode indicator
  if (state.mac) {
    document.getElementById("mac-mode-label").textContent = state.mac.mode ? "ACC" : "MPY";
    const carryEl = document.getElementById("mac-carry-label");
    carryEl.textContent = state.mac.acc_carry ? " CARRY" : "";
  }

  // Registers + SPAD
  updateRegisters(state.registers, state.carry);
  updateSpad(state.spad);

  // Source text is included only when it changes; retain it for lightweight
  // state packets that only carry the live CPU values.
  if (Array.isArray(state.instructions)) {
    _sourceInstructions = state.instructions;
    _sourceLabels = state.labels || {};
  }
  updateSource(_sourceInstructions, state.pc, _sourceLabels);

  // IO pins
  updatePins(state.io);
  updateSDPanel(state.io);
  updatePerifPanel(state.io);
  updateI2CPanel(state.io);

  // Signal graph sample
  graphSample(state);
  requestGraphDraw();

  memAutoOnStateChange();
}

function updateRegisters(regs, carry) {
  for (let i = 0; i < 32; i++) {
    const valEl = document.getElementById(`reg-val-${i}`);
    const rowEl = document.getElementById(`reg-row-${i}`);
    if (!valEl) continue;

    if (valEl.querySelector("input")) continue;

    const newVal = regs[i];
    valEl.textContent = newVal;

    if (newVal !== prevRegisters[i]) {
      rowEl.classList.add("changed");
    } else {
      rowEl.classList.remove("changed");
    }
  }
  prevRegisters = [...regs];
  if (carry !== null && carry !== undefined) {
    regCarry.textContent = carry ? "1" : "0";
  }
}

function updateSpad(spad) {
  if (!spad) return;
  for (const bank of SPAD_BANKS) {
    if (!spadVisible.has(bank.key)) continue;
    const words = spad[bank.key];
    if (!words) continue;
    for (let j = 0; j < bank.count; j++) {
      const regIdx = j + bank.regStart;
      const cell = document.getElementById(`spad-${bank.key}-${regIdx}`);
      if (!cell) continue;
      const newVal = words[j];
      cell.textContent = newVal;
      cell.classList.toggle("changed", newVal !== prevSpad[bank.key][j]);
    }
    prevSpad[bank.key] = [...words];
  }
}

// ---- Breakpoints bar -------------------------------------------------------
// Renders the chip list for a core's breakpoint set, independent of whether
// any of those addresses currently have a rendered source line (e.g. after a
// reload changed the instruction set) so a "stuck" breakpoint always has a
// visible way to remove it.
function renderBpBar(prefix, core, breakpoints) {
  const listEl = document.getElementById(`${prefix}bp-chip-list`);
  if (!listEl) return;
  const addrs = [...breakpoints].sort((a, b) => a - b);
  const key = addrs.join(",");
  if (listEl.dataset?.bpKey === key) return;
  if (listEl.dataset) listEl.dataset.bpKey = key;
  listEl.innerHTML = "";
  if (addrs.length === 0) {
    const empty = document.createElement("span");
    empty.className = "bp-empty";
    empty.textContent = "none";
    listEl.appendChild(empty);
    return;
  }
  addrs.forEach(addr => {
    const chip = document.createElement("span");
    chip.className = "bp-chip";
    const label = document.createElement("span");
    label.textContent = addr;
    const btn = document.createElement("button");
    btn.textContent = "×";
    btn.title = `Clear breakpoint at ${addr}`;
    btn.addEventListener("click", () => sendAction({ action: "toggle_breakpoint", core, addr }));
    chip.appendChild(label);
    chip.appendChild(btn);
    listEl.appendChild(chip);
  });
}

function _parseBpAddr(text) {
  const v = text.trim();
  if (!v) return NaN;
  return v.toLowerCase().startsWith("0x") ? parseInt(v, 16) : parseInt(v, 10);
}

function wireBpBar(prefix, coreOf) {
  const input   = document.getElementById(`${prefix}bp-addr-input`);
  const addBtn  = document.getElementById(`${prefix}bp-add-btn`);
  const clrBtn  = document.getElementById(`${prefix}bp-clear-btn`);
  const doAdd = () => {
    const addr = _parseBpAddr(input.value);
    if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: coreOf(), addr });
    input.value = "";
  };
  if (addBtn) addBtn.addEventListener("click", doAdd);
  if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") doAdd(); });
  if (clrBtn) clrBtn.addEventListener("click", () => sendAction({ action: "clear_breakpoints", core: coreOf() }));
}

function updateSource(instructions, pc, labels) {
  // State packets reuse the same source objects until a load changes them.
  // Compare those references instead of hashing every instruction on every
  // live PC update.
  const sourceChanged = instructions !== _renderedSourceInstructions ||
    labels !== _renderedSourceLabels;
  if (sourceChanged) {
    _renderedSourceInstructions = instructions;
    _renderedSourceLabels = labels;
    _lastSourceBreakpointKey = null;
    _currentSourceLine = null;
    sourceList.innerHTML = "";

    const addrToLabels = {};
    for (const [name, addr] of Object.entries(labels)) {
      if (!addrToLabels[addr]) addrToLabels[addr] = [];
      addrToLabels[addr].push(name);
    }
    for (const addr of Object.keys(addrToLabels)) addrToLabels[addr].sort();

    instructions.forEach((instr) => {
      // Insert a label row for each label pointing to this address
      (addrToLabels[instr.addr] || []).forEach(name => {
        const lblLi = document.createElement("li");
        lblLi.className = "lbl-line";

        const dot = document.createElement("span");
        dot.className = "bp-dot";
        dot.textContent = "●";

        const src = document.createElement("span");
        src.className = "src";
        src.innerHTML = `<span class="hl-lbl">${escapeHtml(name)}:</span>`;

        lblLi.appendChild(dot);
        lblLi.appendChild(src);
        sourceList.appendChild(lblLi);
      });

      // Instruction row
      const li = document.createElement("li");
      li.id = `src-line-${instr.addr}`;

      const bpDot = document.createElement("span");
      bpDot.className = "bp-dot";
      bpDot.textContent = "●";

      const addrSpan = document.createElement("span");
      addrSpan.className = "addr";
      addrSpan.textContent = `${instr.addr}:`;

      const srcSpan = document.createElement("span");
      srcSpan.className = "src";
      srcSpan.innerHTML = highlightAsm(instr.text);

      li.appendChild(bpDot);
      li.appendChild(addrSpan);
      li.appendChild(srcSpan);
      sourceList.appendChild(li);
    });
  }

  // Update breakpoint markers only when the breakpoint set or source list
  // changed; rebuilding these classes on every state packet is expensive.
  const breakpointKey = [...clientBreakpoints].sort((a, b) => a - b).join(",");
  if (breakpointKey !== _lastSourceBreakpointKey) {
    sourceList.querySelectorAll("li[id^='src-line-']").forEach(li => {
      const addr = +li.id.slice(9);
      li.classList.toggle("has-bp", clientBreakpoints.has(addr));
    });
    _lastSourceBreakpointKey = breakpointKey;
  }

  // Update current-pc highlight
  const currentLine = document.getElementById(`src-line-${pc}`);
  if (_currentSourceLine && _currentSourceLine !== currentLine) {
    _currentSourceLine.classList.remove("current-pc");
  }
  if (currentLine && currentLine !== _currentSourceLine) {
    currentLine.classList.add("current-pc");
    queueSourceLineScroll("main", sourceList, currentLine);
  }
  _currentSourceLine = currentLine;

  renderBpBar("", currentCore, clientBreakpoints);
}

function sourceLineNeedsScroll(lineTop, lineBottom, viewportTop, viewportBottom) {
  return lineTop < viewportTop || lineBottom > viewportBottom;
}

const pendingSourceScrolls = new Map();

function queueSourceLineScroll(key, listEl, line) {
  const pending = pendingSourceScrolls.get(key);
  if (pending) {
    pending.line = line;
    return;
  }

  const request = { listEl, line };
  pendingSourceScrolls.set(key, request);
  const schedule = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame
    : (callback) => setTimeout(callback, 0);
  schedule(() => {
    const current = pendingSourceScrolls.get(key);
    pendingSourceScrolls.delete(key);
    if (!current || !current.line || !current.line.isConnected) return;

    const scroller = current.listEl.parentElement;
    if (!scroller || scroller.clientHeight <= 0) return;
    const lineRect = current.line.getBoundingClientRect();
    const viewportRect = scroller.getBoundingClientRect();
    if (sourceLineNeedsScroll(
      lineRect.top,
      lineRect.bottom,
      viewportRect.top,
      viewportRect.bottom,
    )) {
      current.line.scrollIntoView({ block: "nearest", behavior: "auto" });
    }
  });
}

function updatePins(io) {
  if (!io) return;

  (io.gpo_pins || []).forEach((val, i) => {
    const pin = document.getElementById(`pin-gpo-${i}`);
    if (pin) {
      pin.classList.toggle("high", !!val);
    }
  });

  (io.gpi_pins || []).forEach((val, i) => {
    const pin = document.getElementById(`pin-gpi-${i}`);
    if (pin) {
      pin.classList.toggle("high", !!val);
    }
  });
}

// ---- SD Interface rendering ----

const _ACC_SEL_NAMES = ['sinc3', 'sinc2', 'sinc1'];
const _CLK_SEL_NAMES = ['r31[16]', 'own', 'shared', 'rsvd'];

function updateSDPanel(io) {
  const sdSection = document.getElementById('sd-interface');
  if (!sdSection) return;

  const gpioSections = document.querySelectorAll(
    '#io-panel .io-section:not(.sd-interface):not(.perif-interface):not(.io-mux-row):not(.i2c-interface)'
  );

  if (!io || io.mode !== 'sd') {
    sdSection.style.display = 'none';
    // GPIO sections visible only in GPIO mode; in perif mode the perif panel owns the view.
    const showGpio = !io || io.mode === 'gpio';
    gpioSections.forEach(s => { s.style.display = showGpio ? '' : 'none'; });
    return;
  }

  // Switch to SD view
  sdSection.style.display = '';
  gpioSections.forEach(s => { s.style.display = 'none'; });

  const sd = io.sd;
  if (!sd) return;

  // Mode info
  const infoEl = document.getElementById('sd-mode-info');
  if (infoEl) infoEl.textContent = currentCore === 'pru0' ? 'PRU0 owns Ch 0–2' : 'RTU0 owns Ch 0–2';

  // R30 decode
  const r30El = document.getElementById('sd-r30-decode');
  if (r30El) {
    r30El.innerHTML = '<span style="color:var(--accent);">R30:</span> ' +
      `<span class="sd-r30-field">[29:26] ch_sel=<b>${sd.ch_sel}</b></span>` +
      `<span class="sd-r30-field">[25] sd_en=<b style="color:var(--accent);">${sd.sd_en ? 1 : 0}</b></span>` +
      `<span class="sd-r30-field">[24] snoop=<b>${sd.snoop ? 1 : 0}</b></span>` +
      `<span class="sd-r30-field">[23] data_sel=<b>${sd.data_sel ? 1 : 0}</b></span>`;
  }

  // Channel cards
  const chContainer = document.getElementById('sd-channels');
  if (chContainer) {
    chContainer.innerHTML = '';
    (sd.channels || []).forEach(ch => {
      const card = document.createElement('div');
      card.className = 'sd-channel-card' + (ch.selected ? ' selected' : '');
      const cfg = ch.config || {};
      const accName = _ACC_SEL_NAMES[cfg.acc_sel || 0] || 'sinc3';
      const clkName = _CLK_SEL_NAMES[cfg.clk_sel || 0] || 'own';
      const titleColor = ch.selected ? 'var(--accent)' : 'var(--text-dim)';
      const dotColor = ch.valid ? 'var(--accent)' : 'var(--text-dim)';
      card.innerHTML = `
        <div class="sd-channel-title" style="color:${titleColor};">
          CH ${ch.id} <span style="color:${dotColor};">●</span>${ch.selected ? ' <span style="font-size:9px;">★</span>' : ''}
        </div>
        <div class="sd-channel-config">${clkName} | OSR:${cfg.osr || ch.osr} | ${accName}</div>
        <div class="sd-acc-row">acc1: <span class="sd-acc-val">0x${(ch.acc1 || 0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
        <div class="sd-acc-row">acc2: <span class="sd-acc-val">0x${(ch.acc2 || 0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
        <div class="sd-acc-row">acc3: <span class="sd-acc-val">0x${(ch.acc3 || 0).toString(16).toUpperCase().padStart(6,'0')}</span></div>
        <div class="sd-status">
          <div style="color:${ch.valid ? 'var(--accent)' : 'var(--text-dim)'};">ovf=${ch.ovf ? 1 : 0} valid=<b style="color:${ch.valid ? 'var(--accent)' : 'var(--text-dim)'};">${ch.valid ? 1 : 0}</b></div>
          <div style="color:var(--text);">data=0x${(ch.shadow_acc3 || 0).toString(16).toUpperCase().padStart(7,'0')}</div>
        </div>`;
      chContainer.appendChild(card);
    });
  }

  // Pattern generator
  const pgContainer = document.getElementById('sd-pattern-gen');
  // Skip rebuild only when the user is actively editing a number input inside the
  // card — not when a <select> has focus, because a signal-type change requires
  // restructuring the rows (dc shows dc_level, sine shows amplitude/period/phase).
  const _pgFocused = document.activeElement;
  const _pgSkip = pgContainer && pgContainer.contains(_pgFocused) && _pgFocused.tagName === 'INPUT';
  if (pgContainer && !_pgSkip) {
    pgContainer.innerHTML = '';
    (sd.modulators || []).forEach((mod, i) => {
      const card = document.createElement('div');
      card.className = 'sd-mod-card';
      let rows = `<div class="sd-mod-title">CH ${i} Modulator</div>`;
      rows += `<div class="sd-mod-row"><span class="sd-mod-label">Signal:</span>
        <span class="sd-mod-value"><select data-ch="${i}" data-param="signal">
          <option value="dc"${mod.signal === 'dc' ? ' selected' : ''}>DC</option>
          <option value="sine"${mod.signal === 'sine' ? ' selected' : ''}>Sine</option>
        </select></span></div>`;
      rows += `<div class="sd-mod-row"><span class="sd-mod-label">Clk MHz:</span>
        <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="sd_clock_mhz"
          value="${mod.sd_clock_mhz}" min="10" max="40" step="1"></span></div>`;
      if (mod.signal === 'dc') {
        rows += `<div class="sd-mod-row"><span class="sd-mod-label">DC Level:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="dc_level"
            value="${mod.dc_level}" min="-1" max="1" step="0.1"></span></div>`;
      } else {
        rows += `<div class="sd-mod-row"><span class="sd-mod-label">Amp:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="amplitude"
            value="${mod.amplitude}" min="0" max="1" step="0.1"></span></div>`;
        rows += `<div class="sd-mod-row"><span class="sd-mod-label">Period:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="period"
            value="${mod.period}" min="64" max="65536" step="64"></span></div>`;
        rows += `<div class="sd-mod-row"><span class="sd-mod-label">Phase°:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="phase_deg"
            value="${mod.phase_deg}" min="0" max="360" step="1"></span></div>`;
      }
      card.innerHTML = rows;
      pgContainer.appendChild(card);
    });

    // Event listeners for pattern gen controls
    pgContainer.querySelectorAll('select[data-param], input[data-param]').forEach(el => {
      const doSend = () => {
        const ch = parseInt(el.dataset.ch, 10);
        const param = el.dataset.param;
        const value = el.type === 'number' ? parseFloat(el.value) : el.value;
        if (el.type === 'number' && isNaN(value)) return;
        sendAction({ action: 'set_sd_modulator', core: currentCore, channel: ch, params: { [param]: value } });
      };
      el.addEventListener('change', doSend);
      // Also send on `input` so changes reach the server immediately on each
      // spinner click or keystroke — without waiting for blur/change on focus loss.
      if (el.type === 'number') el.addEventListener('input', doSend);
    });
  }

  // Config chips
  const chipsEl = document.getElementById('sd-config-chips');
  if (chipsEl && sd.channels) {
    chipsEl.innerHTML = '';
    sd.channels.forEach((ch, i) => {
      const cfg = ch.config || {};
      const accName = _ACC_SEL_NAMES[cfg.acc_sel || 0] || 'sinc3';
      const btn = document.createElement('button');
      btn.className = 'sd-config-chip';
      btn.textContent = `Ch${i}: OSR=${cfg.osr || ch.osr} ${accName}`;
      // Clicking opens a prompt to edit OSR
      btn.addEventListener('click', () => {
        const newOsr = parseInt(prompt(`Channel ${i} OSR (4-256):`, String(cfg.osr || ch.osr)), 10);
        if (newOsr && newOsr >= 4 && newOsr <= 256) {
          const sampleSize = newOsr - 1;
          const addr = 0x2604C + i * 8;
          sendAction({ action: 'write_sd_register', core: currentCore, addr, value: sampleSize });
        }
      });
      chipsEl.appendChild(btn);
    });
  }

  // GPIO switch link
  const switchEl = document.getElementById('sd-switch-gpio');
  if (switchEl) {
    switchEl.onclick = (e) => {
      e.preventDefault();
      sdSection.style.display = 'none';
      gpioSections.forEach(s => { s.style.display = ''; });
    };
  }
}

// ---- Peripheral Interface (3-channel) panel --------------------------------

function _fifoHex(arr) {
  if (!arr || !arr.length) return '<span style="color:var(--text-dim);">empty</span>';
  return arr.map(b => '0x' + b.toString(16).toUpperCase().padStart(2, '0')).join(' ');
}

function updatePerifPanel(io) {
  const sec = document.getElementById('perif-interface');
  if (!sec) return;

  // RTU0 has no GPCFG GP-mux (TRM) — hide the mux row for it.
  const muxRow = document.getElementById('io-mux-row');
  if (muxRow) muxRow.style.display = (currentCore === 'rtu0') ? 'none' : '';

  // Keep the GP-mux selector in sync (unless the user is interacting with it).
  const muxSel = document.getElementById('io-mux-sel');
  const muxNote = document.getElementById('io-mux-note');
  if (muxSel && document.activeElement !== muxSel) {
    muxSel.value = (io && io.mux_sel != null) ? String(io.mux_sel) : '0';
  }
  if (muxNote) muxNote.textContent = io ? ('mode: ' + io.mode) : '';

  if (!io || io.mode !== 'perif') { sec.style.display = 'none'; return; }
  sec.style.display = '';

  const p = io.perif;
  if (!p) return;

  const info = document.getElementById('perif-mode-info');
  if (info) {
    info.textContent = (currentCore === 'pru0' ? 'PRU0' : 'PRU1') +
      ' · 3 channels · ch_sel=' + p.ch_sel;
  }

  // Shared clock / decode row
  const dec = document.getElementById('perif-r30-decode');
  if (dec) {
    const sh = p.shared || {};
    dec.innerHTML = '<span style="color:var(--accent);">shared:</span> ' +
      `<span class="perif-r30-field">tx_clk_sel=<b>${sh.tx_clk_sel ?? 0}</b></span>` +
      `<span class="perif-r30-field">tx_div=<b>${sh.tx_div_factor ?? 0}</b></span>` +
      `<span class="perif-r30-field">rx_clk_sel=<b>${sh.rx_clk_sel ?? 0}</b></span>` +
      `<span class="perif-r30-field">rx_div=<b>${sh.rx_div_factor ?? 0}</b></span>` +
      `<span class="perif-r30-field">rx_ss=<b>${sh.rx_sample_size ?? 0}</b></span>` +
      `<span class="perif-r30-field">sb_pol=<b>${sh.rx_sb_pol ?? 1}</b></span>`;
  }

  // CFG register editor (skip rebuild while the user edits an input)
  const cfgRegs = document.getElementById('perif-cfg-regs');
  if (cfgRegs) {
    const sh = p.shared || {};
    const cfgFocus = document.activeElement;
    const cfgEditing = cfgRegs.contains(cfgFocus) && cfgFocus.tagName === 'INPUT';
    if (sh.base_addr != null && !cfgEditing) {
      const hex8 = v => '0x' + (v >>> 0).toString(16).toUpperCase().padStart(8, '0');
      cfgRegs.innerHTML = '';
      [['RXCFG', 0x0, sh.rxcfg], ['TXCFG', 0x4, sh.txcfg]].forEach(([name, off, val]) => {
        const addr = sh.base_addr + off;
        const row = document.createElement('div');
        row.className = 'perif-cfgreg-row';
        row.innerHTML = `
          <span class="perif-cfgreg-name">${name}</span>
          <span class="perif-cfgreg-addr">@0x${addr.toString(16).toUpperCase()}</span>
          <input type="text" spellcheck="false" value="${hex8(val ?? 0)}">
          <button class="perif-cfgreg-btn" data-addr="${addr}">Apply</button>
          <span class="perif-cfgreg-err"></span>`;
        cfgRegs.appendChild(row);
      });
      cfgRegs.querySelectorAll('.perif-cfgreg-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const row = btn.closest('.perif-cfgreg-row');
          const err = row.querySelector('.perif-cfgreg-err');
          const v = parseHexOrDec(row.querySelector('input').value.trim());
          if (isNaN(v) || v < 0 || v > 0xFFFFFFFF) {
            err.textContent = 'invalid value';
            return;
          }
          err.textContent = '';
          sendAction({ action: 'write_perif_register', core: currentCore,
                       addr: parseInt(btn.dataset.addr, 10), value: v });
        });
      });
    }
  }

  // Channel cards
  const cont = document.getElementById('perif-channels');
  if (cont) {
    cont.innerHTML = '';
    (p.channels || []).forEach(ch => {
      const cfg = ch.config || {};
      const card = document.createElement('div');
      card.className = 'perif-channel-card' + (ch.busy ? ' busy' : '');
      const flag = (name, on, warn) =>
        `<span class="${on ? (warn ? 'warn' : 'on') : ''}">${name}=${on ? 1 : 0}</span>`;
      card.innerHTML = `
        <div class="perif-channel-title">CH ${ch.id} · ${ch.fsm}${ch.busy ? ' ●' : ''}</div>
        <div class="perif-fifo">TX FIFO: <b>${_fifoHex(ch.tx_fifo)}</b></div>
        <div class="perif-fifo">RX FIFO: <b>${_fifoHex(ch.rx_fifo)}</b></div>
        <div class="perif-flags">
          ${flag('busy', ch.busy)} ${flag('ovr', ch.tx_overrun, true)}
          ${flag('unr', ch.tx_underrun, true)} ${flag('rx_en', ch.rx_en)}
          ${flag('val', ch.rx_valid)} ${flag('ovf', ch.rx_ovf, true)}
          ${flag('eof', ch.rx_eof)}
        </div>
        <div class="perif-cfg">
          txframe=${cfg.tx_frame_size ?? 0}b · rxframe=${cfg.rx_frame_size ?? 0}B ·
          wire=${cfg.tx_wire_delay ?? 0} · tst=${cfg.tst_delay ?? 0} ·
          clkmode=${ch.clk_mode} · swap=${cfg.tx_swap_data_en ?? 0}
        </div>`;
      cont.appendChild(card);
    });
  }

  // Loopback control (skip rebuild while the user edits an input)
  const lb = document.getElementById('perif-loopback');
  const focused = document.activeElement;
  const skip = lb && lb.contains(focused) && (focused.tagName === 'INPUT' || focused.tagName === 'SELECT');
  const lbState = io.loopback;
  if (lb && lbState && !skip) {
    lb.innerHTML = '';
    (lbState.channels || []).forEach(c => {
      const row = document.createElement('div');
      row.className = 'perif-lb-row';
      row.innerHTML = `
        <label><input type="checkbox" data-ch="${c.channel}" data-f="enabled" ${c.enabled ? 'checked' : ''}> ch${c.channel}</label>
        <label>lat ns <input type="number" data-ch="${c.channel}" data-f="latency_ns" value="${c.latency_ns}" step="0.5"></label>
        <label>jit ns <input type="number" data-ch="${c.channel}" data-f="jitter_ns" value="${c.jitter_ns}" step="0.5" min="0"></label>
        <label>drift ppm <input type="number" data-ch="${c.channel}" data-f="drift_ppm" value="${c.drift_ppm}" step="1" min="-100" max="100"></label>
        <button class="perif-lb-btn" data-ch="${c.channel}">Apply</button>`;
      lb.appendChild(row);
    });
    lb.querySelectorAll('.perif-lb-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const ch = parseInt(btn.dataset.ch, 10);
        const row = btn.closest('.perif-lb-row');
        const get = (f) => row.querySelector(`[data-f="${f}"]`);
        sendAction({
          action: 'perif_loopback',
          core: currentCore,
          channel: ch,
          enabled: get('enabled').checked,
          latency_ns: parseFloat(get('latency_ns').value) || 0,
          jitter_ns: parseFloat(get('jitter_ns').value) || 0,
          drift_ppm: parseFloat(get('drift_ppm').value) || 0,
        });
      });
    });
  }
}

// ---- TCA9538 I2C IO expander panel -----------------------------------------

function updateI2CPanel(io) {
  const section = document.getElementById('i2c-interface');
  if (!section) return;

  document.getElementById('i2c-attach-btn')?.classList.toggle('active', !!(io && io.i2c));

  const i2c = io && io.i2c;
  const emptyState = document.getElementById('i2c-empty-state');
  if (!i2c || !i2c.saw_start) {
    section.style.display = 'none';
    if (emptyState) {
      emptyState.textContent = i2c
        ? 'Waiting for a valid I2C START on the attached TCA9538 interface.'
        : 'Attach TCA9538 to monitor I2C transactions.';
      emptyState.style.display = '';
    }
    return;
  }
  section.style.display = '';
  if (emptyState) emptyState.style.display = 'none';

  const infoEl = document.getElementById('i2c-mode-info');
  if (infoEl) {
    infoEl.textContent = `addr=0x${i2c.address.toString(16).toUpperCase()} ` +
      `CONFIG=0x${i2c.config_reg.toString(16).toUpperCase().padStart(2, '0')}`;
  }

  const ledContainer = document.getElementById('i2c-leds');
  if (ledContainer) {
    ledContainer.innerHTML = '';
    const driven = i2c.output_reg & (~i2c.config_reg & 0xFF);
    for (let i = 0; i < 8; i++) {
      const led = document.createElement('div');
      led.className = 'i2c-led' + (((driven >> i) & 1) ? ' on' : '');
      led.title = `P${i}`;
      ledContainer.appendChild(led);
    }
  }

  const logEl = document.getElementById('i2c-log');
  if (logEl && i2c.last_transaction) {
    const t = i2c.last_transaction;
    const regStr = t.reg === null || t.reg === undefined ? '--' : `0x${t.reg.toString(16).toUpperCase().padStart(2, '0')}`;
    const dataStr = t.data === null || t.data === undefined ? '--' : `0x${t.data.toString(16).toUpperCase().padStart(2, '0')}`;
    logEl.textContent = `addr=0x${t.address.toString(16).toUpperCase()} reg=${regStr} data=${dataStr} ${t.ack ? 'ACK' : 'NACK'}`;
  }
}

// ---- Signal graph — buffer -------------------------------------------------

/**
 * Push one sample into the circular buffer.
 * sample = { step, mode, gpo: [20], gpi: [20], perif: [3], perifOe: [3],
 *            perifClk: [3], mem: [number|null, ...] }
 */
function graphPushSample(sample) {
  signalGraph.buf[signalGraph.head] = sample;
  signalGraph.head = (signalGraph.head + 1) % signalGraph.windowSize;
  if (signalGraph.fill < signalGraph.windowSize) signalGraph.fill++;
  // Show Export CSV once we have data
  const exportRow = document.getElementById("graph-export-row");
  const countEl   = document.getElementById("graph-sample-count");
  if (exportRow && signalGraph.fill > 0) {
    exportRow.style.display = "";
    if (countEl) countEl.textContent = `${signalGraph.fill} samples recorded`;
  }
}

/**
 * Return samples in chronological order (oldest first).
 */
function graphGetSamples() {
  const { buf, head, fill, windowSize } = signalGraph;
  if (fill === 0) return [];
  const start = fill < windowSize ? 0 : head;
  const out = [];
  for (let i = 0; i < fill; i++) {
    out.push(buf[(start + i) % windowSize]);
  }
  return out;
}

// ---- UART decoder ----------------------------------------------------------

/**
 * Decode a UART 8N1 bit stream from a flat array of 0/1 values.
 * Each element is one simulator step sample of GPO0.
 * Returns { bytes: number[], tBit: number, error: string|null }
 *   error: 'empty' | 'noisy' | 'idle' | null
 */
function decodeUARTBits(bits) {
  if (!bits || bits.length === 0) return { bytes: [], tBit: 0, error: 'empty' };

  // Run-length encode
  const runs = [];
  let i = 0;
  while (i < bits.length) {
    const bit = bits[i];
    let len = 0;
    while (i < bits.length && bits[i] === bit) { i++; len++; }
    runs.push({ bit, len });
  }

  // Auto-detect bit period = minimum run length
  const tBit = Math.min(...runs.map(r => r.len));
  if (tBit < 2) return { bytes: [], tBit: 0, error: 'noisy' };
  if (runs.every(r => r.bit === 1)) return { bytes: [], tBit: 0, error: 'idle' };

  // Decode UART frames (8N1: 1 start LOW, 8 data LSB-first, 1 stop HIGH)
  const bytes = [];
  let pos = 0;
  while (pos < bits.length) {
    if (bits[pos] === 1) { pos++; continue; }        // idle/stop, skip
    const start = pos;

    // Guard: start-bit run must be >= T/2 to reject glitches
    let startLen = 0;
    let p = pos;
    while (p < bits.length && bits[p] === 0) { p++; startLen++; }
    if (startLen < tBit * 0.5) { pos++; continue; }

    // Sample 8 data bits at midpoints: T*(n+1.5) from start of START bit
    let byteVal = 0;
    let valid = true;
    for (let n = 0; n < 8; n++) {
      const samplePos = start + Math.floor(tBit * (n + 1.5));
      if (samplePos >= bits.length) { valid = false; break; }
      byteVal |= bits[samplePos] << n;
    }
    if (!valid) break;                               // partial frame, stop

    // Framing check: stop bit must be HIGH
    const stopPos = start + Math.floor(tBit * 9.5);
    if (stopPos < bits.length && bits[stopPos] === 0) {
      pos = start + tBit;                            // framing error — skip one T, retry
      continue;
    }

    bytes.push(byteVal);
    pos = start + Math.floor(tBit * 10);             // advance past complete frame
  }

  return { bytes, tBit, error: null };
}

/**
 * Extract GPO0 from signal graph samples and call decodeUARTBits.
 * samples: return value of graphGetSamples().
 * Each sample is expanded by its step-count delta so that Run-mode
 * batching (1000 steps/message) produces the correct bit widths.
 */
function decodeUART(samples) {
  const bits = [];
  for (let i = 0; i < samples.length; i++) {
    const val = (samples[i].gpo && samples[i].gpo[0] !== undefined) ? (samples[i].gpo[0] ? 1 : 0) : 1;
    const nextStep = i + 1 < samples.length ? samples[i + 1].step : samples[i].step + 1;
    const weight = Math.max(1, nextStep - samples[i].step);
    for (let j = 0; j < weight; j++) bits.push(val);
  }
  return decodeUARTBits(bits);
}

/**
 * Render decoded bytes as an HTML string.
 * CR → \r, LF → \n in accent colour; non-printable → \xNN dim; else literal.
 */
function renderUARTBytes(bytes) {
  return bytes.map(b => {
    if (b === 0x0D) return '<span class="uart-esc">\\r</span>';
    if (b === 0x0A) return '<span class="uart-esc">\\n</span>';
    if (b >= 0x20 && b <= 0x7E) return String.fromCharCode(b).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return `<span class="uart-esc">\\x${b.toString(16).padStart(2, '0').toUpperCase()}</span>`;
  }).join('');
}

/**
 * Resize the circular buffer to newSize, keeping the most recent samples.
 */
function graphResizeWindow(newSize) {
  const samples = graphGetSamples(); // oldest → newest, chronological
  signalGraph.windowSize = newSize;
  signalGraph.buf = new Array(newSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;

  // Split by core so each core keeps proportional history, then merge
  const cores = [...new Set(samples.map(s => s.core || "pru0"))];
  if (cores.length <= 1) {
    // Single-core path: simple slice
    samples.slice(-newSize).forEach(s => graphPushSample(s));
    return;
  }
  // Multi-core: allocate slots evenly per core, keep newest
  const perCore = Math.max(1, Math.floor(newSize / cores.length));
  const kept = [];
  for (const c of cores) {
    const cs = samples.filter(s => (s.core || "pru0") === c);
    kept.push(...cs.slice(-perCore));
  }
  // Sort by runStep (shared axis) so the circular buffer is in order
  kept.sort((a, b) => (a.runStep ?? a.step) - (b.runStep ?? b.step));
  kept.forEach(s => graphPushSample(s));
}

// ---- Signal graph — zoom / pan -------------------------------------------

/** Update canvas cursor to reflect zoom state (crosshair = full view, grab = zoomed). */
function _graphSetCursor() {
  const canvas = document.getElementById("signal-graph-canvas");
  if (!canvas) return;
  canvas.style.cursor = signalGraph.view ? "grab" : "crosshair";
}

function graphViewReset() {
  signalGraph.view = null;
  _graphSetCursor();
  drawDigitalGraph();
}

/**
 * Zoom the view by `factor` centered on `fracX` (0..1 position across canvas).
 * factor < 1 zooms in; factor > 1 zooms out.
 */
function graphViewZoom(factor, fracX) {
  const samples = graphGetSamples();
  if (samples.length < 2) return;
  const allSteps = samples.map(s => s.runStep ?? s.step);
  const dataMin = Math.min(...allSteps);
  const dataMax = Math.max(...allSteps);
  const v = signalGraph.view || { minStep: dataMin, maxStep: dataMax };
  const range = v.maxStep - v.minStep;
  const pivot = v.minStep + fracX * range;
  const newRange = Math.max(20, Math.min(range * factor, dataMax - dataMin));
  let newMin = pivot - fracX * newRange;
  let newMax = newMin + newRange;
  // Clamp to data bounds
  if (newMin < dataMin) { newMin = dataMin; newMax = newMin + newRange; }
  if (newMax > dataMax) { newMax = dataMax; newMin = newMax - newRange; }
  if (newMin < dataMin) newMin = dataMin;
  // If view covers full range, clear zoom state
  if (newMin <= dataMin && newMax >= dataMax) {
    signalGraph.view = null;
  } else {
    signalGraph.view = { minStep: newMin, maxStep: newMax };
  }
  _graphSetCursor();
  drawDigitalGraph();
}

// ---- Signal graph — sampling -----------------------------------------------

/**
 * Called on every state message while recording === true.
 * Pushes a new sample and fires read_memory for each memory channel.
 */
const GRAPH_MEM_BPS = { uint8: 1, uint16: 2, uint32: 4, int32: 4 };

function graphSample(state) {
  // Always refresh memory snapshots if channels are configured
  signalGraph.memChannels.forEach((ch, i) => {
    sendAction({
      action: "read_memory",
      addr: ch.addr,
      length: ch.count * (GRAPH_MEM_BPS[ch.format] || 4),
      tag: `graph-M${i}`,
    });
  });

  // A run loop's samples already arrived in a "capture" message; sampling this
  // closing state push too would append a duplicate of its last sample.
  if (!signalGraph.recording || state.captured) return;
  const perifChannels = (state.io.perif && state.io.perif.channels) || [];
  const sample = {
    step: state.instruction_count,
    runStep: state.instruction_count,
    // IO mux mode at capture time: in perif mode the GPO/GPI pins are owned by
    // the Peripheral Interface, so R30/R31 must not be plotted as pin state.
    mode: state.io.mode || "gpio",
    core: state.core || "pru0",
    gpo: (state.io.gpo_pins || []).slice(0, 20),
    gpi: (state.io.gpi_pins || []).slice(0, 20),
    perif: perifChannels.map(ch => ch.tx_line ? 1 : 0),
    // Bit clock alongside the data line: the perif serializer has no framing
    // of its own, so the clock is the only reference for where bits start.
    perifClk: perifChannels.map(ch => ch.tx_clk_pin ? 1 : 0),
    // Output enable: shows when the channel actually drives the pad.
    perifOe: perifChannels.map(ch => ch.tx_out_en ? 1 : 0),
  };
  graphPushSample(sample);
}

/**
 * Called when a "capture" message arrives — the per-instruction samples the
 * server took inside a run loop. Run executes up to `max_steps` instructions
 * per round-trip, so sampling only its closing state push gives one sample per
 * 100+ instructions: far too coarse for a perif bit (2 core cycles at the
 * channel-0 N=2 divider), which is why a Run-mode capture looked empty while
 * the same firmware traced fine under SIM (one instruction per push).
 *
 * Wire format is packed ints, see _capture_sample() in ui/server.py:
 *   [step, r30(20 bits), gpi bits, perif out bits, out_en bits, clk bits, run_step]
 */
function graphHandleCaptureLegacy(msg) {
  return graphHandleCapture(msg); /* legacy implementation retained below for reference
  if (!signalGraph.recording) return;
  const mode = msg.mode || "gpio";
  const core = msg.core || "pru0";
  // Single-shot, perif captures only: those sample every instruction, so a run
  // fills the whole window within a few ms of wall clock and a rolling buffer
  // would just blur — evicting the transmission before anyone could look at it.
  // Fill once, then stop recording, the way a logic analyzer does. GP-mode
  // captures use fixed-stride sampling and keep rolling as before.
  const singleShot = mode === "perif";
  for (const s of (msg.samples || [])) {
    if (singleShot && signalGraph.fill >= signalGraph.windowSize) {
      graphSetRecording(false);
      break;
    }
    const [step, r30, gpiBits, outBits, oeBits, clkBits, runStep] = s;
    graphPushSample({
      step,
      runStep: runStep ?? step,
      mode,
      core,
      gpo: Array.from({ length: 20 }, (_, i) => (r30 >> i) & 1),
      gpi: Array.from({ length: 20 }, (_, i) => (gpiBits >> i) & 1),
      perif: [0, 1, 2].map(i => (outBits >> i) & 1),
      perifOe: [0, 1, 2].map(i => (oeBits >> i) & 1),
      perifClk: [0, 1, 2].map(i => (clkBits >> i) & 1),
    });
  }
*/
}

function graphAppendCaptureSample(msg, s) {
  if (!signalGraph.recording) return false;
  const mode = msg.mode || "gpio";
  const core = msg.core || "pru0";
  const singleShot = mode === "perif";
  // Peripheral captures are intentionally single-shot: sampling every
  // instruction would otherwise roll the transmission out of view quickly.
  if (singleShot && signalGraph.fill >= signalGraph.windowSize) {
    graphSetRecording(false);
    return false;
  }
  const [step, r30, gpiBits, outBits, oeBits, clkBits, runStep] = s;
  graphPushSample({
    step,
    runStep: runStep ?? step,
    mode,
    core,
    gpo: Array.from({ length: 20 }, (_, i) => (r30 >> i) & 1),
    gpi: Array.from({ length: 20 }, (_, i) => (gpiBits >> i) & 1),
    perif: [0, 1, 2].map(i => (outBits >> i) & 1),
    perifOe: [0, 1, 2].map(i => (oeBits >> i) & 1),
    perifClk: [0, 1, 2].map(i => (clkBits >> i) & 1),
  });
  return true;
}

function graphHandleCapture(msg) {
  if (!signalGraph.recording) return;
  const groupId = msg.capture_group;
  if (groupId === undefined || groupId === null) {
    for (const sample of (msg.samples || [])) {
      if (!graphAppendCaptureSample(msg, sample)) break;
    }
    return;
  }

  let group = pendingGraphCaptures.get(String(groupId));
  if (!group) {
    group = new Map();
    pendingGraphCaptures.set(String(groupId), group);
  }
  group.set(msg.core || "pru0", msg);
  if (group.size < 2) return;
  pendingGraphCaptures.delete(String(groupId));

  // Both cores use the same runStep values. Interleave by that shared time
  // axis so the circular buffer retains both cores' recent history.
  const merged = [];
  for (const batch of group.values()) {
    for (const sample of (batch.samples || [])) {
      merged.push({ core: batch.core || "pru0", batch, sample });
    }
  }
  merged.sort((a, b) => {
    const aStep = a.sample[6] ?? a.sample[0];
    const bStep = b.sample[6] ?? b.sample[0];
    return aStep - bStep || a.core.localeCompare(b.core);
  });
  for (const item of merged) {
    if (!graphAppendCaptureSample(item.batch, item.sample)) break;
  }
}

/**
 * Called when a read_memory response with tag "graph-M*" arrives.
 * Back-fills the value into the most recently pushed sample.
 */
function _graphChannelIdxFromTag(tag) {
  return parseInt(tag.slice(7), 10); // skip "graph-M"
}

function graphMarkChannelError(tag) {
  const idx = _graphChannelIdxFromTag(tag);
  if (isNaN(idx)) return;
  const row = document.querySelector(`.graph-mem-row[data-index="${idx}"]`);
  if (row) row.querySelector(".graph-ch-addr")?.classList.add("addr-error");
}

function graphHandleMemory(msg) {
  // tag format: "graph-M{channelIdx}"
  const idx = _graphChannelIdxFromTag(msg.tag);
  if (isNaN(idx) || idx >= signalGraph.memChannels.length) return;
  const ch = signalGraph.memChannels[idx];
  const raw = msg.data || msg.bytes || msg.values;
  if (!Array.isArray(raw) || raw.length === 0) return;

  // Decode bytes into array of samples (little-endian)
  const bps = GRAPH_MEM_BPS[ch.format] || 4;
  const count = Math.floor(raw.length / bps);
  const snapshot = new Array(count);
  for (let i = 0; i < count; i++) {
    const off = i * bps;
    let val = 0;
    for (let b = 0; b < bps; b++) val |= (raw[off + b] & 0xff) << (b * 8);
    snapshot[i] = (ch.format === 'int32') ? (val | 0) : (val >>> 0);
  }
  ch.snapshot = snapshot;

  const row = document.querySelector(`.graph-mem-row[data-index="${idx}"]`);
  if (row) row.querySelector(".graph-ch-addr")?.classList.remove("addr-error");
}

// ---- Signal graph — rendering ----------------------------------------------

function drawGraph() {
  drawDigitalGraph();
  drawMemGraph();
  drawFocWorkspace();
}

let graphDrawPending = false;

function requestGraphDraw() {
  if (graphDrawPending) return;
  graphDrawPending = true;
  const schedule = typeof requestAnimationFrame === "function"
    ? requestAnimationFrame
    : (callback) => setTimeout(callback, 0);
  schedule(() => {
    graphDrawPending = false;
    drawGraph();
  });
}

function graphFindNewestSsiFrame(samples, preferredCore = "pru0", clockPin = 0) {
  if (!Array.isArray(samples) || samples.length < 2) return null;

  const cores = [...new Set(samples.map(s => s.core || "pru0"))];
  const core = cores.includes(preferredCore) ? preferredCore : cores[0];
  if (!core) return null;

  const lane = samples
    .filter(s => (s.core || "pru0") === core)
    .map(s => ({ step: s.runStep ?? s.step, value: (s.gpo && s.gpo[clockPin]) ? 1 : 0 }))
    .filter(s => Number.isFinite(s.step))
    .sort((a, b) => a.step - b.step);
  if (lane.length < 2) return null;

  const transitions = [];
  for (let i = 1; i < lane.length; i++) {
    if (lane[i].value !== lane[i - 1].value) transitions.push(lane[i].step);
  }
  if (transitions.length < 24) return null;

  const gaps = [];
  for (let i = 1; i < transitions.length; i++) {
    const gap = transitions[i] - transitions[i - 1];
    if (gap > 0) gaps.push(gap);
  }
  if (gaps.length === 0) return null;
  const sortedGaps = [...gaps].sort((a, b) => a - b);
  const halfPeriod = sortedGaps[Math.floor((sortedGaps.length - 1) / 2)];
  const idleThreshold = Math.max(20, halfPeriod * 5);

  const groups = [[]];
  for (const transition of transitions) {
    const group = groups[groups.length - 1];
    if (group.length && transition - group[group.length - 1] > idleThreshold) {
      groups.push([]);
    }
    groups[groups.length - 1].push(transition);
  }
  const group = [...groups].reverse().find(candidate => candidate.length >= 24);
  if (!group) return null;

  const dataMin = lane[0].step;
  const dataMax = lane[lane.length - 1].step;
  const minStep = Math.max(dataMin, group[0] - halfPeriod);
  const end = group.length > 24 ? group[24] : group[23] + halfPeriod;
  return { minStep, maxStep: Math.min(dataMax, end) };
}

function graphBuildDigitalBuckets(samples, data, visMin, visMax, width) {
  const pixelWidth = Math.floor(width);
  const visibleRange = visMax - visMin;
  const count = Math.min(samples.length, data.length);
  if (count === 0 || pixelWidth <= 0 || !Number.isFinite(visibleRange) || visibleRange <= 0) {
    return [];
  }

  const times = new Array(count);
  const values = new Array(count);
  for (let i = 0; i < count; i++) {
    times[i] = samples[i].runStep ?? samples[i].step;
    values[i] = data[i] ? 1 : 0;
    if (!Number.isFinite(times[i])) return [];
  }

  const bucketMap = new Map();
  const getBucket = x => {
    let bucket = bucketMap.get(x);
    if (!bucket) {
      bucket = { x, enter: null, exit: null, sawHigh: false, sawLow: false };
      bucketMap.set(x, bucket);
    }
    return bucket;
  };

  const markSegment = (start, end, value) => {
    if (end <= start || end < visMin || start > visMax) return;
    const clippedStart = Math.max(start, visMin);
    const clippedEnd = Math.min(end, visMax);
    if (clippedEnd <= clippedStart) return;

    const startX = (clippedStart - visMin) / visibleRange * pixelWidth;
    const endX = (clippedEnd - visMin) / visibleRange * pixelWidth;
    const firstPixel = Math.max(0, Math.min(pixelWidth - 1, Math.floor(startX)));
    const lastPixel = Math.max(0, Math.min(pixelWidth - 1, Math.ceil(endX) - 1));
    for (let x = firstPixel; x <= lastPixel; x++) {
      const bucket = getBucket(x);
      if (bucket.enter === null) bucket.enter = value;
      bucket.exit = value;
      if (value) bucket.sawHigh = true;
      else bucket.sawLow = true;
    }
  };

  let segmentStart = times[0];
  let segmentValue = values[0];
  for (let i = 1; i < count; i++) {
    if (values[i] === values[i - 1]) continue;
    const transition = (times[i - 1] + times[i]) / 2;
    markSegment(segmentStart, transition, segmentValue);
    segmentStart = transition;
    segmentValue = values[i];
  }
  markSegment(segmentStart, times[count - 1], segmentValue);

  if (bucketMap.size === 0 && times[0] >= visMin && times[0] <= visMax) {
    const x = Math.max(0, Math.min(pixelWidth - 1,
      Math.floor((times[0] - visMin) / visibleRange * pixelWidth)));
    const bucket = getBucket(x);
    bucket.enter = values[0];
    bucket.exit = values[0];
    if (values[0]) bucket.sawHigh = true;
    else bucket.sawLow = true;
  }

  return [...bucketMap.values()].sort((a, b) => a.x - b.x);
}

function graphResolutionInfo(channels, visMin, visMax, width) {
  const cyclesPerPixel = width > 0 ? Math.max(0, visMax - visMin) / width : 0;
  if (cyclesPerPixel <= 0) return { cyclesPerPixel, subPixel: false };

  const subPixel = channels.some(channel => {
    let runStart = null;
    for (let i = 1; i < channel.data.length; i++) {
      if (channel.data[i] === channel.data[i - 1]) continue;
      const step = channel.samples[i].runStep ?? channel.samples[i].step;
      if (runStart !== null && step > runStart && step - runStart < cyclesPerPixel) return true;
      runStart = step;
    }
    return false;
  });
  return { cyclesPerPixel, subPixel };
}

function drawDigitalGraph() {
  const canvas = document.getElementById("signal-graph-canvas");
  if (!canvas) return;
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  if (W === 0 || H === 0) return;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");

  // Background
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, W, H);

  const samples = graphGetSamples();

  // Faint vertical grid lines (8 divisions)
  ctx.strokeStyle = "#222";
  ctx.lineWidth = 1;
  for (let gx = 1; gx < 8; gx++) {
    const x = (gx / 8) * W;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }

  // ---- Determine active digital channels (pin transitioned at least once) ----
  // In Peripheral mode the GPO/GPI pads belong to the Peripheral Interface, so
  // plotting R30/R31 bits there would show pins that do not exist on the wire.
  // Only the perif lanes (out / out_en / tx_clk) are drawn in that mode.
  const perifMode = samples.length > 0 &&
                    samples[samples.length - 1].mode === "perif";
  const activeDig = [];

  // Collect the set of cores that appear in the buffer. In single-core mode
  // this is just ["pru0"]. In multi-core mode it may be ["pru0","pru1"].
  const coresInBuf = [...new Set(samples.map(s => s.core || "pru0"))].sort();
  // Prefix labels with core name only when multiple cores are present.
  const multiCoreBuf = coresInBuf.length > 1;

  if (samples.length >= 2) {
    for (const coreName of coresInBuf) {
      // Work only on this core's own samples — no interleaving with other cores.
      // Each core's channels are drawn independently at their own sample positions.
      const coreSamples = samples.filter(s => (s.core || "pru0") === coreName);
      if (coreSamples.length < 2) continue;

      const prefix = multiCoreBuf ? coreName.toUpperCase() + ":" : "";

      if (!perifMode) {
        for (let i = 0; i < 20; i++) {
          const vals = coreSamples.map(s => s.gpo[i] || 0);
          if (vals.some(v => v !== vals[0])) {
            activeDig.push({ label: `${prefix}GPO ${i}`, color: GRAPH_GPO_COLORS[i],
                             data: vals, samples: coreSamples });
          }
        }
        for (let i = 0; i < 20; i++) {
          const vals = coreSamples.map(s => s.gpi[i] || 0);
          if (vals.some(v => v !== vals[0])) {
            activeDig.push({ label: `${prefix}GPI ${i}`, color: GRAPH_GPI_COLORS[i],
                             data: vals, samples: coreSamples });
          }
        }
      }
      for (let i = 0; i < 3; i++) {
        const out = coreSamples.map(s => (s.perif && s.perif[i]) || 0);
        const oe  = coreSamples.map(s => (s.perifOe && s.perifOe[i]) || 0);
        const clk = coreSamples.map(s => (s.perifClk && s.perifClk[i]) || 0);
        const moved = a => a.some(v => v !== a[0]);
        const chActive = moved(out) || moved(oe) || moved(clk);
        if (!chActive) continue;
        activeDig.push({ label: `${prefix}perif${i}_out`,    color: GRAPH_PERIF_COLORS[i],     data: out, samples: coreSamples });
        activeDig.push({ label: `${prefix}perif${i}_out_en`, color: GRAPH_PERIF_OE_COLORS[i],  data: oe,  samples: coreSamples });
        activeDig.push({ label: `${prefix}perif${i}_clk`,    color: GRAPH_PERIF_CLK_COLORS[i], data: clk, samples: coreSamples });
      }
    }
  }

  if (samples.length < 2) {
    _graphUpdateStepLabel(samples);
    _graphUpdateLegend([], "graph-legend");
    return;
  }

  // ---- Draw digital lanes --------------------------------------------------
  // All channels share a common time axis based on step numbers so that
  // signals from different cores align correctly on screen.
  let resolutionInfo = null;
  if (activeDig.length > 0) {
    // Compute global step range across all samples in the buffer.
    const allSteps = samples.map(s => s.runStep ?? s.step);
    const stepMin = Math.min(...allSteps);
    const stepMax = Math.max(...allSteps);
    const stepRange = stepMax - stepMin || 1;

    // Apply zoom view if set
    const view = signalGraph.view;
    const visMin = view ? view.minStep : stepMin;
    const visMax = view ? view.maxStep : stepMax;
    const visRange = visMax - visMin || 1;
    resolutionInfo = graphResolutionInfo(activeDig, visMin, visMax, W);

    const rowH = H / activeDig.length;
    activeDig.forEach((ch, ri) => {
      const yBase = ri * rowH;
      const yHigh = yBase + rowH * 0.12;
      const yLow  = yBase + rowH * 0.84;
      ctx.strokeStyle = ch.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      const buckets = graphBuildDigitalBuckets(ch.samples, ch.data, visMin, visMax, W);
      let previousBucket = null;
      const yFor = value => value ? yHigh : yLow;
      buckets.forEach(bucket => {
        const x = bucket.x + 0.5;
        if (previousBucket === null) {
          ctx.moveTo(x, yFor(bucket.enter));
        } else {
          ctx.lineTo(x, yFor(previousBucket.exit));
          if (previousBucket.exit !== bucket.enter) {
            ctx.lineTo(x, yFor(bucket.enter));
          }
        }
        if (bucket.sawHigh && bucket.sawLow) {
          ctx.lineTo(x, yHigh);
          ctx.lineTo(x, yLow);
          ctx.lineTo(x, yFor(bucket.exit));
        } else {
          ctx.lineTo(x, yFor(bucket.exit));
        }
        previousBucket = bucket;
      });
      ctx.stroke();
      ctx.fillStyle = ch.color;
      ctx.font = "8px Consolas, monospace";
      ctx.fillText(ch.label, 3, yBase + 9);
    });

    // Zoom indicator overlay
    if (signalGraph.view) {
      ctx.fillStyle = "rgba(255,255,255,0.15)";
      ctx.fillRect(W - 60, H - 12, 58, 10);
      ctx.fillStyle = "#aaa";
      ctx.font = "8px Consolas, monospace";
      const zoomPct = Math.round((visRange / (stepMax - stepMin || 1)) * 100);
      ctx.fillText(`zoom ${zoomPct}%`, W - 58, H - 4);
    }
  }

  _graphUpdateStepLabel(samples, resolutionInfo);
  _graphUpdateLegend(activeDig, "graph-legend");
}

function drawMemGraph() {
  const canvas = document.getElementById("mem-graph-canvas");
  if (!canvas) return;
  const W = canvas.offsetWidth;
  const H = canvas.offsetHeight;
  if (W === 0 || H === 0) return;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");

  // Background
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, W, H);

  // Faint vertical grid lines (8 divisions)
  ctx.strokeStyle = "#222";
  ctx.lineWidth = 1;
  for (let gx = 1; gx < 8; gx++) {
    const x = (gx / 8) * W;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }

  const activeAna = signalGraph.memChannels.map(ch => ({
    label: ch.label,
    color: ch.color,
    format: ch.format,
    snapshot: ch.snapshot || null,
  }));

  if (activeAna.length === 0) {
    _graphUpdateLegend([], "mgraph-legend");
    return;
  }

  // ---- Draw memory snapshot lanes ------------------------------------------
  const rowH = Math.floor(H / activeAna.length);
  activeAna.forEach((ch, ri) => {
    const yBase = ri * rowH;
    const yH    = rowH - 1;

    if (ri > 0) {
      ctx.strokeStyle = "#2a2a2a"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, yBase); ctx.lineTo(W, yBase); ctx.stroke();
    }

    ctx.fillStyle = ch.color;
    ctx.font = "8px Consolas, monospace";
    ctx.fillText(ch.label, 3, yBase + 9);

    const snap = ch.snapshot;
    if (!snap || snap.length < 2) {
      ctx.fillStyle = "#444";
      ctx.fillText("waiting…", 28, yBase + yH * 0.6);
      return;
    }

    let vMin = snap[0], vMax = snap[0];
    for (const v of snap) { if (v < vMin) vMin = v; if (v > vMax) vMax = v; }
    const vRange = vMax - vMin;
    const flat = vRange === 0;

    const fmtV = v => (ch.format === 'uint32' && v > 0xFFFF)
      ? '0x' + v.toString(16).toUpperCase()
      : v.toString();

    // Y-axis labels
    ctx.fillStyle = "#555";
    ctx.font = "8px Consolas, monospace";
    if (flat) {
      ctx.fillText(`= ${fmtV(vMin)}`, 28, yBase + yH * 0.5 + 4);
    } else {
      ctx.fillText(fmtV(vMax), 28, yBase + 9);
      ctx.fillText(fmtV(vMin), 28, yBase + yH - 2);
    }

    // Waveform — flat signals are drawn centred in the lane
    ctx.strokeStyle = ch.color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    const yMid = yBase + yH * 0.5;
    snap.forEach((v, i) => {
      const x = (i / (snap.length - 1)) * W;
      const y = flat ? yMid
        : yBase + (yH - 12) - ((v - vMin) / vRange) * (yH - 12) + 6;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });

  _graphUpdateLegend(activeAna, "mgraph-legend");
}

function _graphUpdateStepLabel(samples, resolutionInfo = null) {
  const el = document.getElementById("graph-step-label");
  if (!el) return;
  if (samples.length === 0) { el.textContent = ""; return; }
  const resolution = resolutionInfo
    ? ` · ${resolutionInfo.cyclesPerPixel.toFixed(2)} cycles/pixel${resolutionInfo.subPixel ? " · sub-pixel transitions" : ""}`
    : "";
  el.textContent = `step ${samples[samples.length - 1].step} / ${signalGraph.windowSize}${resolution}`;
}

function _graphUpdateLegend(channels, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = channels.map(ch =>
    `<div class="graph-legend-item">` +
    `<div class="graph-legend-swatch" style="background:${ch.color}"></div>` +
    `<span>${ch.label}</span></div>`
  ).join("");
}

// ---- Signal graph — memory channel management ------------------------------

function graphRequestSnapshots() {
  signalGraph.memChannels.forEach((ch, i) => {
    sendAction({
      action: "read_memory",
      addr: ch.addr,
      length: ch.count * (GRAPH_MEM_BPS[ch.format] || 4),
      tag: `graph-M${i}`,
    });
  });
}

function renderMemChannelRows() {
  const container = document.getElementById("graph-mem-channels");
  if (!container) return;
  container.innerHTML = "";
  signalGraph.memChannels.forEach((ch, i) => {
    const row = document.createElement("div");
    row.className = "graph-mem-row";
    row.dataset.index = i;

    const swatch = document.createElement("div");
    swatch.className = "graph-ch-swatch";
    swatch.style.background = ch.color;

    const lbl = document.createElement("span");
    lbl.className = "graph-ch-lbl";
    lbl.textContent = `M${i + 1}`;

    const addrInput = document.createElement("input");
    addrInput.type = "text";
    addrInput.className = "graph-ch-addr";
    addrInput.value = "0x" + ch.addr.toString(16).toUpperCase().padStart(8, "0");
    const applyAddr = () => {
      const v = parseInt(addrInput.value, 16);
      if (!isNaN(v)) {
        signalGraph.memChannels[i].addr = v >>> 0;
        addrInput.value = "0x" + (v >>> 0).toString(16).toUpperCase().padStart(8, "0");
        signalGraph.memChannels[i].snapshot = null;
        graphRequestSnapshots(); drawGraph();
      }
    };
    addrInput.addEventListener("change", applyAddr);
    addrInput.addEventListener("keydown", e => { if (e.key === "Enter") applyAddr(); });

    const cntInput = document.createElement("input");
    cntInput.type = "text";
    cntInput.className = "graph-ch-len";
    cntInput.value = ch.count;
    cntInput.title = "Number of samples";
    const applyCnt = () => {
      const v = parseInt(cntInput.value, 10);
      if (!isNaN(v) && v >= 1 && v <= 8192) {
        signalGraph.memChannels[i].count = v;
        signalGraph.memChannels[i].snapshot = null;
        graphRequestSnapshots(); drawGraph();
      }
    };
    cntInput.addEventListener("change", applyCnt);
    cntInput.addEventListener("keydown", e => { if (e.key === "Enter") applyCnt(); });

    const fmtSel = document.createElement("select");
    fmtSel.className = "graph-ch-fmt";
    ['uint8','uint16','uint32','int32'].forEach(f => {
      const opt = document.createElement("option");
      opt.value = f; opt.textContent = f;
      if (f === ch.format) opt.selected = true;
      fmtSel.appendChild(opt);
    });
    fmtSel.addEventListener("change", () => {
      signalGraph.memChannels[i].format = fmtSel.value;
      signalGraph.memChannels[i].snapshot = null;
      graphRequestSnapshots(); drawGraph();
    });

    const rm = document.createElement("span");
    rm.className = "graph-ch-remove";
    rm.textContent = "×";
    rm.addEventListener("click", () => removeMemChannel(i));

    row.append(swatch, lbl, addrInput, cntInput, fmtSel, rm);
    container.appendChild(row);
  });
}

function addMemChannel() {
  if (signalGraph.memChannels.length >= 8) return;
  const i = signalGraph.memChannels.length;
  signalGraph.memChannels.push({
    addr: 0x00010000,
    count: 64,
    format: 'uint32',
    color: GRAPH_MEM_COLORS[i % GRAPH_MEM_COLORS.length],
    label: `M${i + 1}`,
    snapshot: null,
  });
  renderMemChannelRows();
  graphRequestSnapshots();
  drawGraph();
}

function removeMemChannel(index) {
  signalGraph.memChannels.splice(index, 1);
  signalGraph.memChannels.forEach((ch, i) => { ch.label = `M${i + 1}`; });
  renderMemChannelRows();
}

// ---- Open-loop FOC workspace ----------------------------------------------

const FOC_Q_ONE = 1 << 24;
const FOC_PHASE_U32 = 0x100000000;
const FOC_POLE_PAIRS = 4;
// Decimate stored samples by theta (not by emission count) so the plot
// buffer holds a fixed resolution per electrical revolution regardless of
// motor speed -- a fast motor emits far more foc_state messages per
// revolution than a slow one at the server's fixed instruction cadence.
const FOC_POINTS_PER_REV = 256;
const FOC_MIN_THETA_STEP = FOC_PHASE_U32 / FOC_POINTS_PER_REV;
const FOC_SAMPLE_PERIOD_SECONDS = 0.0001;
const FOC_WINDOW_SECONDS = 0.1;
const FOC_MAX_SAMPLES = Math.ceil(FOC_WINDOW_SECONDS / FOC_SAMPLE_PERIOD_SECONDS) + 16;
const FOC_COLORS = {
  a: "#70d6b4",
  b: "#7eb8e8",
  c: "#e5a66d",
  alpha: "#da8ee6",
  beta: "#e4d27d",
};

function focQ24(value) {
  return Number(value || 0) / FOC_Q_ONE;
}

function focCanvas(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const width = Math.floor(rect.width);
  const height = Math.floor(rect.height);
  if (width <= 0 || height <= 0) return null;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pixelWidth = Math.max(1, Math.floor(width * dpr));
  const pixelHeight = Math.max(1, Math.floor(height * dpr));
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { canvas, ctx, width, height };
}

function focThemeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
  return value || fallback;
}

function focRotorAngle(value) {
  return ((Number(value || 0) >>> 0) / FOC_PHASE_U32) * Math.PI * 2;
}

function focNeedleEndpoint(angle, length) {
  return {
    x: length * Math.cos(angle),
    y: -Math.sin(angle) * length,
  };
}

function focFormatThroughput(clock) {
  const ratio = Number(clock?.sim_wall_ratio);
  if (!Number.isFinite(ratio)) return "--";
  const digits = ratio < 0.01 ? 4 : ratio < 0.1 ? 3 : ratio < 1 ? 2 : 1;
  const milliseconds = Number(clock?.simulated_ms_per_wall_second);
  const rate = Number.isFinite(milliseconds)
    ? ` (${milliseconds < 10 ? milliseconds.toFixed(2) : milliseconds.toFixed(1)} ms/s)`
    : "";
  return `${ratio.toFixed(digits)}x${rate}`;
}

function renderLegacyFocState(message) {
  const wasLoaded = !!focLatestState?.loaded;
  focLatestState = message;
  if (!message.loaded || (message.loaded && !wasLoaded)) focSamples = [];

  const status = document.getElementById("foc-runtime-status");
  const controlStatus = document.getElementById("foc-control-status");
  const pwm = message.pwm;
  const fb = message.fb;
  const model = message.model;
  const loaded = !!message.loaded;
  const runningNow = !!model?.running;

  if (status) {
    status.textContent = loaded
      ? (runningNow ? "Runtime active · IEP observer running" : "Runtime ready · observer stopped")
      : (message.status || "Firmware not loaded");
    status.style.color = loaded && runningNow ? "var(--green)" : "var(--text-dim)";
  }
  if (controlStatus) {
    controlStatus.textContent = loaded
      ? (runningNow ? "Streaming feedback from the PMSM plant." : "Ready · apply references and start.")
      : "Load firmware to begin.";
  }

  const start = document.getElementById("foc-start");
  const stop = document.getElementById("foc-stop");
  if (start) start.disabled = !loaded || runningNow;
  if (stop) stop.disabled = !loaded || !runningNow;

  const speedRpm = fb ? focQ24(fb.speed_rpm_q24) * 1000 : null;
  const theta = fb ? focRotorAngle(fb.rotor_theta_u32) : null;
  const dutyText = pwm
    ? [pwm.ta_q24, pwm.tb_q24, pwm.tc_q24]
      .map(value => focQ24(value).toFixed(3)).join(" / ")
    : "— / — / —";
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setText("foc-speed-readout", speedRpm === null ? "— RPM" : `${speedRpm.toFixed(1)} RPM`);
  setText("foc-theta-readout", theta === null ? "—°" : `${(theta * 180 / Math.PI).toFixed(1)}°`);
  setText("foc-duty-readout", dutyText);
  setText("foc-time-readout", fb ? `0x${Number(fb.timestamp || 0).toString(16).toUpperCase().padStart(8, "0")}` : "—");
  setText("foc-dial-state", loaded ? (runningNow ? "live" : "paused") : "waiting");

  if (pwm && fb) {
    const timestamp = Number(fb.timestamp || 0);
    const theta = Number(pwm.theta_cmd_u32 || 0) >>> 0;
    const previous = focSamples[focSamples.length - 1];
    const thetaAdvance = previous
      ? ((theta - previous.theta) % FOC_PHASE_U32 + FOC_PHASE_U32) % FOC_PHASE_U32
      : FOC_MIN_THETA_STEP;
    if (thetaAdvance >= FOC_MIN_THETA_STEP) {
      focSamples.push({
        timestamp,
        loop: Number(pwm.loop_counter || 0),
        theta,
        ta: focQ24(pwm.ta_q24),
        tb: focQ24(pwm.tb_q24),
        tc: focQ24(pwm.tc_q24),
        ia: focQ24(fb.ia_q24) * 10,
        ib: focQ24(fb.ib_q24) * 10,
        ic: focQ24(fb.ic_q24) * 10,
        valpha: focQ24(pwm.valpha_q24),
        vbeta: focQ24(pwm.vbeta_q24),
      });
      if (focSamples.length > FOC_MAX_SAMPLES) focSamples.shift();
    }
  }
  if (runningNow) scheduleFocDialAnimation();
}

function focLegacyAdaptiveWindow() {
  // Samples are now stored at a fixed theta spacing (one per
  // FOC_MIN_THETA_STEP of electrical revolution), so a target number of
  // revolutions is just a constant sample count -- no unwrapping needed.
  const TARGET_TURNS = 2;
  return Math.min(focSamples.length, TARGET_TURNS * FOC_POINTS_PER_REV);
}

function drawFocLineChart(canvasId, samples, series, minValue, maxValue) {
  const surface = focCanvas(canvasId);
  if (!surface) return;
  const { ctx, width: W, height: H } = surface;
  const inset = focThemeColor("--panel-inset", "#1b2127");
  const grid = focThemeColor("--border", "#39434d");
  const text = focThemeColor("--text-dim", "#8996a1");
  ctx.fillStyle = inset;
  ctx.fillRect(0, 0, W, H);

  const left = 34, right = 8, top = 12, bottom = 18;
  const plotW = Math.max(1, W - left - right);
  const plotH = Math.max(1, H - top - bottom);
  ctx.strokeStyle = grid;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = top + (i / 4) * plotH + 0.5;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(W - right, y); ctx.stroke();
  }
  for (let i = 1; i < 6; i++) {
    const x = left + (i / 6) * plotW + 0.5;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, H - bottom); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const formatAxis = value => Math.abs(value) >= 10 ? value.toFixed(0) : value.toFixed(2);
  ctx.fillStyle = text;
  ctx.font = "9px Consolas, monospace";
  ctx.fillText(formatAxis(maxValue), 3, top + 3);
  ctx.fillText(formatAxis(minValue), 3, H - bottom + 1);
  if (minValue < 0 && maxValue > 0) {
    const y = top + (maxValue / (maxValue - minValue)) * plotH + 0.5;
    ctx.strokeStyle = text;
    ctx.globalAlpha = 0.45;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(W - right, y); ctx.stroke();
    ctx.globalAlpha = 1;
  }

  const labelWidth = series.reduce((total, lane) => total + lane.label.length * 6 + 14, 0);
  let labelX = Math.max(left, W - labelWidth - 4);
  series.forEach(lane => {
    ctx.fillStyle = lane.color;
    ctx.fillRect(labelX, 3, 6, 6);
    ctx.fillText(lane.label, labelX + 9, 9);
    labelX += lane.label.length * 6 + 14;
  });

  if (samples.length < 2) {
    ctx.fillStyle = text;
    ctx.fillText("waiting for foc_state…", left + 5, top + plotH / 2);
    return;
  }

  const yFor = value => top + (maxValue - value) / (maxValue - minValue) * plotH;
  series.forEach(lane => {
    ctx.strokeStyle = lane.color;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    const firstTimestamp = Number(samples[0]?.timestamp || 0);
    const lastTimestamp = Number(samples[samples.length - 1]?.timestamp || firstTimestamp);
    const timestampSpan = Math.max(1, lastTimestamp - firstTimestamp);
    samples.forEach((sample, index) => {
      const timestamp = Number(sample.timestamp ?? firstTimestamp);
      const x = left + Math.max(0, Math.min(1,
        (timestamp - firstTimestamp) / timestampSpan)) * plotW;
      const y = yFor(lane.value(sample));
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

function drawFocDial() {
  const surface = focCanvas("foc-rotor-dial");
  if (!surface) return;
  const { ctx, width: W, height: H } = surface;
  const bg = focThemeColor("--panel-inset", "#1b2127");
  const border = focThemeColor("--border-strong", "#707d88");
  const dim = focThemeColor("--text-dim", "#8996a1");
  const cx = W / 2;
  const cy = H / 2;
  const radius = Math.max(30, Math.min(W, H) * 0.38);
  const fb = focLatestState?.fb;
  const pwm = focLatestState?.pwm;
  const rotor = fb ? focRotorAngle(fb.rotor_theta_u32) : 0;
  const command = pwm ? focRotorAngle(pwm.theta_cmd_u32) : 0;

  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.translate(cx, cy);
  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  ctx.globalAlpha = 0.65;
  ctx.beginPath(); ctx.arc(0, 0, radius, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(0, 0, radius * 0.72, 0, Math.PI * 2); ctx.stroke();
  ctx.globalAlpha = 1;

  for (let pole = 0; pole < FOC_POLE_PAIRS * 2; pole++) {
    ctx.save();
    ctx.rotate(pole * Math.PI / FOC_POLE_PAIRS);
    ctx.fillStyle = pole % 2 ? "#da8ee6" : "#7eb8e8";
    ctx.globalAlpha = 0.6;
    ctx.fillRect(-5, -radius * 0.92, 10, radius * 0.18);
    ctx.restore();
  }
  ctx.globalAlpha = 1;

  const needle = (angle, length, color, lineWidth, dash) => {
    const endpoint = focNeedleEndpoint(angle, length);
    ctx.save();
    if (dash) ctx.setLineDash([5, 4]);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(endpoint.x, endpoint.y); ctx.stroke();
    if (!dash) {
      ctx.fillStyle = color;
      const direction = Math.atan2(endpoint.y, endpoint.x);
      const left = direction + Math.PI - Math.PI / 7;
      const right = direction + Math.PI + Math.PI / 7;
      ctx.beginPath();
      ctx.moveTo(endpoint.x, endpoint.y);
      ctx.lineTo(endpoint.x + 9 * Math.cos(left), endpoint.y + 9 * Math.sin(left));
      ctx.lineTo(endpoint.x + 9 * Math.cos(right), endpoint.y + 9 * Math.sin(right));
      ctx.closePath(); ctx.fill();
    }
    ctx.restore();
  };
  needle(command, radius * 0.82, "#da8ee6", 1.5, true);
  needle(rotor, radius * 0.68, "#70d6b4", 3, false);
  ctx.fillStyle = "#70d6b4";
  ctx.beginPath(); ctx.arc(0, 0, 5, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = dim;
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(-radius - 8, 0); ctx.lineTo(radius + 8, 0); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, -radius - 8); ctx.lineTo(0, radius + 8); ctx.stroke();
  ctx.restore();

  ctx.fillStyle = dim;
  ctx.font = "9px Consolas, monospace";
  ctx.fillText("0°", cx + radius + 4, cy + 3);
  ctx.fillText("90°", cx - 11, cy - radius - 8);
  ctx.fillText("180°", cx - radius - 30, cy + 3);
  ctx.fillText("270°", cx - 14, cy + radius + 15);
}

function drawFocWorkspace() {
  const duties = [
    { label: "Ta", color: FOC_COLORS.a, value: sample => sample.ta },
    { label: "Tb", color: FOC_COLORS.b, value: sample => sample.tb },
    { label: "Tc", color: FOC_COLORS.c, value: sample => sample.tc },
  ];
  const currents = [
    { label: "Ia", color: FOC_COLORS.a, value: sample => sample.ia },
    { label: "Ib", color: FOC_COLORS.b, value: sample => sample.ib },
    { label: "Ic", color: FOC_COLORS.c, value: sample => sample.ic },
  ];
  const voltages = [
    { label: "Vα", color: FOC_COLORS.alpha, value: sample => sample.valpha },
    { label: "Vβ", color: FOC_COLORS.beta, value: sample => sample.vbeta },
  ];
  const visible = focSamples.slice(-focAdaptiveWindow());
  let currentPeak = 1;
  visible.forEach(sample => {
    currents.forEach(lane => { currentPeak = Math.max(currentPeak, Math.abs(lane.value(sample))); });
  });
  drawFocDial();
  drawFocLineChart("foc-duty-plot", visible, duties, 0, 1);
  drawFocLineChart("foc-current-plot", visible, currents, -currentPeak * 1.15, currentPeak * 1.15);
  drawFocLineChart("foc-voltage-plot", visible, voltages, -1, 1);
}

function scheduleFocDialAnimation() {
  // Redraw only from the latest timestamped simulator state.  The needles
  // must not advance on a browser animation clock between simulation samples.
  drawFocDial();
}

// The FOC stream is timestamp-driven.  The runtime batches 100 us samples
// and publishes those batches at roughly 30 Hz, so zero-speed and reverse
// motion remain visible instead of being discarded by an angle-delta filter.
function focAdaptiveWindow() {
  return focSamples.length;
}

function renderFocState(message) {
  const wasLoaded = !!focLatestState?.loaded;
  const sessionChanged = focLatestState?.session_id &&
    focLatestState.session_id !== message.session_id;
  focLatestState = message;
  if (!message.loaded || (message.loaded && !wasLoaded) || sessionChanged) {
    focSamples = [];
  }

  const setText = (id, value) => {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  };
  const pwm = message.pwm;
  const fb = message.fb;
  const model = message.model;
  const telemetry = message.telemetry || {};
  const clock = message.clock || {};
  const loaded = !!message.loaded;
  const runningNow = !!model?.running;
  const speedRpm = Number.isFinite(telemetry.measured_speed_rpm)
    ? Number(telemetry.measured_speed_rpm)
    : (fb ? focQ24(fb.speed_rpm_q24) * 1000 : null);

  const status = document.getElementById("foc-runtime-status");
  const controlStatus = document.getElementById("foc-control-status");
  if (status) {
    status.textContent = loaded
      ? (runningNow ? "Runtime active - IEP observer running" : "Runtime ready - observer stopped")
      : (message.status || "Firmware not loaded");
    status.style.color = loaded && runningNow ? "var(--green)" : "var(--text-dim)";
  }
  if (controlStatus) {
    controlStatus.textContent = loaded
      ? (runningNow ? "Streaming feedback from the PMSM plant." : "Ready - apply references and start.")
      : "Load firmware to begin.";
  }
  const start = document.getElementById("foc-start");
  const stop = document.getElementById("foc-stop");
  if (start) start.disabled = !loaded || runningNow;
  if (stop) stop.disabled = !loaded || !runningNow;

  setText("foc-speed-readout", speedRpm === null ? "-- RPM" : `${speedRpm.toFixed(1)} RPM`);
  setText("foc-requested-speed-readout", Number.isFinite(telemetry.requested_speed_rpm)
    ? `${Number(telemetry.requested_speed_rpm).toFixed(1)} RPM` : "-- RPM");
  setText("foc-ramped-speed-readout", Number.isFinite(telemetry.ramped_speed_rpm)
    ? `${Number(telemetry.ramped_speed_rpm).toFixed(1)} RPM` : "-- RPM");
  const rotor = fb ? focRotorAngle(fb.rotor_theta_u32) : null;
  setText("foc-theta-readout", rotor === null ? "-- deg" : `${(rotor * 180 / Math.PI).toFixed(1)} deg`);
  setText("foc-angle-error-readout", Number.isFinite(telemetry.angle_error_deg)
    ? `${Number(telemetry.angle_error_deg).toFixed(1)} deg` : "-- deg");
  setText("foc-duty-readout", pwm
    ? [pwm.ta_q24, pwm.tb_q24, pwm.tc_q24].map(value => focQ24(value).toFixed(3)).join(" / ")
    : "-- / -- / --");
  setText("foc-time-readout", Number.isFinite(clock.sim_time_s)
    ? `${Number(clock.sim_time_s).toFixed(4)} s` : "--");
  const formatClock = value => Number.isFinite(Number(value))
    ? `${(Number(value) / 1e6).toFixed(3)} MHz`
    : "--";
  setText("foc-pru-clock-readout", formatClock(clock.pru_clock_hz));
  setText("foc-iep-clock-readout", formatClock(clock.iep_clock_hz ?? clock.iep_hz));
  const controlLoopHz = clock.control_loop_frequency_hz ?? clock.loop_frequency_hz;
  setText("foc-loop-frequency-readout", Number.isFinite(Number(controlLoopHz))
    ? `${Number(controlLoopHz).toFixed(0)} Hz` : "-- Hz");
  setText("foc-sim-wall-readout", focFormatThroughput(clock));
  const statusFlags = Number(pwm?.status || 0);
  const faultText = message.fault
    ? (message.fault.error || "firmware fault")
    : (statusFlags & 4 ? "deadline miss" : "none");
  setText("foc-fault-readout", faultText);
  setText("foc-dial-state", loaded ? (runningNow ? "live" : "paused") : "waiting");

  const append = sample => {
    if (!sample) return;
    const timestamp = Number(sample.timestamp ?? fb?.timestamp ?? 0);
    const previous = focSamples[focSamples.length - 1];
    if (previous && timestamp === previous.timestamp) return;
    focSamples.push({
      timestamp,
      loop: Number(sample.loop_counter || pwm?.loop_counter || 0),
      theta: Number(sample.theta_cmd_u32 ?? pwm?.theta_cmd_u32 ?? 0) >>> 0,
      ta: focQ24(sample.ta_q24 ?? pwm?.ta_q24),
      tb: focQ24(sample.tb_q24 ?? pwm?.tb_q24),
      tc: focQ24(sample.tc_q24 ?? pwm?.tc_q24),
      ia: focQ24(sample.ia_q24 ?? fb?.ia_q24) * 10,
      ib: focQ24(sample.ib_q24 ?? fb?.ib_q24) * 10,
      ic: focQ24(sample.ic_q24 ?? fb?.ic_q24) * 10,
      valpha: focQ24(sample.valpha_q24 ?? pwm?.valpha_q24),
      vbeta: focQ24(sample.vbeta_q24 ?? pwm?.vbeta_q24),
    });
  };
  const samples = Array.isArray(message.samples) ? message.samples : [];
  samples.forEach(append);
  if (pwm && fb && samples.length === 0) {
    append({
      timestamp: fb.timestamp,
      loop_counter: pwm.loop_counter,
      theta_cmd_u32: pwm.theta_cmd_u32,
      ta_q24: pwm.ta_q24,
      tb_q24: pwm.tb_q24,
      tc_q24: pwm.tc_q24,
      ia_q24: fb.ia_q24,
      ib_q24: fb.ib_q24,
      ic_q24: fb.ic_q24,
      valpha_q24: pwm.valpha_q24,
      vbeta_q24: pwm.vbeta_q24,
    });
  }
  const now = Number(model?.timestamp ?? fb?.timestamp ?? 0);
  const windowTicks = Number(clock.iep_hz || 0) * FOC_WINDOW_SECONDS;
  if (windowTicks > 0) {
    focSamples = focSamples.filter(sample => now - sample.timestamp <= windowTicks);
  }
  while (focSamples.length > FOC_MAX_SAMPLES) focSamples.shift();
  if (runningNow) scheduleFocDialAnimation();
}

function initFocControls() {
  const form = document.getElementById("foc-reference-form");
  const speed = document.getElementById("foc-speed-rpm");
  const speedPu = document.getElementById("foc-speed-pu");
  const load = document.getElementById("foc-load");
  const start = document.getElementById("foc-start");
  const stop = document.getElementById("foc-stop");
  const controlStatus = document.getElementById("foc-control-status");
  const updateSpeed = () => {
    if (speedPu) speedPu.textContent = `${(Number(speed?.value || 0) / 1000).toFixed(3)} pu`;
  };
  speed?.addEventListener("input", updateSpeed);
  updateSpeed();

  load?.addEventListener("click", () => {
    if (controlStatus) controlStatus.textContent = "Loading open-loop firmware…";
    sendAction({ action: "foc_load", filename: "foc_open_loop/foc_open_loop.asm" });
  });
  form?.addEventListener("submit", event => {
    event.preventDefault();
    const sent = sendAction({
      action: "foc_set_reference",
      speed_rpm: Number(speed?.value || 0),
      vd_ref: Number(document.getElementById("foc-id-ref")?.value || 0),
      vq_ref: Number(document.getElementById("foc-iq-ref")?.value || 0),
      acceleration_rpm_s: Number(document.getElementById("foc-ramp-rate")?.value || 0),
    });
    if (sent && controlStatus) controlStatus.textContent = "References staged for the next control commit.";
  });
  const startFoc = () => {
    const sent = sendAction({
      action: "foc_start",
      speed_rpm: Number(speed?.value || 0),
      vd_ref: Number(document.getElementById("foc-id-ref")?.value || 0),
      vq_ref: Number(document.getElementById("foc-iq-ref")?.value || 0),
      acceleration_rpm_s: Number(document.getElementById("foc-ramp-rate")?.value || 0),
    });
    if (sent && controlStatus) controlStatus.textContent = "Starting the IEP-clocked plant…";
  };
  start?.addEventListener("click", startFoc);
  stop?.addEventListener("click", () => {
    if (sendAction({ action: "foc_stop" }) && controlStatus) controlStatus.textContent = "Stopping the plant observer…";
  });
}

// ---- Signal graph — CSV export ---------------------------------------------

function exportGraphCSV() {
  const samples = graphGetSamples();
  if (samples.length === 0) return;

  const gpoHeaders = Array.from({ length: 20 }, (_, i) => `gpo${i}`);
  const gpiHeaders = Array.from({ length: 20 }, (_, i) => `gpi${i}`);
  const perifHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_out`);
  const perifOeHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_out_en`);
  const perifClkHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_clk`);
  const header = ["step", "core", "mode", ...gpoHeaders, ...gpiHeaders,
                  ...perifHeaders, ...perifOeHeaders, ...perifClkHeaders].join(",");

  const rows = samples.map(s => {
    const gpo = Array.from({ length: 20 }, (_, i) => s.gpo[i] ?? 0);
    const gpi = Array.from({ length: 20 }, (_, i) => s.gpi[i] ?? 0);
    const perif = Array.from({ length: 3 }, (_, i) => (s.perif && s.perif[i]) ?? 0);
    const perifOe = Array.from({ length: 3 }, (_, i) => (s.perifOe && s.perifOe[i]) ?? 0);
    const perifClk = Array.from({ length: 3 }, (_, i) => (s.perifClk && s.perifClk[i]) ?? 0);
    return [s.step, s.core ?? "pru0", s.mode ?? "gpio", ...gpo, ...gpi,
            ...perif, ...perifOe, ...perifClk].join(",");
  });

  // Append memory channel snapshots as separate blocks after the signal rows
  const memBlocks = signalGraph.memChannels.map((ch, ci) => {
    if (!ch.snapshot) return "";
    const addrHex = "0x" + ch.addr.toString(16).toUpperCase().padStart(8, "0");
    const hdr = `\n# M${ci + 1} ${addrHex} ${ch.count}×${ch.format}\nindex,value`;
    const dataRows = ch.snapshot.map((v, i) => `${i},${v}`).join("\n");
    return hdr + "\n" + dataRows;
  }).filter(Boolean).join("\n");

  const csv = [header, ...rows].join("\n") + memBlocks;
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const now = new Date();
  const ts = now.getFullYear().toString() +
    String(now.getMonth() + 1).padStart(2, "0") +
    String(now.getDate()).padStart(2, "0") + "-" +
    String(now.getHours()).padStart(2, "0") +
    String(now.getMinutes()).padStart(2, "0") +
    String(now.getSeconds()).padStart(2, "0");
  const a = document.createElement("a");
  a.href = url;
  a.download = `pru-trace-${ts}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---- Button handlers ------------------------------------------------------

coreSelect.addEventListener("change", () => {
  stopRun(); stopSim();
  graphClear();
  // Reset loopback state on the core we're leaving
  for (let g = 0; g < 5; g++) {
    sendAction({ action: 'set_loopback', core: currentCore, group: g, enabled: false });
  }
  // Detach any I2C device left attached on the core we're leaving
  sendAction({ action: 'i2c_attach', core: currentCore, enabled: false });
  document.getElementById('i2c-attach-btn')?.classList.remove('active');
  currentCore = coreSelect.value;
  prevRegisters = new Array(32).fill("0x00000000");
  document.querySelectorAll('#loopback-strip .lb-btn').forEach(b => b.classList.remove('active'));
  initSpadState();
  sourceList.innerHTML = "";
  _sourceInstructions = [];
  _sourceLabels = {};
  _lastSourceBreakpointKey = null;
  _currentSourceLine = null;
  sendAction({ action: "get_state", core: currentCore });
  updateCtableForCore();
});

btnStep.addEventListener("click", () => {
  stopRun(); stopSim();
  if (genericSsiLoaded) {
    if (genericSsiRunInFlight) return;
    const request_id = "ssi-runtime-step-" + nextRunRequestId++;
    const sent = sendAction({
      action: "run_multicore",
      core: "pru1",
      partner: "pru0",
      max_steps: 1,
      capture: signalGraph.recording,
      request_id,
    });
    if (sent) {
      genericSsiRunInFlight = true;
      genericSsiRequestId = request_id;
      trackRunRequest(request_id);
    }
  } else if (multiCoreMode) {
    sendAction({ action: "run_multicore", core: "pru0", partner: mcPartner,
                 max_steps: 1 });
  } else {
    sendAction({ action: "step", core: currentCore, count: 1 });
  }
});


btnRun.addEventListener("click", () => {
  if (running) {
    stopRun();
  } else {
    startRun();
  }
});

btnReset.addEventListener("click", () => {
  stopRun(); stopSim();
  cancelAllRunRequests();
  graphClear();
  clearErrors();
  prevRegisters = new Array(32).fill("0x00000000");
  if (genericSsiLoaded) {
    mcPrevRegs = {
      pru0: new Array(32).fill("0x00000000"),
      rtu0: new Array(32).fill("0x00000000"),
      pru1: new Array(32).fill("0x00000000"),
    };
    sendAction({ action: "reset", core: "pru1" });
    sendAction({ action: "reset", core: "pru0" });
    sendAction({ action: "ssi_runtime_read" });
  } else if (multiCoreMode) {
    mcPrevRegs = {
      pru0: new Array(32).fill("0x00000000"),
      rtu0: new Array(32).fill("0x00000000"),
      pru1: new Array(32).fill("0x00000000"),
    };
    sendAction({ action: "reset", core: "pru0" });
    sendAction({ action: "reset", core: mcPartner });
  } else {
    sendAction({ action: "reset", core: currentCore });
  }
});

btnHardReset.addEventListener("click", () => {
  stopRun(); stopSim();
  cancelAllRunRequests();
  graphClear();
  clearErrors();
  prevRegisters = new Array(32).fill("0x00000000");
  if (genericSsiLoaded) {
    genericSsiLoaded = false;
    genericSsiRunInFlight = false;
    mcPrevRegs = {
      pru0: new Array(32).fill("0x00000000"),
      rtu0: new Array(32).fill("0x00000000"),
      pru1: new Array(32).fill("0x00000000"),
    };
    sendAction({ action: "hard_reset", core: "pru1" });
    // Hard reset clears the runtime object as well as the cores.  Reload the
    // complete pair so the normal Run and Apply controls remain usable.
    sendAction({ action: "ssi_runtime_load" });
  } else if (multiCoreMode) {
    mcPrevRegs = {
      pru0: new Array(32).fill("0x00000000"),
      rtu0: new Array(32).fill("0x00000000"),
      pru1: new Array(32).fill("0x00000000"),
    };
    sendAction({ action: "hard_reset", core: "pru0" });
    sendAction({ action: "get_state", core: mcPartner });
  } else {
    sendAction({ action: "hard_reset", core: currentCore });
  }
});

btnLoad.addEventListener("click", async () => {
  stopRun(); stopSim();
  graphClear();
  clearErrors();

  // Sync active textarea content into tab buffer
  if (activeTab >= 0 && tabs[activeTab]) {
    tabs[activeTab].content = asmSource.value;
  }

  // Auto-save all dirty tabs
  for (let i = 0; i < tabs.length; i++) {
    const tab = tabs[i];
    if (tab.dirty) {
      try {
        await fetch(`/source/${tab.path}`, { method: "PUT", body: tab.content });
        tab.dirty = false;
      } catch (e) { console.error(`Auto-save failed for ${tab.path}:`, e); }
    }
  }
  renderTabs();

  // Determine source text and filename for include path resolution
  let source = asmSource.value;
  let filename;
  if (activeTab >= 0 && tabs[activeTab]) {
    const activeTabPath = tabs[activeTab].path;
    const projName = activeTabPath.includes('/') ? activeTabPath.split('/')[0] : null;

    if (projName && activeProjectManifest &&
        activeProjectManifest.project === projName &&
        Array.isArray(activeProjectManifest.files)) {
      // Concatenate tabs in manifest order
      const parts = activeProjectManifest.files.map(fname => {
        const path = `${projName}/${fname}`;
        const t = tabs.find(tab => tab.path === path);
        return t ? t.content : '';
      });
      source = parts.join('\n');
      filename = `${projName}/${activeProjectManifest.files[0]}`;
    } else {
      source = asmSource.value;
      filename = activeTabPath;
    }
  }

  if (multiCoreMode) {
    const targetCore = mcLoadCore.value || "pru0";
    mcPrevRegs[targetCore] = new Array(32).fill("0x00000000");
    mcSourceInstructions[targetCore] = [];
    mcSourceLabels[targetCore] = {};
    mcLastSourceBreakpointKey[targetCore] = null;
    const srcList = document.getElementById(`mc-${targetCore}-source-list`);
    if (srcList) srcList.innerHTML = "";
    sendAction({ action: "load", core: targetCore, source, filename });
  } else {
    prevRegisters = new Array(32).fill("0x00000000");
    _sourceInstructions = [];
    _sourceLabels = {};
    _lastSourceBreakpointKey = null;
    sourceList.innerHTML = "";
    sendAction({ action: "load", core: currentCore, source, filename });
  }
});

// ---- Signal graph controls -------------------------------------------------

function graphSetRecording(on) {
  signalGraph.recording = on;
  if (!on) pendingGraphCaptures.clear();
  const btn = document.getElementById("graph-rec-btn");
  const dot = document.getElementById("graph-rec-dot");
  if (btn) btn.classList.toggle("rec-on", on);
  if (dot) dot.classList.toggle("active", on);
}

function graphClear() {
  signalGraph.buf = new Array(signalGraph.windowSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;
  signalGraph.view = null;
  pendingGraphCaptures.clear();
  const exportRow = document.getElementById("graph-export-row");
  if (exportRow) exportRow.style.display = "none";
}

document.getElementById("graph-rec-btn").addEventListener("click", () => {
  graphSetRecording(!signalGraph.recording);
});

document.getElementById("graph-clear-btn").addEventListener("click", () => {
  graphClear();
  drawGraph();
});

document.getElementById("graph-fit-frame-btn").addEventListener("click", () => {
  const frame = graphFindNewestSsiFrame(graphGetSamples(), "pru0", 0);
  if (!frame) {
    const label = document.getElementById("graph-step-label");
    if (label) label.textContent = "No complete SSI frame in capture";
    return;
  }
  signalGraph.view = frame;
  _graphSetCursor();
  drawDigitalGraph();
});

document.getElementById("graph-win-sel").addEventListener("change", (e) => {
  graphResizeWindow(parseInt(e.target.value, 10));
  drawGraph();
});

// ---- Signal graph — zoom / pan event listeners ---------------------------
(function() {
  const canvas = document.getElementById("signal-graph-canvas");
  if (!canvas) return;

  // Mouse wheel: zoom
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const fracX = (e.clientX - rect.left) / rect.width;
    const factor = e.deltaY > 0 ? 1.3 : (1 / 1.3);
    graphViewZoom(factor, fracX);
  }, { passive: false });

  // Double-click: reset zoom
  canvas.addEventListener("dblclick", () => {
    graphViewReset();
  });

  // Drag to pan
  canvas.addEventListener("mousedown", (e) => {
    const rect = canvas.getBoundingClientRect();
    const v = signalGraph.view;
    if (!v) return; // can't pan when not zoomed (view is null)
    signalGraph._dragStart = { clientX: e.clientX, view: { ...v } };
    canvas.style.cursor = "grabbing";
  });

  window.addEventListener("mousemove", (e) => {
    const ds = signalGraph._dragStart;
    if (!ds) return;
    const rect = canvas.getBoundingClientRect();
    const dFrac = (ds.clientX - e.clientX) / rect.width;
    const range = ds.view.maxStep - ds.view.minStep;
    const samples = graphGetSamples();
    if (samples.length < 2) return;
    const allSteps = samples.map(s => s.runStep ?? s.step);
    const dataMin = Math.min(...allSteps);
    const dataMax = Math.max(...allSteps);
    let newMin = ds.view.minStep + dFrac * range;
    let newMax = ds.view.maxStep + dFrac * range;
    if (newMin < dataMin) { newMin = dataMin; newMax = newMin + range; }
    if (newMax > dataMax) { newMax = dataMax; newMin = newMax - range; }
    signalGraph.view = { minStep: newMin, maxStep: newMax };
    drawDigitalGraph();
  });

  window.addEventListener("mouseup", () => {
    if (signalGraph._dragStart) {
      signalGraph._dragStart = null;
      _graphSetCursor();
    }
  });
})();

document.getElementById("graph-mem-refresh-btn").addEventListener("click", () => {
  graphRequestSnapshots();
  drawGraph();
});

document.getElementById("graph-taller-btn").addEventListener("click", () => {
  if (signalGraph.heightIdx < GRAPH_HEIGHT_STEPS.length - 1) {
    signalGraph.heightIdx++;
    document.getElementById("signal-graph-canvas").closest(".graph-canvas-wrap").style.height =
      GRAPH_HEIGHT_STEPS[signalGraph.heightIdx] + "px";
    drawDigitalGraph();
  }
});

document.getElementById("graph-shorter-btn").addEventListener("click", () => {
  if (signalGraph.heightIdx > 0) {
    signalGraph.heightIdx--;
    document.getElementById("signal-graph-canvas").closest(".graph-canvas-wrap").style.height =
      GRAPH_HEIGHT_STEPS[signalGraph.heightIdx] + "px";
    drawDigitalGraph();
  }
});

document.getElementById("mgraph-taller-btn").addEventListener("click", () => {
  if (signalGraph.memHeightIdx < GRAPH_HEIGHT_STEPS.length - 1) {
    signalGraph.memHeightIdx++;
    document.getElementById("mgraph-canvas-wrap").style.height =
      GRAPH_HEIGHT_STEPS[signalGraph.memHeightIdx] + "px";
    drawMemGraph();
  }
});

document.getElementById("mgraph-shorter-btn").addEventListener("click", () => {
  if (signalGraph.memHeightIdx > 0) {
    signalGraph.memHeightIdx--;
    document.getElementById("mgraph-canvas-wrap").style.height =
      GRAPH_HEIGHT_STEPS[signalGraph.memHeightIdx] + "px";
    drawMemGraph();
  }
});

document.getElementById("graph-add-ch-btn").addEventListener("click", () => {
  addMemChannel();
});

// ---- Signal graph — drag-to-resize ------------------------------------------
(function () {
  const handle = document.getElementById("graph-resize-handle");
  const wrap   = handle.closest(".graph-canvas-wrap");
  const MIN_H  = 60;
  const MAX_H  = 1400;
  let startY, startH;

  handle.addEventListener("mousedown", (e) => {
    startY = e.clientY;
    startH = wrap.offsetHeight;
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";

    function onMove(e) {
      const newH = Math.max(MIN_H, Math.min(MAX_H, startH + (e.clientY - startY)));
      wrap.style.height = newH + "px";
      signalGraph.heightIdx = GRAPH_HEIGHT_STEPS.reduce((best, h, i) =>
        Math.abs(h - newH) < Math.abs(GRAPH_HEIGHT_STEPS[best] - newH) ? i : best, 0);
      drawDigitalGraph();
    }

    function onUp() {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    e.preventDefault();
  });
}());

// ---- Memory graph — drag-to-resize ------------------------------------------
(function () {
  const handle = document.getElementById("mgraph-resize-handle");
  const wrap   = document.getElementById("mgraph-canvas-wrap");
  const MIN_H  = 60;
  const MAX_H  = 1400;
  let startY, startH;

  handle.addEventListener("mousedown", (e) => {
    startY = e.clientY;
    startH = wrap.offsetHeight;
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";

    function onMove(e) {
      const newH = Math.max(MIN_H, Math.min(MAX_H, startH + (e.clientY - startY)));
      wrap.style.height = newH + "px";
      signalGraph.memHeightIdx = GRAPH_HEIGHT_STEPS.reduce((best, h, i) =>
        Math.abs(h - newH) < Math.abs(GRAPH_HEIGHT_STEPS[best] - newH) ? i : best, 0);
      drawMemGraph();
    }

    function onUp() {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    }

    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    e.preventDefault();
  });
}());

document.getElementById("graph-export-btn").addEventListener("click", () => {
  exportGraphCSV();
});

// File picker
const btnFile = document.getElementById("btn-file");
const fileInput = document.getElementById("file-input");

// Dropdown populated from server's /source directory
btnFile.addEventListener("click", async () => {
  document.getElementById("_src-menu")?.remove();
  let sourceData = { files: [], lib: [], projects: {} };
  try { sourceData = await fetch("/source").then(r => r.json()); } catch (_) {}

  const allFiles = [
    ...sourceData.files,
    ...sourceData.lib.map(f => 'lib/' + f),
  ];
  if (allFiles.length === 0) { fileInput.click(); return; }

  const menu = document.createElement("div");
  menu.id = "_src-menu";
  menu.style.cssText = "position:fixed;background:var(--panel);border:1px solid var(--border);border-radius:5px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.55);font-size:12px;";
  const rect = btnFile.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  // "Browse..." item at top — opens native file picker for .asm/.out files
  const browseItem = document.createElement("div");
  browseItem.style.cssText = "padding:6px 12px;cursor:pointer;color:var(--accent);border-bottom:1px solid var(--border);font-style:italic;";
  browseItem.textContent = "Browse file system...";
  browseItem.addEventListener("mouseover", () => browseItem.style.background = "var(--highlight)");
  browseItem.addEventListener("mouseout",  () => browseItem.style.background = "");
  browseItem.addEventListener("click", () => { menu.remove(); fileInput.click(); });
  menu.appendChild(browseItem);

  allFiles.forEach(path => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:var(--text);";
    item.textContent = path;
    item.addEventListener("mouseover", () => item.style.background = "var(--highlight)");
    item.addEventListener("mouseout",  () => item.style.background = "");
    item.addEventListener("click", async () => {
      menu.remove();
      activeProjectManifest = null;
      try {
        const text = await fetch(`/source/${path}`).then(r => r.text());
        openFileAsTab(path, text);
      } catch (e) { console.error(e); }
    });
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  const close = (e) => {
    if (!menu.contains(e.target) && e.target !== btnFile) {
      menu.remove(); document.removeEventListener("click", close);
    }
  };
  setTimeout(() => document.addEventListener("click", close), 0);
});

btnSaveAsm.addEventListener("click", async () => {
  if (activeTab < 0 || !tabs[activeTab]) return;
  tabs[activeTab].content = asmSource.value;
  const path = tabs[activeTab].path;
  try {
    const res = await fetch(`/source/${path}`, { method: "PUT", body: asmSource.value });
    if (res.ok) {
      tabs[activeTab].dirty = false;
      renderTabs();
      flashStatus("SAVED", "reloaded");
    } else {
      const d = await res.json();
      console.error("Save failed:", d.error);
    }
  } catch (e) { console.error(e); }
});

fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;

  // Binary .out ELF files — load directly into simulator
  if (file.name.toLowerCase().endsWith('.out')) {
    const elfName = file.name;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const bytes = new Uint8Array(ev.target.result);
      const b64 = btoa(String.fromCharCode(...bytes));
      const core = multiCoreMode
        ? document.getElementById("mc-load-core").value
        : currentCore;
      graphClear();
      if (multiCoreMode) {
        mcSourceInstructions[core] = [];
        mcSourceLabels[core] = {};
      } else {
        _sourceInstructions = [];
        _sourceLabels = {};
      }
      sendAction({ action: "load_elf", core, data: b64 });
      // Show loaded filename in source panel title
      const srcTitle = document.querySelector('#source-panel .panel-title');
      if (srcTitle) {
        const span = srcTitle.querySelector('span') || srcTitle;
        span.textContent = 'Source: ' + elfName;
      }
    };
    reader.readAsArrayBuffer(file);
    fileInput.value = "";
    return;
  }

  // Text assembly files — open in editor tab
  const reader = new FileReader();
  reader.onload = (ev) => {
    activeProjectManifest = null;
    openFileAsTab(file.name, ev.target.result);
  };
  reader.readAsText(file);
  fileInput.value = "";
});

// ---- Open Project ----------------------------------------------------------

async function openProject(projName, projFiles) {
  let manifest = null;
  try {
    const res = await fetch(`/source/${projName}/project.json`);
    if (res.ok) {
      const m = await res.json();
      if (Array.isArray(m.files)) manifest = m;
    }
  } catch (_) {}

  const asmFiles = manifest
    ? manifest.files
    : projFiles.filter(f => f.endsWith('.asm') || f.endsWith('.s')).sort();
  const incFiles = projFiles.filter(
    f => !asmFiles.includes(f) && f !== 'project.json'
  );
  const orderedFiles = [...asmFiles, ...incFiles];

  for (const fname of orderedFiles) {
    const path = `${projName}/${fname}`;
    try {
      const text = await fetch(`/source/${path}`).then(r => r.text());
      openFileAsTab(path, text);
    } catch (e) { console.error(`Failed to open ${path}:`, e); }
  }

  const firstAsm = tabs.findIndex(
    t => t.path.startsWith(projName + '/') &&
         (t.path.endsWith('.asm') || t.path.endsWith('.s'))
  );
  if (firstAsm >= 0) switchTab(firstAsm);

  activeProjectManifest = manifest ? { project: projName, files: manifest.files } : null;
}

btnOpenProject.addEventListener("click", async () => {
  document.getElementById("_proj-menu")?.remove();
  let sourceData = { files: [], lib: [], projects: {} };
  try { sourceData = await fetch("/source").then(r => r.json()); } catch (_) {}

  const projectNames = Object.keys(sourceData.projects);
  if (projectNames.length === 0) { alert("No projects found in source/"); return; }

  const menu = document.createElement("div");
  menu.id = "_proj-menu";
  menu.style.cssText = "position:fixed;background:var(--panel);border:1px solid var(--border);border-radius:5px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 8px 24px rgba(0,0,0,.55);font-size:12px;";
  const rect = btnOpenProject.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  projectNames.forEach(projName => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:var(--text);";
    item.textContent = projName;
    item.addEventListener("mouseover", () => item.style.background = "var(--highlight)");
    item.addEventListener("mouseout",  () => item.style.background = "");
    item.addEventListener("click", async () => {
      menu.remove();
      await openProject(projName, sourceData.projects[projName]);
    });
    menu.appendChild(item);
  });
  document.body.appendChild(menu);
  const close = (e) => {
    if (!menu.contains(e.target) && e.target !== btnOpenProject) {
      menu.remove(); document.removeEventListener("click", close);
    }
  };
  setTimeout(() => document.addEventListener("click", close), 0);
});

// ---- Config modal ----------------------------------------------------------

btnConfig.addEventListener("click", async () => {
  configError.textContent = "";
  configTextarea.value    = "";
  try {
    const res = await fetch("/config");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    configTextarea.value = await res.text();
    configModal.style.display = "flex";
    configTextarea.focus();
  } catch (e) {
    configError.textContent = "Could not load config: " + String(e);
  }
});

btnConfigCancel.addEventListener("click", () => {
  configModal.style.display = "none";
});

configModal.addEventListener("click", (e) => {
  if (e.target === configModal) configModal.style.display = "none";
});

btnConfigSave.addEventListener("click", async () => {
  configError.textContent      = "";
  btnConfigSave.disabled       = true;
  btnConfigSave.textContent    = "Saving...";
  try {
    const res  = await fetch("/config", { method: "PUT", body: configTextarea.value });
    const data = await res.json();
    if (data.ok) {
      configModal.style.display = "none";
      flashStatus("RELOADED", "reloaded");
      refreshMemory();
      loadRegions();
      loadClockSpeed();
    } else {
      configError.textContent = data.error || "Unknown error";
    }
  } catch (e) {
    configError.textContent = String(e);
  } finally {
    btnConfigSave.disabled    = false;
    btnConfigSave.textContent = "Save \u0026 Reload";
  }
});

// ---- PRU core speed selector ------------------------------------------------

async function loadClockSpeed() {
  try {
    const res  = await fetch("/config/clock_speed");
    const data = await res.json();
    pruSpeedSelect.value = String(Math.round(data.mhz));
  } catch (e) {
    // leave dropdown at its last-known value
  }
}
loadClockSpeed();

pruSpeedSelect.addEventListener("change", async () => {
  pruSpeedSelect.disabled = true;
  try {
    const res = await fetch("/config/clock_speed", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mhz: Number(pruSpeedSelect.value) }),
    });
    const data = await res.json();
    if (data.ok) {
      flashStatus("RELOADED", "reloaded");
      refreshMemory();
      loadRegions();
    } else {
      alert(data.error || "Failed to set clock speed");
      loadClockSpeed();
    }
  } catch (e) {
    alert(String(e));
    loadClockSpeed();
  } finally {
    pruSpeedSelect.disabled = false;
  }
});

// ---- Help modal ------------------------------------------------------------
const btnHelp      = document.getElementById("btn-help");
const helpModal    = document.getElementById("help-modal");
const btnHelpClose = document.getElementById("btn-help-close");

function openHelp()  { helpModal.style.display = "flex"; }
function closeHelp() { helpModal.style.display = "none"; }

btnHelp.addEventListener("click", openHelp);
btnHelpClose.addEventListener("click", closeHelp);
helpModal.addEventListener("click", e => { if (e.target === helpModal) closeHelp(); });

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (helpModal.style.display === "flex") { closeHelp(); return; }
    if (configModal.style.display === "flex") { configModal.style.display = "none"; }
  }
});

// ---- Run mode -------------------------------------------------------------

function startRun() {
  stopSim();
  running = true;
  setButtonLabel(btnRun, "Stop");
  btnRun.classList.add("btn-reset");
  btnRun.classList.remove("btn-run");
  runInterval = setInterval(() => {
    const capture = signalGraph.recording;
    const max_steps = 1000;
    if (!canStartRunRequest(runRequestInFlight)) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const request_id = genericSsiLoaded
      ? "ssi-runtime-general-" + nextRunRequestId++
      : nextRunRequestId++;
    let sent;
    if (genericSsiLoaded) {
      sent = sendAction({ action: "run_multicore", core: "pru1",
                          partner: "pru0", max_steps, capture, request_id });
    } else if (multiCoreMode) {
      sent = sendAction({ action: "run_multicore", core: "pru0",
                          partner: mcPartner, max_steps, capture, request_id });
    } else {
      sent = sendAction({ action: "run", core: currentCore, max_steps, capture,
                          request_id });
    }
    if (sent) trackRunRequest(request_id, "toolbar");
  }, 10);
}

function stopRun() {
  if (!running) return;
  running = false;
  setButtonLabel(btnRun, "Run");
  btnRun.classList.add("btn-run");
  btnRun.classList.remove("btn-reset");
  if (runInterval !== null) {
    clearInterval(runInterval);
    runInterval = null;
  }
  if (activeToolbarRequestId !== null) {
    const requestId = activeToolbarRequestId;
    abandonRunRequest(requestId);
    if (requestId === genericSsiRequestId) {
      genericSsiRunInFlight = false;
      genericSsiRequestId = null;
    }
  }
  if (genericSsiLoaded) queueSsiRuntimeRead(true);
}

function startSim() {
  stopRun();
  simRunning = true;
  setButtonLabel(btnSim, "Stop SIM");
  btnSim.classList.add("btn-reset");
  btnSim.classList.remove("btn-sim");
  const ms = Math.round((parseFloat(simIntervalInput.value) || 1.0) * 1000);
  simTimer = setInterval(() => {
    if (genericSsiLoaded) {
      if (runRequestInFlight) return;
      const request_id = "ssi-runtime-sim-" + nextRunRequestId++;
      const sent = sendAction({
        action: "run_multicore",
        core: "pru1",
        partner: "pru0",
        max_steps: 1,
        capture: signalGraph.recording,
        request_id,
      });
      if (sent) {
        genericSsiRunInFlight = true;
        genericSsiRequestId = request_id;
        trackRunRequest(request_id, "sim");
      }
    } else if (multiCoreMode) {
      sendAction({ action: "step", core: "pru0", count: 1 });
      sendAction({ action: "step", core: mcPartner, count: 1 });
    } else {
      sendAction({ action: "step", core: currentCore, count: 1 });
    }
  }, ms);
}

function stopSim() {
  if (!simRunning) return;
  simRunning = false;
  setButtonLabel(btnSim, "SIM");
  btnSim.classList.remove("btn-reset");
  btnSim.classList.add("btn-sim");
  if (simTimer !== null) {
    clearInterval(simTimer);
    simTimer = null;
  }
  if (activeSimRequestId !== null) {
    const requestId = activeSimRequestId;
    abandonRunRequest(requestId);
    if (requestId === genericSsiRequestId) {
      genericSsiRunInFlight = false;
      genericSsiRequestId = null;
    }
  }
  if (genericSsiLoaded) queueSsiRuntimeRead(true);
}

btnSim.addEventListener("click", () => {
  if (simRunning) { stopSim(); } else { startSim(); }
});

// ---- GPI pin click --------------------------------------------------------

function handleGpiClick(pinIndex, pinEl) {
  // Toggle: determine current state from CSS class
  const isHigh = pinEl.classList.contains("high");
  const newVal = isHigh ? 0 : 1;
  const targetCore = multiCoreMode ? "pru0" : currentCore;
  sendAction({ action: "set_input", core: targetCore, pin: pinIndex, value: newVal });
}

// ---- GPIO Wires -----------------------------------------------------------

const WIRE_CORES = ["pru0", "rtu0", "pru1"];
let renderedWireKey = null;

function wireListKey(wires) {
  return (wires || []).map(w =>
    `${w.src_core}:${w.src_pin}->${w.dst_core}:${w.dst_pin}`
  ).join("|");
}

function buildPinSelect(selectedPin, type) {
  // type: "gpo" (0-19) or "gpi" (0-19)
  const sel = document.createElement("select");
  for (let i = 0; i < 20; i++) {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = (type === "gpo" ? "GPO" : "GPI") + i;
    if (i === selectedPin) opt.selected = true;
    sel.appendChild(opt);
  }
  return sel;
}

function buildCoreSelect(selectedCore) {
  const sel = document.createElement("select");
  WIRE_CORES.forEach(c => {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c.toUpperCase();
    if (c === selectedCore) opt.selected = true;
    sel.appendChild(opt);
  });
  return sel;
}

function renderWires(wires) {
  const tbody = document.getElementById("wire-tbody");
  const empty = document.getElementById("wire-empty");
  if (!tbody) return;
  const key = wireListKey(wires);
  if (key === renderedWireKey) return;
  renderedWireKey = key;
  wires = wires || [];
  tbody.innerHTML = "";
  if (empty) empty.style.display = wires.length === 0 ? "" : "none";

  wires.forEach(w => {
    const tr = document.createElement("tr");
    tr.className = "wire-row";

    const srcCoreSel = buildCoreSelect(w.src_core);
    const srcPinSel  = buildPinSelect(w.src_pin, "gpo");
    const dstCoreSel = buildCoreSelect(w.dst_core);
    const dstPinSel  = buildPinSelect(w.dst_pin, "gpi");
    const rmBtn      = document.createElement("button");
    rmBtn.className  = "wire-rm";
    rmBtn.textContent = "×";

    const onRemove = () =>
      sendAction({ action: "remove_wire",
        src_core: w.src_core, src_pin: w.src_pin,
        dst_core: w.dst_core, dst_pin: w.dst_pin });

    const onChangeWire = () => {
      // Remove old wire, add new wire with updated selects
      sendAction({ action: "remove_wire",
        src_core: w.src_core, src_pin: w.src_pin,
        dst_core: w.dst_core, dst_pin: w.dst_pin });
      w.src_core = srcCoreSel.value;
      w.src_pin  = parseInt(srcPinSel.value, 10);
      w.dst_core = dstCoreSel.value;
      w.dst_pin  = parseInt(dstPinSel.value, 10);
      sendAction({ action: "add_wire",
        src_core: w.src_core, src_pin: w.src_pin,
        dst_core: w.dst_core, dst_pin: w.dst_pin });
    };

    srcCoreSel.addEventListener("change", onChangeWire);
    srcPinSel.addEventListener("change", onChangeWire);
    dstCoreSel.addEventListener("change", onChangeWire);
    dstPinSel.addEventListener("change", onChangeWire);
    rmBtn.addEventListener("click", onRemove);

    [srcCoreSel, srcPinSel].forEach(el => {
      const td = document.createElement("td"); td.appendChild(el); tr.appendChild(td);
    });
    const arrowTd = document.createElement("td");
    arrowTd.className = "wire-arrow"; arrowTd.textContent = "→"; tr.appendChild(arrowTd);
    [dstCoreSel, dstPinSel].forEach(el => {
      const td = document.createElement("td"); td.appendChild(el); tr.appendChild(td);
    });
    const rmTd = document.createElement("td"); rmTd.appendChild(rmBtn); tr.appendChild(rmTd);
    tbody.appendChild(tr);
  });
}

document.getElementById("btn-add-wire").addEventListener("click", () => {
  // Read existing wires directly from the rendered DOM rows — reliable regardless of async timing
  const existingWires = [];
  document.querySelectorAll("#wire-tbody .wire-row").forEach(tr => {
    const sels = tr.querySelectorAll("select");
    if (sels.length >= 4) {
      existingWires.push({
        src_core: sels[0].value,
        src_pin:  parseInt(sels[1].value, 10),
        dst_core: sels[2].value,
        dst_pin:  parseInt(sels[3].value, 10),
      });
    }
  });

  const hasDup = (sc, sp, dc, dp) =>
    existingWires.some(w => w.src_core === sc && w.src_pin === sp && w.dst_core === dc && w.dst_pin === dp);

  // Try SSI defaults first, then scan for any non-duplicate
  const preferred = [
    { src_core: "pru0", src_pin: 0,  dst_core: "pru1", dst_pin: 16 },
    { src_core: "pru1", src_pin: 0,  dst_core: "pru0", dst_pin: 8  },
  ];
  let wire = preferred.find(w => !hasDup(w.src_core, w.src_pin, w.dst_core, w.dst_pin));
  if (!wire) {
    for (let pin = 0; pin < 20 && !wire; pin++) {
      if (!hasDup("pru0", pin, "pru1", pin)) wire = { src_core: "pru0", src_pin: pin, dst_core: "pru1", dst_pin: pin };
    }
  }
  if (wire) sendAction({ action: "add_wire", ...wire });
});

// ---- Loopback strip -------------------------------------------------------

function onLoopbackToggle(btn) {
  const group = parseInt(btn.dataset.group, 10);
  const enabled = !btn.classList.contains('active');
  btn.classList.toggle('active', enabled);
  sendAction({ action: 'set_loopback', core: currentCore, group, enabled });
}

document.querySelectorAll('#loopback-strip .lb-btn').forEach(btn => {
  btn.addEventListener('click', () => onLoopbackToggle(btn));
});

// ---- I2C attach toggle (TCA9538) -------------------------------------------

function onI2CAttachToggle(btn) {
  const enabled = !btn.classList.contains('active');
  btn.classList.toggle('active', enabled);
  sendAction({ action: 'i2c_attach', core: currentCore, enabled, address: 0x23 });
  if (!enabled) {
    const section = document.getElementById('i2c-interface');
    if (section) section.style.display = 'none';
  }
}

const _i2cAttachBtn = document.getElementById('i2c-attach-btn');
if (_i2cAttachBtn) {
  _i2cAttachBtn.addEventListener('click', () => onI2CAttachToggle(_i2cAttachBtn));
}

// ---- Memory panel ---------------------------------------------------------

const memAddrInput = document.getElementById("mem-addr-input");
const memLenInput = document.getElementById("mem-len-input");
const btnMemRefresh = document.getElementById("btn-mem-refresh");
const memAutoRefreshBox = document.getElementById("mem-auto-refresh");
const memGrid = document.getElementById("mem-grid");

let prevMemData = [];
let memBaseAddr = 0;
let memFormat = "32b";
let memMsbFirst = false;
let regionMap = {};  // name -> base address
let memAutoRefresh = memAutoRefreshBox.checked;
let _memAutoLastFetch = 0;
let memRequestId = 0;
let memViewKey = "";
let memReadInFlight = false;
let memReadPending = false;
let memReadPendingAuto = false;

// ---- Memory panel 2 -------------------------------------------------------
const memAddrInput2  = document.getElementById("mem-addr-input-2");
const memLenInput2   = document.getElementById("mem-len-input-2");
const btnMemRefresh2 = document.getElementById("btn-mem-refresh-2");
const memAutoRefreshBox2 = document.getElementById("mem-auto-refresh-2");
const memGrid2       = document.getElementById("mem-grid-2");

let prevMemData2 = [];
let memBaseAddr2 = 0x00010000;  // default: Shared RAM (C28)
let memFormat2   = "32b";
let memMsbFirst2 = false;
let memAutoRefresh2 = memAutoRefreshBox2.checked;
let _memAutoLastFetch2 = 0;
let memRequestId2 = 0;
let memViewKey2 = "";
let memReadInFlight2 = false;
let memReadPending2 = false;
let memReadPendingAuto2 = false;

// Auto-refresh: throttled trigger on every simulation "state" update (near
// real-time while stepping/running), plus a 1 s floor interval that catches
// changes with no accompanying state message (Fill, writes from the other
// panel, config reload, UART/perif injection).
const MEM_AUTO_THROTTLE_MS = 300;

function memAutoOnStateChange() {
  const now = Date.now();
  if (memAutoRefresh && now - _memAutoLastFetch >= MEM_AUTO_THROTTLE_MS) {
    _memAutoLastFetch = now;
    refreshMemory(true);
  }
  if (memAutoRefresh2 && now - _memAutoLastFetch2 >= MEM_AUTO_THROTTLE_MS) {
    _memAutoLastFetch2 = now;
    refreshMemory2(true);
  }
}

setInterval(() => {
  const now = Date.now();
  if (memAutoRefresh && now - _memAutoLastFetch >= 1000) {
    _memAutoLastFetch = now;
    refreshMemory(true);
  }
  if (memAutoRefresh2 && now - _memAutoLastFetch2 >= 1000) {
    _memAutoLastFetch2 = now;
    refreshMemory2(true);
  }
}, 1000);

memAutoRefreshBox.addEventListener("change", () => {
  memAutoRefresh = memAutoRefreshBox.checked;
  if (memAutoRefresh) {
    _memAutoLastFetch = Date.now();
    refreshMemory(true);
  } else if (memReadPendingAuto) {
    memReadPending = false;
    memReadPendingAuto = false;
  }
});

memAutoRefreshBox2.addEventListener("change", () => {
  memAutoRefresh2 = memAutoRefreshBox2.checked;
  if (memAutoRefresh2) {
    _memAutoLastFetch2 = Date.now();
    refreshMemory2(true);
  } else if (memReadPendingAuto2) {
    memReadPending2 = false;
    memReadPendingAuto2 = false;
  }
});

btnMemRefresh2.addEventListener("click", refreshMemory2);
memAddrInput2.addEventListener("keydown", (e) => { if (e.key === "Enter") refreshMemory2(); });
memAddrInput2.addEventListener("change", refreshMemory2);

// Region quick-jump selectors — set address and refresh
document.getElementById("mem-region-sel").addEventListener("change", (e) => {
  if (!e.target.value) return;
  memAddrInput.value = e.target.value;
  e.target.value = "";
  refreshMemory();
});
document.getElementById("mem-region-sel-2").addEventListener("change", (e) => {
  if (!e.target.value) return;
  memAddrInput2.value = e.target.value;
  e.target.value = "";
  refreshMemory2();
});

document.getElementById("mem-fmt-group-2").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-fmt]");
  if (!btn) return;
  memFormat2 = btn.dataset.fmt;
  document.querySelectorAll("#mem-fmt-group-2 button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  if (prevMemData2.length > 0) renderMemory2({ tag: "mem2", addr: memBaseAddr2, data: prevMemData2 });
});

document.getElementById("mem-end-group-2").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-end]");
  if (!btn) return;
  memMsbFirst2 = btn.dataset.end === "msb";
  document.querySelectorAll("#mem-end-group-2 button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  if (prevMemData2.length > 0) renderMemory2({ tag: "mem2", addr: memBaseAddr2, data: prevMemData2 });
});

function finishMemoryRequest(panel) {
  const second = panel === 2;
  const pending = second ? memReadPending2 : memReadPending;
  const pendingAuto = second ? memReadPendingAuto2 : memReadPendingAuto;
  const autoEnabled = second ? memAutoRefresh2 : memAutoRefresh;
  if (second) {
    memReadInFlight2 = false;
    memReadPending2 = false;
    memReadPendingAuto2 = false;
  } else {
    memReadInFlight = false;
    memReadPending = false;
    memReadPendingAuto = false;
  }
  if (pending && (!pendingAuto || autoEnabled)) {
    setTimeout(() => second ? refreshMemory2(pendingAuto) : refreshMemory(pendingAuto), 0);
  }
}

function refreshMemory2(fromAuto = false) {
  const raw = memAddrInput2.value.trim();
  let addr;
  if (regionMap[raw] !== undefined) {
    addr = regionMap[raw];
    memAddrInput2.value = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
  } else {
    addr = parseInt(raw, 16) || parseInt(raw, 10) || 0x00010000;
  }
  const length = parseInt(memLenInput2.value, 10) || 1024;
  const viewKey = `${addr}:${length}`;
  if (viewKey !== memViewKey2) prevMemData2 = [];
  memViewKey2 = viewKey;
  if (memReadInFlight2) {
    memReadPending2 = true;
    memReadPendingAuto2 = fromAuto;
    return;
  }
  const request_id = ++memRequestId2;
  memReadInFlight2 = sendAction({
    action: "read_memory", addr, length, tag: "mem2", request_id,
  });
}

function memoryBytesEqual(left, right) {
  if (left === right) return true;
  if (!left || !right || left.length !== right.length) return false;
  for (let i = 0; i < left.length; i++) {
    if (left[i] !== right[i]) return false;
  }
  return true;
}

function renderMemory2(msg) {
  if (msg.request_id !== undefined && msg.request_id !== null &&
      msg.request_id !== memRequestId2) return;
  if (msg.request_id !== undefined && msg.request_id !== null) {
    finishMemoryRequest(2);
    const responseKey = `${msg.addr}:${msg.length ?? (msg.data || []).length}`;
    if (responseKey !== memViewKey2) return;
  }
  const addr = msg.addr;
  const data = msg.data;
  const isReadResponse = msg.request_id !== undefined && msg.request_id !== null;
  if (isReadResponse && addr === memBaseAddr2 && memoryBytesEqual(prevMemData2, data)) {
    return;
  }
  memBaseAddr2 = addr;

  const { wordSize, wordsPerRow } = MEM_FORMATS[memFormat2];
  const bytesPerRow = wordSize * wordsPerRow;

  memGrid2.innerHTML = "";
  memGrid2.style.gridTemplateColumns = `80px repeat(${wordsPerRow}, 1fr)`;

  for (let row = 0; row < data.length; row += bytesPerRow) {
    const addrEl = document.createElement("div");
    addrEl.className = "mem-addr";
    addrEl.textContent = "0x" + (addr + row).toString(16).padStart(8, "0").toUpperCase();
    memGrid2.appendChild(addrEl);

    for (let w = 0; w < wordsPerRow; w++) {
      const byteIdx = row + w * wordSize;
      const wordEl = document.createElement("div");
      wordEl.className = "mem-byte";

      const value = assembleBytes2(data, byteIdx, wordSize);
      if (value !== null) {
        wordEl.textContent = fmtWord(value, memFormat2);
        wordEl.dataset.offset = byteIdx;

        let changed = false;
        for (let k = 0; k < wordSize; k++) {
          if (byteIdx + k < prevMemData2.length && prevMemData2[byteIdx + k] !== data[byteIdx + k]) {
            changed = true; break;
          }
        }
        if (changed) wordEl.classList.add("changed");

        if (memFormat2 !== "bin") {
          wordEl.addEventListener("dblclick", () => editWord2(wordEl, addr + byteIdx, wordSize));
        }
      } else {
        wordEl.textContent = "--".repeat(Math.min(wordSize, 4));
      }
      memGrid2.appendChild(wordEl);
    }
  }

  prevMemData2 = [...data];
}

function assembleBytes2(data, offset, wordSize) {
  if (offset + wordSize > data.length) return null;
  let v = 0;
  if (memMsbFirst2) {
    for (let i = 0; i < wordSize; i++) v = ((v << 8) | data[offset + i]) >>> 0;
  } else {
    for (let i = 0; i < wordSize; i++) v = (v | (data[offset + i] << (8 * i))) >>> 0;
  }
  return v;
}

function editWord2(el, absoluteAddr, wordSize) {
  const maxLen = wordSize * 2;
  const oldVal = el.textContent;
  const input = document.createElement("input");
  input.style.cssText = `width:${Math.max(56, maxLen * 8)}px;font-size:13px;text-align:center;background:var(--panel-inset);color:var(--text);border:1px solid var(--accent);padding:0;font-family:inherit;`;
  input.value = oldVal;
  input.maxLength = maxLen;
  el.textContent = "";
  el.appendChild(input);
  input.focus();
  input.select();

  const maxVal = wordSize === 4 ? 0xFFFFFFFF : (Math.pow(2, wordSize * 8) - 1);

  const commit = () => {
    const newVal = parseInt(input.value, 16);
    if (!isNaN(newVal) && newVal >= 0 && newVal <= maxVal) {
      const bytes = [];
      if (memMsbFirst2) {
        for (let i = wordSize - 1; i >= 0; i--) bytes.push((newVal >>> (8 * i)) & 0xFF);
      } else {
        for (let i = 0; i < wordSize; i++) bytes.push((newVal >>> (8 * i)) & 0xFF);
      }
      sendAction({ action: "write_memory", addr: absoluteAddr, data: bytes });
    }
    el.textContent = !isNaN(newVal) ? fmtWord(newVal >>> 0, memFormat2) : oldVal;
    setTimeout(refreshMemory2, 100);
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { el.textContent = oldVal; }
  });
  input.addEventListener("blur", commit);
}

// ---- Memory fill modal ----------------------------------------------------

const fillModal      = document.getElementById("fill-modal");
const fillAddrInput  = document.getElementById("fill-addr");
const fillLenInput   = document.getElementById("fill-len");
const fillErrEl      = document.getElementById("fill-error");
const fillPatternVal = document.getElementById("fill-pattern-val");
const fillSeqStart   = document.getElementById("fill-seq-start");
const fillSeqStep    = document.getElementById("fill-seq-step");
const fillWaveAmp    = document.getElementById("fill-wave-amp");
const fillWaveCycles = document.getElementById("fill-wave-cycles");
const fillWaveOffset = document.getElementById("fill-wave-offset");
const fillWaveSigned = document.getElementById("fill-wave-signed");
const fillWaveCanvas = document.getElementById("fill-wave-preview");
let fillFmt   = "byte";
let fillWtype = "sine";

function parseHexOrDec(s) {
  s = (s || "").trim();
  return (s.startsWith("0x") || s.startsWith("0X")) ? parseInt(s, 16) : parseInt(s, 10);
}

function drawWavePreview() {
  const W       = fillWaveCanvas.offsetWidth || 420;
  const LABEL_H = 16;   // bottom: x-axis labels
  const Y_MAR   = 52;   // left: y-axis labels
  const H       = 60 + LABEL_H;
  const WAVE_W  = W - Y_MAR;
  const WAVE_H  = H - LABEL_H;
  const midY    = WAVE_H / 2;
  const yTopPx  = 5;
  const yBotPx  = WAVE_H - 5;

  fillWaveCanvas.width  = W;
  fillWaveCanvas.height = H;

  const ctx = fillWaveCanvas.getContext("2d");
  ctx.fillStyle = getThemeColor("--graph-bg", "#ffffff");
  ctx.fillRect(0, 0, W, H);

  // ---- Compute y range from current parameters ----------------------------
  const amp      = parseHexOrDec(fillWaveAmp.value)    || 0x7FFF;
  const dc       = parseHexOrDec(fillWaveOffset.value) || 0;
  const signed   = fillWaveSigned.checked;
  const elemSize = {"byte": 1, "16b": 2, "32b": 4}[fillFmt] || 1;
  const bits     = elemSize * 8;
  const mask     = bits >= 32 ? 0xFFFFFFFF : (1 << bits) - 1;
  let yMax, yMin;
  if (signed) {
    yMax = Math.min(dc + amp,  (1 << (bits - 1)) - 1);
    yMin = Math.max(dc - amp, -(1 << (bits - 1)));
  } else {
    yMax = Math.min(dc + amp, mask);
    yMin = Math.max(dc, 0);
  }
  const yMid = Math.round((yMax + yMin) / 2);

  function fmtYLabel(v) {
    if (v < 0) return "-0x" + (-v).toString(16).toUpperCase();
    return "0x" + v.toString(16).toUpperCase();
  }

  // ---- Y reference lines --------------------------------------------------
  ctx.lineWidth = 1;
  for (const [py, bright] of [[yTopPx, false], [midY, true], [yBotPx, false]]) {
    ctx.strokeStyle = bright
      ? getThemeColor("--graph-grid-strong", "#c2cbd4")
      : getThemeColor("--graph-grid", "#d8dee5");
    ctx.beginPath(); ctx.moveTo(Y_MAR, py); ctx.lineTo(W, py); ctx.stroke();
  }

  // ---- Y-axis labels -------------------------------------------------------
  ctx.font      = "9px Consolas, monospace";
  ctx.fillStyle = getThemeColor("--graph-label", "#53616d");
  ctx.textAlign = "right";
  ctx.fillText(fmtYLabel(yMax), Y_MAR - 4, yTopPx + 4);
  ctx.fillText(fmtYLabel(yMid), Y_MAR - 4, midY   + 3);
  ctx.fillText(fmtYLabel(yMin), Y_MAR - 4, yBotPx + 4);

  // ---- Waveform -----------------------------------------------------------
  const cycles = parseFloat(fillWaveCycles.value) || 1;
  ctx.strokeStyle = getThemeColor("--graph-wave", "#b6202b");
  ctx.lineWidth   = 1.5;
  ctx.beginPath();
  for (let xi = 0; xi < WAVE_W; xi++) {
    const t = (xi / WAVE_W) * cycles * 2 * Math.PI;
    let y;
    if      (fillWtype === "sine")     y = Math.sin(t);
    else if (fillWtype === "square")   y = Math.sin(t) >= 0 ? 1 : -1;
    else if (fillWtype === "triangle") y = 2/Math.PI * Math.asin(Math.sin(t));
    else                               y = 2*((t/(2*Math.PI)) % 1.0) - 1.0;
    const py = midY - y * (midY - yTopPx);
    xi === 0 ? ctx.moveTo(Y_MAR + xi, py) : ctx.lineTo(Y_MAR + xi, py);
  }
  ctx.stroke();

  // ---- X-axis: sample-index labels ----------------------------------------
  const len    = parseInt(fillLenInput.value, 10) || 256;
  const nElems = Math.max(1, Math.floor(len / elemSize));
  const nTicks = 5;

  ctx.font      = "9px Consolas, monospace";
  ctx.fillStyle = getThemeColor("--graph-label", "#53616d");
  for (let i = 0; i <= nTicks; i++) {
    const frac  = i / nTicks;
    const px    = Y_MAR + Math.round(frac * WAVE_W);
    const label = Math.round(frac * nElems).toString();

    ctx.strokeStyle = getThemeColor("--graph-grid-strong", "#c2cbd4");
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(px + 0.5, WAVE_H); ctx.lineTo(px + 0.5, WAVE_H + 3); ctx.stroke();

    ctx.textAlign = i === 0 ? "left" : i === nTicks ? "right" : "center";
    ctx.fillText(label, px, H - 2);
  }
}

function openFillModal(addr, len) {
  fillAddrInput.value   = addr;
  fillLenInput.value    = len;
  fillErrEl.textContent = "";
  fillModal.style.display = "flex";
  if (document.querySelector(".fill-tab-btn.active")?.dataset.tab === "waveform") {
    setTimeout(drawWavePreview, 0);
  }
}

function sendFill() {
  const addr = parseHexOrDec(fillAddrInput.value);
  const len  = parseInt(fillLenInput.value, 10);
  if (isNaN(addr) || isNaN(len) || len <= 0) {
    fillErrEl.textContent = "Invalid address or length"; return;
  }
  fillErrEl.textContent = "";
  const activeTab = document.querySelector(".fill-tab-btn.active").dataset.tab;
  const msg = { action: "fill_memory", core: currentCore,
                addr, length: len, mode: activeTab, fmt: fillFmt };
  if (activeTab === "pattern") {
    msg.value = fillPatternVal.value.trim();
  } else if (activeTab === "sequence") {
    msg.start = fillSeqStart.value.trim();
    msg.step  = fillSeqStep.value.trim();
  } else {
    msg.wtype     = fillWtype;
    msg.amplitude = fillWaveAmp.value.trim();
    msg.cycles    = parseFloat(fillWaveCycles.value) || 1;
    msg.dc_offset = fillWaveOffset.value.trim();
    msg.signed    = fillWaveSigned.checked;
  }
  sendAction(msg);
  fillModal.style.display = "none";
}

// Tab switching
document.querySelectorAll(".fill-tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".fill-tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".fill-tab-pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("fill-tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "waveform") setTimeout(drawWavePreview, 0);
  });
});

// Format group
document.querySelectorAll("#fill-fmt-group button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#fill-fmt-group button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    fillFmt = btn.dataset.fmt;
  });
});

// Waveform type group
document.querySelectorAll("#fill-wave-type-group button").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#fill-wave-type-group button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    fillWtype = btn.dataset.wtype;
    drawWavePreview();
  });
});
fillWaveCycles.addEventListener("input",  drawWavePreview);
fillWaveAmp   .addEventListener("input",  drawWavePreview);
fillWaveOffset.addEventListener("input",  drawWavePreview);
fillWaveSigned.addEventListener("change", drawWavePreview);
fillLenInput.addEventListener("input", () => {
  if (document.querySelector(".fill-tab-btn.active")?.dataset.tab === "waveform") drawWavePreview();
});
document.querySelectorAll("#fill-fmt-group button").forEach(btn => {
  btn.addEventListener("click", () => {
    if (document.querySelector(".fill-tab-btn.active")?.dataset.tab === "waveform") drawWavePreview();
  });
});

// Button wiring
document.getElementById("btn-mem-fill")  .addEventListener("click", () =>
  openFillModal(memAddrInput.value, memLenInput.value));
document.getElementById("btn-mem-fill-2").addEventListener("click", () =>
  openFillModal(memAddrInput2.value, memLenInput2.value));
document.getElementById("btn-fill-apply") .addEventListener("click", sendFill);
document.getElementById("btn-fill-cancel").addEventListener("click", () =>
  { fillModal.style.display = "none"; });
fillModal.addEventListener("click", e => {
  if (e.target === fillModal) fillModal.style.display = "none";
});

// ---------------------------------------------------------------------------

btnMemRefresh.addEventListener("click", refreshMemory);
memAddrInput.addEventListener("keydown", (e) => { if (e.key === "Enter") refreshMemory(); });
memAddrInput.addEventListener("change", refreshMemory);

document.getElementById("mem-fmt-group").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-fmt]");
  if (!btn) return;
  memFormat = btn.dataset.fmt;
  document.querySelectorAll("#mem-fmt-group button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  if (prevMemData.length > 0) renderMemory({ addr: memBaseAddr, data: prevMemData });
});

document.getElementById("mem-end-group").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-end]");
  if (!btn) return;
  memMsbFirst = btn.dataset.end === "msb";
  document.querySelectorAll("#mem-end-group button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  if (prevMemData.length > 0) renderMemory({ addr: memBaseAddr, data: prevMemData });
});

function refreshMemory(fromAuto = false) {
  const raw = memAddrInput.value.trim();
  let addr;
  if (regionMap[raw] !== undefined) {
    addr = regionMap[raw];
    memAddrInput.value = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
  } else {
    addr = parseInt(raw, 16) || parseInt(raw, 10) || 0;
  }
  const length = parseInt(memLenInput.value, 10) || 1024;
  const viewKey = `${addr}:${length}`;
  if (viewKey !== memViewKey) prevMemData = [];
  memViewKey = viewKey;
  if (memReadInFlight) {
    memReadPending = true;
    memReadPendingAuto = fromAuto;
    return;
  }
  const request_id = ++memRequestId;
  memReadInFlight = sendAction({
    action: "read_memory", addr, length, tag: "mem1", request_id,
  });
}

async function loadRegions() {
  try {
    const res = await fetch("/regions");
    const regions = await res.json();
    regionMap = {};
    const dl = document.getElementById("mem-region-list");
    const dl2 = document.getElementById("mem-region-list-2");
    dl.innerHTML = "";
    dl2.innerHTML = "";
    regions.forEach(r => {
      regionMap[r.name] = r.base;
      [dl, dl2].forEach(list => {
        const opt = document.createElement("option");
        opt.value = r.name;
        list.appendChild(opt);
      });
    });
  } catch (_) {}
}

const MEM_FORMATS = {
  "bin":  { wordSize: 1, wordsPerRow: 8  },
  "byte": { wordSize: 1, wordsPerRow: 16 },
  "16b":  { wordSize: 2, wordsPerRow: 8  },
  "24b":  { wordSize: 3, wordsPerRow: 4  },
  "32b":  { wordSize: 4, wordsPerRow: 4  },
};

function fmtWord(value, fmt) {
  if (fmt === "bin")  return value.toString(2).padStart(8, "0");
  if (fmt === "byte") return value.toString(16).padStart(2, "0").toUpperCase();
  if (fmt === "16b")  return value.toString(16).padStart(4, "0").toUpperCase();
  if (fmt === "24b")  return value.toString(16).padStart(6, "0").toUpperCase();
  return value.toString(16).padStart(8, "0").toUpperCase();
}

function assembleBytes(data, offset, wordSize) {
  if (offset + wordSize > data.length) return null;
  let v = 0;
  if (memMsbFirst) {
    for (let i = 0; i < wordSize; i++) v = ((v << 8) | data[offset + i]) >>> 0;
  } else {
    for (let i = 0; i < wordSize; i++) v = (v | (data[offset + i] << (8 * i))) >>> 0;
  }
  return v;
}

function renderMemory(msg) {
  if (msg.request_id !== undefined && msg.request_id !== null &&
      msg.request_id !== memRequestId) return;
  if (msg.request_id !== undefined && msg.request_id !== null) {
    finishMemoryRequest(1);
    const responseKey = `${msg.addr}:${msg.length ?? (msg.data || []).length}`;
    if (responseKey !== memViewKey) return;
  }
  const addr = msg.addr;
  const data = msg.data;
  const isReadResponse = msg.request_id !== undefined && msg.request_id !== null;
  if (isReadResponse && addr === memBaseAddr && memoryBytesEqual(prevMemData, data)) {
    return;
  }
  memBaseAddr = addr;

  const { wordSize, wordsPerRow } = MEM_FORMATS[memFormat];
  const bytesPerRow = wordSize * wordsPerRow;

  memGrid.innerHTML = "";
  memGrid.style.gridTemplateColumns = `80px repeat(${wordsPerRow}, 1fr)`;

  for (let row = 0; row < data.length; row += bytesPerRow) {
    const addrEl = document.createElement("div");
    addrEl.className = "mem-addr";
    addrEl.textContent = "0x" + (addr + row).toString(16).padStart(8, "0").toUpperCase();
    memGrid.appendChild(addrEl);

    for (let w = 0; w < wordsPerRow; w++) {
      const byteIdx = row + w * wordSize;
      const wordEl = document.createElement("div");
      wordEl.className = "mem-byte";

      const value = assembleBytes(data, byteIdx, wordSize);
      if (value !== null) {
        wordEl.textContent = fmtWord(value, memFormat);
        wordEl.dataset.offset = byteIdx;

        let changed = false;
        for (let k = 0; k < wordSize; k++) {
          if (byteIdx + k < prevMemData.length && prevMemData[byteIdx + k] !== data[byteIdx + k]) {
            changed = true; break;
          }
        }
        if (changed) wordEl.classList.add("changed");

        if (memFormat !== "bin") {
          wordEl.addEventListener("dblclick", () => editWord(wordEl, addr + byteIdx, wordSize));
        }
      } else {
        wordEl.textContent = "--".repeat(Math.min(wordSize, 4));
      }
      memGrid.appendChild(wordEl);
    }
  }

  prevMemData = [...data];
}

function editWord(el, absoluteAddr, wordSize) {
  const maxLen = wordSize * 2;
  const oldVal = el.textContent;
  const input = document.createElement("input");
  input.style.cssText = `width:80px;font-size:13px;text-align:center;background:var(--panel-inset);color:var(--text);border:1px solid var(--accent);padding:0;font-family:inherit;`;
  input.value = oldVal;
  input.maxLength = maxLen;
  el.textContent = "";
  el.appendChild(input);
  input.focus();
  input.select();

  const maxVal = wordSize === 4 ? 0xFFFFFFFF : (Math.pow(2, wordSize * 8) - 1);

  const commit = () => {
    const newVal = parseInt(input.value, 16);
    if (!isNaN(newVal) && newVal >= 0 && newVal <= maxVal) {
      const bytes = [];
      if (memMsbFirst) {
        for (let i = wordSize - 1; i >= 0; i--) bytes.push((newVal >>> (8 * i)) & 0xFF);
      } else {
        for (let i = 0; i < wordSize; i++) bytes.push((newVal >>> (8 * i)) & 0xFF);
      }
      sendAction({ action: "write_memory", addr: absoluteAddr, data: bytes });
    }
    el.textContent = !isNaN(newVal) ? fmtWord(newVal >>> 0, memFormat) : oldVal;
    setTimeout(refreshMemory, 100);
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { el.textContent = oldVal; }
  });
  input.addEventListener("blur", commit);
}

function editRegister(valEl, index) {
  const oldVal = valEl.textContent;
  const input = document.createElement("input");
  input.style.cssText = "width:88px;font-size:13px;text-align:right;background:var(--panel-inset);color:var(--text);border:1px solid var(--accent);padding:0 2px;font-family:inherit;";
  input.value = oldVal.slice(2);  // strip leading "0x"
  input.maxLength = 8;
  valEl.textContent = "";
  valEl.appendChild(input);
  input.focus();
  input.select();

  let committed = false;
  const commit = () => {
    if (committed) return;
    committed = true;
    const newVal = parseInt(input.value, 16);
    if (!isNaN(newVal) && newVal >= 0 && newVal <= 0xFFFFFFFF) {
      sendAction({ action: "set_register", core: currentCore, index, value: newVal });
      valEl.textContent = "0x" + newVal.toString(16).padStart(8, "0").toUpperCase();
    } else {
      valEl.textContent = oldVal;
    }
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { committed = true; valEl.textContent = oldVal; }
  });
  input.addEventListener("blur", commit);
}

// ---- Constant table (AM243x ICSSG, Table 6-408) --------------------------
//   addr: fixed address; pru0/rtu0: core-dependent (null = not programmable/reserved)

const CTABLE = [
  { c:  0, name: "INTC",          addr: 0x00020000 },
  { c:  1, name: "IEP1",          addr: 0x0002F000 },
  { c:  2, name: "IEP1+100h",     addr: 0x0002F100 },
  { c:  3, name: "ECAP0",         addr: 0x00030000 },
  { c:  4, name: "CFG",           addr: 0x00026000 },
  { c:  5, name: "CFG+100h",      addr: 0x00026100 },
  { c:  6, name: "INTC+200h",     addr: 0x00020200 },
  { c:  7, name: "UART0",         addr: 0x00028000 },
  { c:  8, name: "IEP0+100h",     addr: 0x0002E100 },
  { c:  9, name: "ICSSG CFG",     addr: 0x00033000 },
  { c: 10, name: "TM_CFG",        pru0: 0x0002A000, rtu0: 0x0002A000 },
  { c: 11, name: "PRU Ctrl",      pru0: 0x00022000, rtu0: 0x00023800 },
  { c: 12, name: "PA_STATS_QRAM", addr: 0x00027000 },
  { c: 13, name: "PA_STATS_CRAM", addr: 0x0002C000 },
  { c: 14, name: "Reserved",      addr: 0x00024800 },
  { c: 15, name: "Reserved",      addr: 0x60000000 },
  { c: 16, name: "Reserved",      addr: 0x70000000 },
  { c: 17, name: "Reserved",      addr: 0x80000000 },
  { c: 18, name: "Reserved",      addr: 0x90000000 },
  { c: 19, name: "Reserved",      addr: 0xA0000000 },
  { c: 20, name: "Reserved",      addr: 0xB0000000 },
  { c: 21, name: "MDIO",          addr: 0x00032400 },
  { c: 22, name: "RAT SLICE",     pru0: 0x00008000, rtu0: 0x0000A000 },
  { c: 23, name: "Reserved",      addr: 0xC0000000 },
  { c: 24, name: "Data RAM0",     addr: 0x00000000 },
  { c: 25, name: "Data RAM1",     addr: 0x00002000 },
  { c: 26, name: "IEP0",          addr: 0x0002E000 },
  { c: 27, name: "MII_RT",        addr: 0x00032000 },
  { c: 28, name: "Shared RAM",    addr: 0x00010000 },
  { c: 29, name: "Reserved",      addr: 0x00000000 },
  { c: 30, name: "Reserved",      addr: 0x00000000 },
  { c: 31, name: "Reserved",      addr: 0x00000000 },
];

function ctableAddrForCore(entry, core) {
  if (entry.addr !== undefined) return entry.addr;
  return entry[core] ?? entry.pru0 ?? 0;
}

function buildCtable() {
  const tbody = document.getElementById("ctable-tbody");
  tbody.innerHTML = "";
  for (const entry of CTABLE) {
    const isCoreDep = entry.addr === undefined;
    const addr = ctableAddrForCore(entry, currentCore);
    const addrHex = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
    const tr = document.createElement("tr");
    if (isCoreDep) tr.classList.add("ct-dep");
    tr.innerHTML =
      `<td class="ct-idx">C${entry.c}</td>` +
      `<td class="ct-name">${entry.name}</td>` +
      `<td class="ct-addr" title="Jump memory view to ${addrHex}">${addrHex}</td>`;
    tr.querySelector(".ct-addr").addEventListener("click", () => {
      memAddrInput.value = addrHex;
      refreshMemory();
    });
    tbody.appendChild(tr);
  }
}

function updateCtableForCore() {
  document.querySelectorAll("#ctable-tbody tr.ct-dep").forEach((tr, i) => {
    const depEntries = CTABLE.filter(e => e.addr === undefined);
    const entry = depEntries[i];
    if (!entry) return;
    const addr = ctableAddrForCore(entry, currentCore);
    const addrHex = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
    const cell = tr.querySelector(".ct-addr");
    cell.textContent = addrHex;
    cell.title = `Jump memory view to ${addrHex}`;
    cell.onclick = () => { memAddrInput.value = addrHex; refreshMemory(); };
  });
}

const btnCtableToggle = document.getElementById("ctable-toggle");
const ctableSection   = document.getElementById("ctable-section");
const ctableDivider   = document.getElementById("ctable-divider");

btnCtableToggle.addEventListener("click", () => {
  const open = ctableSection.classList.toggle("open");
  btnCtableToggle.textContent = open ? "C-Table ▲" : "C-Table";
  ctableDivider.style.display = open ? "" : "none";
  if (open && !document.getElementById("ctable-tbody").hasChildNodes()) {
    buildCtable();
  }
});

// ---- C-table divider drag-to-resize -----------------------------------------
(function () {
  const regBody = document.getElementById("reg-body");
  let startY, startRegH, startCtH;

  ctableDivider.addEventListener("mousedown", (e) => {
    if (!ctableSection.classList.contains("open")) return;
    e.preventDefault();
    startY = e.clientY;
    startRegH = regBody.offsetHeight;
    startCtH = ctableSection.offsetHeight;
    ctableDivider.classList.add("dragging");
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  function onMove(e) {
    const delta = e.clientY - startY;
    const newRegH = Math.max(40, startRegH + delta);
    const newCtH  = Math.max(40, startCtH - delta);
    regBody.style.flex = `0 0 ${newRegH}px`;
    ctableSection.style.flex = `0 0 ${newCtH}px`;
  }

  function onUp() {
    ctableDivider.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
  }
}());

// ---- ASM syntax highlighting ---------------------------------------------

const _OPCODES = new Set([
  'LDI','MOV','ADD','ADC','SUB','SUC','RSB','RSC',
  'AND','OR','XOR','NOT','LSL','LSR',
  'SET','CLR','LMBD','MIN','MAX',
  'JMP','JAL','QBA',
  'QBEQ','QBNE','QBGT','QBGE','QBLT','QBLE','QBBS','QBBC',
  'LOOP','LBBO','SBBO','LBCO','SBCO','XIN','XOUT','XCHG',
  'HALT','SLP','NOP','WBS','WBC','ZERO','FILL','LDI32','TSEN',
  'MVIB','MVIW','MVID',
]);

function _tokenize(text) {
  let out = '';
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    // Comment
    if (ch === ';') {
      out += `<span class="hl-cmt">${escapeHtml(text.slice(i))}</span>`;
      break;
    }
    // &register prefix
    if (ch === '&') {
      const m = text.slice(i + 1).match(/^r\d{1,2}(?:\.[bw]\d)?/i);
      if (m) {
        out += `<span class="hl-reg">&amp;${escapeHtml(m[0])}</span>`;
        i += 1 + m[0].length;
        continue;
      }
    }
    // Hex number
    if (ch === '0' && /^0[xX]/.test(text.slice(i))) {
      const m = text.slice(i).match(/^0[xX][0-9a-fA-F]+/);
      out += `<span class="hl-num">${m[0]}</span>`;
      i += m[0].length;
      continue;
    }
    // Negative number
    if (ch === '-') {
      const m = text.slice(i).match(/^-\d+/);
      if (m) { out += `<span class="hl-num">${m[0]}</span>`; i += m[0].length; continue; }
    }
    // Decimal number
    if (ch >= '0' && ch <= '9') {
      const m = text.slice(i).match(/^\d+/);
      out += `<span class="hl-num">${m[0]}</span>`;
      i += m[0].length;
      continue;
    }
    // Identifier: opcode, register, or symbol reference
    if (/[A-Za-z_]/.test(ch)) {
      const m = text.slice(i).match(/^[A-Za-z_]\w*(?:\.[bw]\d)?/i);
      if (m) {
        const word = m[0];
        const up = word.toUpperCase();
        if (_OPCODES.has(up)) {
          out += `<span class="hl-op">${escapeHtml(word)}</span>`;
        } else if (/^r\d{1,2}(?:\.[bw]\d)?$/i.test(word)) {
          out += `<span class="hl-reg">${escapeHtml(word)}</span>`;
        } else {
          out += `<span class="hl-sym">${escapeHtml(word)}</span>`;
        }
        i += word.length;
        continue;
      }
    }
    out += escapeHtml(ch);
    i++;
  }
  return out;
}

function highlightAsm(rawText) {
  const text = rawText.trim();
  if (!text) return '';
  // Parser strips label prefixes from source_text, but handle them defensively
  const labelM = text.match(/^([A-Za-z_]\w*):(.*)/);
  if (labelM) {
    const lbl = `<span class="hl-lbl">${escapeHtml(labelM[1])}:</span>`;
    const rest = labelM[2].trim();
    return rest ? lbl + ' ' + _tokenize(rest) : lbl;
  }
  // Align operands: pad opcode to column 8 (all PRU opcodes are ≤ 4 chars)
  const spaceIdx = text.search(/\s/);
  if (spaceIdx === -1) {
    // opcode-only instruction (HALT, NOP, …)
    return '    ' + _tokenize(text);
  }
  const opcode   = text.slice(0, spaceIdx);
  const operands = text.slice(spaceIdx).trimStart();
  const pad      = ' '.repeat(Math.max(1, 8 - opcode.length));
  return '    ' + _tokenize(opcode) + pad + _tokenize(operands);
}

// ---- Error display --------------------------------------------------------

const errorBand = document.getElementById("error-band");

function showErrors(errors) {
  if (!errors || errors.length === 0) { clearErrors(); return; }
  errorBand.textContent = errors.join("\n");
  errorBand.style.display = "block";
}

function clearErrors() {
  errorBand.style.display = "none";
  errorBand.textContent = "";
}

// ---- Utility --------------------------------------------------------------

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function flashStatus(text, cssClass) {
  if (_flashTimer !== null) {
    clearTimeout(_flashTimer);
    _flashTimer = null;
  }
  const origText  = statusBadge.textContent;
  const origClass = statusBadge.className;
  statusBadge.textContent = text;
  statusBadge.className   = cssClass;
  _flashTimer = setTimeout(() => {
    _flashTimer = null;
    statusBadge.textContent = origText;
    statusBadge.className   = origClass;
  }, 2000);
}

// ---- Multi-core mode ------------------------------------------------------

btnMulticore.addEventListener("click", toggleMultiCore);
document.getElementById("btn-reset-layout").addEventListener("click", resetLayout);

// ---- Multi-core partner (second core in the MC view: RTU0 or PRU1) --------
const mcPartnerSelect = document.getElementById("mc-partner-select");

function selectGenericSsiPartner() {
  mcPartner = "pru1";
  if (mcPartnerSelect) mcPartnerSelect.value = "pru1";
  applyMCPartnerLabels();
  if (multiCoreMode) {
    mcPrevRegs.rtu0 = new Array(32).fill("0x00000000");
    mcSourceInstructions.rtu0 = [];
    mcSourceLabels.rtu0 = {};
    mcLastSourceBreakpointKey.rtu0 = null;
    mcBreakpoints.rtu0 = new Set();
    mcHaltedState.rtu0 = false;
    mcBreakState.rtu0 = false;
    buildMCRegTable("rtu0");
    sendAction({ action: "get_state", core: "pru0" });
    sendAction({ action: "get_state", core: "pru1" });
  }
}

function applyMCPartnerLabels() {
  const label = mcPartner === "pru1" ? "PRU1" : "RTU0";
  const srcTitle = document.getElementById("mc-partner-source-title");
  const regTitle = document.getElementById("mc-partner-reg-title");
  const cntLabel = document.getElementById("cnt-p1-label");
  if (srcTitle) srcTitle.textContent = `${label} Source`;
  if (regTitle) regTitle.textContent = `${label} Registers`;
  if (cntLabel) cntLabel.textContent = label;
}

mcPartnerSelect.addEventListener("change", () => {
  stopRun(); stopSim();
  graphClear();
  mcPartner = mcPartnerSelect.value;
  applyMCPartnerLabels();
  if (multiCoreMode) {
    // Reset the partner DOM slot and re-request state for the new core.
    mcPrevRegs.rtu0 = new Array(32).fill("0x00000000");
    mcSourceInstructions.rtu0 = [];
    mcSourceLabels.rtu0 = {};
    mcLastSourceBreakpointKey.rtu0 = null;
    mcBreakpoints.rtu0 = new Set();
    mcHaltedState.rtu0 = false;
    mcBreakState.rtu0 = false;
    buildMCRegTable("rtu0");
    sendAction({ action: "get_state", core: "pru0" });
    sendAction({ action: "get_state", core: mcPartner });
  }
});

function toggleMultiCore() {
  multiCoreMode = !multiCoreMode;
  stopRun(); stopSim();
  graphClear();

  if (multiCoreMode) {
    coreSelect.style.display = "none";
    mcLoadCore.style.display = "";
    mcPartnerSelect.style.display = "";
    applyMCPartnerLabels();
    btnMulticore.classList.add("mc-active");

    // Show per-core counter labels and RTU0 counter spans
    document.getElementById("cnt-p0-label").style.display = "";
    document.getElementById("cnt-sep").style.display = "";
    document.getElementById("cnt-p1-label").style.display = "";
    document.getElementById("cnt-rtu-cycles-wrap").style.display = "";
    document.getElementById("cnt-rtu-stalls-wrap").style.display = "";
    document.getElementById("cnt-rtu-pc-wrap").style.display = "";
    document.getElementById("cnt-instrs-wrap").style.display = "none";
    document.getElementById("cnt-ipc-wrap").style.display = "none";

    // Reset MC SPAD state
    for (const b of SPAD_BANKS) {
      mcPrevSpad[b.key] = new Array(b.count).fill("0x00000000");
    }

    // Build register tables for both cores
    buildMCRegTable("pru0");
    buildMCRegTable("rtu0");

    // Switch to MC layout
    switchLayoutMode("mc");

    // Request state for both cores
    sendAction({ action: "get_state", core: "pru0" });
    sendAction({ action: "get_state", core: mcPartner });

  } else {
    coreSelect.style.display = "";
    mcLoadCore.style.display = "none";
    mcPartnerSelect.style.display = "none";
    btnMulticore.classList.remove("mc-active");

    // Restore SC counter layout
    document.getElementById("cnt-p0-label").style.display = "none";
    document.getElementById("cnt-sep").style.display = "none";
    document.getElementById("cnt-p1-label").style.display = "none";
    document.getElementById("cnt-rtu-cycles-wrap").style.display = "none";
    document.getElementById("cnt-rtu-stalls-wrap").style.display = "none";
    document.getElementById("cnt-rtu-pc-wrap").style.display = "none";
    document.getElementById("cnt-instrs-wrap").style.display = "";
    document.getElementById("cnt-ipc-wrap").style.display = "";

    // Switch to SC layout
    switchLayoutMode("sc");

    sendAction({ action: "get_state", core: currentCore });
  }
}

function buildMCRegTable(core) {
  const tbody = document.getElementById(`mc-${core}-reg-tbody`);
  tbody.innerHTML = "";
  // Only PRU0 MC panel shows SPAD columns
  const visibleBanks = (core === "pru0") ? SPAD_BANKS.filter(b => mcSpadVisible.has(b.key)) : [];

  if (visibleBanks.length > 0) {
    const hdrRow = document.createElement("tr");
    hdrRow.innerHTML = `<td></td><td></td>`;
    for (const bank of visibleBanks) {
      hdrRow.innerHTML += `<td class="spad-hdr">${bank.label}</td>`;
    }
    tbody.appendChild(hdrRow);
  }

  for (let i = 0; i < 32; i++) {
    const tr = document.createElement("tr");
    tr.id = `mc-${core}-reg-row-${i}`;
    let html = `<td class="reg-name">R${i}</td><td class="reg-val" id="mc-${core}-reg-val-${i}">0x00000000</td>`;
    for (const bank of visibleBanks) {
      const wordIdx = i - bank.regStart;
      if (wordIdx >= 0 && wordIdx < bank.count) {
        html += `<td class="spad-val" id="mc-spad-${bank.key}-${i}">0x00000000</td>`;
      } else {
        html += `<td class="spad-val empty">--</td>`;
      }
    }
    tr.innerHTML = html;
    const valCell = tr.querySelector(".reg-val");
    const regIdx = i;
    valCell.addEventListener("dblclick", () => editMCRegister(valCell, core, regIdx));
    tbody.appendChild(tr);
  }
}

function editMCRegister(valEl, core, index) {
  const oldVal = valEl.textContent;
  const input = document.createElement("input");
  input.style.cssText = "width:88px;font-size:13px;text-align:right;background:var(--panel-inset);color:var(--text);border:1px solid var(--accent);padding:0 2px;font-family:inherit;";
  input.value = oldVal.slice(2);
  input.maxLength = 8;
  valEl.textContent = "";
  valEl.appendChild(input);
  input.focus();
  input.select();

  let committed = false;
  const commit = () => {
    if (committed) return;
    committed = true;
    const newVal = parseInt(input.value, 16);
    if (!isNaN(newVal) && newVal >= 0 && newVal <= 0xFFFFFFFF) {
      sendAction({ action: "set_register", core, index, value: newVal });
      valEl.textContent = "0x" + newVal.toString(16).padStart(8, "0").toUpperCase();
    } else {
      valEl.textContent = oldVal;
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { committed = true; valEl.textContent = oldVal; }
  });
  input.addEventListener("blur", commit);
}

function updateMCUI(state) {
  if (state.core !== "pru0" && state.core !== mcPartner) return;   // core not shown
  // The second MC panel's DOM ids are the "rtu0" slot; the partner core
  // (RTU0 or PRU1) renders into it.
  const core = state.core === "pru0" ? "pru0" : "rtu0";

  // PC badge in panel title
  const pcBadge = document.getElementById(`mc-${core}-pc`);
  if (pcBadge) pcBadge.textContent = `PC: ${state.pc}`;

  // Carry
  const carryEl = document.getElementById(`mc-${core}-carry`);
  if (carryEl) carryEl.textContent = state.carry ? "1" : "0";

  // MAC indicator
  if (state.mac) {
    const modeEl = document.getElementById(`mc-${core}-mac-mode`);
    if (modeEl) modeEl.textContent = state.mac.mode ? "ACC" : "MPY";
    const macCarryEl = document.getElementById(`mc-${core}-mac-carry`);
    if (macCarryEl) macCarryEl.textContent = state.mac.acc_carry ? " CARRY" : "";
  }

  // Registers
  updateMCRegisters(core, state.registers);

  // Source text is included only when it changes; retain it between ticks.
  if (Array.isArray(state.instructions)) {
    mcSourceInstructions[core] = state.instructions;
    mcSourceLabels[core] = state.labels || {};
  }
  updateMCSource(core, mcSourceInstructions[core], state.pc, mcSourceLabels[core]);

  // Breakpoints
  if (state.breakpoints) mcBreakpoints[core] = new Set(state.breakpoints);

  // Per-core halted/break state → update status badge
  mcHaltedState[core] = !!state.halted;
  mcBreakState[core]  = !!state.at_breakpoint;
  updateMCStatus();

  // Per-core counters and IO
  if (core === "pru0") {
    cntCycles.textContent = state.cycles;
    cntStalls.textContent = state.stall_cycles;
    cntInstrs.textContent = state.instruction_count;
    cntIpc.textContent    = state.ipc.toFixed(3);
    cntPc.textContent     = state.pc;
    updatePins(state.io);
    updateSDPanel(state.io);
    updateI2CPanel(state.io);
    // Update SPAD columns in PRU0 MC reg panel
    if (mcSpadVisible.size > 0) updateMCSpad(state.spad);
  } else if (core === "rtu0") {
    document.getElementById("cnt-rtu-cycles").textContent = state.cycles;
    document.getElementById("cnt-rtu-stalls").textContent = state.stall_cycles;
    document.getElementById("cnt-rtu-pc").textContent     = state.pc;
  }

  // Signal graph: sample both cores in MC mode (covers SIM and step modes)
  graphSample(state);
  requestGraphDraw();

  memAutoOnStateChange();
}

function updateMCSpad(spad) {
  if (!spad) return;
  for (const bank of SPAD_BANKS) {
    if (!mcSpadVisible.has(bank.key)) continue;
    const words = spad[bank.key];
    if (!words) continue;
    const prev = mcPrevSpad[bank.key] || new Array(bank.count).fill("0x00000000");
    for (let j = 0; j < bank.count; j++) {
      const regIdx = j + bank.regStart;
      const cell = document.getElementById(`mc-spad-${bank.key}-${regIdx}`);
      if (!cell) continue;
      const newVal = words[j];
      cell.textContent = newVal;
      cell.classList.toggle("changed", newVal !== prev[j]);
    }
    mcPrevSpad[bank.key] = [...words];
  }
}

function updateMCStatus() {
  const anyBreak  = mcBreakState.pru0  || mcBreakState.rtu0;
  const anyHalted = mcHaltedState.pru0 || mcHaltedState.rtu0;
  if (anyBreak) {
    statusBadge.textContent = "BREAK";
    statusBadge.className   = "halted";
    stopRun(); stopSim();
  } else if (anyHalted) {
    statusBadge.textContent = "HALTED";
    statusBadge.className   = "halted";
    stopRun(); stopSim();
  } else {
    statusBadge.textContent = "RUNNING";
    statusBadge.className   = "";
  }
}

function updateMCRegisters(core, regs) {
  const prev = mcPrevRegs[core];
  for (let i = 0; i < 32; i++) {
    const valEl = document.getElementById(`mc-${core}-reg-val-${i}`);
    const rowEl = document.getElementById(`mc-${core}-reg-row-${i}`);
    if (!valEl) continue;
    if (valEl.querySelector("input")) continue;
    const newVal = regs[i];
    valEl.textContent = newVal;
    rowEl.classList.toggle("changed", newVal !== prev[i]);
  }
  mcPrevRegs[core] = [...regs];
}

function updateMCSource(core, instructions, pc, labels) {
  const listEl = document.getElementById(`mc-${core}-source-list`);
  if (!listEl) return;

  // State packets reuse the same source objects until a load changes them.
  const sourceChanged = instructions !== mcRenderedSourceInstructions[core] ||
    labels !== mcRenderedSourceLabels[core];
  if (sourceChanged) {
    mcRenderedSourceInstructions[core] = instructions;
    mcRenderedSourceLabels[core] = labels;
    mcLastSourceBreakpointKey[core] = null;
    mcCurrentSourceLine[core] = null;
    listEl.innerHTML = "";

    const addrToLabels = {};
    for (const [name, addr] of Object.entries(labels)) {
      if (!addrToLabels[addr]) addrToLabels[addr] = [];
      addrToLabels[addr].push(name);
    }
    for (const addr of Object.keys(addrToLabels)) addrToLabels[addr].sort();

    instructions.forEach((instr) => {
      (addrToLabels[instr.addr] || []).forEach(name => {
        const lblLi = document.createElement("li");
        lblLi.className = "lbl-line";
        const dot = document.createElement("span");
        dot.className = "bp-dot";
        dot.textContent = "●";
        const src = document.createElement("span");
        src.className = "src";
        src.innerHTML = `<span class="hl-lbl">${escapeHtml(name)}:</span>`;
        lblLi.appendChild(dot);
        lblLi.appendChild(src);
        listEl.appendChild(lblLi);
      });

      const li = document.createElement("li");
      li.id = `mc-${core}-src-line-${instr.addr}`;
      const bpDot = document.createElement("span");
      bpDot.className = "bp-dot";
      bpDot.textContent = "●";
      const addrSpan = document.createElement("span");
      addrSpan.className = "addr";
      addrSpan.textContent = `${instr.addr}:`;
      const srcSpan = document.createElement("span");
      srcSpan.className = "src";
      srcSpan.innerHTML = highlightAsm(instr.text);
      li.appendChild(bpDot);
      li.appendChild(addrSpan);
      li.appendChild(srcSpan);
      listEl.appendChild(li);
    });
  }

  // Breakpoint markers only need to be touched when the set or source list
  // changes; doing this for every state packet makes the source view lag.
  const breakpointKey = [...mcBreakpoints[core]].sort((a, b) => a - b).join(",");
  if (breakpointKey !== mcLastSourceBreakpointKey[core]) {
    listEl.querySelectorAll(`li[id^='mc-${core}-src-line-']`).forEach(li => {
      const addr = parseInt(li.id.replace(`mc-${core}-src-line-`, ""), 10);
      li.classList.toggle("has-bp", mcBreakpoints[core].has(addr));
    });
    mcLastSourceBreakpointKey[core] = breakpointKey;
  }

  // Current PC highlight
  const currentLine = document.getElementById(`mc-${core}-src-line-${pc}`);
  if (mcCurrentSourceLine[core] && mcCurrentSourceLine[core] !== currentLine) {
    mcCurrentSourceLine[core].classList.remove("current-pc");
  }
  if (currentLine && currentLine !== mcCurrentSourceLine[core]) {
    currentLine.classList.add("current-pc");
    queueSourceLineScroll(`mc-${core}`, listEl, currentLine);
  }
  mcCurrentSourceLine[core] = currentLine;

  // "rtu0" is this panel's fixed DOM slot; the core actually loaded into it
  // (RTU0 or PRU1) is whatever mcPartner currently points at.
  const realCore = core === "pru0" ? "pru0" : mcPartner;
  renderBpBar(`mc-${core}-`, realCore, mcBreakpoints[core]);
}

// Breakpoint toggle via dblclick on MC source panels
document.getElementById("mc-pru0-source-panel").addEventListener("dblclick", (e) => {
  const li = e.target.closest("li[id^='mc-pru0-src-line-']");
  if (!li) return;
  const addr = parseInt(li.id.replace("mc-pru0-src-line-", ""), 10);
  if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: "pru0", addr });
});

document.getElementById("mc-rtu0-source-panel").addEventListener("dblclick", (e) => {
  const li = e.target.closest("li[id^='mc-rtu0-src-line-']");
  if (!li) return;
  const addr = parseInt(li.id.replace("mc-rtu0-src-line-", ""), 10);
  if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: mcPartner, addr });
});

// ---- Init -----------------------------------------------------------------

wireBpBar("", () => currentCore);
wireBpBar("mc-pru0-", () => "pru0");
wireBpBar("mc-rtu0-", () => mcPartner);

initUI();
connect();

// Breakpoint toggle via double-click on any source line (event delegation)
sourceList.addEventListener("dblclick", (e) => {
  const li = e.target.closest("li[id^='src-line-']");
  if (!li) return;
  const addr = parseInt(li.id.replace("src-line-", ""), 10);
  if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: currentCore, addr });
});

// Keyboard shortcuts (Space, ArrowRight, ArrowLeft, ?) — skip when typing in inputs
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.key === "?") {
    helpModal.style.display !== "none" ? closeHelp() : openHelp();
    return;
  }
  if (e.key === " ") {
    e.preventDefault();
    if (multiCoreMode) {
      const pru0Line = document.querySelector("#mc-pru0-source-list li.current-pc");
      if (pru0Line) {
        const addr = parseInt(pru0Line.id.replace("mc-pru0-src-line-", ""), 10);
        if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: "pru0", addr });
      }
      const partnerLine = document.querySelector("#mc-rtu0-source-list li.current-pc");
      if (partnerLine) {
        const addr = parseInt(partnerLine.id.replace("mc-rtu0-src-line-", ""), 10);
        if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: mcPartner, addr });
      }
    } else {
      const pcLine = sourceList.querySelector("li.current-pc");
      if (!pcLine) return;
      const addr = parseInt(pcLine.id.replace("src-line-", ""), 10);
      if (!isNaN(addr)) sendAction({ action: "toggle_breakpoint", core: currentCore, addr });
    }
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    stopRun(); stopSim();
    if (genericSsiLoaded) {
      btnStep.click();
    } else if (multiCoreMode) {
      sendAction({ action: "step", core: "pru0", count: 1 });
      sendAction({ action: "step", core: mcPartner, count: 1 });
    } else {
      sendAction({ action: "step", core: currentCore, count: 1 });
    }
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    stopRun(); stopSim();
    graphClear();
    if (multiCoreMode) {
      sendAction({ action: "step_back", core: "pru0" });
      sendAction({ action: "step_back", core: mcPartner });
    } else {
      sendAction({ action: "step_back", core: currentCore });
    }
  }
});

// ---- UART decoder controls -------------------------------------------------

document.getElementById("uart-decode-btn").addEventListener("click", () => {
  const samples  = graphGetSamples();
  const statusEl = document.getElementById("uart-status");
  const outputEl = document.getElementById("uart-output");
  const hintEl   = document.getElementById("uart-hint");

  const showHint = (msg) => {
    hintEl.textContent = msg;
    hintEl.style.display = "";
    statusEl.style.display = "none";
    outputEl.style.display = "none";
  };

  if (samples.length === 0) {
    return showHint("Run or step the simulator to capture GPO0 first");
  }

  const { bytes, tBit, error } = decodeUART(samples);

  if (error === "empty" || error === "noisy") {
    return showHint("Signal too noisy to auto-detect bit width");
  }
  if (error === "idle") {
    return showHint("No UART activity detected on GPO0");
  }

  hintEl.style.display = "none";
  statusEl.textContent = `Auto-detected: ${tBit} steps/bit · ${bytes.length} bytes decoded`;
  statusEl.style.display = "";
  outputEl.innerHTML = renderUARTBytes(bytes);
  outputEl.style.display = "";
});

document.getElementById("uart-clear-btn").addEventListener("click", () => {
  document.getElementById("uart-status").style.display = "none";
  document.getElementById("uart-output").style.display = "none";
  document.getElementById("uart-hint").textContent = "Run or step the simulator to capture GPO0 first";
  document.getElementById("uart-hint").style.display = "";
});

/* ───────────── UART RX Inject ───────────── */

(function() {
  const modeHex   = document.getElementById("uart-inj-mode-hex");
  const modeAscii = document.getElementById("uart-inj-mode-ascii");
  const payloadEl = document.getElementById("uart-inj-payload");
  const countEl   = document.getElementById("uart-inj-byte-count");
  if (!modeHex || !modeAscii || !payloadEl || !countEl) {
    console.warn("UART Inject: missing DOM elements, skipping init");
    return;
  }
  let currentMode = "hex";

  function parseHexInput(str) {
    const tokens = str.trim().split(/\s+/).filter(t => t.length > 0);
    const bytes = [];
    for (const t of tokens) {
      if (!/^[0-9a-fA-F]{1,2}$/.test(t)) return null;
      bytes.push(parseInt(t, 16));
    }
    return bytes.length > 0 ? bytes : null;
  }

  function parseAsciiInput(str) {
    if (str.length === 0) return null;
    const bytes = [];
    for (let i = 0; i < str.length; i++) {
      bytes.push(str.charCodeAt(i) & 0xFF);
    }
    return bytes;
  }

  function bytesToHex(bytes) {
    return bytes.map(b => b.toString(16).padStart(2, "0").toUpperCase()).join(" ");
  }

  function bytesToAscii(bytes) {
    return bytes.map(b => (b >= 32 && b < 127) ? String.fromCharCode(b) : "\u00B7").join("");
  }

  function updateByteCount() {
    if (currentMode === "ascii") {
      const len = payloadEl.value.length;
      countEl.textContent = "(" + len + " byte" + (len !== 1 ? "s" : "") + ")";
      countEl.style.display = "";
    } else {
      countEl.style.display = "none";
    }
  }

  function switchMode(mode) {
    if (mode === currentMode) return;
    let bytes;
    if (currentMode === "hex") {
      bytes = parseHexInput(payloadEl.value);
    } else {
      bytes = parseAsciiInput(payloadEl.value);
    }
    currentMode = mode;
    modeHex.classList.toggle("active", mode === "hex");
    modeAscii.classList.toggle("active", mode === "ascii");
    if (bytes) {
      payloadEl.value = (mode === "hex") ? bytesToHex(bytes) : bytesToAscii(bytes);
    }
    updateByteCount();
  }

  modeHex.addEventListener("click", () => switchMode("hex"));
  modeAscii.addEventListener("click", () => switchMode("ascii"));
  payloadEl.addEventListener("input", updateByteCount);

  // Expose for inject handler
  window._uartInjectGetBytes = function() {
    if (currentMode === "hex") {
      return parseHexInput(payloadEl.value);
    } else {
      return parseAsciiInput(payloadEl.value);
    }
  };

  // Inject button handler
  const injBtn = document.getElementById("uart-inj-btn");
  if (!injBtn) { console.warn("UART Inject: btn not found"); return; }
  injBtn.addEventListener("click", () => {
    const statusEl = document.getElementById("uart-inj-status");
    const bytes = window._uartInjectGetBytes();

    if (!bytes || bytes.length === 0) {
      statusEl.textContent = "\u2717 Invalid payload \u2014 enter space-separated hex bytes or ASCII text";
      statusEl.style.color = "var(--halted)";
      statusEl.style.display = "";
      return;
    }

    const baudStr = document.getElementById("uart-inj-baud").value.trim();
    const baudMb = parseFloat(baudStr);
    if (isNaN(baudMb) || baudMb <= 0 || baudMb > 10) {
      statusEl.textContent = "\u2717 Baud must be between 0.01 and 10.00 Mb";
      statusEl.style.color = "var(--halted)";
      statusEl.style.display = "";
      return;
    }
    const baudrate = Math.round(baudMb * 1_000_000);

    const framesVal = parseInt(document.getElementById("uart-inj-frames").value, 10);
    if (isNaN(framesVal) || framesVal < 1 || framesVal > 100) {
      statusEl.textContent = "\u2717 Frames must be between 1 and 100";
      statusEl.style.color = "var(--halted)";
      statusEl.style.display = "";
      return;
    }

    const pin = parseInt(document.getElementById("uart-inj-pin").value, 10);

    sendAction({
      action: "uart_inject",
      core: currentCore,
      pin: pin,
      payload: bytes,
      baudrate: baudrate,
      frames: framesVal,
    });

    statusEl.textContent = "\u231B Arming...";
    statusEl.style.color = "var(--text-dim)";
    statusEl.style.display = "";
  });
})();

// ---- SSI Encoder Inject panel -----------------------------------------------
(function () {
  const injBtn = document.getElementById("ssi-inj-btn");
  if (!injBtn) { console.warn("SSI Inject: btn not found"); return; }

  // Hex / Dec mode toggle
  const modeHex = document.getElementById("ssi-inj-mode-hex");
  const modeDec = document.getElementById("ssi-inj-mode-dec");
  let ssiMode = "hex";
  if (modeHex && modeDec) {
    modeHex.addEventListener("click", () => {
      ssiMode = "hex";
      modeHex.classList.add("active");
      modeDec.classList.remove("active");
      document.getElementById("ssi-inj-value").placeholder = "Position (0-FFF)";
    });
    modeDec.addEventListener("click", () => {
      ssiMode = "dec";
      modeDec.classList.add("active");
      modeHex.classList.remove("active");
      document.getElementById("ssi-inj-value").placeholder = "Position (0-4095)";
    });
  }

  injBtn.addEventListener("click", () => {
    const statusEl = document.getElementById("ssi-inj-status");
    const rawVal = document.getElementById("ssi-inj-value").value.trim();
    const bits = parseInt(document.getElementById("ssi-inj-bits").value, 10) || 12;
    const maxVal = (1 << bits) - 1;

    let value;
    try {
      value = ssiMode === "hex" ? parseInt(rawVal, 16) : parseInt(rawVal, 10);
    } catch (e) { value = NaN; }

    if (isNaN(value) || value < 0 || value > maxVal) {
      statusEl.textContent = "✗ Value must be 0–" + (ssiMode === "hex" ? maxVal.toString(16).toUpperCase() : maxVal) + " (" + bits + " bits)";
      statusEl.style.color = "var(--halted)";
      statusEl.style.display = "";
      return;
    }

    const clkPin = parseInt(document.getElementById("ssi-inj-clk-pin").value, 10);
    const dataPin = parseInt(document.getElementById("ssi-inj-data-pin").value, 10);

    sendAction({
      action: "ssi_inject",
      core: currentCore,
      clk_pin: clkPin,
      data_pin: dataPin,
      value: value,
      bits: bits,
    });

    statusEl.textContent = "⏳ Arming...";
    statusEl.style.color = "var(--text-dim)";
    statusEl.style.display = "";
  });
})();

// ---- Generic SSI runtime panel ----------------------------------------------
(function () {
  const loadBtn = document.getElementById("ssi-runtime-load");
  if (!loadBtn) return;

  const profileSelect = document.getElementById("ssi-runtime-profile");
  const fieldIds = {
    topology: "ssi-runtime-topology",
    encoding_type: "ssi-runtime-encoding",
    alignment: "ssi-runtime-alignment",
    formation_mode: "ssi-runtime-formation",
    frame_width_bits: "ssi-runtime-frame-bits",
    position_offset_bits: "ssi-runtime-position-offset",
    position_width_bits: "ssi-runtime-position-bits",
    singleturn_width_bits: "ssi-runtime-singleturn-bits",
    multiturn_width_bits: "ssi-runtime-multiturn-bits",
    error_offset_bits: "ssi-runtime-error-offset",
    error_width_bits: "ssi-runtime-error-bits",
    padding_width_bits: "ssi-runtime-padding-bits",
    clock_high_cycles: "ssi-runtime-clock-high",
    clock_low_cycles: "ssi-runtime-clock-low",
    sample_delay_cycles: "ssi-runtime-sample-delay",
    tv_cycles: "ssi-runtime-tv",
    tm_pause_outer_iters: "ssi-runtime-tm",
    tp_pause_outer_iters: "ssi-runtime-tp",
    formation_pause_outer_iters: "ssi-runtime-formation-pause",
    sequence_hold_mode: "ssi-runtime-hold-mode",
    sequence_hold_count: "ssi-runtime-hold",
    capture_mode: "ssi-runtime-capture",
    fault_mode: "ssi-runtime-fault",
    fault_argument: "ssi-runtime-fault-argument",
    fault_repeat_count: "ssi-runtime-fault-repeat",
    producer_mode: "ssi-runtime-producer-mode",
    producer_period_iep_ticks: "ssi-runtime-producer-period",
  };

  function setRuntimeStatus(text, color) {
    const el = document.getElementById("ssi-runtime-status");
    if (!el) return;
    el.textContent = text;
    el.style.color = color || "";
  }

  function setRuntimeFields(values) {
    if (!values) return;
    Object.entries(fieldIds).forEach(([key, id]) => {
      const el = document.getElementById(id);
      if (el && values[key] !== undefined && document.activeElement !== el) {
        const value = String(values[key]);
        if (el.value !== value) el.value = value;
      }
    });
  }

  function renderRuntimeProfiles(profiles) {
    if (!profileSelect || !Array.isArray(profiles)) return;
    const profileKey = profiles.map((profile) =>
      `${profile.name}:${profile.frame_width_bits}:${profile.clock_hz}`
    ).join("|");
    if (profileSelect.dataset.profileKey === profileKey) return;

    const selected = profileSelect.value;
    profileSelect.innerHTML = "";
    profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile.name;
      const mhz = (profile.clock_hz / 1e6).toFixed(3);
      option.textContent = profile.name + " · " + profile.frame_width_bits + "b · " + mhz + " MHz default";
      profileSelect.appendChild(option);
    });
    if (selected && profiles.some((p) => p.name === selected)) {
      profileSelect.value = selected;
    }
    profileSelect.dataset.profileKey = profileKey;
  }

  function formatDebugHex(value, width) {
    if (value === null || value === undefined) return "\u2014";
    try {
      const bits = width * 4;
      return "0x" + BigInt.asUintN(bits, BigInt(String(value)))
        .toString(16).toUpperCase().padStart(width, "0");
    } catch (_) {
      return "\u2014";
    }
  }

  function mailboxAddress(msg, field, fallback) {
    const layout = msg.mailbox_layout && msg.mailbox_layout.fields;
    return formatDebugHex(layout && layout[field] !== undefined ? layout[field] : fallback, 8);
  }

  function traceAddress(msg, field, fallback) {
    const layout = msg.trace_layout && msg.trace_layout.fields;
    return formatDebugHex(layout && layout[field] !== undefined ? layout[field] : fallback, 8);
  }

  function mailboxValue(msg, field, fallback, width) {
    const display = msg.mailbox_display && msg.mailbox_display[field];
    return display !== undefined && display !== null
      ? display
      : formatDebugHex(fallback, width);
  }

  function traceValue(msg, field, fallback) {
    const display = msg.trace_display && msg.trace_display[field];
    return display !== undefined && display !== null
      ? display
      : formatDebugHex(fallback, 8);
  }

  window.renderSsiRuntimeState = function (msg) {
    renderRuntimeProfiles(msg.profiles);
    const staged = msg.staged || msg.active || {};
    const profileName = msg.staged_profile || msg.selected_profile;
    if (profileName && [...profileSelect.options].some((o) => o.value === profileName)) {
      profileSelect.value = profileName;
    }
    setRuntimeFields(staged);

    const status = msg.loaded ? msg.status : (msg.status || "Not loaded");
    const generation = msg.loaded
      ? " · gen " + msg.requested_generation +
        " ack " + msg.pru0_ack_generation + "/" + msg.pru1_ack_generation +
        " · " + (Number(msg.effective_clock_hz || 0) / 1e6).toFixed(3) + " MHz"
      : "";
    setRuntimeStatus(status + generation, msg.loaded ? "#6a9955" : "var(--text-dim)");

    const wires = document.getElementById("ssi-runtime-wires");
    if (wires) {
      const wireText = (msg.wires || []).map((wire) =>
        `${wire.src_core}:GPO${wire.src_pin} -> ${wire.dst_core}:GPI${wire.dst_pin}`
      );
      wires.textContent = wireText.length
        ? "Wires: " + wireText.join(" | ")
        : "Wires: ...";
    }

    const mailbox = document.getElementById("ssi-runtime-mailbox");
    if (mailbox) {
      const mb = msg.mailbox;
      if (!mb) {
        mailbox.textContent = "Mailbox: ...";
      } else {
        const position = mailboxValue(msg, "position", mb.position_value, 8) ||
          "invalid for active width";
        const frameCounter = msg.mailbox_display &&
          msg.mailbox_display.frame_counter_decimal !== undefined
          ? msg.mailbox_display.frame_counter_decimal
          : (mb.frame_counter || 0);
        mailbox.textContent = [
          "Shared RAM mailbox (seqlock snapshot)",
          "sequence       [" + mailboxAddress(msg, "sequence", 0x00010200) + "] = " +
            mailboxValue(msg, "sequence", mb.seq, 8),
          "raw frame      [" + mailboxAddress(msg, "raw_frame", 0x00010204) + "] = " +
            mailboxValue(msg, "raw_frame", mb.raw_frame, 16),
          "raw position   [" + mailboxAddress(msg, "raw_position", 0x0001020C) + "] = " +
            mailboxValue(msg, "raw_position", mb.raw_position_value, 8),
          "position       [" + mailboxAddress(msg, "position", 0x0001020C) + "] = " +
            position + " (decoded)",
          "status         [" + mailboxAddress(msg, "status", 0x00010210) + "] = " +
            mailboxValue(msg, "status", mb.status_bits, 8),
          "frame counter  [" + mailboxAddress(msg, "frame_counter", 0x00010214) + "] = " +
            mailboxValue(msg, "frame_counter", mb.frame_counter, 8) +
            " (" + frameCounter + ")",
          "timestamp      [" + mailboxAddress(msg, "timestamp", 0x00010218) + "] = " +
            mailboxValue(msg, "timestamp", mb.timestamp_cycles, 16),
        ].join("\n");
      }
    }
    const trace = document.getElementById("ssi-runtime-trace");
    if (trace) {
      const tr = msg.trace;
      if (!tr) {
        trace.textContent = "Trace: ...";
      } else {
        const records = msg.trace_display &&
          msg.trace_display.write_index_decimal !== undefined
          ? msg.trace_display.write_index_decimal
          : tr.write_index;
        const overruns = msg.trace_display &&
          msg.trace_display.overrun_count_decimal !== undefined
          ? msg.trace_display.overrun_count_decimal
          : tr.overrun_count;
        trace.textContent = [
          "Trace counters",
          "write index    [" + traceAddress(msg, "write_index", 0x00010240) + "] = " +
            traceValue(msg, "write_index", tr.write_index) +
            " (" + records + " records)",
          "overruns       [" + traceAddress(msg, "overrun_count", 0x00010244) + "] = " +
            traceValue(msg, "overrun_count", tr.overrun_count) +
            " (" + overruns + ")",
        ].join("\n");
      }
    }
    const producerDiagnostics = document.getElementById("ssi-runtime-producer-diagnostics");
    if (producerDiagnostics) {
      const producer = msg.producer;
      const diag = msg.producer_diagnostics;
      if (!producer || !diag) {
        producerDiagnostics.textContent = "Timestamped producer: ...";
      } else {
        producerDiagnostics.textContent = [
          "Timestamped ARM producer / PRU0 estimator",
          "state          = " + (producer.running ? "RUNNING" : "stopped") +
            " · " + producer.trajectory + " · " + producer.period_iep_ticks +
            " ticks (" + producer.period_ns + " ns)",
          "published      = " + producer.published_count +
            " · skipped/overwritten=" + producer.skipped_overwritten_count,
          "latest seq     [0x00018400] = " + formatDebugHex(diag.latest_write_seq, 16),
          "accepted       [0x00018408] = " + diag.accepted_count,
          "coherence retry[0x0001840C] = " + diag.coherence_retry_count,
          "stale          [0x00018410] = " + diag.stale_sample_count,
          "ring overrun   [0x00018414] = " + diag.ring_overrun_count,
          "request time   [0x00018418] = " + formatDebugHex(diag.last_request_timestamp_iep, 16),
          "estimate Q31.32[0x00018420] = " + formatDebugHex(diag.last_estimate_position_q31_32, 16),
          "status         [0x00018428] = " + formatDebugHex(diag.status, 8),
          "generation     [0x0001842C] = " + formatDebugHex(diag.generation, 8),
          "head seq       [0x00018430] = " + formatDebugHex(diag.head_seq, 8),
          "latest slot    [0x00018434] = " + diag.latest_slot_index,
          "sample seq     [0x00018438] = " + formatDebugHex(diag.latest_stable_sample_seq, 16),
        ].join("\n");
        const trajectory = document.getElementById("ssi-runtime-producer-trajectory");
        if (trajectory && document.activeElement !== trajectory) trajectory.value = producer.trajectory;
      }
    }
    if (Array.isArray(msg.frames) && msg.frames.length) {
      const frames = msg.frames.map((value) => BigInt(value).toString(16).toUpperCase());
      document.getElementById("ssi-runtime-frames").value = frames.join(", ");
    }
  };

  function numericOverrides() {
    const overrides = {};
    Object.entries(fieldIds).forEach(([key, id]) => {
      const value = Number(document.getElementById(id).value);
      if (Number.isFinite(value)) overrides[key] = Math.trunc(value);
    });
    return overrides;
  }

  function selectedProfile() {
    return profileSelect && profileSelect.value ? profileSelect.value : "";
  }

  function frameTokens() {
    return document.getElementById("ssi-runtime-frames").value
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
      .map((value) => value.startsWith("0x") || value.startsWith("0X") ? value : "0x" + value);
  }

  function positionTokens() {
    return document.getElementById("ssi-runtime-positions").value
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean)
      .map((value) => value.startsWith("0x") || value.startsWith("0X") ? value : "0x" + value);
  }

  function optionalNumber(id) {
    const value = document.getElementById(id).value.trim();
    return value === "" ? undefined : Math.trunc(Number(value));
  }

  function sendPositionPacking() {
    const message = {
      action: "ssi_runtime_positions",
      positions: positionTokens(),
    };
    const count = optionalNumber("ssi-runtime-position-count");
    const offset = optionalNumber("ssi-runtime-gray-excess-offset");
    if (count !== undefined) message.position_count = count;
    if (offset !== undefined) message.gray_excess_offset = offset;
    sendAction(message);
  }

  loadBtn.addEventListener("click", () => {
    genericSsiLoaded = false;
    stopRun();
    stopSim();
    cancelAllRunRequests();
    graphClear();
    clearErrors();
    setRuntimeStatus("Loading generic PRU0 emulator / PRU1 reader...", "#888");
    sendAction({ action: "ssi_runtime_load" });
  });

  document.getElementById("ssi-runtime-refresh").addEventListener("click", () => {
    sendAction({ action: "ssi_runtime_read" });
  });

  document.getElementById("ssi-runtime-pack").addEventListener("click", () => {
    sendPositionPacking();
    setRuntimeStatus("Natural positions packed into staged frame slots.", "var(--accent)");
  });

  profileSelect.addEventListener("change", () => {
    const selected = [...(window.ssiRuntimeProfileCatalog || [])]
      .find((profile) => profile.name === profileSelect.value);
    if (selected) setRuntimeFields(selected);
  });

  document.getElementById("ssi-runtime-stage").addEventListener("click", () => {
    sendAction({
      action: "ssi_runtime_stage",
      profile: selectedProfile(),
      overrides: numericOverrides(),
    });
    setRuntimeStatus("Configuration staged; press Apply atomically.", "var(--accent)");
  });

  document.getElementById("ssi-runtime-producer-configure").addEventListener("click", () => {
    sendAction({
      action: "ssi_runtime_producer_configure",
      trajectory: document.getElementById("ssi-runtime-producer-trajectory").value,
      initial_position: document.getElementById("ssi-runtime-producer-initial").value,
      velocity_counts_per_second: document.getElementById("ssi-runtime-producer-velocity").value,
      triangle_low: document.getElementById("ssi-runtime-producer-low").value,
      triangle_high: document.getElementById("ssi-runtime-producer-high").value,
      period_iep_ticks: Math.trunc(Number(document.getElementById("ssi-runtime-producer-period").value)),
    });
    setRuntimeStatus("Timestamped producer configured; Start when ready.", "var(--accent)");
  });

  document.getElementById("ssi-runtime-producer-start").addEventListener("click", () => {
    sendAction({ action: "ssi_runtime_producer_start" });
  });

  document.getElementById("ssi-runtime-producer-stop").addEventListener("click", () => {
    sendAction({ action: "ssi_runtime_producer_stop" });
  });

  document.getElementById("ssi-runtime-producer-step").addEventListener("click", () => {
    sendAction({ action: "ssi_runtime_producer_step" });
  });

  document.getElementById("ssi-runtime-apply").addEventListener("click", () => {
    if (!genericSsiLoaded) {
      setRuntimeStatus("Load the generic SSI pair before Apply.", "var(--halted)");
      return;
    }
    if (running || genericSsiRunInFlight) {
      setRuntimeStatus("Wait for the current paired run to finish before Apply.", "var(--halted)");
      return;
    }
    graphClear();
    // Send one complete transaction.  The server validates the layout and
    // every frame before publishing a new generation.
    sendAction({
      action: "ssi_runtime_apply",
      profile: selectedProfile(),
      overrides: numericOverrides(),
      frames: frameTokens(),
    });
    setRuntimeStatus("Applying at an idle SSI frame boundary...", "var(--accent)");
  });

  document.getElementById("ssi-runtime-run").addEventListener("click", () => {
    if (!genericSsiLoaded) {
      setRuntimeStatus("Load the generic SSI pair before running it.", "var(--halted)");
      return;
    }
    if (running || genericSsiRunInFlight) {
      setRuntimeStatus("A paired run is already in progress.", "var(--halted)");
      return;
    }
    const steps = Math.max(100, Math.min(
      200000,
      Number(document.getElementById("ssi-runtime-run-steps").value) || 20000,
    ));
    const request_id = "ssi-runtime-run-" + nextRunRequestId++;
    const sent = sendAction({
      action: "run_multicore",
      core: "pru1",
      partner: "pru0",
      max_steps: Math.trunc(steps),
      capture: signalGraph.recording,
      request_id,
    });
    genericSsiRunInFlight = sent;
    if (sent) {
      genericSsiRequestId = request_id;
      trackRunRequest(request_id);
    }
    setRuntimeStatus("Running paired SSI cores...", "#888");
  });

  // Keep the catalog available to the profile-change handler without coupling
  // it to the websocket message format.
  const originalRender = window.renderSsiRuntimeState;
  window.renderSsiRuntimeState = function (msg) {
    window.ssiRuntimeProfileCatalog = msg.profiles || window.ssiRuntimeProfileCatalog || [];
    const newlyLoaded = msg.loaded === true && !genericSsiLoaded;
    genericSsiLoaded = msg.loaded === true;
    originalRender(msg);
    if (newlyLoaded) selectGenericSsiPartner();
  };
})();

// ---- GP Mux mode selector (GPCFG.PRU_GP_MUX_SEL) ---------------------------
(function () {
  const sel = document.getElementById("io-mux-sel");
  if (!sel) return;
  sel.addEventListener("change", () => {
    const mux = parseInt(sel.value, 10) || 0;
    graphClear();
    sendAction({ action: "gpcfg_write", core: currentCore, mux_sel: mux });
  });
})();
