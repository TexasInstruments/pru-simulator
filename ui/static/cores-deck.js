"use strict";

// The overlay is built once. State updates patch its controls, preserving hover,
// focus and pointer targets while the simulator sends new core state.
function createCoresDeck(root, handlers) {
  const chipIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4m4-4v4m-4 10v4m4-4v4M3 10h4m-4 4h4m10-4h4m-4 4h4"/></svg>';
  const eyeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  root.innerHTML = `<button type="button" id="cores-toggle" aria-expanded="false" aria-controls="cores-deck">
    <b>CORES</b><span class="cores-pill-items"></span><span class="cores-mode"></span><span class="cores-chevron" aria-hidden="true">▾</span></button>
    <div class="cores-hover-bridge" id="cores-deck" inert><div class="cores-deck-surface">
    <div class="cores-cards">${PruCores.ORDER.map((core,i) => `<section class="core-card" data-card="${core}" style="--core-color:var(--core-${core});--deck-index:${i};--deck-rotation:${[-6,4,-3,7][i]}deg">
      <button type="button" class="core-card-heading" data-solo="${core}" title="Show only ${core.toUpperCase()}"><span class="core-status-dot"></span><b>${core.toUpperCase()}</b><small data-core-pc>PC 0</small></button>
      <div class="core-card-toggles"><button type="button" data-use="${core}" aria-pressed="false">${chipIcon}Use</button><button type="button" data-show="${core}" aria-pressed="false">${eyeIcon}Show</button></div>
      <div class="core-card-clock"><b data-core-rate></b></div>
    </section>`).join('')}</div>
    <div class="cores-clock-single"><span class="cores-clock-head">PRU core frequency: <b data-core-rate>— MHz</b></span><div class="cores-clock-track"><input type="range" class="cores-clock-slider" min="0" max="0" step="1" value="0" aria-label="PRU core clock"><div class="cores-clock-ticks" aria-hidden="true"></div></div></div>
    <p class="cores-clock-notice">The PRU core frequency applies to all cores within the PRU subsystem. Changing it reloads the simulator and clears loaded programs. IEP is separate.</p>
    <div class="cores-deck-footer"><span>Use = include in RUN/STEP · Show = open Source &amp; Registers panes</span><button type="button" data-action="use-all">Use all</button><button type="button" data-action="show-all">Show all used</button></div>
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
  function positionDeck() {
    const trigger = root.getBoundingClientRect();
    const box = root.closest('.toolbar-context').getBoundingClientRect();
    root.style.setProperty('--deck-gap',`${Math.max(0,box.bottom-trigger.bottom)+8}px`);
    root.style.setProperty('--deck-trigger-width',`${trigger.width+12}px`);
    root.style.setProperty('--deck-top',`${box.bottom+4}px`);
  }
  function open(value) {
    positionDeck();
    root.classList.toggle('is-open',value);
    toggle.setAttribute('aria-expanded',String(value));
    overlay.inert = !value;
  }
  window.addEventListener('resize',positionDeck);
  root.addEventListener('pointerenter',event => { if (event.pointerType !== 'touch') open(true); });
  root.addEventListener('pointerleave',() => { if (!root.contains(document.activeElement)) open(false); });
  root.addEventListener('focusin',() => open(true));
  root.addEventListener('focusout',event => { if (!root.contains(event.relatedTarget) && !root.matches(':hover')) open(false); });
  root.addEventListener('keydown',event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      toggle.focus(); open(false);
    }
  });
  root.addEventListener('click',event => {
    const target = event.target.closest('button'); if (!target) return;
    for (const type of ['use','show','solo']) if (target.dataset[type]) { handlers.action({type,core:target.dataset[type]}); return; }
    if (target.dataset.action) { handlers.action({type:target.dataset.action}); return; }
  });
  root.addEventListener('input',event => {
    const slider = event.target.closest('.cores-clock-slider'); if (!slider) return;
    const choices = slider._choices; if (!choices || !choices.length) return;
    const mhz = choices[Number(slider.value)];
    if (mhz !== undefined && typeof handlers.setClock === 'function') handlers.setClock(mhz);
  });
  document.addEventListener('pointerdown',event => {
    if (!root.contains(event.target)) {
      open(false);
    }
  });
  return {
    shake(core) {
      const card = root.querySelector(`[data-card="${core}"]`);
      if (!card || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      card.animate([{transform:'translateX(0)'},{transform:'translateX(-3px)'},{transform:'translateX(3px)'},{transform:'translateX(0)'}],{duration:180});
    },
    pulse(core) {
      const card = root.querySelector(`[data-card="${core}"]`);
      if (!card || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      card.animate([
        {boxShadow:'0 0 0 0 var(--core-color)', transform:'scale(1)'},
        {boxShadow:'0 0 0 8px transparent', transform:'scale(1.03)', offset:0.5},
        {boxShadow:'0 0 0 0 transparent', transform:'scale(1)'}
      ], {duration:360});
    },
    update({selection,available,states,clocks,running,busy}) {
      positionDeck();
      root.querySelector('.cores-mode').textContent = '×' + selection.used.length;
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
        card.querySelector('[data-core-rate]').textContent = clocks.mhz ? `${clocks.mhz} MHz` : '— MHz';
      }
      const singleRow = root.querySelector('.cores-clock-single');
      if (singleRow) {
        singleRow.querySelector('[data-core-rate]').textContent = clocks.mhz ? `${clocks.mhz} MHz` : '— MHz';
        const disabled = running || busy || selection.used.length === 0;
        const slider = singleRow.querySelector('.cores-clock-slider');
        if (slider && clocks.choices_mhz && clocks.choices_mhz.length) {
          slider._choices = clocks.choices_mhz;
          slider.min = 0;
          slider.max = clocks.choices_mhz.length - 1;
          const idx = clocks.choices_mhz.indexOf(clocks.mhz);
          slider.value = idx >= 0 ? idx : 0;
          slider.disabled = disabled;
        }
        const ticks = singleRow.querySelector('.cores-clock-ticks');
        if (ticks && clocks.choices_mhz && clocks.choices_mhz.length) {
          ticks.replaceChildren();
          for (const mhz of clocks.choices_mhz) {
            const tick = document.createElement('span');
            tick.className = 'cores-clock-tick' + (mhz === clocks.mhz ? ' active' : '');
            tick.title = mhz + ' MHz';
            ticks.appendChild(tick);
          }
        }
      }
      for (const type of ['use-all','show-all']) root.querySelector(`[data-action="${type}"]`).disabled = busy;
    }
  };
}
