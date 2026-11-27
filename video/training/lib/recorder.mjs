// Frame-by-frame recorder for a live page.
//
// Time is driven by Playwright's page clock: every frame advances the page by exactly
// 1/fps s, so timers and animation frames do not depend on how long a screenshot takes.
// Nothing in the page's own DOM is changed; the only added node is #__cap_overlay
// (pointer, click ring, group outline), which ignores the mouse.
//
// A frame whose picture cannot have changed (no DOM mutation, no running animation, the
// pointer did not move) is written as a hard link to the previous image.
import fs from 'node:fs';
import path from 'node:path';

const ease = (t) => (t < 0.5 ? 2 * t * t : 1 - 2 * (1 - t) * (1 - t)); // ease-in-out
const CUE_GAP = 0.38;       // silence after a cue
const CHAPTER_GAP_S = 0.9;  // silence after the last cue of a chapter
const TAIL = 1.6;           // after the last cue of the part

// Runs in the page after each clock step: keeps the overlay in place and reports whether
// the picture may have changed since the previous call.
function pageFrame({ x, y, ring, outline }) {
  let box = document.getElementById('__cap_overlay');
  if (!box) {
    box = document.createElement('div');
    box.id = '__cap_overlay';
    box.innerHTML = `
<div id="__cap_outline" style="position:fixed;left:0;top:0;width:0;height:0;box-sizing:border-box;border-radius:8px;
  border:2px solid #ffd54a;box-shadow:0 0 0 2px rgba(0,0,0,.55),0 0 14px rgba(255,213,74,.45);opacity:0;
  pointer-events:none;z-index:2147483645"></div>
<div id="__cap_ring" style="position:fixed;left:0;top:0;width:0;height:0;border-radius:50%;box-sizing:border-box;
  border:3px solid #ffd54a;background:rgba(255,213,74,.18);opacity:0;pointer-events:none;z-index:2147483646"></div>
<svg id="__cap_cursor" width="30" height="34" viewBox="-2 -2 22 25" style="position:fixed;left:0;top:0;
  pointer-events:none;z-index:2147483647;filter:drop-shadow(0 2px 3px rgba(0,0,0,.6));overflow:visible">
  <path d="M0 0 L0 18.5 L4.9 14.2 L8.2 21.6 L11.3 20.2 L8 13 L14.4 13 Z" fill="#fff" stroke="#14181f"
    stroke-width="1.5" stroke-linejoin="round"/></svg>`;
    document.documentElement.appendChild(box);
  }
  if (!window.__capSeen) {
    window.__capSeen = { n: 1 };              // a fresh document counts as a change
    new MutationObserver((list) => {
      for (const m of list) if (!box.contains(m.target)) { window.__capSeen.n++; break; }
    }).observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
  }
  document.getElementById('__cap_cursor').style.transform = `translate(${x - 2.7}px,${y - 2.7}px)`;
  const r = document.getElementById('__cap_ring').style;
  if (ring) {
    const d = 14 + 44 * ring.t;
    r.width = r.height = d + 'px';
    r.transform = `translate(${ring.x - d / 2}px,${ring.y - d / 2}px)`;
    r.opacity = String(1 - ring.t);
  } else r.opacity = '0';
  const o = document.getElementById('__cap_outline').style;
  let rect = '';
  const el = outline && document.querySelector(outline);
  if (el) {
    const b = el.getBoundingClientRect(), pad = 4;
    o.transform = `translate(${b.left - pad}px,${b.top - pad}px)`;
    o.width = b.width + 2 * pad + 'px';
    o.height = b.height + 2 * pad + 'px';
    o.opacity = '1';
    rect = [b.left, b.top, b.width, b.height].map(Math.round).join();
  } else o.opacity = '0';
  const changed = window.__capSeen.n;
  window.__capSeen.n = 0;
  const animating = document.getAnimations().some((a) => a.playState === 'running');
  return { changed, animating, rect };
}

