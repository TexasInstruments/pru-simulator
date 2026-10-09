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
let simRunning = false;
let simTimer = null;
let _flashTimer = null;
let clientBreakpoints = new Set();
let _lastSourceKey = '';

// ---- Multi-core state ------------------------------------------------------
let multiCoreMode = false;
let mcPartner = "rtu0";            // second core shown in multi-core view
let mcPrevRegs = {
  pru0: new Array(32).fill("0x00000000"),
  rtu0: new Array(32).fill("0x00000000"),
  pru1: new Array(32).fill("0x00000000"),
};
let mcLastSourceKey = { pru0: '', rtu0: '', pru1: '' };
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
};

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

function initUI() {
  initSpadState();
  buildRegTable();
  buildPinGrid(gpoGrid, 20, "gpo", null);
  buildPinGrid(gpiGrid, 20, "gpi", handleGpiClick);
  initLayout('sc');
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
    refreshMemory();
    refreshMemory2();
    loadRegions();
  };

  ws.onclose = () => {
    wsStatus.textContent = "Disconnected";
    wsStatus.className = "error";
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
        if (multiCoreMode) {
          updateMCUI(msg);
        } else if (!msg.core || msg.core === currentCore) {
          updateUI(msg);
        }
      } else if (msg.type === "capture") {
        graphHandleCapture(msg);
        drawGraph();
      } else if (msg.type === "memory") {
        if (msg.tag === "mem2") renderMemory2(msg);
        else if (msg.tag && msg.tag.startsWith("graph-")) { graphHandleMemory(msg); drawGraph(); }
        else renderMemory(msg);
      } else if (msg.type === "uart_inject_ok") {
        const st = document.getElementById("uart-inj-status");
        if (st) {
          st.textContent = "\u2713 Armed: " + msg.payload_len + " bytes \u00D7 " +
            msg.frames + " frame" + (msg.frames > 1 ? "s" : "") +
            " \u00B7 trigger cycle " + msg.trigger_cycle + " \u00B7 run to receive";
          st.style.color = "#6a9955";
          st.style.display = "";
        }
      } else if (msg.type === "perif_ok") {
        const st = document.getElementById("perif-lb-status");
        if (st) {
          st.textContent = "✓ Loopback ch" + msg.channel + " " +
            (msg.enabled ? "enabled" : "disabled");
          st.style.display = "";
        }
      } else if (msg.type === "error") {
        if (msg.tag && msg.tag.startsWith("graph-")) graphMarkChannelError(msg.tag);
        else showErrors(msg.errors);
      }
    } catch (e) {
      console.error("Failed to parse message", e);
    }
  };
}

