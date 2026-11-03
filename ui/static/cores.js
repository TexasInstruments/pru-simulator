"use strict";

// Pure selection state shared by the deck, inspectors, and execution controls.
globalThis.PruCores = (() => {
  const ORDER = Object.freeze(['pru0', 'pru1', 'rtu0', 'rtu1']);

  function targetCores(available) {
    const cores = ORDER.filter(core => Array.isArray(available) && available.includes(core));
    return cores.length ? cores : ORDER.slice();
  }

  function normalize(saved, available) {
    const cores = targetCores(available);
    const source = saved && typeof saved === 'object' && !Array.isArray(saved) ? saved : {};
    let used = source.used;
    let shown = source.shown;
    let focus = source.focus;
    // Old layouts alone do not establish that multicore was enabled.
    if (!Object.hasOwn(source, 'used') && typeof source.multiCoreMode === 'boolean') {
      used = source.multiCoreMode
        ? ['pru0', source.mcPartner, ...(Array.isArray(source.mcExtras) ? source.mcExtras : [])]
        : [source.currentCore];
      shown = used;
      focus = source.currentCore;
    }
    used = cores.filter(core => Array.isArray(used) && used.includes(core));
    if (!used.length) used = [cores[0]];
    shown = used.filter(core => Array.isArray(shown) && shown.includes(core));
    if (!shown.length) shown = [used[0]];
    if (!shown.includes(focus)) focus = shown[0];
    return { used, shown, focus, pinned: source.pinned === true };
  }

  function transition(state, action, available) {
    const next = normalize(state, available);
    const cores = targetCores(available);
    const { type, core } = action || {};
    switch (type) {
      case 'use':
        if (!cores.includes(core)) break;
        if (next.used.includes(core)) {
          if (next.used.length === 1) break;
          next.used = next.used.filter(name => name !== core);
          next.shown = next.shown.filter(name => name !== core);
        } else {
          next.used.push(core);
        }
        break;
      case 'show':
        if (!next.used.includes(core)) break;
        if (next.shown.includes(core)) {
          if (next.shown.length === 1) break;
          next.shown = next.shown.filter(name => name !== core);
        } else {
          next.shown.push(core);
        }
        break;
      case 'solo':
        if (!next.used.includes(core)) break;
        next.shown = [core];
        next.focus = core;
        break;
      case 'focus':
        if (!next.used.includes(core)) break;
        if (!next.shown.includes(core)) next.shown.push(core);
        next.focus = core;
        break;
      case 'use-all':
        next.used = cores.slice();
        break;
      case 'show-all':
        next.shown = next.used.slice();
        break;
      case 'pin':
        next.pinned = !next.pinned;
        break;
      case 'reset':
        return normalize(null, cores);
    }
    return normalize(next, cores);
  }

  return { ORDER, normalize, transition };
})();
