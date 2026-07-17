"use strict";

// ---- Panel registry --------------------------------------------------------
// Populated in initLayout() after DOM is ready
const PANEL_REGISTRY = {};

// ---- Default trees ---------------------------------------------------------

const SC_DEFAULT_TREE = {
  type: 'split', dir: 'h', sizes: [30, 40, 30],
  children: [
    {
      type: 'split', dir: 'v', sizes: [50, 50],
      children: [
        { type: 'leaf', panelId: 'source' },
        { type: 'leaf', panelId: 'editor' },
      ]
    },
    {
      type: 'split', dir: 'v', sizes: [40, 15, 45],
      children: [
        { type: 'leaf', panelId: 'registers' },
        { type: 'leaf', panelId: 'io' },
        { type: 'leaf', panelId: 'signal-graph' },
      ]
    },
    {
      type: 'split', dir: 'v', sizes: [40, 30, 30],
      children: [
        { type: 'leaf', panelId: 'mem-graph' },
        { type: 'leaf', panelId: 'memory1' },
        { type: 'leaf', panelId: 'memory2' },
      ]
    }
  ]
};

const MC_DEFAULT_TREE = {
  type: 'split', dir: 'h', sizes: [55, 20, 25],
  children: [
    {
      type: 'split', dir: 'v', sizes: [50, 50],
      children: [
        {
          type: 'split', dir: 'h', sizes: [67, 33],
          children: [
            { type: 'leaf', panelId: 'mc-pru0-source' },
            { type: 'leaf', panelId: 'mc-pru0-registers' },
          ]
        },
        {
          type: 'split', dir: 'h', sizes: [67, 33],
          children: [
            { type: 'leaf', panelId: 'mc-rtu0-source' },
            { type: 'leaf', panelId: 'mc-rtu0-registers' },
          ]
        }
      ]
    },
    {
      type: 'split', dir: 'v', sizes: [25, 15, 30, 30],
      children: [
        { type: 'leaf', panelId: 'editor' },
        { type: 'leaf', panelId: 'io' },
        { type: 'leaf', panelId: 'signal-graph' },
        { type: 'leaf', panelId: 'mem-graph' },
      ]
    },
    {
      type: 'split', dir: 'v', sizes: [50, 50],
      children: [
        { type: 'leaf', panelId: 'memory1' },
        { type: 'leaf', panelId: 'memory2' },
      ]
    }
  ]
};

// ---- Live state ------------------------------------------------------------
let currentMode = 'sc';   // 'sc' | 'mc'
let currentTree = null;
let tileRoot    = null;
let panelPool   = null;

// ---- localStorage helpers --------------------------------------------------

function saveLayout(mode, tree) {
  try {
    localStorage.setItem(`pru-layout-${mode}`, JSON.stringify(tree));
  } catch (e) { /* quota exceeded — ignore */ }
}

