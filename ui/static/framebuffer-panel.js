"use strict";

// Framebuffer panel: reads emulated memory over the dashboard WebSocket and
// draws it as a picture. Decoding lives in framebuffer.js; app.js forwards
// "state", "memory" (tags fb-hdr / fb-px) and "error" messages to PruFbPanel.
(() => {
  const FB = globalThis.PruFramebuffer;
  const KEY = 'pru-fb-settings-v1';
  const MANUAL_MS = 100;
  const $ = id => document.getElementById(id);
  const panel = $('framebuffer-panel');
  if (!panel || !FB) return;

  const els = {
    auto: $('fb-auto'), base: $('fb-base'), grid: $('fb-grid'), big: $('fb-bigview'),
    manual: $('fb-manual'), preset: $('fb-preset'), width: $('fb-width'), height: $('fb-height'),
    format: $('fb-format'), stride: $('fb-stride'), fg: $('fb-fg'), bg: $('fb-bg'),
    status: $('fb-status'), wrap: $('fb-canvas-wrap'), canvas: $('fb-canvas'),
    overlay: $('fb-big'), bigCanvas: $('fb-big-canvas'), bigStatus: $('fb-big-status'),
    bigStage: $('fb-big-stage'), bigClose: $('fb-big-close'),
  };

  const settings = { auto: true, base: '0x00011000', preset: 'mono', width: 32, height: 32,
    format: 1, stride: '', fg: FB.hexColor565(FB.DEFAULT_FG), bg: FB.hexColor565(FB.DEFAULT_BG),
    grid: false };
  try { Object.assign(settings, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (_) {}

  for (const [id, p] of Object.entries(FB.PRESETS)) {
    const o = document.createElement('option');
    o.value = id; o.textContent = p.label;
    els.preset.appendChild(o);
  }
  const custom = document.createElement('option');
  custom.value = 'custom'; custom.textContent = 'Custom';
  els.preset.appendChild(custom);

  let header = null;          // last parsed header (null = none found)
  let headerSeen = false;     // a header read has completed for current settings
  let lastKey = null;         // settings + frame of the last pixel read we asked for
  let pendingHdr = 0;         // time a header read was sent (0 = none outstanding)
  let pendingPx = null;       // {cfg, frame} of the outstanding pixel read
  let lastManualAt = 0;
  let image = null;           // {rgba, width, height}
  let errorText = '';
  let statusLine = '';

  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (_) {} };
  const bigOpen = () => !els.overlay.hidden;
  const visible = () => bigOpen() ||
    (typeof getPanelVisibility === 'function' && getPanelVisibility('framebuffer') &&
     !panel.classList.contains('panel-collapsed'));

  function baseAddr() {
    const n = parseInt(String(settings.base).trim(), 16);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }

  function manualConfig() {
    const color = c => FB.parseHexColor565(c);
    return { width: parseInt(settings.width, 10), height: parseInt(settings.height, 10),
      format: parseInt(settings.format, 10), stride: parseInt(settings.stride, 10) || 0,
      dataOffset: 0, paletteOffset: 0, fg: color(settings.fg), bg: color(settings.bg) };
  }

  const configKey = (cfg, base) => JSON.stringify([base, cfg]);

  function setStatus(text, isError) {
    statusLine = text;
    els.status.textContent = text;
    els.status.classList.toggle('fb-error', !!isError);
    els.bigStatus.textContent = text;
  }

  function syncControls() {
    els.auto.checked = settings.auto;
    els.base.value = settings.base;
    els.grid.checked = settings.grid;
    els.preset.value = settings.preset;
    els.width.value = settings.width;
    els.height.value = settings.height;
    els.format.value = String(settings.format);
    els.stride.value = settings.stride;
    els.fg.value = settings.fg;
    els.bg.value = settings.bg;
    els.manual.hidden = settings.auto && header !== null;
  }

  // Draw the decoded picture onto `canvas`, integer-scaled into `box`.
  function drawTo(canvas, box) {
    if (!image) return;
    const bw = Math.max(1, box.clientWidth - 2), bh = Math.max(1, box.clientHeight - 2);
    const scale = FB.fitScale(image.width, image.height, bw, bh);
    canvas.width = image.width * scale;
    canvas.height = image.height * scale;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    const small = document.createElement('canvas');
    small.width = image.width; small.height = image.height;
    small.getContext('2d').putImageData(new ImageData(image.rgba, image.width, image.height), 0, 0);
    ctx.drawImage(small, 0, 0, canvas.width, canvas.height);
    if (settings.grid && scale >= 4) {
      ctx.strokeStyle = 'rgba(128,128,128,0.45)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 1; x < image.width; x++) { ctx.moveTo(x * scale + 0.5, 0); ctx.lineTo(x * scale + 0.5, canvas.height); }
      for (let y = 1; y < image.height; y++) { ctx.moveTo(0, y * scale + 0.5); ctx.lineTo(canvas.width, y * scale + 0.5); }
      ctx.stroke();
    }
  }

  function redraw() {
    if (bigOpen()) drawTo(els.bigCanvas, els.bigStage);
    if (visible() && panel.isConnected && els.wrap.clientWidth > 0) drawTo(els.canvas, els.wrap);
  }

  function requestHeader() {
    const base = baseAddr();
    if (base === null) { setStatus('Invalid base address', true); return; }
    pendingHdr = Date.now();
    sendAction({ action: 'read_memory', addr: base, length: FB.HEADER_BYTES, tag: 'fb-hdr', encoding: 'base64' });
  }

  function requestPixels(cfg, lay, frame) {
    const base = baseAddr();
    pendingPx = { cfg: lay, frame, key: configKey(cfg, base) };
    sendAction({ action: 'read_memory', addr: base, length: lay.total, tag: 'fb-px', encoding: 'base64' });
  }

  // Manual settings: draw from the base address with no header.
  function manualRead() {
    const cfg = manualConfig();
    const lay = FB.layout(cfg);
    if (lay.error) { setStatus(lay.error, true); return; }
    lastManualAt = Date.now();
    requestPixels(cfg, lay, null);
  }

  function refresh(force) {
    if (!visible()) return;
    if (force) { lastKey = null; headerSeen = false; }
    if (baseAddr() === null) { setStatus('Invalid base address', true); return; }
    if (settings.auto) {
      if (pendingHdr && Date.now() - pendingHdr < 1000) return;
      requestHeader();
    } else {
      header = null;
      syncControls();
      if (!pendingPx || force) manualRead();
    }
  }

  function onHeader(msg) {
    pendingHdr = 0;
    if (!settings.auto) return;
    const base = baseAddr();
    header = FB.parseHeader(FB.base64ToBytes(msg.data_b64));
    headerSeen = true;
    syncControls();
    let cfg, frame = null;
    if (header) {
      cfg = FB.configFromHeader(header);
      frame = header.frame;
    } else {
      cfg = manualConfig();
    }
    const lay = FB.layout(cfg);
    if (lay.error) { setStatus(lay.error, true); return; }
    if (pendingPx) return;                    // one pixel read at a time
    const key = configKey(cfg, base) + (header ? ':' + frame : ':manual');
    if (header) {
      if (key === lastKey) return;            // same frame, same settings
    } else if (Date.now() - lastManualAt < MANUAL_MS) {
      return;
    } else {
      lastManualAt = Date.now();
    }
    lastKey = key;
    requestPixels(cfg, lay, frame);
  }

  function onPixels(msg) {
    const req = pendingPx;
    pendingPx = null;
    if (!req) return;
    try {
      const bytes = FB.base64ToBytes(msg.data_b64);
      const rgba = FB.decode(bytes, req.cfg);
      image = { rgba, width: req.cfg.width, height: req.cfg.height };
      errorText = '';
      const fmt = FB.FORMATS[req.cfg.format];
      const addr = '0x' + msg.addr.toString(16).toUpperCase();
      if (req.frame !== null && settings.auto && header) {
        setStatus(FB.statusText(header));
      } else if (settings.auto) {
        setStatus(`No FBUF header at ${addr} — manual ${req.cfg.width}×${req.cfg.height} ${fmt}`);
      } else {
        setStatus(`Manual ${req.cfg.width}×${req.cfg.height} ${fmt} at ${addr}`);
      }
      redraw();
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  }

  function onError(msg) {
    if (msg.tag === 'fb-hdr') pendingHdr = 0; else pendingPx = null;
    lastKey = null;
    const text = (msg.errors || []).join('; ') || 'read failed';
    setStatus(`Cannot read memory: ${text}`, true);
  }

  // ---- Controls ---------------------------------------------------------
  function changed(force = true) { save(); syncControls(); refresh(force); }

  els.auto.addEventListener('change', () => { settings.auto = els.auto.checked; header = null; changed(); });
  els.base.addEventListener('change', () => { settings.base = els.base.value.trim(); changed(); });
  els.grid.addEventListener('change', () => { settings.grid = els.grid.checked; save(); redraw(); });
  els.preset.addEventListener('change', () => {
    settings.preset = els.preset.value;
    const p = FB.PRESETS[settings.preset];
    if (p) { settings.width = p.width; settings.height = p.height; settings.format = p.format; settings.stride = ''; }
    changed();
  });
  for (const [el, key] of [[els.width, 'width'], [els.height, 'height'], [els.format, 'format'],
                           [els.stride, 'stride'], [els.fg, 'fg'], [els.bg, 'bg']]) {
    el.addEventListener('change', () => {
      settings[key] = el.value;
      if (key !== 'fg' && key !== 'bg') settings.preset = 'custom';
      changed();
    });
  }
  els.big.addEventListener('click', () => { els.overlay.hidden = false; refresh(true); redraw(); });
  const closeBig = () => { els.overlay.hidden = true; };
  els.bigClose.addEventListener('click', closeBig);
  els.overlay.addEventListener('click', e => {
    if (e.target === els.overlay || e.target === els.bigStage) closeBig();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && bigOpen()) closeBig(); });
  window.addEventListener('resize', redraw);
  if (typeof ResizeObserver === 'function') new ResizeObserver(redraw).observe(els.wrap);
  document.addEventListener('pru-layout-changed', () => {
    // Panel just became visible (or moved): read afresh and redraw.
    setTimeout(() => { refresh(true); redraw(); }, 0);
  });

  syncControls();

  globalThis.PruFbPanel = {
    onState() {
      if (!visible()) return;
      if (settings.auto) refresh(false);
      else if (Date.now() - lastManualAt >= MANUAL_MS) refresh(false);
    },
    onMemory(msg) { if (msg.tag === 'fb-hdr') onHeader(msg); else onPixels(msg); },
    onError,
    refresh,
  };
})();
