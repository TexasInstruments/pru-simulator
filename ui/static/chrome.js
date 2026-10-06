"use strict";

// Small helpers for the dashboard chrome (tabs, glider, toolbar labels, fault
// badge). The pure ones take plain values so tests/ui_chrome.test.js can run
// them without a browser.
(root => {
  // Toolbar buttons wrap their label as .text-container > .text so the hover
  // wipe can animate it; writing button.textContent would destroy the spans.
  function setButtonLabel(button, label) {
    const text = button && button.querySelector('.text');
    if (text) text.textContent = label;
    else if (button) button.textContent = label;
  }

  // WAI-ARIA tabs keyboard model: index to focus for a key, or -1 to ignore it.
  function nextTabIndex(key, current, count) {
    if (!count) return -1;
    if (key === 'ArrowRight' || key === 'ArrowDown') return (current + 1) % count;
    if (key === 'ArrowLeft' || key === 'ArrowUp') return (current - 1 + count) % count;
    if (key === 'Home') return 0;
    if (key === 'End') return count - 1;
    return -1;
  }

  // The glider sits under the active tab: its x offset and width come from that
  // tab alone, so any number of tabs (or a hidden strip, width 0) just works.
  function gliderBox(active) {
    if (!active || !(active.offsetWidth > 0)) return null;
    return { x: active.offsetLeft, width: active.offsetWidth };
  }

  function gliderStyle(box) {
    return box ? { '--glider-x': `${box.x}px`, '--glider-w': `${box.width}px` } : null;
  }

  // Move the glider of a tab strip under `active`. The transition is only
  // enabled (data-ready) after the first placement has been painted, so the
  // initial paint, and a strip first shown later, never animates.
  function placeGlider(strip, active) {
    const style = gliderStyle(gliderBox(active));
    if (!strip || !style) return false;
    for (const [name, value] of Object.entries(style)) strip.style.setProperty(name, value);
    strip.classList.add('has-glider');
    if (!strip.dataset.ready && !strip.dataset.pending) {
      strip.dataset.pending = '1';
      const frame = root.requestAnimationFrame || (fn => setTimeout(fn, 16));
      frame(() => frame(() => { delete strip.dataset.pending; strip.dataset.ready = '1'; }));
    }
    return true;
  }

  // Faults the user may want to look at: bus contention and device protocol
  // faults (device_bus.faults lists both) plus one per core memory fault.
  function faultTotal(state) {
    const bus = (state && state.io && state.io.device_bus) || {};
    const coreFaults = (state && state.core_faults) ||
      (state && state.fault ? { [state.core]: state.fault } : {});
    return (bus.faults || []).length + Object.keys(coreFaults).length;
  }

  // `seen` is the total the user last acknowledged by opening Events. It never
  // exceeds the current total, so a reset (faults cleared) re-arms the badge.
  function newFaultCount(total, seen) {
    return Math.max(0, total - Math.min(seen, total));
  }

  function badgeLabel(count) {
    return count === 1 ? '1 new fault' : `${count} new faults`;
  }

  function coreFaultLines(state, cores) {
    const faults = state.core_faults || (state.fault ? { [state.core]: state.fault } : {});
    return cores.filter(core => faults[core])
      .map(core => `${core} · ${faults[core].opcode} at ${faults[core].address}: ${faults[core].error}`);
  }

  root.setButtonLabel = setButtonLabel;
  root.PruChrome = { setButtonLabel, nextTabIndex, gliderBox, gliderStyle, placeGlider,
    faultTotal, newFaultCount, badgeLabel, coreFaultLines };
})(globalThis);