function sendAction(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(obj));
  }
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

  // CRC accelerator indicator
  if (state.crc) {
    document.getElementById("crc-mode-label").textContent = state.crc.mode;
    document.getElementById("crc-value-label").textContent = state.crc.value;
  }

  // Registers + SPAD
  updateRegisters(state.registers, state.carry);
  updateSpad(state.spad);

  // Source listing
  updateSource(state.instructions, state.pc, state.labels || {});

  // IO pins
  updatePins(state.io);
  updateSDPanel(state.io);
  updatePerifPanel(state.io);
  updateI2CPanel(state.io);

  // Signal graph sample
  graphSample(state);
  drawGraph();

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
  listEl.innerHTML = "";
  const addrs = [...breakpoints].sort((a, b) => a - b);
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
  // Build addr -> [sorted label names] map
  const addrToLabels = {};
  for (const [name, addr] of Object.entries(labels)) {
    if (!addrToLabels[addr]) addrToLabels[addr] = [];
    addrToLabels[addr].push(name);
  }
  for (const addr of Object.keys(addrToLabels)) addrToLabels[addr].sort();

  // Rebuild when instruction set or label set changes
  const newKey = instructions.map(i => i.addr + ':' + i.text).join('|')
               + '|' + Object.entries(labels).sort().join('|');
  if (newKey !== _lastSourceKey) {
    _lastSourceKey = newKey;
    sourceList.innerHTML = "";

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

  // Update breakpoint markers (instruction lines only)
  sourceList.querySelectorAll("li[id^='src-line-']").forEach(li => {
    const addr = +li.id.slice(9);
    li.classList.toggle("has-bp", clientBreakpoints.has(addr));
  });

  // Update current-pc highlight
  document.querySelectorAll("#source-list li.current-pc").forEach(el => el.classList.remove("current-pc"));
  const currentLine = document.getElementById(`src-line-${pc}`);
  if (currentLine) {
    currentLine.classList.add("current-pc");
    currentLine.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  renderBpBar("", currentCore, clientBreakpoints);
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
    r30El.innerHTML = '<span style="color:#ce93d8;">R30:</span> ' +
      `<span class="sd-r30-field">[29:26] ch_sel=<b>${sd.ch_sel}</b></span>` +
      `<span class="sd-r30-field">[25] sd_en=<b style="color:#66bb6a;">${sd.sd_en ? 1 : 0}</b></span>` +
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
      const titleColor = ch.selected ? '#4fc3f7' : '#888';
      const dotColor = ch.valid ? '#66bb6a' : '#666';
      card.innerHTML = `
        <div class="sd-channel-title" style="color:${titleColor};">
          CH ${ch.id} <span style="color:${dotColor};">●</span>${ch.selected ? ' <span style="font-size:9px;">★</span>' : ''}
        </div>
        <div class="sd-channel-config">${clkName} | OSR:${cfg.osr || ch.osr} | ${accName}</div>
        <div class="sd-acc-row">acc1: <span class="sd-acc-val">0x${(ch.acc1 || 0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
        <div class="sd-acc-row">acc2: <span class="sd-acc-val">0x${(ch.acc2 || 0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
        <div class="sd-acc-row">acc3: <span class="sd-acc-val">0x${(ch.acc3 || 0).toString(16).toUpperCase().padStart(6,'0')}</span></div>
        <div class="sd-status">
          <div style="color:${ch.valid ? '#ffb74d' : '#555'};">ovf=${ch.ovf ? 1 : 0} valid=<b style="color:${ch.valid ? '#66bb6a' : '#666'};">${ch.valid ? 1 : 0}</b></div>
          <div style="color:#fff;">data=0x${(ch.shadow_acc3 || 0).toString(16).toUpperCase().padStart(7,'0')}</div>
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
  if (!arr || !arr.length) return '<span style="color:#555;">empty</span>';
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
    dec.innerHTML = '<span style="color:#ce93d8;">shared:</span> ' +
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
  if (!i2c || !i2c.saw_start) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';

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
  const samples = graphGetSamples(); // oldest → newest
  signalGraph.windowSize = newSize;
  signalGraph.buf = new Array(newSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;
  // Re-insert keeping at most the last newSize samples
  const keep = samples.slice(-newSize);
  keep.forEach(s => graphPushSample(s));
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
    // IO mux mode at capture time: in perif mode the GPO/GPI pins are owned by
    // the Peripheral Interface, so R30/R31 must not be plotted as pin state.
    mode: state.io.mode || "gpio",
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
 *   [step, r30(20 bits), gpi bits, perif out bits, out_en bits, clk bits]
 */
function graphHandleCapture(msg) {
  if (!signalGraph.recording) return;
  const mode = msg.mode || "gpio";
  // Single-shot, perif captures only: those sample every instruction, so a run
  // fills the whole window within a few ms of wall clock and a rolling buffer
  // would just blur — evicting the transmission before anyone could look at it.
  // Fill once, then stop recording, the way a logic analyzer does. GP-mode
  // captures are decimated 100:1 and keep rolling as before.
  const singleShot = mode === "perif";
  for (const s of (msg.samples || [])) {
    if (singleShot && signalGraph.fill >= signalGraph.windowSize) {
      graphSetRecording(false);
      break;
    }
    const [step, r30, gpiBits, outBits, oeBits, clkBits] = s;
    graphPushSample({
      step,
      mode,
      gpo: Array.from({ length: 20 }, (_, i) => (r30 >> i) & 1),
      gpi: Array.from({ length: 20 }, (_, i) => (gpiBits >> i) & 1),
      perif: [0, 1, 2].map(i => (outBits >> i) & 1),
      perifOe: [0, 1, 2].map(i => (oeBits >> i) & 1),
      perifClk: [0, 1, 2].map(i => (clkBits >> i) & 1),
    });
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
  if (samples.length >= 2) {
    if (!perifMode) {
      for (let i = 0; i < 20; i++) {
        const vals = samples.map(s => s.gpo[i] || 0);
        if (vals.some(v => v !== vals[0])) {
          activeDig.push({ label: `GPO ${i}`, color: GRAPH_GPO_COLORS[i], data: vals });
        }
      }
      for (let i = 0; i < 20; i++) {
        const vals = samples.map(s => s.gpi[i] || 0);
        if (vals.some(v => v !== vals[0])) {
          activeDig.push({ label: `GPI ${i}`, color: GRAPH_GPI_COLORS[i], data: vals });
        }
      }
    }
    for (let i = 0; i < 3; i++) {
      // Data lane first, then out_en and the bit clock, so the group reads
      // together: out_en says when the pad is driven, the clock says where the
      // bits start (the perif serializer has no framing of its own).
      const out = samples.map(s => (s.perif && s.perif[i]) || 0);
      const oe  = samples.map(s => (s.perifOe && s.perifOe[i]) || 0);
      const clk = samples.map(s => (s.perifClk && s.perifClk[i]) || 0);
      const moved = a => a.some(v => v !== a[0]);
      // A channel that moved at all shows its full group, so a steady out_en
      // stays visible next to the data it qualifies.
      const chActive = moved(out) || moved(oe) || moved(clk);
      if (!chActive) continue;
      activeDig.push({ label: `perif${i}_out`,    color: GRAPH_PERIF_COLORS[i],     data: out });
      activeDig.push({ label: `perif${i}_out_en`, color: GRAPH_PERIF_OE_COLORS[i],  data: oe  });
      activeDig.push({ label: `perif${i}_clk`,    color: GRAPH_PERIF_CLK_COLORS[i], data: clk });
    }
  }

  if (samples.length < 2) {
    _graphUpdateStepLabel(samples);
    _graphUpdateLegend([], "graph-legend");
    return;
  }

  const N = samples.length;

  // ---- Draw digital lanes --------------------------------------------------
  if (activeDig.length > 0) {
    const rowH = H / activeDig.length;
    activeDig.forEach((ch, ri) => {
      const yBase = ri * rowH;
      const yHigh = yBase + rowH * 0.12;
      const yLow  = yBase + rowH * 0.84;
      ctx.strokeStyle = ch.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ch.data.forEach((v, i) => {
        const x = (i / (N - 1)) * W;
        const y = v ? yHigh : yLow;
        if (i === 0) { ctx.moveTo(x, y); return; }
        if (v !== ch.data[i - 1]) {
          const xm = ((i - 0.5) / (N - 1)) * W;
          ctx.lineTo(xm, ch.data[i - 1] ? yHigh : yLow);
          ctx.lineTo(xm, y);
        }
        ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.fillStyle = ch.color;
      ctx.font = "8px Consolas, monospace";
      ctx.fillText(ch.label, 3, yBase + 9);
    });
  }

  _graphUpdateStepLabel(samples);
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

function _graphUpdateStepLabel(samples) {
  const el = document.getElementById("graph-step-label");
  if (!el) return;
  if (samples.length === 0) { el.textContent = ""; return; }
  el.textContent = `step ${samples[samples.length - 1].step} / ${signalGraph.windowSize}`;
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

// ---- Signal graph — CSV export ---------------------------------------------

function exportGraphCSV() {
  const samples = graphGetSamples();
  if (samples.length === 0) return;

  const gpoHeaders = Array.from({ length: 20 }, (_, i) => `gpo${i}`);
  const gpiHeaders = Array.from({ length: 20 }, (_, i) => `gpi${i}`);
  const perifHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_out`);
  const perifOeHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_out_en`);
  const perifClkHeaders = Array.from({ length: 3 }, (_, i) => `perif${i}_clk`);
  const header = ["step", "mode", ...gpoHeaders, ...gpiHeaders,
                  ...perifHeaders, ...perifOeHeaders, ...perifClkHeaders].join(",");

  const rows = samples.map(s => {
    const gpo = Array.from({ length: 20 }, (_, i) => s.gpo[i] ?? 0);
    const gpi = Array.from({ length: 20 }, (_, i) => s.gpi[i] ?? 0);
    const perif = Array.from({ length: 3 }, (_, i) => (s.perif && s.perif[i]) ?? 0);
    const perifOe = Array.from({ length: 3 }, (_, i) => (s.perifOe && s.perifOe[i]) ?? 0);
    const perifClk = Array.from({ length: 3 }, (_, i) => (s.perifClk && s.perifClk[i]) ?? 0);
    return [s.step, s.mode ?? "gpio", ...gpo, ...gpi,
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
  sendAction({ action: "get_state", core: currentCore });
  updateCtableForCore();
});

btnStep.addEventListener("click", () => {
  stopRun(); stopSim();
  if (multiCoreMode) {
    sendAction({ action: "step", core: "pru0", count: 1 });
    sendAction({ action: "step", core: mcPartner, count: 1 });
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
  clearErrors();
  prevRegisters = new Array(32).fill("0x00000000");
  if (multiCoreMode) {
    mcPrevRegs = { pru0: new Array(32).fill("0x00000000"), rtu0: new Array(32).fill("0x00000000") };
    sendAction({ action: "reset", core: "pru0" });
    sendAction({ action: "reset", core: mcPartner });
  } else {
    sendAction({ action: "reset", core: currentCore });
  }
});

btnHardReset.addEventListener("click", () => {
  stopRun(); stopSim();
  clearErrors();
  prevRegisters = new Array(32).fill("0x00000000");
  if (multiCoreMode) {
    mcPrevRegs = { pru0: new Array(32).fill("0x00000000"), rtu0: new Array(32).fill("0x00000000") };
    sendAction({ action: "hard_reset", core: "pru0" });
    sendAction({ action: "get_state", core: mcPartner });
  } else {
    sendAction({ action: "hard_reset", core: currentCore });
  }
});

btnLoad.addEventListener("click", async () => {
  stopRun(); stopSim();
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
    mcLastSourceKey[targetCore] = '';
    const srcList = document.getElementById(`mc-${targetCore}-source-list`);
    if (srcList) srcList.innerHTML = "";
    sendAction({ action: "load", core: targetCore, source, filename });
  } else {
    prevRegisters = new Array(32).fill("0x00000000");
    sourceList.innerHTML = "";
    sendAction({ action: "load", core: currentCore, source, filename });
  }
});

// ---- Signal graph controls -------------------------------------------------

function graphSetRecording(on) {
  signalGraph.recording = on;
  const btn = document.getElementById("graph-rec-btn");
  const dot = document.getElementById("graph-rec-dot");
  if (btn) btn.classList.toggle("rec-on", on);
  if (dot) dot.classList.toggle("active", on);
}

document.getElementById("graph-rec-btn").addEventListener("click", () => {
  graphSetRecording(!signalGraph.recording);
});

document.getElementById("graph-clear-btn").addEventListener("click", () => {
  signalGraph.buf = new Array(signalGraph.windowSize);
  signalGraph.head = 0;
  signalGraph.fill = 0;
  const exportRow = document.getElementById("graph-export-row");
  if (exportRow) exportRow.style.display = "none";
  drawGraph();
});

document.getElementById("graph-win-sel").addEventListener("change", (e) => {
  graphResizeWindow(parseInt(e.target.value, 10));
  drawGraph();
});

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
  menu.style.cssText = "position:fixed;background:#252526;border:1px solid #3c3c3c;border-radius:4px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,.6);font-size:12px;";
  const rect = btnFile.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  // "Browse..." item at top — opens native file picker for .asm/.out files
  const browseItem = document.createElement("div");
  browseItem.style.cssText = "padding:6px 12px;cursor:pointer;color:#569cd6;border-bottom:1px solid #3c3c3c;font-style:italic;";
  browseItem.textContent = "Browse file system...";
  browseItem.addEventListener("mouseover", () => browseItem.style.background = "#094771");
  browseItem.addEventListener("mouseout",  () => browseItem.style.background = "");
  browseItem.addEventListener("click", () => { menu.remove(); fileInput.click(); });
  menu.appendChild(browseItem);

  allFiles.forEach(path => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:#d4d4d4;";
    item.textContent = path;
    item.addEventListener("mouseover", () => item.style.background = "#094771");
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
  menu.style.cssText = "position:fixed;background:#252526;border:1px solid #3c3c3c;border-radius:4px;z-index:2000;min-width:200px;max-height:300px;overflow-y:auto;box-shadow:0 4px 12px rgba(0,0,0,.6);font-size:12px;";
  const rect = btnOpenProject.getBoundingClientRect();
  menu.style.left = rect.left + "px";
  menu.style.top  = (rect.bottom + 4) + "px";

  projectNames.forEach(projName => {
    const item = document.createElement("div");
    item.style.cssText = "padding:6px 12px;cursor:pointer;color:#d4d4d4;";
    item.textContent = projName;
    item.addEventListener("mouseover", () => item.style.background = "#094771");
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
  btnRun.textContent = "Stop";
  btnRun.classList.add("btn-reset");
  btnRun.classList.remove("btn-run");
  runInterval = setInterval(() => {
    // While recording, the server samples the graph inside its run loop and
    // ships the batch as a "capture" message (per instruction in perif mode,
    // 100:1 otherwise). The chunk size no longer sets the sample rate, so it
    // stays at the fast 1000.
    const capture = signalGraph.recording;
    const max_steps = 1000;
    if (multiCoreMode) {
      sendAction({ action: "run_multicore", core: "pru0",
                   partner: mcPartner, max_steps, capture });
    } else {
      sendAction({ action: "run", core: currentCore, max_steps, capture });
    }
  }, 10);
}

function stopRun() {
  if (!running) return;
  running = false;
  btnRun.textContent = "Run";
  btnRun.classList.add("btn-run");
  btnRun.classList.remove("btn-reset");
  if (runInterval !== null) {
    clearInterval(runInterval);
    runInterval = null;
  }
}

function startSim() {
  stopRun();
  simRunning = true;
  btnSim.textContent = "Stop SIM";
  btnSim.classList.add("btn-reset");
  btnSim.classList.remove("btn-sim");
  const ms = Math.round((parseFloat(simIntervalInput.value) || 1.0) * 1000);
  simTimer = setInterval(() => {
    if (multiCoreMode) {
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
  btnSim.textContent = "SIM";
  btnSim.classList.remove("btn-reset");
  btnSim.classList.add("btn-sim");
  if (simTimer !== null) {
    clearInterval(simTimer);
    simTimer = null;
  }
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
let memAutoRefresh = false;
let _memAutoLastFetch = 0;

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
let memAutoRefresh2 = false;
let _memAutoLastFetch2 = 0;

// Auto-refresh: throttled trigger on every simulation "state" update (near
// real-time while stepping/running), plus a 1 s floor interval that catches
// changes with no accompanying state message (Fill, writes from the other
// panel, config reload, UART/perif injection).
const MEM_AUTO_THROTTLE_MS = 300;

function memAutoOnStateChange() {
  const now = Date.now();
  if (memAutoRefresh && now - _memAutoLastFetch >= MEM_AUTO_THROTTLE_MS) {
    _memAutoLastFetch = now;
    refreshMemory();
  }
  if (memAutoRefresh2 && now - _memAutoLastFetch2 >= MEM_AUTO_THROTTLE_MS) {
    _memAutoLastFetch2 = now;
    refreshMemory2();
  }
}

setInterval(() => {
  const now = Date.now();
  if (memAutoRefresh && now - _memAutoLastFetch >= 1000) {
    _memAutoLastFetch = now;
    refreshMemory();
  }
  if (memAutoRefresh2 && now - _memAutoLastFetch2 >= 1000) {
    _memAutoLastFetch2 = now;
    refreshMemory2();
  }
}, 1000);

memAutoRefreshBox.addEventListener("change", () => {
  memAutoRefresh = memAutoRefreshBox.checked;
  if (memAutoRefresh) { _memAutoLastFetch = Date.now(); refreshMemory(); }
});

memAutoRefreshBox2.addEventListener("change", () => {
  memAutoRefresh2 = memAutoRefreshBox2.checked;
  if (memAutoRefresh2) { _memAutoLastFetch2 = Date.now(); refreshMemory2(); }
});

btnMemRefresh2.addEventListener("click", refreshMemory2);
memAddrInput2.addEventListener("keydown", (e) => { if (e.key === "Enter") refreshMemory2(); });
memAddrInput2.addEventListener("change", refreshMemory2);

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

function refreshMemory2() {
  const raw = memAddrInput2.value.trim();
  let addr;
  if (regionMap[raw] !== undefined) {
    addr = regionMap[raw];
    memAddrInput2.value = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
  } else {
    addr = parseInt(raw, 16) || parseInt(raw, 10) || 0x00010000;
  }
  const length = parseInt(memLenInput2.value, 10) || 1024;
  sendAction({ action: "read_memory", addr, length, tag: "mem2" });
}

function renderMemory2(msg) {
  const addr = msg.addr;
  const data = msg.data;
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
  input.style.cssText = `width:${Math.max(56, maxLen * 8)}px;font-size:13px;text-align:center;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0;font-family:inherit;`;
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
  ctx.fillStyle = "#1a1a1a";
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
    ctx.strokeStyle = bright ? "#3c3c3c" : "#262626";
    ctx.beginPath(); ctx.moveTo(Y_MAR, py); ctx.lineTo(W, py); ctx.stroke();
  }

  // ---- Y-axis labels -------------------------------------------------------
  ctx.font      = "9px Consolas, monospace";
  ctx.fillStyle = "#858585";
  ctx.textAlign = "right";
  ctx.fillText(fmtYLabel(yMax), Y_MAR - 4, yTopPx + 4);
  ctx.fillText(fmtYLabel(yMid), Y_MAR - 4, midY   + 3);
  ctx.fillText(fmtYLabel(yMin), Y_MAR - 4, yBotPx + 4);

  // ---- Waveform -----------------------------------------------------------
  const cycles = parseFloat(fillWaveCycles.value) || 1;
  ctx.strokeStyle = "#569cd6";
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
  ctx.fillStyle = "#858585";
  for (let i = 0; i <= nTicks; i++) {
    const frac  = i / nTicks;
    const px    = Y_MAR + Math.round(frac * WAVE_W);
    const label = Math.round(frac * nElems).toString();

    ctx.strokeStyle = "#555";
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

function refreshMemory() {
  const raw = memAddrInput.value.trim();
  let addr;
  if (regionMap[raw] !== undefined) {
    addr = regionMap[raw];
    memAddrInput.value = "0x" + addr.toString(16).padStart(8, "0").toUpperCase();
  } else {
    addr = parseInt(raw, 16) || parseInt(raw, 10) || 0;
  }
  const length = parseInt(memLenInput.value, 10) || 1024;
  sendAction({ action: "read_memory", addr, length, tag: "mem1" });
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
  const addr = msg.addr;
  const data = msg.data;
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
  input.style.cssText = `width:80px;font-size:13px;text-align:center;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0;font-family:inherit;`;
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
  input.style.cssText = "width:88px;font-size:13px;text-align:right;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0 2px;font-family:inherit;";
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
  mcPartner = mcPartnerSelect.value;
  applyMCPartnerLabels();
  if (multiCoreMode) {
    // Reset the partner DOM slot and re-request state for the new core.
    mcPrevRegs.rtu0 = new Array(32).fill("0x00000000");
    mcLastSourceKey.rtu0 = '';
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
  input.style.cssText = "width:88px;font-size:13px;text-align:right;background:#1a1a1a;color:#fff;border:1px solid var(--accent);padding:0 2px;font-family:inherit;";
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

  // CRC indicator
  if (state.crc) {
    const crcModeEl = document.getElementById(`mc-${core}-crc-mode`);
    if (crcModeEl) crcModeEl.textContent = state.crc.mode;
    const crcValueEl = document.getElementById(`mc-${core}-crc-value`);
    if (crcValueEl) crcValueEl.textContent = state.crc.value;
  }

  // Registers
  updateMCRegisters(core, state.registers);

  // Source listing
  updateMCSource(core, state.instructions, state.pc, state.labels || {});

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

  const addrToLabels = {};
  for (const [name, addr] of Object.entries(labels)) {
    if (!addrToLabels[addr]) addrToLabels[addr] = [];
    addrToLabels[addr].push(name);
  }
  for (const addr of Object.keys(addrToLabels)) addrToLabels[addr].sort();

  const newKey = instructions.map(i => i.addr + ':' + i.text).join('|')
               + '|' + Object.entries(labels).sort().join('|');

  if (newKey !== mcLastSourceKey[core]) {
    mcLastSourceKey[core] = newKey;
    listEl.innerHTML = "";

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

  // Breakpoint markers
  listEl.querySelectorAll(`li[id^='mc-${core}-src-line-']`).forEach(li => {
    const addr = parseInt(li.id.replace(`mc-${core}-src-line-`, ""), 10);
    li.classList.toggle("has-bp", mcBreakpoints[core].has(addr));
  });

  // Current PC highlight
  listEl.querySelectorAll("li.current-pc").forEach(el => el.classList.remove("current-pc"));
  const currentLine = document.getElementById(`mc-${core}-src-line-${pc}`);
  if (currentLine) {
    currentLine.classList.add("current-pc");
    currentLine.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

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
    if (multiCoreMode) {
      sendAction({ action: "step", core: "pru0", count: 1 });
      sendAction({ action: "step", core: mcPartner, count: 1 });
    } else {
      sendAction({ action: "step", core: currentCore, count: 1 });
    }
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    stopRun(); stopSim();
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
    return showHint("Use Signal Graph ● REC to capture GPO0 first");
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
  document.getElementById("uart-hint").textContent = "Use Signal Graph ● REC to capture GPO0 first";
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
      statusEl.style.color = "#f38ba8";
      statusEl.style.display = "";
      return;
    }

    const baudStr = document.getElementById("uart-inj-baud").value.trim();
    const baudMb = parseFloat(baudStr);
    if (isNaN(baudMb) || baudMb <= 0 || baudMb > 10) {
      statusEl.textContent = "\u2717 Baud must be between 0.01 and 10.00 Mb";
      statusEl.style.color = "#f38ba8";
      statusEl.style.display = "";
      return;
    }
    const baudrate = Math.round(baudMb * 1_000_000);

    const framesVal = parseInt(document.getElementById("uart-inj-frames").value, 10);
    if (isNaN(framesVal) || framesVal < 1 || framesVal > 100) {
      statusEl.textContent = "\u2717 Frames must be between 1 and 100";
      statusEl.style.color = "#f38ba8";
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
    statusEl.style.color = "#888";
    statusEl.style.display = "";
  });
})();

// ---- GP Mux mode selector (GPCFG.PRU_GP_MUX_SEL) ---------------------------
(function () {
  const sel = document.getElementById("io-mux-sel");
  if (!sel) return;
  sel.addEventListener("change", () => {
    const mux = parseInt(sel.value, 10) || 0;
    sendAction({ action: "gpcfg_write", core: currentCore, mux_sel: mux });
  });
})();
