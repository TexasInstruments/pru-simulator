"use strict";

// Audio view: live spectrograms of the PDM microphone demo.
//
// PRU1 firmware synthesises two PDM microphones, PRU0 firmware decodes them to
// 16 kHz PCM in shared RAM, and the server ships that PCM here (pdm_pcm). The
// FFTs run in the browser: 256-point Hann window, hop 128 = one column per 8 ms
// of audio. The pure helpers (fft, hann, spectrumColumn, colormap, Spectrogram)
// have no DOM dependency; the view controller at the bottom wires them to
// #audio-view and the pdm_* websocket messages that app.js hands to
// PruAudio.handleMessage().
(() => {
  const PCM_RATE = 16000;
  const FULL_SCALE = 8388607;
  const FLOOR_DB = -140;

  // ---- DSP -----------------------------------------------------------------

  const twiddles = new Map();
  function twiddle(n) {
    let table = twiddles.get(n);
    if (!table) {
      table = { cos: new Float64Array(n / 2), sin: new Float64Array(n / 2) };
      for (let k = 0; k < n / 2; k++) {
        table.cos[k] = Math.cos(2 * Math.PI * k / n);
        table.sin[k] = -Math.sin(2 * Math.PI * k / n);
      }
      twiddles.set(n, table);
    }
    return table;
  }

  /** In-place radix-2 FFT; the length must be a power of two. */
  function fft(re, im) {
    const n = re.length;
    if (n < 2 || (n & (n - 1)) !== 0 || im.length !== n) throw new Error('fft length must be a power of two');
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) {
        [re[i], re[j]] = [re[j], re[i]];
        [im[i], im[j]] = [im[j], im[i]];
      }
    }
    const { cos, sin } = twiddle(n);
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1;
      const step = n / size;
      for (let start = 0; start < n; start += size) {
        for (let k = 0; k < half; k++) {
          const a = start + k;
          const b = a + half;
          const wr = cos[k * step];
          const wi = sin[k * step];
          const tr = re[b] * wr - im[b] * wi;
          const ti = re[b] * wi + im[b] * wr;
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
        }
      }
    }
  }

  const windows = new Map();
  /** Periodic Hann window: exactly 0 at index 0, 1 at n/2. */
  function hann(n) {
    const w = new Float64Array(n);
    for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / n);
    return w;
  }
  function cachedHann(n) {
    let w = windows.get(n);
    if (!w) windows.set(n, w = hann(n));
    return w;
  }

  /**
   * n samples -> n/2 + 1 levels in dBFS. A full-scale sine centred on a bin
   * reads 0 dB: its windowed amplitude is fullScale * sum(window) / 2.
   */
  function spectrumColumn(samples, fullScale = FULL_SCALE) {
    const n = samples.length;
    const w = cachedHann(n);
    const re = new Float64Array(n);
    const im = new Float64Array(n);
    let sum = 0;
    for (let i = 0; i < n; i++) { re[i] = samples[i] * w[i]; sum += w[i]; }
    fft(re, im);
    const norm = fullScale * sum / 2;
    const out = new Float64Array(n / 2 + 1);
    for (let k = 0; k < out.length; k++) {
      const magnitude = Math.hypot(re[k], im[k]);
      out[k] = magnitude > 0 ? Math.max(FLOOR_DB, 20 * Math.log10(magnitude / norm)) : FLOOR_DB;
    }
    return out;
  }

  // Magma-like ramp, black -> purple -> red -> pale yellow. Luminance rises monotonically.
  const MAGMA = [[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122],
    [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191]];
  function colormap(db, min = -90, max = 0) {
    let t = (db - min) / (max - min);
    t = Number.isNaN(t) ? 0 : Math.min(1, Math.max(0, t));
    const position = t * (MAGMA.length - 1);
    const i = Math.min(MAGMA.length - 2, Math.floor(position));
    const f = position - i;
    return [0, 1, 2].map(c => Math.round(MAGMA[i][c] + (MAGMA[i + 1][c] - MAGMA[i][c]) * f));
  }
  const rgb = ([r, g, b]) => `rgb(${r},${g},${b})`;

  // ---- Spectrogram -----------------------------------------------------------

  const cssColor = (name, fallback) => {
    if (typeof getComputedStyle !== 'function' || typeof document === 'undefined' || !document.documentElement) return fallback;
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  };

  class Spectrogram {
    constructor(canvas, { fftSize = 256, hop = 128, minDb = -90, maxDb = 0 } = {}) {
      this.canvas = canvas;
      this.fftSize = fftSize;
      this.hop = hop;
      this.minDb = minDb;
      this.maxDb = maxDb;
      this.spanSamples = 4000;   // the time axis; grows if more audio arrives
      this.reset();
    }

    get columns() { return this.cols.length; }

    reset() {
      this.samples = [];
      this.cols = [];
    }

    /** Length of the time axis in milliseconds of audio. */
    setSpanMs(ms) {
      if (ms > 0) this.spanSamples = Math.round(ms * PCM_RATE / 1000);
    }

    /**
     * Add PCM that starts at sample index *start*. Samples already held are
     * skipped, so a history replay is harmless; a start beyond the held
     * samples is a gap and throws (the caller refetches the history).
     */
    append(start, samples) {
      const held = this.samples.length;
      if (start > held) throw new Error(`gap: have ${held} samples, new block starts at ${start}`);
      const skip = held - start;
      if (skip >= samples.length) return;
      for (let i = skip; i < samples.length; i++) this.samples.push(samples[i]);
      for (let c = this.cols.length; c * this.hop + this.fftSize <= this.samples.length; c++) {
        const db = spectrumColumn(this.samples.slice(c * this.hop, c * this.hop + this.fftSize));
        this.cols.push(Array.from(db, v => rgb(colormap(v, this.minDb, this.maxDb))));
      }
    }

    // Match the bitmap to the displayed size so text stays sharp on hi-dpi screens.
    fit() {
      const canvas = this.canvas;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (!(w > 0) || !(h > 0)) return;
      const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
      const pw = Math.round(w * dpr);
      const ph = Math.round(h * dpr);
      if (canvas.width !== pw) canvas.width = pw;
      if (canvas.height !== ph) canvas.height = ph;
    }

    draw() {
      const canvas = this.canvas;
      this.fit();
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const W = canvas.width;
      const H = canvas.height;
      // Text follows the canvas height, so booth mode is readable from a distance;
      // the width caps it so the time-axis labels do not run into each other.
      const font = Math.round(Math.min(48, Math.max(9, Math.min(H * 0.07, W * 0.035))));
      const left = Math.round(font * 3.6);
      const right = Math.round(font * 4.6);
      const top = Math.round(font * 0.8);
      const bottom = Math.round(font * 2);
      const x0 = left;
      const y0 = top;
      const pw = Math.max(1, W - left - right);
      const ph = Math.max(1, H - top - bottom);
      const label = cssColor('--graph-label', '#c0c7cd');
      const grid = cssColor('--graph-grid-strong', '#4b5863');

      ctx.fillStyle = cssColor('--graph-bg', '#1b2127');
      ctx.fillRect(0, 0, W, H);
      ctx.font = `${font}px sans-serif`;

      // Plot area: unfilled time is the colormap floor.
      ctx.fillStyle = rgb(colormap(this.minDb, this.minDb, this.maxDb));
      ctx.fillRect(x0, y0, pw, ph);
      const span = Math.max(this.spanSamples, this.samples.length);
      const colW = this.hop / span * pw;
      const inset = (this.fftSize - this.hop) / 2;   // a column is centred on its FFT window
      const bins = this.cols.length ? this.cols[0].length : 129;
      const rowH = ph / bins;
      for (let c = 0; c < this.cols.length; c++) {
        const x = x0 + (c * this.hop + inset) / span * pw;
        const column = this.cols[c];
        for (let k = 0; k < bins; k++) {
          ctx.fillStyle = column[k];
          ctx.fillRect(x, y0 + ph - (k + 1) * rowH, colW + 0.5, rowH + 0.5);
        }
      }

      // Cursor at the build-up edge, dark under light so it shows on any colour.
      const edge = x0 + (this.cols.length * this.hop + inset) / span * pw;
      if (edge < x0 + pw - 1) {
        const line = Math.max(1, Math.round(font / 8));
        ctx.fillStyle = 'rgba(0,0,0,0.75)';
        ctx.fillRect(edge - line, y0, line * 3, ph);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(edge, y0, line, ph);
      }

      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.strokeRect(x0 + 0.5, y0 + 0.5, pw - 1, ph - 1);

      // Frequency axis, 0-8 kHz bottom to top.
      ctx.fillStyle = label;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let k = 0; k <= 8; k += 2) {
        const y = y0 + ph - (k / 8) * ph;
        ctx.fillRect(x0 - Math.round(font / 3), Math.min(y0 + ph - 1, Math.max(y0, y)), Math.round(font / 3), 1);
        ctx.fillText(`${k} kHz`, x0 - Math.round(font / 2.2), Math.min(y0 + ph - font / 2, Math.max(y0 + font / 2, y)));
      }

      // Time axis in ms of audio.
      const spanMs = span / PCM_RATE * 1000;
      const stepMs = [10, 20, 25, 50, 100, 200, 250, 500, 1000].find(s => spanMs / s <= 5) || 1000;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      for (let ms = 0; ms <= spanMs + 1e-6; ms += stepMs) {
        const x = x0 + ms / spanMs * pw;
        ctx.fillRect(x, y0 + ph, 1, Math.round(font / 3));
        ctx.fillText(`${ms} ms`, Math.min(W - font, Math.max(x0, x)), y0 + ph + Math.round(font / 2.5));
      }

      // Colour bar, loud at the top.
      const barX = x0 + pw + Math.round(font * 0.8);
      const barW = Math.max(4, Math.round(font * 0.9));
      const steps = 48;
      for (let s = 0; s < steps; s++) {
        ctx.fillStyle = rgb(colormap(this.maxDb - (s + 0.5) / steps * (this.maxDb - this.minDb), this.minDb, this.maxDb));
        ctx.fillRect(barX, y0 + s * ph / steps, barW, ph / steps + 1);
      }
      ctx.fillStyle = label;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      for (let db = this.maxDb; db >= this.minDb; db -= 30) {
        const y = y0 + (this.maxDb - db) / (this.maxDb - this.minDb) * ph;
        ctx.fillText(String(db), barX + barW + Math.round(font / 3),
          Math.min(y0 + ph - font / 2, Math.max(y0 + font / 2, y)));
      }
      ctx.textBaseline = 'top';
      ctx.fillText('dBFS', barX, y0 + ph + Math.round(font / 2.5));
    }
  }

  // ---- View controller -------------------------------------------------------

  const NOT_SET_UP = /not set up/i;   // the server has no demo for the current simulator
  const OBSERVER_POLL_MS = 5000;

  function initView() {
    const $ = id => document.getElementById(id);
    const view = $('audio-view');
    const canvases = [$('audio-canvas-left'), $('audio-canvas-right')];
    if (!view || !canvases[0] || !canvases[1]) return null;
    const el = {
      left: $('audio-left-clip'), right: $('audio-right-clip'), swap: $('audio-swap'),
      routing: $('audio-routing'), fill: $('audio-progress-fill'), progress: $('audio-progress-text'),
      speed: $('audio-speed'), load0: $('audio-load-pru0'), load1: $('audio-load-pru1'),
      mode: $('audio-mode'), notice: $('audio-notice'), overrun: $('audio-overrun'),
      error: $('audio-error'), observer: $('audio-observer'), run: $('audio-run'),
      stop: $('audio-stop'), reset: $('audio-reset'), booth: $('audio-booth'),
      titles: [$('audio-title-left'), $('audio-title-right')],
    };
    const spectrograms = canvases.map(canvas => new Spectrogram(canvas, {}));
    // sendAction drops a message while the socket is closed and returns false then.
    const send = obj => globalThis.sendAction?.(obj) !== false;

    let clips = [];
    let status = null;          // last pdm_status
    let running = false;        // this tab drives the run loop
    let pendingStart = false;   // pdm_setup sent, waiting for its status
    let selfChange = false;     // our own setup/reset is in flight
    let pendingReset = false;   // pdm_reset sent, waiting for its status
    let pendingRun = null;      // run_id of the chunk we wait for
    let runSeq = 0;
    let otherRunning = false;   // another tab owns the run
    let dirty = false;          // the user changed the pickers since the last status
    let opened = false;
    let recovering = false;
    let localNotice = '';
    let errorText = '';
    let poll = null;
    let boothFullscreen = false;
    let drawQueued = false;

    function redraw() { spectrograms.forEach(spec => spec.draw()); }
    function scheduleDraw() {
      if (drawQueued) return;
      drawQueued = true;
      const frame = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : fn => fn();
      frame(() => { drawQueued = false; redraw(); });
    }

    function routingText() {
      const wire = 'PRU1 GPO1 → SD0_D/SD1_D, clock GPO0 → SD0_CLK';
      const lines = [`Gen L → PRU0 SD ch0 · rising edge · ${wire}`,
        'Gen R → PRU0 SD ch1 · falling edge · same data line'];
      if (status?.clips) {
        const l = status.swap ? status.clips.right : status.clips.left;
        const r = status.swap ? status.clips.left : status.clips.right;
        lines.push(`Gen L plays ${l} · Gen R plays ${r}${status.swap ? ' (swapped)' : ''}`);
      }
      return lines.join('\n');
    }

    function render() {
      const busy = running || pendingStart;
      el.run.disabled = busy || otherRunning;
      el.stop.disabled = !(busy || otherRunning);
      el.reset.disabled = busy || otherRunning;
      const clipMs = status?.clip_ms || 0;
      const pct = clipMs > 0 ? Math.min(100, Math.max(0, (status.audio_ms || 0) / clipMs * 100)) : 0;
      el.fill.style.width = `${Math.round(pct * 10) / 10}%`;
      el.progress.textContent = status
        ? `${Math.round(status.audio_ms)} ms of ${Math.round(clipMs)} ms audio`
        : 'No run yet';
      el.speed.textContent = status ? `${Number(status.speed_ms_per_s || 0).toFixed(1)} ms audio / s` : '– ms audio / s';
      const percent = name => status?.load ? `${Math.round((status.load[name] || 0) * 100)} %` : '–';
      el.load0.textContent = `PRU0 ${percent('pru0')}`;
      el.load1.textContent = `PRU1 ${percent('pru1')}`;
      el.mode.textContent = status?.mode || '–';
      el.overrun.hidden = !status?.overrun;
      el.overrun.textContent = status?.overrun ? `Overrun: ${status.overrun}` : '';
      const notices = [...new Set([localNotice, ...(status?.notices || [])].filter(Boolean))];
      el.notice.textContent = notices.join(' · ');
      el.notice.hidden = notices.length === 0;
      el.error.textContent = errorText;
      el.error.hidden = !errorText;
      el.observer.hidden = !otherRunning;
      el.observer.textContent = otherRunning
        ? 'Running in another tab. This is a snapshot that refreshes every 5 s; it is not the running tab.'
        : '';
      const swapped = Boolean(status?.swap);
      const names = status?.clips
        ? [swapped ? status.clips.right : status.clips.left, swapped ? status.clips.left : status.clips.right] : [];
      el.titles.forEach((title, ch) => { title.textContent = names[ch] ? `ch${ch} · ${names[ch]}` : `ch${ch}`; });
      el.routing.textContent = routingText();
      const booth = view.classList.contains('audio-booth');
      el.booth.textContent = booth ? 'Exit booth' : 'Booth';
      el.booth.setAttribute('aria-pressed', String(booth));
      spectrograms.forEach(spec => spec.setSpanMs(clipMs));
    }

    function syncPickers() {
      if (dirty || !status?.clips) return;
      const has = (select, name) => Array.from(select.children).some(option => option.value === name);
      if (has(el.left, status.clips.left)) el.left.value = status.clips.left;
      if (has(el.right, status.clips.right)) el.right.value = status.clips.right;
      el.swap.checked = Boolean(status.swap);
    }

    function fillPickers() {
      for (const [select, fallback] of [[el.left, 0], [el.right, Math.min(1, clips.length - 1)]]) {
        const keep = select.value;
        select.replaceChildren(...clips.map(clip => {
          const option = document.createElement('option');
          option.value = clip.name;
          option.textContent = `${clip.name} (${Math.round(clip.ms)} ms)`;
          return option;
        }));
        select.value = clips.some(clip => clip.name === keep) ? keep : (clips[fallback]?.name || '');
      }
      syncPickers();
    }

    async function loadClips() {
      if (typeof fetch !== 'function') return;
      try {
        const response = await fetch('/pdm/clips');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        clips = (await response.json()).clips || [];
        fillPickers();
        if (!clips.length) errorText = 'No audio clips found in source/pdm/clips.';
      } catch (error) {
        errorText = `Could not load the clip list: ${error.message || error}`;
      }
      render();
    }

    function openView() {
      opened = true;
      loadClips();
      send({ action: 'pdm_history' });
      render();
      scheduleDraw();
    }

    function setPolling(on) {
      if (on && poll === null && typeof setInterval === 'function')
        poll = setInterval(() => send({ action: 'pdm_history' }), OBSERVER_POLL_MS);
      else if (!on && poll !== null) { clearInterval(poll); poll = null; }
    }

    function dropPicture() { spectrograms.forEach(spec => spec.reset()); }

    const NOT_CONNECTED = 'Not connected to the server. Try again once it is back.';
    // The firmware the setup loaded, shown in the Simulator view's editors so
    // the code the cores execute is visible there (spec §11).
    const DEMO_FIRMWARE = [['pru0', 'pdm/pdm_rx.asm'], ['pru1', 'pdm/pdm_gen.asm']];

    async function showFirmware() {
      const open = globalThis.openFileAsTab;
      if (typeof open !== 'function') return;
      for (const [core, path] of DEMO_FIRMWARE) {
        try {
          const response = await fetch(`/source/${path}`);
          if (response.ok) open(path, await response.text(), core);
        } catch (_) { /* the demo runs either way; the editor just keeps its file */ }
      }
    }

    function sendRun() {
      const id = runSeq + 1;
      if (!send({ action: 'pdm_run', run_id: id })) return false;
      runSeq = id;
      pendingRun = id;
      return true;
    }

    function startLoop() {
      running = true;
      if (!sendRun()) { running = false; errorText = NOT_CONNECTED; }
      render();
    }

    function needsSetup() {
      return !status || status.done || status.clips.left !== el.left.value
        || status.clips.right !== el.right.value || Boolean(status.swap) !== el.swap.checked;
    }

    function onRun() {
      if (running || pendingStart || otherRunning) return;
      errorText = '';
      if (!el.left.value || !el.right.value) { errorText = 'Pick a clip for Gen L and Gen R first.'; render(); return; }
      if (needsSetup()) {
        if (!send({ action: 'pdm_setup', clip_left: el.left.value, clip_right: el.right.value, swap: el.swap.checked })) {
          errorText = NOT_CONNECTED;       // nothing was sent, so nothing is pending
          render();
          return;
        }
        dropPicture();
        localNotice = '';
        pendingStart = true;
        selfChange = true;
        dirty = false;
        render();
        scheduleDraw();
      } else {
        startLoop();
      }
    }

    function onStop() {
      // pdm_stop is not answered when no demo is set up, so nothing waits for a reply.
      running = false;
      pendingStart = false;
      pendingRun = null;
      send({ action: 'pdm_stop' });
      render();
    }

    function onReset() {
      if (running || pendingStart || otherRunning) return;
      errorText = '';
      dropPicture();
      localNotice = '';
      if (status) {
        if (send({ action: 'pdm_reset' })) {
          selfChange = true;
          pendingReset = true;
          dirty = false;
        } else {
          errorText = NOT_CONNECTED;
        }
      }
      render();
      scheduleDraw();
    }

    function onStatus(msg) {
      const prev = status;
      status = msg;
      recovering = false;
      const own = msg.run_id !== undefined;
      if (!own && !selfChange && !running && prev && (
        prev.clips.left !== msg.clips.left || prev.clips.right !== msg.clips.right
        || Boolean(prev.swap) !== Boolean(msg.swap) || msg.audio_ms < prev.audio_ms)) {
        // Another tab set the demo up again: what we hold belongs to the old run.
        dropPicture();
        send({ action: 'pdm_history' });
      }
      if (!own) {
        otherRunning = Boolean(msg.running) && !running && !pendingStart;
        setPolling(otherRunning);
      }
      if (pendingStart && !own) {
        pendingStart = false;
        selfChange = false;
        syncPickers();
        showFirmware();
        startLoop();
        return;
      }
      if (!own) { selfChange = false; pendingReset = false; }
      if (own && msg.run_id === pendingRun) {
        pendingRun = null;
        if (running) {
          if (msg.running && !msg.done) { sendRun(); }
          else {
            running = false;
            if (msg.breakpoint) localNotice = `Stopped at a breakpoint on ${msg.breakpoint}`;
          }
        }
      }
      syncPickers();
      render();
      scheduleDraw();
    }

    function onPcm(msg) {
      const spec = spectrograms[msg.ch];
      if (!spec) return;
      try {
        spec.append(msg.start, msg.samples);
      } catch (error) {
        // A chunk went missing: rebuild from the server's history, once.
        if (!recovering) {
          recovering = true;
          dropPicture();
          send({ action: 'pdm_history' });
        }
      }
      scheduleDraw();
    }

    function onError(errors) {
      running = false;
      pendingStart = false;
      pendingRun = null;
      selfChange = false;
      pendingReset = false;
      errorText = (errors || []).join('; ');
      render();
    }

    // pdm_history is also sent passively (on open, on reconnect, by the observer poll). On a
    // server with no demo it is answered with the not-set-up error, which is no failure of
    // ours: it only tells us there is nothing to show. It must not cancel a Run clicked
    // meanwhile (that setup is answered later, in order), and it must forget any stale
    // status so the next Run sets the demo up again.
    function onNotSetUp(errors) {
      status = null;
      otherRunning = false;
      setPolling(false);
      if (pendingRun !== null || pendingReset) { onError(errors); return; }   // it answers a Run or Reset
      render();
    }

    function handleMessage(msg) {
      if (!msg || typeof msg.type !== 'string') return false;
      if (msg.type === 'pdm_pcm') { onPcm(msg); return true; }
      if (msg.type === 'pdm_status') { onStatus(msg); return true; }
      if (msg.type === 'pdm_notice') {
        // Our own setup can send several (clock change, programs replaced): keep them all.
        const text = msg.text || '';
        localNotice = selfChange && localNotice && text && !localNotice.includes(text)
          ? `${localNotice} · ${text}` : text;
        render();
        return true;
      }
      if (msg.type === 'error') {
        if (msg.tag === 'pdm') {
          if ((msg.errors || []).some(e => NOT_SET_UP.test(e))) onNotSetUp(msg.errors);
          else onError(msg.errors);
          return true;
        }
        // The server answers a refused request (another tab owns execution, ...) with an
        // untagged error. While one of our requests is pending it is the answer to that.
        if (running || pendingStart || pendingRun !== null || pendingReset) {
          onError(msg.errors);
          return true;
        }
      }
      return false;
    }

    // ---- Booth mode ----
    function enterBooth() {
      view.classList.add('audio-booth');
      if (typeof view.requestFullscreen === 'function') {
        boothFullscreen = true;
        try {
          const result = view.requestFullscreen();
          result?.catch?.(() => { boothFullscreen = false; });
        } catch (_) { boothFullscreen = false; }
      }
      render();
      scheduleDraw();
    }
    function exitBooth() {
      view.classList.remove('audio-booth');
      boothFullscreen = false;
      if (document.fullscreenElement && typeof document.exitFullscreen === 'function') {
        try { document.exitFullscreen()?.catch?.(() => {}); } catch (_) { /* already out */ }
      }
      render();
      scheduleDraw();
    }
    const inBooth = () => view.classList.contains('audio-booth');

    el.booth.addEventListener('click', () => (inBooth() ? exitBooth() : enterBooth()));
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && inBooth()) exitBooth(); });
    document.addEventListener('fullscreenchange', () => {
      // Esc in fullscreen is consumed by the browser: leaving fullscreen leaves booth mode too.
      if (inBooth() && boothFullscreen && !document.fullscreenElement) exitBooth();
    });

    el.run.addEventListener('click', onRun);
    el.stop.addEventListener('click', onStop);
    el.reset.addEventListener('click', onReset);
    for (const control of [el.left, el.right, el.swap]) control.addEventListener('change', () => { dirty = true; });
    document.addEventListener('pru-workspace-view-changed', event => {
      const shown = event.detail?.view === 'audio';
      if (shown && !opened) openView();
      else if (shown) scheduleDraw();
      else if (opened) {
        opened = false;
        setPolling(false);
        if (inBooth()) exitBooth();
      }
    });
    document.addEventListener('pru-theme-changed', scheduleDraw);
    if (typeof window !== 'undefined') window.addEventListener?.('resize', scheduleDraw);
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(scheduleDraw);
      canvases.forEach(canvas => observer.observe(canvas));
    }
    render();

    return {
      spectrograms, handleMessage,
      connected() {
        // The server keeps the demo across a reconnect; this tab only pauses. Replies to
        // requests of the old socket will never arrive.
        running = false; pendingStart = false; pendingRun = null; selfChange = false; pendingReset = false;
        if (opened) openView();
        else render();
      },
      disconnected() {
        running = false; pendingStart = false; pendingRun = null; selfChange = false; pendingReset = false;
        setPolling(false);
        otherRunning = false;
        render();
      },
    };
  }

  const controller = initView();
  globalThis.PruAudio = {
    fft, hann, spectrumColumn, colormap, Spectrogram,
    handleMessage: msg => (controller ? controller.handleMessage(msg) : false),
    connected: () => controller?.connected(),
    disconnected: () => controller?.disconnected(),
    view: controller ? { spectrograms: controller.spectrograms } : null,
  };
})();
