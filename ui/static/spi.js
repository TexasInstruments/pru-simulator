"use strict";

// SPI view (I/O & Devices -> SPI). The drawing and decoding helpers are pure so
// they can be tested without a DOM; render() only assigns their output.
(() => {
  const SCENARIO = 'SPI multi-core (controller + peripheral)';
  const FRAME_BYTES = 19;
  const GPI_DELAY_NOTE = '(calibrated 18 ns @ 333 MHz)';
  const COLORS = { cs: '#d64545', clk: '#f5a623', mosi: '#2f7fd6', miso: '#1f9d6b' };
  const FIELD_COLORS = { CMD: '#7b4fd6', ADR: '#2f7fd6', IDLE: '#888888', D: '#1f9d6b' };

  const esc = value => String(value ?? '').replace(/[&<>"]/g,
    ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
  const hex2 = value => (value & 0xFF).toString(16).toUpperCase().padStart(2, '0');
  const num = value => (Math.round(value * 100) / 100).toString();
  const cs = word => word & 1;
  const clk = word => (word >> 1) & 1;
  const mosiBit = word => (word >> 2) & 1;
  const misoBit = word => (word >> 3) & 1;

  function fieldName(index) {
    return index === 0 ? 'CMD' : index === 1 ? 'ADR' : index === 2 ? 'IDLE' : `D${index - 3}`;
  }
  function fieldColor(index) {
    return index === 0 ? FIELD_COLORS.CMD : index === 1 ? FIELD_COLORS.ADR
      : index === 2 ? FIELD_COLORS.IDLE : FIELD_COLORS.D;
  }

  // ---- rate choices -----------------------------------------------------------
  function rateOptions(supported, mode) {
    const rows = (supported && (supported[mode] || supported[String(mode)])) || [];
    return rows.map(row => {
      const spi = Number(row.spi_mhz), core = Number(row.core_mhz);
      let disabled = false, title = `Core ${core} MHz (set automatically, this session only)`;
      if (row.pass === false) {
        disabled = true;
        title = 'Not proven: this rate did not pass the full test sequence';
      }
      if (spi >= 50 && core <= 200) {
        disabled = true;
        title = '50 MHz is not offered at a 200 MHz core clock';
      }
      return { spi_mhz: spi, core_mhz: core, label: `${num(spi)} MHz (core ${core} MHz)`, disabled, title };
    });
  }

  // ---- MISO sample delay choices (Ruling R17) ---------------------------------
  // Mirrors spi_link.controller_delay_ok: the sample is its own R31 read, so it
  // cannot land on a CLK write (offset 0 or k in a bit), and at most 6 bits deep.
  const MISO_Q_MAX = 6;
  function delayOk(halfK, d) {
    if (!Number.isInteger(d) || d < 0) return false;
    const s = d % (2 * halfK);
    return s !== 0 && s !== halfK && Math.floor(d / (2 * halfK)) <= MISO_Q_MAX;
  }
  function findRow(supported, mode, spiMhz) {
    const rows = (supported && (supported[mode] || supported[String(mode)])) || [];
    return rows.find(r => Math.abs(Number(r.spi_mhz) - spiMhz) < 5e-4) || null;
  }
  // Realisable miso_delay values around the row's passing window (one bit
  // period either side), passing ones and the sweep's choice marked.
  function misoDelayOptions(row) {
    if (!row || !row.half_k) return [];
    const k = Number(row.half_k), per = 2 * k;
    const pass = new Set(row.miso_delay_pass || []);
    const win = row.miso_delay_window || [row.miso_delay, row.miso_delay];
    const lo = Math.max(1, win[0] - per), hi = Math.min(per * (MISO_Q_MAX + 1) - 1, win[1] + per);
    const out = [];
    for (let d = lo; d <= hi; d++) {
      if (!delayOk(k, d)) continue;
      const passes = pass.has(d), chosen = d === row.miso_delay;
      out.push({ value: d, pass: passes, chosen,
        label: `${d} cycles${passes ? ' \u2713 passes' : ' \u2717 fails'}${chosen ? ' (default)' : ''}`,
        title: passes ? `In the passing window ${win[0]}..${win[1]}: PRU0 reads PRU1 correctly`
          : `Outside the passing window ${win[0]}..${win[1]}: PRU0 reads the wrong bit` });
    }
    return out;
  }

  // ---- decode ------------------------------------------------------------------
  // edges: [[cycle, word], ...] word bits 0 /CS, 1 CLK, 2 MOSI, 3 MISO. The first
  // entry is the edge before the frame. Returns the frame's records plus its
  // capture and launch edge cycles for the selected mode.
  function frameRecords(edges, mode) {
    const recs = [];
    let start = null;
    for (let i = 0; i < edges.length; i++) {
      const [cycle, word] = edges[i];
      const prev = i > 0 ? edges[i - 1][1] : 1;
      if (start === null) {
        if (cs(prev) === 1 && cs(word) === 0) { start = cycle; recs.push([cycle, word]); }
      } else {
        recs.push([cycle, word]);
        if (cs(word) === 1) break;
      }
    }
    if (start === null) return null;
    const capture = [], rise = [], fall = [];
    for (let i = 1; i < recs.length; i++) {
      const a = clk(recs[i - 1][1]), b = clk(recs[i][1]);
      if (a === b || cs(recs[i][1]) === 1) continue;
      (b === 1 ? rise : fall).push(recs[i][0]);
    }
    const launch = mode === 1 ? rise : [start].concat(fall);
    return { start, end: recs[recs.length - 1][0], recs, capture: mode === 0 ? rise : fall, launch };
  }
  function levelAt(edges, cycle, pick) {
    let level = edges.length ? pick(edges[0][1]) : 0;
    for (const [c, w] of edges) { if (c > cycle) break; level = pick(w); }
    return level;
  }
  function median(values) {
    const sorted = values.slice().sort((a, b) => a - b);
    return sorted.length ? sorted[sorted.length >> 1] : 0;
  }

  // opts.miso_delay / opts.hop_delay: where PRU0 reads MISO (launch + miso_delay -
  // hop_delay cycles). Without them MISO is read at the capture edge like MOSI.
  function decodeBoxes(edges, mode, opts) {
    const frame = edges && frameRecords(edges, mode);
    if (!frame || !frame.capture.length) return [];
    const cap = frame.capture;
    const half = cap.length > 1 ? median(cap.slice(1).map((c, i) => c - cap[i])) / 2 : 4;
    const delayed = opts && opts.miso_delay != null && opts.hop_delay != null;
    const hop = delayed ? opts.hop_delay : 0;
    const bits = { mosi: [], miso: [] };
    cap.forEach((c, i) => {
      bits.mosi.push(levelAt(frame.recs, c, mosiBit));
      const sample = delayed && frame.launch[i] != null ? frame.launch[i] + opts.miso_delay - hop : c;
      bits.miso.push(levelAt(edges, sample, misoBit));
    });
    const boxes = [];
    for (const lane of ['mosi', 'miso']) {
      for (let b = 0; b * 8 < cap.length; b++) {
        const first = b * 8, last = Math.min(first + 8, cap.length) - 1;
        let value = 0;
        for (let i = first; i <= last; i++) value = (value << 1) | bits[lane][i];
        const partial = last - first < 7;
        if (partial) value <<= 7 - (last - first);
        let startCycle = Math.max(cap[first] - half, frame.start);
        let endCycle = cap[last] + half;
        if (lane === 'miso' && delayed) {
          startCycle = (frame.launch[first] ?? startCycle) + hop;
          endCycle = (frame.launch[last + 1] ?? endCycle) + hop;
        }
        const name = fieldName(b);
        boxes.push({ lane, index: b, name, startCycle, endCycle, value, partial,
          label: `${name} ${hex2(value)}` });
      }
    }
    return boxes;
  }

  // ---- compare -----------------------------------------------------------------
  function mismatchIndexes(a, b) {
    const out = [];
    if (!a || !b) return out;
    for (let i = 0; i < Math.max(a.length, b.length); i++) if (a[i] !== b[i]) out.push(i);
    return out;
  }
  function compareBadge(state) {
    if (!state || !state.last) return { ok: null, mismatches: 0, text: 'No frame sent yet' };
    const mem = state.memory || {};
    if (!mem.p_rx || !mem.p_tx) return { ok: null, mismatches: 0, text: 'Address out of range: nothing to compare' };
    const expect = state.expect || { mosi: true, miso: true };
    const mosi = mismatchIndexes(mem.c_tx, mem.p_rx).length;
    const miso = mismatchIndexes(mem.p_tx, mem.c_rx).length;
    const bad = (expect.mosi ? mosi : 0) + (expect.miso ? miso : 0);
    const parts = [];
    if (expect.mosi) parts.push(`MOSI ${16 - mosi}/16`);
    if (expect.miso) parts.push(`MISO ${16 - miso}/16`);
    if (!parts.length) return { ok: true, mismatches: 0, text: 'No data transfer expected for this command' };
    if (bad) return { ok: false, mismatches: bad, text: `${bad} mismatch${bad === 1 ? '' : 'es'} (${parts.join(', ')})` };
    const both = expect.mosi && expect.miso;
    return { ok: true, mismatches: 0,
      text: both ? '16/16 bytes match both ways' : `16/16 bytes match (${expect.mosi ? 'MOSI to PRU1 RX' : 'PRU1 TX to MISO'})` };
  }

  // ---- timing table ------------------------------------------------------------
  function timingRows(rows) {
    const out = (rows || []).map(row => ({ ...row, worst: false }));
    const flagged = (rows || []).findIndex(row => row.worst);
    let pick = flagged;
    if (pick < 0) {
      let best = Infinity;
      out.forEach((row, i) => {
        if (row.margin_ns != null && !row.derived && row.margin_ns < best) { best = row.margin_ns; pick = i; }
      });
    }
    if (pick >= 0) out[pick].worst = true;
    return out;
  }
  const unitOf = symbol => symbol === 'SRI' ? 'V/ns' : symbol === 'CL' ? 'pF' : 'ns';
  function cell(value, unit, signed) {
    if (value == null) return '—';
    const text = num(value);
    return `${signed && value > 0 ? '+' : ''}${text} ${unit}`;
  }
  function symbolHtml(symbol) {
    return String(symbol).split('/').map(part => {
      const m = /^(t|SR|C)(.+)$/.exec(part);
      return m && !/^(MISO_DELAY|HOP)$/.test(part) ? `${esc(m[1])}<sub>${esc(m[2])}</sub>` : esc(part);
    }).join('/');
  }
  const isDatasheet = row => /^Table 6-11\d/.test(String(row.source || ''));
  function timingTableHtml(rows) {
    const list = timingRows(rows);
    const head = '<tr><th>Parameter</th><th>Symbol</th><th>Design</th><th>Cycles</th><th>Sim</th>' +
      '<th>AM243 min</th><th>AM243 max</th><th>Source</th>' +
      '<th>Worst-case margin</th></tr>';
    const body = list.map(r => {
      const unit = unitOf(r.symbol);
      const none = r.am243_min == null && r.am243_max == null;
      // Only a datasheet table (SPRSP65J Table 6-11x) is tinted as datasheet data (R18).
      const dsClass = isDatasheet(r) ? 'spi-ds' : none ? 'spi-na' : '';
      const signed = r.am243_min != null && r.am243_min < 0;
      const lo = none ? 'none' : (r.am243_min == null ? 'n/a' : cell(r.am243_min, unit, signed));
      const hi = none ? 'none' : (r.am243_max == null ? 'n/a' : cell(r.am243_max, unit, signed));
      const margin = r.margin_ns == null ? 'n/a' : cell(r.margin_ns, 'ns');
      const marginClass = r.worst ? 'spi-warn' : r.margin_ns != null && r.margin_ns < 0 ? 'spi-bad' : 'spi-ok';
      const sim = r.sim_ns == null ? '—' : cell(r.sim_ns, 'ns') + (r.derived ? ' (derived)' : '');
      return `<tr class="${r.worst ? 'spi-worst' : ''}"><td>${esc(r.param)}</td><td>${symbolHtml(r.symbol)}</td>` +
        `<td>${cell(r.design_ns, 'ns')}</td><td>${r.cycles == null ? '—' : esc(r.cycles)}</td><td>${sim}</td>` +
        `<td class="${dsClass}">${esc(lo)}</td><td class="${dsClass}">${esc(hi)}</td>` +
        `<td class="${dsClass}">${esc(r.source)}</td>` +
        `<td class="${marginClass}">${esc(margin)}${r.worst ? ' (worst)' : ''}</td></tr>`;
    }).join('');
    return `<table class="spi-tp"><thead>${head}</thead><tbody>${body}</tbody></table>`;
  }

  // ---- frame strip and memory --------------------------------------------------
  // MISO as PRU0 reads it (R15): never the backend monitor's nominal-edge decode.
  function misoBytes(state) {
    const frame = state.frame, cfg = state.config;
    if (!frame || !frame.edges || !cfg) return [];
    return decodeBoxes(frame.edges, cfg.mode, { miso_delay: cfg.miso_delay, hop_delay: state.hop_delay })
      .filter(b => b.lane === 'miso' && !b.partial).map(b => hex2(b.value));
  }
  function frameStripHtml(state, cursorByte) {
    const frame = state.frame || {};
    const mosi = frame.mosi || [], miso = misoBytes(state);
    const cells = [];
    for (let i = 0; i < FRAME_BYTES; i++) {
      const cls = i === 0 ? 'spi-c-cmd' : i === 1 ? 'spi-c-adr' : i === 2 ? 'spi-c-idl' : 'spi-c-dat';
      cells.push(`<div class="${cls}${i === cursorByte ? ' spi-c-cur' : ''}" title="${fieldName(i)}">` +
        `<span class="spi-c-name">${fieldName(i)}</span>` +
        `<span class="spi-c-val">${mosi[i] != null ? esc(mosi[i]) : '--'}</span>` +
        `<span class="spi-c-val spi-c-miso">${miso[i] != null ? esc(miso[i]) : '--'}</span></div>`);
    }
    return cells.join('');
  }
  function memoryHtml(values, other, active, nowIndex) {
    if (!values) return '<div class="spi-mem-empty">out of range</div>';
    const bad = new Set(active ? mismatchIndexes(values, other) : []);
    return values.map((v, i) => {
      const cls = bad.has(i) ? 'spi-bad' : active ? (i === nowIndex ? 'spi-now' : 'spi-hit') : '';
      return `<div class="${cls}">${esc(v)}</div>`;
    }).join('');
  }

  // ---- scope -------------------------------------------------------------------
  const LANES = [['cs', '/CS', 30], ['clk', 'CLK', 82], ['mosi', 'MOSI', 142], ['miso', 'MISO', 230]];
  const W = 1100, H = 292, X0 = 52, HI = -12, LO = 12;

  function scopeModel(state, cursorBit) {
    const frame = state && state.frame;
    const cfg = state && state.config;
    if (!frame || !frame.edges || !frame.edges.length || frame.start == null) return null;
    const rec = frameRecords(frame.edges, cfg.mode);
    if (!rec) return null;
    const span = rec.end - rec.start;
    const pad = Math.max(16, Math.round(span * 0.02));
    const t0 = rec.start - pad, t1 = rec.end + pad;
    const x = c => X0 + (c - t0) / (t1 - t0) * (W - X0 - 10);
    const bit = Math.min(Math.max(cursorBit, 0), Math.max(rec.capture.length - 1, 0));
    return { frame: rec, cfg, t0, t1, x, bit, ns: 1000 / cfg.core_mhz };
  }

  function path(edges, pick, y, x, t0, t1) {
    let level = pick(edges[0][1]);
    let d = `M${x(t0).toFixed(1)} ${y + (level ? HI : LO)}`;
    for (const [c, w] of edges.slice(1)) {
      const next = pick(w);
      if (next === level) continue;
      level = next;
      d += ` H${x(c).toFixed(1)} V${y + (level ? HI : LO)}`;
    }
    return d + ` H${x(t1).toFixed(1)}`;
  }

  function scopeSvg(state, cursorBit) {
    const m = scopeModel(state, cursorBit);
    if (!m) return '<div class="spi-scope-empty">No frame captured yet. Send a frame to see the pin trace.</div>';
    const { cfg, frame, x } = m;
    const edges = state.frame.edges;
    const opts = { miso_delay: cfg.miso_delay, hop_delay: state.hop_delay };
    let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="SPI pin trace" class="spi-svg">` +
      '<defs><marker id="spi-ah" viewBox="0 0 6 6" refX="3" refY="3" markerWidth="5" markerHeight="5" orient="auto-start-reverse">' +
      '<path d="M0 0L6 3L0 6z" fill="currentColor"/></marker></defs>';
    const pick = { cs, clk, mosi: mosiBit, miso: misoBit };
    for (const [key, name, y] of LANES) {
      s += `<text x="4" y="${y + 4}" class="spi-lane-name">${name}</text>`;
      s += `<path d="${path(edges, pick[key], y, x, m.t0, m.t1)}" stroke="${COLORS[key]}" fill="none" stroke-width="1.6"/>`;
    }
    for (const box of decodeBoxes(edges, cfg.mode, opts)) {
      const y = box.lane === 'mosi' ? 142 : 230;
      const xa = x(box.startCycle), w = x(box.endCycle) - xa;
      s += `<rect x="${(xa + 1).toFixed(1)}" y="${y - 34}" width="${Math.max(w - 2, 1).toFixed(1)}" height="14" rx="3" ` +
        `fill="${fieldColor(box.index)}" opacity=".88"><title>${esc(box.lane.toUpperCase())} ${esc(box.label)}</title></rect>`;
      if (w > 36) s += `<text x="${(xa + w / 2).toFixed(1)}" y="${y - 24}" class="spi-box-text" text-anchor="middle">${esc(box.label)}</text>`;
    }
    // capture-edge markers on CLK
    for (const c of frame.capture) {
      s += `<path d="M${x(c).toFixed(1)} ${82 + HI - 4} l-2.5 -5 h5 z" fill="${COLORS.clk}" opacity=".8"/>`;
    }
    // PRU0's MISO sample points
    frame.launch.forEach(l => {
      const sample = l + cfg.miso_delay - state.hop_delay;
      if (sample <= frame.end) s += `<circle cx="${x(sample).toFixed(1)}" cy="${230 + HI - 4}" r="1.8" fill="${COLORS.miso}" opacity=".75"/>`;
    });
    s += cursorGroup(state, m, edges);
    return s + '</svg>';
  }

  function cursorGroup(state, m, edges) {
    const { frame, x, ns, bit, cfg } = m;
    const capture = frame.capture[bit];
    if (capture == null) return '';
    const launch = frame.launch[bit] ?? capture - cfg.half_k;
    const nextLaunch = frame.launch[bit + 1] ?? launch + 2 * cfg.half_k;
    const label = (a, b) => `${num((b - a) * ns)} ns`;
    const arrow = (a, b, y, text, color) => `<line x1="${x(a).toFixed(1)}" x2="${x(b).toFixed(1)}" y1="${y}" y2="${y}" ` +
      `stroke="${color}" stroke-width="1.2" marker-start="url(#spi-ah)" marker-end="url(#spi-ah)" style="color:${color}"/>` +
      `<text x="${((x(a) + x(b)) / 2).toFixed(1)}" y="${y - 3}" fill="${color}" class="spi-arrow-text" text-anchor="middle">${esc(text)}</text>`;
    let s = `<line x1="${x(capture).toFixed(1)}" x2="${x(capture).toFixed(1)}" y1="60" y2="${230 + 22}" stroke="${COLORS.clk}" stroke-dasharray="3 3"/>`;
    s += `<line x1="${x(launch).toFixed(1)}" x2="${x(launch).toFixed(1)}" y1="60" y2="${230 + 22}" stroke="currentColor" stroke-dasharray="2 4" opacity=".4"/>`;
    s += arrow(launch, nextLaunch, 56, `tCLK ${label(launch, nextLaunch)}`, COLORS.clk);
    const first = frame.capture[0];
    const firstClk = Math.min(frame.launch[0] ?? first, first);
    s += arrow(frame.start, Math.max(firstClk, frame.start + 1), 56, `tLEAD ${label(frame.start, firstClk)}`, COLORS.cs);
    // MOSI setup and hold around the capture edge
    const recs = frame.recs;
    const changes = recs.slice(1).filter((r, i) => mosiBit(r[1]) !== mosiBit(recs[i][1])).map(r => r[0]);
    const before = changes.filter(c => c <= capture).pop();
    const after = changes.find(c => c > capture);
    const per = 2 * cfg.half_k;
    if (before != null && before < capture && capture - before <= per) s += arrow(before, capture, 142 + 24, `tSU ${label(before, capture)}`, COLORS.mosi);
    if (after != null && after - capture <= per) s += arrow(capture, after, 142 + 40, `tH ${label(capture, after)}`, COLORS.mosi);
    // MISO change for this bit up to PRU0's sample point
    const sample = launch + cfg.miso_delay - state.hop_delay;
    const misoChanges = edges.slice(1).filter((r, i) => misoBit(r[1]) !== misoBit(edges[i][1])).map(r => r[0]);
    const own = misoChanges.find(c => c >= launch + state.hop_delay && c < nextLaunch + state.hop_delay);
    if (own != null && own < sample) s += arrow(own, sample, 230 + 28, `tSU,MISO ${label(own, sample)}`, '#d68a00');
    s += `<text x="${(x(capture) + 4).toFixed(1)}" y="${H - 6}" class="spi-arrow-text" opacity=".75">cursor: capture edge, bit ${bit} (${fieldName(Math.floor(bit / 8))})</text>`;
    return s;
  }

  // ---- recorded test-sequence steps -----------------------------------------------
  const CMD_NAMES = { 1: 'WRITE (01)', 2: 'READ (02)', 3: 'READ/WRITE (03)', 4: 'Unknown (04)' };

  function chipsHtml(steps, loop, current, locked) {
    const inLoop = new Set(loop || []);
    return (steps || []).map((st, i) => {
      const cls = ['spi-chip', st.ok ? 'spi-chip-ok' : 'spi-chip-bad'];
      if (inLoop.has(st.step)) cls.push('spi-chip-loop');
      if (i === current) cls.push('spi-chip-cur');
      const title = !st.ok && st.notes && st.notes.length ? ` title="${esc(st.notes.join('; '))}"` : '';
      return `<button type="button" class="${cls.join(' ')}" data-step="${i}"${title}${locked ? ' disabled' : ''}>` +
        `${st.ok ? '\u2713' : '\u2717'} ${esc(st.step)}</button>`;
    }).join('');
  }

  // A recorded step shown in place of the live frame: frame, panels, badge and
  // timing come from the step; configuration, status and faults stay live.
  function stepState(state, step) {
    return { ...state, frame: step.frame, memory: step.memory, last: step.last,
      expect: step.expect, match: step.match, timing: step.timing };
  }

  function stepTitle(steps, i) {
    const st = steps[i];
    const head = `Step ${i + 1}/${steps.length} \u00b7 ${st.step}`;
    if (st.cmd == null) return st.tcsh_cycles != null ? `${head} \u00b7 /CS-high gap ${st.tcsh_cycles} cycles` : head;
    return `${head} \u00b7 ${CMD_NAMES[st.cmd] || hex2(st.cmd)} @ 0x${hex2(st.addr)} \u00b7 pattern ${st.pattern}`;
  }

  // ---- DOM ---------------------------------------------------------------------
  const view = { send: null, stop: null, supported: null, requested: false, busy: false,
    cursor: 31, last: null, seeded: false, bound: false, layout: null, html: {},
    steps: null, stepsAsked: null, shown: null, locked: false, displayed: null };
  const byId = id => document.getElementById(id);

  function setHtml(id, html) {
    if (view.html[id] === html) return;
    view.html[id] = html;
    const el = byId(id);
    if (el) el.innerHTML = html;
  }
  function setText(id, text) { const el = byId(id); if (el) el.textContent = text; }

  function setBusy(busy) {
    view.busy = busy;
    for (const id of ['spi-send', 'spi-sequence', 'spi-mode', 'spi-rate', 'spi-miso-delay']) {
      const el = byId(id);
      if (el) el.disabled = busy || ((id === 'spi-rate' || id === 'spi-miso-delay') && !view.supported);
    }
    const status = byId('spi-status');
    if (status) status.textContent = busy ? 'Running…' : '';
  }

  function populateRates(mode, selected) {
    const options = rateOptions(view.supported, mode);
    const chosen = options.find(o => Math.abs(o.spi_mhz - selected) < 1e-6) || options.find(o => !o.disabled);
    setHtml('spi-rate', options.map(o => `<option value="${o.spi_mhz}"${o.disabled ? ' disabled' : ''} title="${esc(o.title)}">${esc(o.label)}</option>`).join(''));
    const el = byId('spi-rate');
    if (el && chosen) el.value = String(chosen.spi_mhz);
  }

  function populateMisoDelay(cfg) {
    const options = misoDelayOptions(findRow(view.supported, cfg.mode, cfg.spi_mhz));
    if (!options.some(o => o.value === cfg.miso_delay)) {
      options.push({ value: cfg.miso_delay, pass: false, chosen: false,
        label: `${cfg.miso_delay} cycles`, title: 'In effect' });
      options.sort((a, b) => a.value - b.value);
    }
    setHtml('spi-miso-delay', options.map(o => `<option value="${o.value}" class="${o.pass ? 'spi-ok' : 'spi-bad'}" ` +
      `title="${esc(o.title)}">${esc(o.label)}</option>`).join(''));
    const el = byId('spi-miso-delay');
    if (el) el.value = String(cfg.miso_delay);
  }

  function show(state) {
    const result = byId('spi-result');
    if (result) result.hidden = !state;
    if (!state) { setBusy(false); view.last = null; return; }
    view.last = state;
    syncSteps(state.sequence);
    if (!view.supported && !view.requested && view.send) {
      view.requested = true;
      view.send({ action: 'spi_supported' });
    }
    const cfg = state.config;
    const modeEl = byId('spi-mode');
    if (modeEl) modeEl.value = String(cfg.mode);
    populateRates(cfg.mode, cfg.spi_mhz);
    populateMisoDelay(cfg);
    setText('spi-core-note', `Core ${cfg.core_mhz} MHz (set automatically, session only)`);
    setText('spi-gpi-delay', `GPI delay ${state.hop_delay} cycles ${GPI_DELAY_NOTE}`);
    if (!view.seeded && state.last) {
      view.seeded = true;
      byId('spi-cmd').value = String(state.last.cmd);
      byId('spi-addr').value = String(state.last.addr);
      byId('spi-pattern').value = state.last.pattern;
      updateAddrNote();
    }
    renderSteps(state.sequence);
    const live = state;
    state = shownState(live);
    view.displayed = state;
    const cursorByte = Math.floor(view.cursor / 8);
    setHtml('spi-frame-strip', frameStripHtml(state, cursorByte));
    setHtml('spi-scope', scopeSvg(state, view.cursor));
    const mem = state.memory || {};
    const expect = state.expect || { mosi: true, miso: true };
    const dataNow = cursorByte >= 3 ? cursorByte - 3 : -1;
    const sent = !!state.last;
    setHtml('spi-mem-c-tx', memoryHtml(mem.c_tx, mem.p_rx, sent && expect.mosi && !!mem.p_rx, dataNow));
    setHtml('spi-mem-p-rx', memoryHtml(mem.p_rx, mem.c_tx, sent && expect.mosi, dataNow));
    setHtml('spi-mem-p-tx', memoryHtml(mem.p_tx, mem.c_rx, sent && expect.miso, dataNow));
    setHtml('spi-mem-c-rx', memoryHtml(mem.c_rx, mem.p_tx, sent && expect.miso && !!mem.p_tx, dataNow));
    const byte = state.last && state.last.addr <= 0x7F ? 16 * state.last.addr : null;
    setText('spi-mem-p-rx-label', `PRU1 RX memory${byte == null ? '' : ` @ byte ${byte}`} (written)`);
    setText('spi-mem-p-tx-label', `PRU1 TX memory${byte == null ? '' : ` @ byte ${byte}`} (read)`);
    const badge = compareBadge(state);
    const badgeEl = byId('spi-compare');
    if (badgeEl) {
      badgeEl.textContent = badge.text;
      badgeEl.className = `spi-pill ${badge.ok === true ? 'spi-pill-ok' : badge.ok === false ? 'spi-pill-bad' : ''}`;
    }
    const st = state.status || {};
    setText('spi-peripheral-status', `PRU1 status: ${st.frames ?? 0} frames, ${st.errors ?? 0} errors, ${st.aborts ?? 0} aborts` +
      (state.faults && state.faults.length ? `; bus faults: ${state.faults.length}` : ''));
    const seq = state.sequence;
    setText('spi-sequence-result', seq ? `Test sequence: ${seq.ok ? 'passed' : 'FAILED'}, ${seq.byte_errors} byte errors over ${seq.steps ? seq.steps.length : 0} steps` : '');
    setText('spi-timing-title', `Timing: mode ${cfg.mode}, SPI CLK ${num(cfg.spi_mhz)} MHz, core ${cfg.core_mhz} MHz (${num(1000 / cfg.core_mhz)} ns per cycle)`);
    setHtml('spi-timing', timingTableHtml(state.timing));
    setBusy(false);
  }

  function shownState(state) {
    const steps = view.steps && view.steps.steps;
    return steps && view.shown != null && steps[view.shown] ? stepState(state, steps[view.shown]) : state;
  }

  // Drop the cached steps when the sequence id changes; ask once per new id.
  function syncSteps(seq) {
    const id = seq && seq.id != null ? seq.id : null;
    if (view.steps && view.steps.id !== id) { view.steps = null; view.shown = null; }
    if (id != null && !view.steps && view.stepsAsked !== id && view.send) {
      view.stepsAsked = id;
      view.send({ action: 'spi_steps' });
    }
    if (id == null) view.stepsAsked = null;
  }

  function renderSteps(seq) {
    const steps = view.steps && view.steps.steps;
    setHtml('spi-steps', steps ? chipsHtml(steps, seq && seq.loop, view.shown, view.locked) : '');
    setText('spi-step-title', steps && view.shown != null && steps[view.shown] ? stepTitle(steps, view.shown) : '');
  }

  function setSteps(msg) {
    const id = view.last && view.last.sequence ? view.last.sequence.id : null;
    if (!msg || msg.id !== id) return;
    view.steps = { id: msg.id, steps: msg.steps || [] };
    view.shown = null;
    show(view.last);
  }

  function setSupported(table) {
    view.supported = table || null;
    if (view.last) show(view.last);
    else setBusy(view.busy);
  }

  function updateAddrNote() {
    const addr = Number(byId('spi-addr').value);
    setText('spi-addr-byte', Number.isInteger(addr) && addr >= 0 && addr <= 0x7F ? `= byte ${16 * addr}` : '(out of range: error path)');
  }

  function showError(errors) {
    const el = byId('spi-error');
    if (el) {
      el.textContent = Array.isArray(errors) ? errors.join('\n') : String(errors || 'SPI action failed');
      el.hidden = false;
    }
    setBusy(false);
  }
  function clearError() { const el = byId('spi-error'); if (el) el.hidden = true; }

  function act(action) {
    clearError();
    setBusy(true);
    view.send?.(action);
  }

  function init(options) {
    view.send = options && options.send;
    view.stop = options && options.stop;
    if (view.bound) return;
    view.bound = true;
    byId('spi-setup-button')?.addEventListener('click', () => {
      view.stop?.();
      clearError();
      view.send?.({ action: 'scenario_load', name: SCENARIO });
    });
    byId('spi-send')?.addEventListener('click', () => {
      const cmd = Number(byId('spi-cmd').value), addr = Number(byId('spi-addr').value);
      if (!Number.isInteger(addr) || addr < 0 || addr > 255) { showError(['Address must be 0 to 255']); return; }
      view.stop?.();
      act({ action: 'spi_send', cmd, addr, pattern: byId('spi-pattern').value });
    });
    byId('spi-sequence')?.addEventListener('click', () => { view.stop?.(); act({ action: 'spi_sequence' }); });
    byId('spi-addr')?.addEventListener('input', updateAddrNote);
    byId('spi-rate')?.addEventListener('change', () => {
      view.stop?.();
      act({ action: 'spi_setup', mode: Number(byId('spi-mode').value), spi_mhz: Number(byId('spi-rate').value) });
    });
    byId('spi-miso-delay')?.addEventListener('change', () => {
      const cfg = view.last && view.last.config;
      if (!cfg) return;
      view.stop?.();
      act({ action: 'spi_setup', mode: cfg.mode, spi_mhz: cfg.spi_mhz,
        miso_delay: Number(byId('spi-miso-delay').value) });
    });
    byId('spi-mode')?.addEventListener('change', () => {
      const mode = Number(byId('spi-mode').value);
      const options = rateOptions(view.supported, mode).filter(o => !o.disabled);
      const want = Number(byId('spi-rate').value);
      const pick = options.find(o => Math.abs(o.spi_mhz - want) < 1e-6) ||
        options.find(o => o.spi_mhz === 25) || options[0];
      if (!pick) { showError([`No supported rate for mode ${mode}`]); return; }
      view.stop?.();
      act({ action: 'spi_setup', mode, spi_mhz: pick.spi_mhz });
    });
    byId('spi-steps')?.addEventListener('click', event => {
      const target = event && event.target;
      const chip = target && (target.closest ? target.closest('[data-step]') : target);
      const i = chip && chip.dataset ? Number(chip.dataset.step) : NaN;
      if (view.locked || !Number.isInteger(i) || !view.steps || !view.steps.steps[i]) return;
      view.shown = i;
      show(view.last);
    });
    byId('spi-scope')?.addEventListener('click', event => {
      const m = view.displayed && scopeModel(view.displayed, view.cursor);
      const target = event && event.currentTarget;
      if (!m || event.clientX == null || !target || !target.getBoundingClientRect) return;
      const box = target.getBoundingClientRect();
      const px = (event.clientX - box.left) / box.width * W;
      let best = 0;
      m.frame.capture.forEach((c, i) => { if (Math.abs(m.x(c) - px) < Math.abs(m.x(m.frame.capture[best]) - px)) best = i; });
      view.cursor = best;
      show(view.last);
    });
  }

  window.SpiView = { SCENARIO, render: show, init, setSupported, setSteps, chipsHtml, stepState, showError, rateOptions, decodeBoxes,
    timingRows, compareBadge, misoBytes, misoDelayOptions, delayOk, frameStripHtml, mismatchIndexes, fieldName, scopeSvg, timingTableHtml,
    get supported() { return view.supported; } };
})();
