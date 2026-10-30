"use strict";

// The overlay is built once. State updates patch its controls, preserving hover,
// focus and pointer targets while the simulator sends new core state.
function createCoresDeck(root, handlers) {
  const chipIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4m4-4v4m-4 10v4m4-4v4M3 10h4m-4 4h4m10-4h4m-4 4h4"/></svg>';
  const eyeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  let seen = false;
  try { seen = localStorage.getItem('pru-cores-seen') === '1'; } catch (_) {}
  root.innerHTML = `<button type="button" id="cores-toggle" aria-expanded="false" aria-controls="cores-deck">
    <b>CORES</b><span class="cores-pill-items"></span><span class="cores-mode"></span><span class="cores-chevron" aria-hidden="true">▾</span></button>
    <div id="cores-coach" role="note"><b>Multi-core starts here.</b> Choose which cores to use and show. Set one clock per group: PRU0 + RTU0, or PRU1 + RTU1.<button type="button" data-coach>Got it</button></div>
    <div class="cores-hover-bridge" id="cores-deck" inert><div class="cores-deck-surface">
    <div class="cores-cards">${PruCores.ORDER.map((core,i) => `<section class="core-card" data-card="${core}" style="--core-color:var(--core-${core});--deck-index:${i};--deck-rotation:${[-6,4,-3,7][i]}deg">
      <button type="button" class="core-card-heading" data-solo="${core}" title="Show only ${core.toUpperCase()}"><span class="core-status-dot"></span><b>${core.toUpperCase()}</b><small data-core-pc>PC 0</small></button>
      <div class="core-card-toggles"><button type="button" data-use="${core}" aria-pressed="false">${chipIcon}Use</button><button type="button" data-show="${core}" aria-pressed="false">${eyeIcon}Show</button></div>
      <div class="core-card-clock"><b data-core-rate></b></div>
    </section>`).join('')}</div>
    <div class="cores-clock-groups">${[0,1].map(group => `<div class="cores-clock-group" data-group="${group}"><div><b>Core frequency</b><small data-group-members></small></div><div class="clock-stepper"><button type="button" data-group-clock="${group}" data-direction="-1" aria-label="Lower clock for ${group === 0 ? 'PRU0 and RTU0' : 'PRU1 and RTU1'}">‹</button><b data-group-rate>— MHz</b><button type="button" data-group-clock="${group}" data-direction="1" aria-label="Higher clock for ${group === 0 ? 'PRU0 and RTU0' : 'PRU1 and RTU1'}">›</button></div></div>`).join('')}</div>
    <p class="cores-clock-notice">Each frequency applies to the listed cores, including hidden cores. Changing it reloads the simulator and clears loaded programs. This pairing is a simulator setting and may change. IEP is separate.</p>
    <div class="cores-deck-footer"><span>Use = runs with RUN/STEP · Show = a Source &amp; Registers pane</span><button type="button" data-action="use-all">Use all</button><button type="button" data-action="show-all">Show all used</button><button type="button" data-action="pin" aria-label="Pin CORES deck" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 3 8 0-1 6 4 4v2H5v-2l4-4-1-6Zm4 12v7"/></svg></button></div>
    </div></div>`;
  const toggle = root.querySelector('#cores-toggle');
  const overlay = root.querySelector('#cores-deck');
  const pill = root.querySelector('.cores-pill-items');
  const indicators = {};
  for (const core of PruCores.ORDER) {
    const indicator = document.createElement('span'); indicator.style.setProperty('--core-color',`var(--core-${core})`);
    indicator.innerHTML = `<span class="core-status-dot"></span><b><span class="core-name-full">${core.toUpperCase()}</span><span class="core-name-short">${core.replace('pru','P').replace('rtu','R')}</span></b>`;
    pill.appendChild(indicator); indicators[core] = indicator;
  }
  let pinned = false;
  function positionDeck() {
    const trigger = root.getBoundingClientRect();
    const box = root.closest('.toolbar-context').getBoundingClientRect();
    root.style.setProperty('--deck-gap',`${Math.max(0,box.bottom-trigger.bottom)+8}px`);
    root.style.setProperty('--deck-trigger-width',`${trigger.width+12}px`);
    root.style.setProperty('--deck-top',`${box.bottom+4}px`);
  }
  function open(value) {
    positionDeck();
    root.classList.toggle('is-open',value || pinned);
    toggle.setAttribute('aria-expanded',String(value || pinned));
    overlay.inert = !(value || pinned);
  }
  function dismiss() {
    if (seen) return;
    seen = true; root.classList.remove('needs-coach');
    try { localStorage.setItem('pru-cores-seen','1'); } catch (_) {}
  }
  root.classList.toggle('needs-coach',!seen);
  window.addEventListener('resize',positionDeck);
  root.addEventListener('pointerenter',event => { if (event.pointerType !== 'touch') open(true); });
  root.addEventListener('pointerleave',() => { if (!root.contains(document.activeElement)) open(false); });
  root.addEventListener('focusin',() => open(true));
  root.addEventListener('focusout',event => { if (!root.contains(event.relatedTarget) && !root.matches(':hover')) open(false); });
  root.addEventListener('keydown',event => {
    if (event.key === 'Escape') {
      event.preventDefault(); if (pinned) handlers.action({type:'pin'});
      toggle.focus(); open(false);
    }
  });
  root.addEventListener('click',event => {
    const target = event.target.closest('button'); if (!target) return;
    if (target.hasAttribute('data-coach')) { dismiss(); return; }
    if (target === toggle) { handlers.action({type:'pin'}); return; }
    if (target.closest('#cores-deck')) dismiss();
    for (const type of ['use','show','solo']) if (target.dataset[type]) { handlers.action({type,core:target.dataset[type]}); return; }
    if (target.dataset.action) { handlers.action({type:target.dataset.action}); return; }
    if (target.dataset.groupClock !== undefined) handlers.clock(Number(target.dataset.groupClock),Number(target.dataset.direction));
  });
  document.addEventListener('pointerdown',event => {
    if (!root.contains(event.target)) {
      if (pinned) handlers.action({type:'pin'});
      open(false);
    }
  });
  return {
    shake(core) {
      const card = root.querySelector(`[data-card="${core}"]`);
      if (!card || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      card.animate([{transform:'translateX(0)'},{transform:'translateX(-3px)'},{transform:'translateX(3px)'},{transform:'translateX(0)'}],{duration:180});
    },
    update({selection,available,states,clocks,running,busy}) {
      positionDeck();
      pinned = selection.pinned;
      root.classList.toggle('pinned',pinned);
      if (pinned) open(true);
      root.querySelector('[data-action="pin"]').setAttribute('aria-pressed',String(pinned));
      root.querySelector('.cores-mode').textContent = selection.used.length > 1 ? `MULTI ×${selection.used.length}` : 'SINGLE';
      root.querySelector('.cores-mode').classList.toggle('multi',selection.used.length > 1);
      for (const core of PruCores.ORDER) {
        const card = root.querySelector(`[data-card="${core}"]`);
        card.hidden = !available.includes(core); indicators[core].hidden = card.hidden;
        const used = selection.used.includes(core), shown = selection.shown.includes(core);
        card.classList.toggle('unused',!used); card.classList.toggle('shown',shown);
        indicators[core].className = shown ? 'core-indicator shown' : used ? 'core-indicator used' : 'core-indicator';
        indicators[core].title = `${core.toUpperCase()}: ${used ? shown ? 'used and shown' : 'used, hidden' : 'not used'}`;
        for (const name of ['use','show']) {
          const button = card.querySelector(`[data-${name}]`);
          button.setAttribute('aria-pressed',String(name === 'use' ? used : shown));
          button.disabled = busy || name === 'show' && !used;
          if (name === 'show') button.hidden = !used;
        }
        card.querySelector('[data-solo]').disabled = !used || busy;
        card.querySelector('[data-core-pc]').textContent = `PC ${states[core]?.pc ?? 0}`;
        const active = used && running && !states[core]?.halted;
        for (const dot of [card.querySelector('.core-status-dot'),indicators[core].firstChild]) {
          dot.classList.toggle('active',active); dot.classList.toggle('off',!used);
        }
        const group = clocks.groups.find(g => g.cores.includes(core));
        card.querySelector('[data-core-rate]').textContent = group ? `${group.mhz} MHz` : '— MHz';
      }
      for (const el of root.querySelectorAll('[data-group]')) {
        const group = clocks.groups.find(g => g.id === Number(el.dataset.group));
        el.hidden = !group;
        if (!group) continue;
        el.querySelector('[data-group-members]').textContent = group.cores.map(core=>core.toUpperCase()).join(' + ');
        el.querySelector('[data-group-rate]').textContent = `${group.mhz} MHz`;
        const disabled = running || busy || !group.cores.some(core => selection.used.includes(core));
        for (const button of el.querySelectorAll('[data-direction]')) {
          const direction = Number(button.dataset.direction);
          button.disabled = disabled || !clocks.choices_mhz.some(mhz => direction > 0 ? mhz > group.mhz : mhz < group.mhz);
        }
      }
      for (const type of ['use-all','show-all']) root.querySelector(`[data-action="${type}"]`).disabled = busy;
    }
  };
}