// Runs in the page. With no argument: the descriptors of every visible control. With an
// element: the descriptor of the control that element belongs to (itself, or the button,
// tab or clickable box around it).
//   #id                      a control with an id
//   #panel > button[Label]   otherwise: nearest ancestor with an id, tag, and its label
function pageControls(element) {
  const SELECTOR = 'button, select, input:not([type=hidden]), textarea, summary, [role=tab], [role=button]';
  const overlay = document.getElementById('__cap_overlay');
  const clickable = (el) => ['pointer', 'grab', 'col-resize', 'row-resize', 'ns-resize', 'ew-resize'].includes(getComputedStyle(el).cursor);
  const control = (el) => {
    const std = el.closest(SELECTOR);
    if (std) return std;
    let top = null;                                   // outermost element that shows a hand
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) if (clickable(e)) top = e;
    return top;
  };
  const describe = (el) => {
    if (el.id) return '#' + el.id;
    let anchor = el.parentElement;
    while (anchor && !anchor.id) anchor = anchor.parentElement;
    const data = Object.entries(el.dataset).map(([k, v]) => `${k}=${v}`).join(' ');
    const field = el.matches('input, select, textarea');
    // A field is named by what it is, never by what it holds: its value changes while the tour runs.
    const named = el.getAttribute('aria-label') || el.title || el.getAttribute('placeholder') || el.getAttribute('name') || data;
    const label = field
      ? named || el.type || el.className.toString()
      : (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40) || named || el.className.toString();
    return `${anchor ? '#' + anchor.id : 'body'} > ${el.tagName.toLowerCase()}[${label}]`;
  };
  if (element) { const c = control(element); return c ? describe(c) : null; }
  const visible = (el) => { const b = el.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const found = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (overlay?.contains(el) || !visible(el)) continue;
    if (el.matches(SELECTOR) || (clickable(el) && control(el) === el)) found.add(describe(el));
  }
  return [...found];
}

export class Recorder {
  /**
   * @param page  Playwright page
   * @param {{dir: string, out?: string, fps?: number, cues?: Record<string, {dur: number, chapter: string}>}} opts
   *   dir: frames go to dir/%06d.png. out: timeline.json goes here (default: parent of dir).
   *   cues: speech length and chapter of every cue, in narration order.
   */
  constructor(page, { dir, out = path.dirname(dir), fps = 30, cues = {} }) {
    this.page = page; this.dir = dir; this.out = out; this.fps = fps;
    this.cues = cues; this.order = Object.keys(cues);
    this.n = 0; this.x = 0; this.y = 0;
    this.ring = null; this.outlined = null;
    this.live = false;          // true: every frame is a real screenshot (for canvas-only changes)
    this.force = 0;             // real screenshots for this many frames after an action
    this.last = null;           // { file, key }: last real image and what the overlay looked like
    this.fatal = null;
    this.dialog = null;
    this.current = null;        // key of the running cue
    this.done = [];             // [{ key, frame }]
    this.chapters = [];         // [{ id, frame }]
    this.seen = new Set();      // descriptors of controls that were on screen at the end of a cue
    this.used = new Set();      // descriptors of controls the tour pointed at
    page.on('dialog', (d) => {
      if (this.dialog) { const answer = this.dialog; this.dialog = null; answer(d); } else {
        this.fatal = new Error(`unexpected ${d.type()} dialog: ${d.message()}`);
        d.dismiss().catch(() => {});
      }
    });
    page.on('pageerror', (e) => { this.fatal = this.fatal || new Error('page error: ' + e.message); });
  }

  /** Clock first, then the page. Writes no frames. */
  async open(url, x, y) {
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.cdp = await this.page.context().newCDPSession(this.page);
    await this.page.clock.install();
    await this.page.goto(url);
    this.x = x; this.y = y;
    await this.page.mouse.move(x, y);
    // install() alone leaves the clock running in real time. Pause it, one second ahead so
    // the page finishes its first render; from here on only frame() moves time.
    await this.page.clock.pauseAt(new Date(Date.now() + 1000));
    await this.page.clock.runFor(200);
  }

  /** Load another page. The cut is hard, like a browser navigation; no frames are written. */
  async goto(url) {
    await this.page.goto(url);
    await this.page.mouse.move(this.x, this.y);
    await this.page.clock.runFor(1200);
    this.force = 3;
  }

  fail(message) { return new Error(this.current ? `cue ${this.current}: ${message}` : message); }