function loadLayout(mode) {
  try {
    const raw = localStorage.getItem(`pru-layout-${mode}`);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function defaultTree(mode) {
  // Deep-clone so mutations don't corrupt the default
  return JSON.parse(JSON.stringify(mode === 'mc' ? MC_DEFAULT_TREE : SC_DEFAULT_TREE));
}

// ---- Tree rendering --------------------------------------------------------

function renderNode(node) {
  if (node.type === 'leaf') {
    const leaf = document.createElement('div');
    leaf.className = 'tile-leaf';
    leaf.dataset.panelId = node.panelId;
    const panel = PANEL_REGISTRY[node.panelId];
    if (panel) {
      leaf.appendChild(panel);
      const titleEl = panel.querySelector('[data-panel-id]');
      if (titleEl && !titleEl.dataset.dragBound) {
        titleEl.dataset.dragBound = '1';
        titleEl.style.cursor = 'grab';
        titleEl.addEventListener('mousedown', (e) => {
          if (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT' ||
              e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;
          e.preventDefault();
          startDrag(node.panelId, e);
        });
      }
      // Add collapse/expand toggle button
      if (titleEl && !titleEl.querySelector('.panel-collapse-btn')) {
        // For panels where the title element is not .panel-title (e.g. memory controls),
        // inject a label that's only visible when collapsed
        if (!titleEl.classList.contains('panel-title')) {
          const lbl = document.createElement('span');
          lbl.className = 'panel-collapse-label';
          lbl.textContent = node.panelId.replace(/\d+$/, ' $&').toUpperCase();
          titleEl.insertBefore(lbl, titleEl.firstChild);
        }
        const btn = document.createElement('span');
        btn.className = 'panel-collapse-btn';
        btn.textContent = '−';
        btn.title = 'Collapse / Expand';
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          togglePanelCollapse(panel);
        });
        titleEl.appendChild(btn);
      }
    }
    return leaf;
  }

  // Split node
  const split = document.createElement('div');
  split.className = `tile-split tile-split-${node.dir}`;

  node.children.forEach((child, i) => {
    const wrapper = document.createElement('div');
    wrapper.className = 'tile-child';
    wrapper.style.flex = node.sizes[i];
    wrapper.appendChild(renderNode(child));
    split.appendChild(wrapper);

    if (i < node.children.length - 1) {
      const div = document.createElement('div');
      div.className = `tile-divider${node.dir === 'v' ? ' tile-divider-v' : ''}`;
      div.dataset.splitDir = node.dir;
      div.addEventListener('mousedown', onDividerMousedown);
      split.appendChild(div);
    }
  });

  return split;
}

function render(tree) {
  // Return all panels to pool first (prevents innerHTML from destroying them)
  for (const panel of Object.values(PANEL_REGISTRY)) {
    if (panel) panelPool.appendChild(panel);
  }
  // Clear tile root
  while (tileRoot.firstChild) tileRoot.removeChild(tileRoot.firstChild);
  // Render tree
  tileRoot.appendChild(renderNode(tree));
}

// ---- Divider resize --------------------------------------------------------

let _divResize = null;

function onDividerMousedown(e) {
  e.preventDefault();
  const divider = e.currentTarget;
  const isV = divider.classList.contains('tile-divider-v');

  // Find the two sibling tile-child wrappers
  const prev = divider.previousElementSibling;
  const next = divider.nextElementSibling;
  if (!prev || !next) return;

  const parentRect = divider.parentElement.getBoundingClientRect();
  const parentSize = isV ? parentRect.height : parentRect.width;

  const prevPx = isV ? prev.getBoundingClientRect().height : prev.getBoundingClientRect().width;
  const nextPx = isV ? next.getBoundingClientRect().height : next.getBoundingClientRect().width;

  _divResize = {
    isV,
    prev, next,
    parentSize,
    startPos: isV ? e.clientY : e.clientX,
    startPrev: prevPx,
    startNext: nextPx,
    divider,
  };

  divider.classList.add('dragging');
  document.body.style.userSelect = 'none';
  document.body.style.cursor = isV ? 'row-resize' : 'col-resize';
  document.addEventListener('mousemove', onDividerMousemove);
  document.addEventListener('mouseup',   onDividerMouseup);
}

function onDividerMousemove(e) {
  if (!_divResize) return;
  const { isV, prev, next, parentSize, startPos, startPrev, startNext } = _divResize;
  const delta = (isV ? e.clientY : e.clientX) - startPos;
  const MIN_PX = parentSize * 0.10; // 10% minimum
  const total = startPrev + startNext;
  const newPrev = Math.max(MIN_PX, Math.min(startPrev + delta, total - MIN_PX));
  const newNext = total - newPrev;
  prev.style.flex = `0 0 ${newPrev}px`;
  next.style.flex = `0 0 ${newNext}px`;
}

function onDividerMouseup(e) {
  if (!_divResize) return;
  const { isV, prev, next, divider } = _divResize;

  // Convert pixel sizes back to percentages and persist
  const prevPx = isV ? prev.getBoundingClientRect().height : prev.getBoundingClientRect().width;
  const nextPx = isV ? next.getBoundingClientRect().height : next.getBoundingClientRect().width;
  const total = prevPx + nextPx;
  if (total > 0) {
    updateSizesInTree(currentTree, prev, next, (prevPx / total) * 100, (nextPx / total) * 100);
    saveLayout(currentMode, currentTree);
    prev.style.flex = (prevPx / total) * 100;
    next.style.flex = (nextPx / total) * 100;
  }

  divider.classList.remove('dragging');
  document.body.style.userSelect = '';
  document.body.style.cursor = '';
  document.removeEventListener('mousemove', onDividerMousemove);
  document.removeEventListener('mouseup',   onDividerMouseup);
  _divResize = null;
}

// Walk the tree and update sizes for the split node whose children match prev/next.
function updateSizesInTree(node, prevEl, nextEl, prevPct, nextPct) {
  if (node.type === 'leaf') return;
  for (let i = 0; i < node.children.length - 1; i++) {
    const prevIds = getLeafIds(node.children[i]);
    const nextIds = getLeafIds(node.children[i + 1]);
    const prevElIds = getRenderedLeafIds(prevEl);
    const nextElIds = getRenderedLeafIds(nextEl);
    if (arraysEqual(prevIds.sort(), prevElIds.sort()) &&
        arraysEqual(nextIds.sort(), nextElIds.sort())) {
      const pairSum = node.sizes[i] + node.sizes[i + 1];
      node.sizes[i]     = (prevPct / 100) * pairSum;
      node.sizes[i + 1] = (nextPct / 100) * pairSum;
      return;
    }
  }
  for (const child of node.children) updateSizesInTree(child, prevEl, nextEl, prevPct, nextPct);
}

function getLeafIds(node) {
  if (node.type === 'leaf') return [node.panelId];
  return node.children.flatMap(getLeafIds);
}

function getRenderedLeafIds(el) {
  const leaves = el.querySelectorAll('.tile-leaf');
  if (leaves.length === 0 && el.classList.contains('tile-leaf')) {
    return [el.dataset.panelId];
  }
  return Array.from(leaves).map(l => l.dataset.panelId);
}

function arraysEqual(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

// ---- Tree manipulation helpers --------------------------------------------

// Returns a new tree with the leaf matching panelId removed.
// If a split is left with one child, it is replaced by that child.
function removeLeafFromTree(node, panelId) {
  if (node.type === 'leaf') {
    return node.panelId === panelId ? null : node;
  }
  const newChildren = [];
  const keptSizes = [];
  for (let i = 0; i < node.children.length; i++) {
    const result = removeLeafFromTree(node.children[i], panelId);
    if (result !== null) {
      newChildren.push(result);
      keptSizes.push(node.sizes[i]);
    }
  }
  if (newChildren.length === 0) return null;
  if (newChildren.length === 1) return newChildren[0]; // collapse single-child split

  const total = keptSizes.reduce((a, b) => a + b, 0);
  return {
    ...node,
    children: newChildren,
    sizes: total > 0 ? keptSizes.map(s => (s / total) * 100) : keptSizes,
  };
}

// Returns a new tree with the leaf matching targetId replaced by newNode.
function replaceLeafInTree(node, targetId, newNode) {
  if (node.type === 'leaf') {
    return node.panelId === targetId ? newNode : node;
  }
  return {
    ...node,
    children: node.children.map(c => replaceLeafInTree(c, targetId, newNode)),
  };
}

// ---- Drag-to-split ---------------------------------------------------------

let _drag = null;
let _ghost = null;

function startDrag(panelId, e) {
  _drag = { panelId, startX: e.clientX, startY: e.clientY, active: false };
  document.addEventListener('mousemove', onDragMove);
  document.addEventListener('mouseup',   onDragUp);
  document.addEventListener('keydown',   onDragKey);
}

function onDragMove(e) {
  if (!_drag) return;
  const dx = e.clientX - _drag.startX;
  const dy = e.clientY - _drag.startY;
  if (!_drag.active && Math.sqrt(dx * dx + dy * dy) < 6) return; // dead zone

  if (!_drag.active) {
    _drag.active = true;
    _ghost = document.createElement('div');
    _ghost.className = 'tile-drag-ghost';
    _ghost.textContent = _drag.panelId;
    document.body.appendChild(_ghost);
    // Inject drop zones into all other tile-leafs
    document.querySelectorAll('#tile-root .tile-leaf').forEach(leaf => {
      if (leaf.dataset.panelId === _drag.panelId) return;
      ['top', 'right', 'bottom', 'left'].forEach(side => {
        const zone = document.createElement('div');
        zone.className = `tile-drop-zone tile-drop-${side}`;
        zone.dataset.side = side;
        zone.dataset.targetPanelId = leaf.dataset.panelId;
        leaf.appendChild(zone);
      });
    });
  }

  _ghost.style.left = (e.clientX + 12) + 'px';
  _ghost.style.top  = (e.clientY + 12) + 'px';
}

function onDragUp(e) {
  if (!_drag) return;
  if (_drag.active) {
    const zone = document.elementFromPoint(e.clientX, e.clientY);
    if (zone && zone.classList.contains('tile-drop-zone')) {
      executeDrop(_drag.panelId, zone.dataset.targetPanelId, zone.dataset.side);
    }
  }
  cancelDrag();
}

function onDragKey(e) {
  if (e.key === 'Escape') cancelDrag();
}

function cancelDrag() {
  if (_ghost) { _ghost.remove(); _ghost = null; }
  document.querySelectorAll('.tile-drop-zone').forEach(z => z.remove());
  document.removeEventListener('mousemove', onDragMove);
  document.removeEventListener('mouseup',   onDragUp);
  document.removeEventListener('keydown',   onDragKey);
  _drag = null;
}

function executeDrop(draggedId, targetId, side) {
  if (!draggedId || !targetId || draggedId === targetId) return;

  let tree = removeLeafFromTree(currentTree, draggedId);
  if (!tree) return;

  const dir = (side === 'left' || side === 'right') ? 'h' : 'v';
  const dragFirst = (side === 'top' || side === 'left');
  const draggedLeaf = { type: 'leaf', panelId: draggedId };
  const newSplit = {
    type: 'split', dir,
    sizes: [50, 50],
    children: dragFirst
      ? [draggedLeaf, { type: 'leaf', panelId: targetId }]
      : [{ type: 'leaf', panelId: targetId }, draggedLeaf],
  };

  tree = replaceLeafInTree(tree, targetId, newSplit);
  currentTree = tree;
  render(currentTree);
  saveLayout(currentMode, currentTree);
}

// ---- Public API ------------------------------------------------------------

function initLayout(mode) {
  currentMode = mode || 'sc';

  tileRoot  = document.getElementById('tile-root');
  panelPool = document.getElementById('panel-pool');

  // Build panel registry
  const SC_PANELS = {
    source:         document.getElementById('source-panel'),
    registers:      document.getElementById('registers-panel'),
    editor:         document.getElementById('editor-panel'),
    memory1:        document.getElementById('memory-panel'),
    memory2:        document.getElementById('memory-panel-2'),
    io:             document.getElementById('io-panel'),
    'signal-graph': document.getElementById('signal-graph-panel'),
    'mem-graph':    document.getElementById('mem-graph-panel'),
  };
  const MC_PANELS = {
    'mc-pru0-source':    document.getElementById('mc-pru0-source-panel'),
    'mc-pru0-registers': document.getElementById('mc-pru0-reg-panel'),
    'mc-rtu0-source':    document.getElementById('mc-rtu0-source-panel'),
    'mc-rtu0-registers': document.getElementById('mc-rtu0-reg-panel'),
  };
  Object.assign(PANEL_REGISTRY, SC_PANELS, MC_PANELS);

  // Invalidate saved layouts when panel set changes
  const LAYOUT_VERSION = 3;
  const storedVer = parseInt(localStorage.getItem('pru-layout-ver') || '0', 10);
  if (storedVer < LAYOUT_VERSION) {
    localStorage.removeItem('pru-layout-sc');
    localStorage.removeItem('pru-layout-mc');
    localStorage.setItem('pru-layout-ver', String(LAYOUT_VERSION));
  }

  currentTree = loadLayout(currentMode) || defaultTree(currentMode);
  render(currentTree);
}

function switchLayoutMode(newMode) {
  // Save current layout
  saveLayout(currentMode, currentTree);
  currentMode = newMode;
  currentTree = loadLayout(newMode) || defaultTree(newMode);
  render(currentTree);
}

function resetLayout() {
  localStorage.removeItem(`pru-layout-${currentMode}`);
  currentTree = defaultTree(currentMode);
  render(currentTree);
}

// ---- Panel collapse/expand ---------------------------------------------------

const COLLAPSED_TITLE_HEIGHT = 26; // px — just the title bar

function togglePanelCollapse(panel) {
  const tileChild = panel.closest('.tile-child');
  if (!tileChild) return;
  const btn = panel.querySelector('.panel-collapse-btn');
  const isCollapsed = panel.classList.toggle('panel-collapsed');

  if (isCollapsed) {
    // Save current flex value and collapse
    tileChild.dataset.prevFlex = tileChild.style.flex || '';
    tileChild.style.flex = `0 0 ${COLLAPSED_TITLE_HEIGHT}px`;
    if (btn) btn.textContent = '+';
  } else {
    // Restore previous flex value
    const prev = tileChild.dataset.prevFlex;
    tileChild.style.flex = prev || '1';
    delete tileChild.dataset.prevFlex;
    if (btn) btn.textContent = '−';
  }
}