  /** Advance the page by 1/fps s and write one frame. */
  async frame() {
    if (this.fatal) throw this.fail(this.fatal.message);
    await this.page.clock.runFor(1000 / this.fps);
    if (this.fatal) throw this.fail(this.fatal.message);
    const state = await this.page.evaluate(pageFrame, { x: this.x, y: this.y, ring: this.ring, outline: this.outlined });
    const key = JSON.stringify([Math.round(this.x * 4), Math.round(this.y * 4), this.ring, this.outlined, state.rect]);
    const file = path.join(this.dir, String(this.n).padStart(6, '0') + '.png');
    const same = this.last && !this.live && this.force <= 0 && !state.changed && !state.animating && key === this.last.key;
    if (same) fs.linkSync(this.last.file, file);
    else {
      const { data } = await this.cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true });
      fs.writeFileSync(file, Buffer.from(data, 'base64'));
      this.last = { file, key };
    }
    if (this.force > 0) this.force--;
    this.n++;
  }

  async hold(seconds) { for (let i = 0, k = Math.round(seconds * this.fps); i < k; i++) await this.frame(); }

  /** Centre of a selector (Playwright syntax), or the point itself for [x, y]. */
  async point(target) {
    if (Array.isArray(target)) return target;
    const loc = this.page.locator(target).first();
    // count() and isVisible() answer at once; boundingBox() alone would wait 30 s for a missing element.
    if (!(await loc.count()) || !(await loc.isVisible())) throw this.fail('not visible: ' + target);
    const box = await loc.boundingBox({ timeout: 2000 }).catch(() => null);
    if (!box) throw this.fail('not visible: ' + target);
    const control = await loc.evaluate(pageControls);
    if (control) this.used.add(control);
    return [box.x + box.width / 2, box.y + box.height / 2];
  }

  /** Eased pointer move; the real mouse follows so hover states show. */
  async moveTo(target, frames) {
    const [tx, ty] = await this.point(target), x0 = this.x, y0 = this.y;
    const k = frames ?? Math.max(10, Math.min(34, Math.round(Math.hypot(tx - x0, ty - y0) / 38)));
    for (let i = 1; i <= k; i++) {
      const e = ease(i / k);
      this.x = x0 + (tx - x0) * e; this.y = y0 + (ty - y0) * e;
      await this.page.mouse.move(this.x, this.y);
      await this.frame();
    }
  }

  /** Expanding ring at the pointer, 8 frames. `action` runs on the second frame of the ring. */
  async ringWith(action) {
    const frames = 8;
    for (let i = 0; i < frames; i++) {
      this.ring = { x: this.x, y: this.y, t: i / (frames - 1) };
      if (i === 1 && action) { await action(); this.force = 6; }
      await this.frame();
    }
    this.ring = null;
  }

  /** Move to the target (if given) and really click there. */
  async click(target, frames) {
    if (target) await this.moveTo(target, frames);
    await this.ringWith(() => this.page.mouse.click(this.x, this.y));
  }

  async dblclick(target, frames) {
    if (target) await this.moveTo(target, frames);
    await this.ringWith(() => this.page.mouse.dblclick(this.x, this.y));
  }

  /** Native <select> lists do not render headless: point at it, ring, change the value in place. */
  async select(selector, value, frames) {
    await this.moveTo(selector, frames);
    await this.ringWith(() => this.page.locator(selector).first().selectOption(value));
  }

  /** Click into a field and type, one character per two frames. */
  async type(selector, text, { clear = false } = {}) {
    await this.click(selector);
    if (clear) await this.page.keyboard.press('Control+A');
    for (const ch of text) {
      await this.page.keyboard.type(ch);
      this.force = 3;
      await this.frame(); await this.frame();
    }
  }

  async press(key, frames = 6) {
    await this.page.keyboard.press(key);
    this.force = 6;
    for (let i = 0; i < frames; i++) await this.frame();
  }

  /** Eased scroll of a scrollable element to scrollTop = top. */
  async scrollTo(selector, top, frames = 18) {
    const loc = this.page.locator(selector).first();
    const from = await loc.evaluate((el) => el.scrollTop);
    for (let i = 1; i <= frames; i++) {
      await loc.evaluate((el, y) => { el.scrollTop = y; }, from + (top - from) * ease(i / frames));
      this.force = 2;
      await this.frame();
    }
  }

  /** Eased scroll of `container` so that `target` (inside it) sits in its middle. */
  async reveal(target, container, frames = 18) {
    const box = this.page.locator(container).first();
    const top = await this.page.locator(target).first().evaluate((el, c) => {
      const outer = el.closest(c) || document.querySelector(c);
      const a = el.getBoundingClientRect(), b = outer.getBoundingClientRect();
      const want = outer.scrollTop + (a.top - b.top) - (b.height - a.height) / 2;
      return Math.max(0, Math.min(want, outer.scrollHeight - outer.clientHeight));
    }, container).catch(() => { throw this.fail(`not found: ${target} in ${container}`); });
    if (Math.abs(top - await box.evaluate((el) => el.scrollTop)) > 2) await this.scrollTo(container, top, frames);
  }

  /** Press on the target, drag by (dx, dy) with easing, release. */
  async drag(target, dx, dy, frames = 24) {
    await this.moveTo(target);
    const x0 = this.x, y0 = this.y;
    await this.page.mouse.down();
    await this.ringWith();
    for (let i = 1; i <= frames; i++) {
      const e = ease(i / frames);
      this.x = x0 + dx * e; this.y = y0 + dy * e;
      await this.page.mouse.move(this.x, this.y);
      this.force = 2;
      await this.frame();
    }
    await this.page.mouse.up();
    this.force = 6;
  }

  /** Point at a control without clicking: the pointer rests on it for `seconds`. Counts as shown. */
  async show(target, seconds = 0.6, frames) {
    await this.moveTo(target, frames);
    await this.hold(seconds);
  }

  /** Thin outline around one element (CSS selector) until clearOutline(). */
  async outline(selector) {
    if (!(await this.page.locator(selector).first().isVisible())) throw this.fail('not visible: ' + selector);
    this.outlined = selector;
  }

  clearOutline() { this.outlined = null; }

  /**
   * Step frames until a JavaScript expression is truthy in the page.
   * Waits for results of the simulator are written with this, never with a frame count.
   */
  async until(expression, { max = 20, what } = {}) {
    const wasLive = this.live;
    this.live = true;
    try {
      for (let i = 0, k = Math.round(max * this.fps); ; i++) {
        if (await this.page.evaluate(expression)) return;
        if (i >= k) throw this.fail(`waited ${max} s for: ${what || expression}`);
        await this.frame();
      }
    } finally { this.live = wasLive; }
  }

  /** The visible text (or value) of an element must match. */
  async expect(selector, regex) {
    const loc = this.page.locator(selector).first();
    if (!(await loc.count())) throw this.fail('not found: ' + selector);
    const text = await loc.evaluate((el) => ('value' in el && typeof el.value === 'string' && el.value !== '' ? el.value : el.innerText ?? el.textContent ?? ''));
    if (!regex.test(text)) throw this.fail(`${selector} shows ${JSON.stringify(text.slice(0, 200))}, expected ${regex}`);
  }

  /** Answer the page's next confirm() or prompt(). Resolves with the dialog's message. */
  answerDialog(value = true) {
    return new Promise((resolve) => {
      this.dialog = (d) => {
        const done = value === false ? d.dismiss() : d.accept(typeof value === 'string' ? value : undefined);
        done.catch(() => {}).then(() => resolve(d.message()));
      };
    });
  }

  /**
   * One narration cue: its speech starts now. `actions` run while it is spoken; the next
   * cue starts when both the speech (plus a gap) and the actions are over.
   */
  async cue(key, actions = async () => {}) {
    const cue = this.cues[key];
    if (!cue) throw new Error(`cue ${key}: not in the narration`);
    if (this.done.some((c) => c.key === key)) throw new Error(`cue ${key}: used twice in the tour`);
    const start = this.n;
    this.done.push({ key, frame: start });
    if (this.chapters.at(-1)?.id !== cue.chapter) this.chapters.push({ id: cue.chapter, frame: start });
    this.current = key;
    try {
      await actions();
      const next = this.cues[this.order[this.order.indexOf(key) + 1]];
      const gap = next && next.chapter === cue.chapter ? CUE_GAP : CHAPTER_GAP_S;
      const end = start + Math.round(cue.dur * this.fps) + Math.round(gap * this.fps);
      while (this.n < end) await this.frame();
      for (const d of await this.page.evaluate(pageControls)) this.seen.add(d);
    } catch (e) {
      throw e.message.startsWith(`cue ${key}:`) ? e : Object.assign(e, { message: `cue ${key}: ${e.message}` });
    } finally { this.current = null; }
  }

  /** Hold the final picture, write timeline.json, return the number of frames. */
  async finish() {
    await this.hold(TAIL);
    fs.mkdirSync(this.out, { recursive: true });
    fs.writeFileSync(path.join(this.out, 'timeline.json'), JSON.stringify(
      { fps: this.fps, frames: this.n, cues: this.done, chapters: this.chapters }, null, 1));
    fs.writeFileSync(path.join(this.out, 'coverage.json'), JSON.stringify(
      { seen: [...this.seen].sort(), used: [...this.used].sort() }, null, 1));
    return this.n;
  }
}
