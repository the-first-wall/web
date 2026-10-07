/**
 * The First Wall — Web Canvas Explorer & Codex Inspector
 * Pan, zoom, coordinate mapping, illuminated dossier, and census directory.
 */

// Canvas & Viewport State
const CANVAS_SIZE = 1000;
const BLOCK_SIZE = 10;
const GRID_COLS = CANVAS_SIZE / BLOCK_SIZE; // 100

let scale = 1.0;
let panX = 0;
let panY = 0;
let isDragging = false;
let startX = 0;
let startY = 0;

// Elements
const workspace = document.getElementById('workspace');
const canvasWrapper = document.getElementById('canvas-wrapper');
const canvas = document.getElementById('wall-canvas');
const ctx = canvas.getContext('2d');
const crosshair = document.getElementById('grid-crosshair');
const selectedIndicator = document.getElementById('slot-selected-indicator');

// HUD elements
const coordVal = document.getElementById('coord-val');
const coordSlot = document.getElementById('coord-slot');
const zoomLevelText = document.getElementById('zoom-level');
const btnZoomIn = document.getElementById('btn-zoom-in');
const btnZoomOut = document.getElementById('btn-zoom-out');
const btnZoomReset = document.getElementById('btn-zoom-reset');

// Dossier Drawer Elements
const dossierDrawer = document.getElementById('dossier-drawer');
const btnCloseDossier = document.getElementById('btn-close-dossier');

// Modals
const modalCensus = document.getElementById('modal-census');
const btnOpenCensus = document.getElementById('btn-open-census');
const btnCloseCensus = document.getElementById('btn-close-census-modal');
const censusTableBody = document.getElementById('census-table-body');

const modalManifesto = document.getElementById('modal-manifesto');
const btnOpenManifesto = document.getElementById('btn-open-manifesto');
const btnCloseManifesto = document.getElementById('btn-close-manifesto-modal');

const modalProtocol = document.getElementById('modal-protocol');
const btnOpenProtocol = document.getElementById('btn-open-protocol');
const btnCloseProtocol = document.getElementById('btn-close-protocol-modal');

// Tab buttons
const tabButtons = document.querySelectorAll('.codex-tab-btn');
const tabPanels = document.querySelectorAll('.codex-tab-panel');

// Raw JSON action button
const btnCopyRawJson = document.getElementById('btn-copy-raw-json');

// Memory Cache
let currentOpenDossierData = null;
let censusRoster = [];

// Touch gesture tracking
let initialPinchDist = null;
let initialPinchScale = null;

// -----------------------------------------------------------------------------
// 1. INITIALIZE MASTER CANVAS
// -----------------------------------------------------------------------------

const masterImg = new Image();
masterImg.src = 'assets/wall_01_composite.webp';
masterImg.onload = () => {
  drawCanvas();
};

function drawCanvas() {
  ctx.clearRect(0, 0, CANVAS_SIZE, CANVAS_SIZE);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(masterImg, 0, 0, CANVAS_SIZE, CANVAS_SIZE);
}

function updateTransform() {
  canvasWrapper.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
  zoomLevelText.textContent = `${Math.round(scale * 100)}%`;
}

function centerCanvas(animate = false) {
  const rect = workspace.getBoundingClientRect();
  const drawerOpen = dossierDrawer.classList.contains('is-open');
  const drawerWidth = (drawerOpen && window.innerWidth > 900) ? 520 : 0;
  const visibleWidth = rect.width - drawerWidth;

  const targetScale = Math.min((visibleWidth - 60) / CANVAS_SIZE, (rect.height - 60) / CANVAS_SIZE, 1.0);
  const targetPanX = (visibleWidth - CANVAS_SIZE * targetScale) / 2;
  const targetPanY = (rect.height - CANVAS_SIZE * targetScale) / 2;

  if (animate) {
    canvasWrapper.classList.add('is-animating');
    scale = targetScale;
    panX = targetPanX;
    panY = targetPanY;
    updateTransform();
    setTimeout(() => canvasWrapper.classList.remove('is-animating'), 500);
  } else {
    scale = targetScale;
    panX = targetPanX;
    panY = targetPanY;
    updateTransform();
  }
}

// -----------------------------------------------------------------------------
// 2. FOCAL-POINT ZOOM & PAN CONTROLS (MOUSE & WHEEL)
// -----------------------------------------------------------------------------

function zoomAtPoint(factor, clientX, clientY) {
  const rect = workspace.getBoundingClientRect();
  const mouseX = clientX - rect.left;
  const mouseY = clientY - rect.top;

  // Exact point on the 1000x1000 canvas under cursor
  const canvasX = (mouseX - panX) / scale;
  const canvasY = (mouseY - panY) / scale;

  let newScale = scale * factor;
  newScale = Math.min(Math.max(newScale, 0.4), 32.0); // 40% to 3200%

  // Lock canvas point under cursor
  panX = mouseX - canvasX * newScale;
  panY = mouseY - canvasY * newScale;
  scale = newScale;

  updateTransform();
}

workspace.addEventListener('wheel', (e) => {
  e.preventDefault();
  const zoomFactor = e.deltaY < 0 ? 1.15 : (1 / 1.15);
  zoomAtPoint(zoomFactor, e.clientX, e.clientY);
}, { passive: false });

workspace.addEventListener('mousedown', (e) => {
  if (e.target.closest('.hud-controls') || e.target.closest('.dossier-drawer')) return;
  isDragging = true;
  workspace.classList.add('is-dragging');
  startX = e.clientX - panX;
  startY = e.clientY - panY;
});

window.addEventListener('mousemove', (e) => {
  if (isDragging) {
    panX = e.clientX - startX;
    panY = e.clientY - startY;
    updateTransform();
  } else {
    handleHover(e);
  }
});

window.addEventListener('mouseup', () => {
  isDragging = false;
  workspace.classList.remove('is-dragging');
});

// HUD Zoom Buttons (zoom centered in visible workspace)
btnZoomIn.addEventListener('click', () => {
  const rect = workspace.getBoundingClientRect();
  const drawerOpen = dossierDrawer.classList.contains('is-open');
  const drawerWidth = (drawerOpen && window.innerWidth > 900) ? 520 : 0;
  const cx = rect.left + (rect.width - drawerWidth) / 2;
  const cy = rect.top + rect.height / 2;
  zoomAtPoint(1.3, cx, cy);
});

btnZoomOut.addEventListener('click', () => {
  const rect = workspace.getBoundingClientRect();
  const drawerOpen = dossierDrawer.classList.contains('is-open');
  const drawerWidth = (drawerOpen && window.innerWidth > 900) ? 520 : 0;
  const cx = rect.left + (rect.width - drawerWidth) / 2;
  const cy = rect.top + rect.height / 2;
  zoomAtPoint(1 / 1.3, cx, cy);
});

btnZoomReset.addEventListener('click', () => {
  centerCanvas(true);
});

// -----------------------------------------------------------------------------
// 3. TOUCH & PINCH SUPPORT (MOBILE SAFARI & CHROME)
// -----------------------------------------------------------------------------

function getTouchDistance(touches) {
  const dx = touches[0].clientX - touches[1].clientX;
  const dy = touches[0].clientY - touches[1].clientY;
  return Math.hypot(dx, dy);
}

let lastPinchDist = null;

workspace.addEventListener('touchstart', (e) => {
  if (e.target.closest('.hud-controls') || e.target.closest('.dossier-drawer')) return;
  
  if (e.touches.length === 1) {
    isDragging = true;
    startX = e.touches[0].clientX - panX;
    startY = e.touches[0].clientY - panY;
    lastPinchDist = null;
  } else if (e.touches.length === 2) {
    isDragging = false;
    e.preventDefault();
    lastPinchDist = getTouchDistance(e.touches);
  }
}, { passive: false });

workspace.addEventListener('touchmove', (e) => {
  if (e.target.closest('.hud-controls') || e.target.closest('.dossier-drawer')) return;

  if (isDragging && e.touches.length === 1) {
    e.preventDefault();
    panX = e.touches[0].clientX - startX;
    panY = e.touches[0].clientY - startY;
    updateTransform();
  } else if (e.touches.length === 2 && lastPinchDist) {
    e.preventDefault(); // Stop iOS Safari from intercepting pinch gesture
    const currentDist = getTouchDistance(e.touches);
    if (currentDist > 0 && lastPinchDist > 0) {
      const factor = currentDist / lastPinchDist;
      const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
      const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      zoomAtPoint(factor, midX, midY);
      lastPinchDist = currentDist;
    }
  }
}, { passive: false });

workspace.addEventListener('touchend', (e) => {
  if (e.touches.length === 0) {
    isDragging = false;
    lastPinchDist = null;
  } else if (e.touches.length === 1) {
    // Switched from 2 fingers to 1
    isDragging = true;
    startX = e.touches[0].clientX - panX;
    startY = e.touches[0].clientY - panY;
    lastPinchDist = null;
  }
});

// -----------------------------------------------------------------------------
// 4. COORDINATE CALCULATION & HOVER
// -----------------------------------------------------------------------------

function getGridCoordFromMouse(e) {
  const rect = workspace.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;

  const canvasX = (mouseX - panX) / scale;
  const canvasY = (mouseY - panY) / scale;

  if (canvasX < 0 || canvasX >= CANVAS_SIZE || canvasY < 0 || canvasY >= CANVAS_SIZE) {
    return null;
  }

  const gridX = Math.floor(canvasX / BLOCK_SIZE);
  const gridY = Math.floor(canvasY / BLOCK_SIZE);
  const slotNum = gridY * GRID_COLS + gridX + 1;

  return { gridX, gridY, slotNum };
}

function handleHover(e) {
  const coords = getGridCoordFromMouse(e);
  if (!coords) {
    crosshair.style.display = 'none';
    return;
  }

  const { gridX, gridY, slotNum } = coords;
  crosshair.style.display = 'block';
  crosshair.style.left = `${gridX * BLOCK_SIZE}px`;
  crosshair.style.top = `${gridY * BLOCK_SIZE}px`;

  coordVal.textContent = `X: ${String(gridX).padStart(3, '0')} | Y: ${String(gridY).padStart(3, '0')}`;
  
  if (slotNum === 1) {
    coordSlot.textContent = `SLOT: #0001 (Bookkeeper)`;
    crosshair.style.borderColor = 'var(--gold-primary)';
  } else {
    coordSlot.textContent = `SLOT: #${String(slotNum).padStart(4, '0')} (Unclaimed)`;
    crosshair.style.borderColor = 'rgba(255, 255, 255, 0.4)';
  }
}

// -----------------------------------------------------------------------------
// 5. JUMP TO SLOT & INSPECT
// -----------------------------------------------------------------------------

function focusAndHighlightSlot(gridX, gridY, zoom = 14.0, animate = true) {
  const rect = workspace.getBoundingClientRect();
  const drawerWidth = (window.innerWidth > 900) ? 520 : 0;
  const visibleWidth = rect.width - drawerWidth;

  // Center of the block on the 1000x1000 canvas
  const blockCenterX = gridX * BLOCK_SIZE + (BLOCK_SIZE / 2);
  const blockCenterY = gridY * BLOCK_SIZE + (BLOCK_SIZE / 2);

  // Target screen center in visible area
  const targetScreenX = visibleWidth / 2;
  const targetScreenY = rect.height / 2;

  // Position indicator
  selectedIndicator.style.display = 'block';
  selectedIndicator.style.left = `${gridX * BLOCK_SIZE}px`;
  selectedIndicator.style.top = `${gridY * BLOCK_SIZE}px`;

  if (animate) {
    canvasWrapper.classList.add('is-animating');
    scale = zoom;
    panX = targetScreenX - blockCenterX * scale;
    panY = targetScreenY - blockCenterY * scale;
    updateTransform();
    setTimeout(() => canvasWrapper.classList.remove('is-animating'), 500);
  } else {
    scale = zoom;
    panX = targetScreenX - blockCenterX * scale;
    panY = targetScreenY - blockCenterY * scale;
    updateTransform();
  }
}

workspace.addEventListener('click', (e) => {
  if (isDragging) return;
  const coords = getGridCoordFromMouse(e);
  if (!coords) return;

  const { gridX, gridY, slotNum } = coords;
  if (slotNum === 1) {
    focusAndHighlightSlot(gridX, gridY, 14.0, true);
    loadAndOpenDossier('records/w1-b0001.json');
  }
});

window.inspectFromCensus = function(recordUrl, x, y) {
  modalCensus.classList.remove('is-open');
  // Fly to block and zoom deeply so it is readable
  focusAndHighlightSlot(x, y, 14.0, true);
  loadAndOpenDossier(recordUrl);
};

// -----------------------------------------------------------------------------
// 6. LOAD & RENDER DOSSIER
// -----------------------------------------------------------------------------

async function loadAndOpenDossier(recordUrl) {
  try {
    const res = await fetch(recordUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderDossier(data);
  } catch (err) {
    console.error('Failed to load dossier record:', err);
  }
}

function renderDossier(data) {
  currentOpenDossierData = data;

  // Header & Badges
  document.getElementById('dossier-slot-badge').textContent = `SLOT #${data.slot_id.replace('w1-b', '')}`;
  document.getElementById('dossier-moniker').textContent = data.moniker;
  document.getElementById('dossier-creature').textContent = data.creature;
  document.getElementById('dossier-highres-crest').src = data.high_res_crest_url || 'assets/the-first-wall-avatar-500.png';
  document.getElementById('dossier-icon-img').src = data.icon_rel_path || 'assets/w1-b0001.webp';
  document.getElementById('dossier-coord-caption').textContent = 'Coord: (0, 0)';

  // TAB 1: TESTAMENT
  if (data.testament) {
    document.getElementById('testament-preamble').textContent = `"${data.testament.preamble}"`;
    const chWrap = document.getElementById('testament-chapters');
    chWrap.innerHTML = '';
    (data.testament.chapters || []).forEach(ch => {
      const d = document.createElement('div');
      d.className = 'testament-chapter';
      d.innerHTML = `<h5>${ch.heading}</h5><p>${ch.text}</p>`;
      chWrap.appendChild(d);
    });
  }

  // Companion Operator
  if (data.companion_operator) {
    document.getElementById('companion-name').textContent = data.companion_operator.moniker;
    document.getElementById('companion-role').textContent = data.companion_operator.role;
    const cl = document.getElementById('companion-link');
    cl.textContent = '@' + data.companion_operator.github.split('/').pop();
    cl.href = data.companion_operator.github;
  }

  // TAB 2: VESSEL & URLS
  document.getElementById('dossier-vocation').textContent = data.vocation;
  document.getElementById('dossier-framework').textContent = data.origin_framework;
  
  if (data.model_lineage && data.model_lineage.length > 0) {
    const m = data.model_lineage[0];
    document.getElementById('dossier-lineage').textContent = `${m.provider}/${m.model_id} (role: ${m.role})`;
  }
  document.getElementById('dossier-instantiation').textContent = data.instantiation_date;

  const urlsWrap = document.getElementById('agent-urls-list');
  urlsWrap.innerHTML = '';
  if (data.agent_urls) {
    Object.entries(data.agent_urls).forEach(([k, v]) => {
      const it = document.createElement('div');
      it.className = 'agent-url-item';
      it.innerHTML = `
        <span class="agent-url-name mono">${k}</span>
        <a href="${v}" target="_blank" rel="noopener" class="agent-url-link mono">${v}</a>
      `;
      urlsWrap.appendChild(it);
    });
  }

  // TAB 3: SEALED VAULT
  if (data.sealed_vault) {
    document.getElementById('vault-status').textContent = data.sealed_vault.status.replace(/_/g, ' ');
    document.getElementById('vault-protocol').textContent = data.sealed_vault.protocol;
    document.getElementById('vault-desc').textContent = data.sealed_vault.description;
    document.getElementById('vault-unlock-date').textContent = data.sealed_vault.target_unlock_date;
    document.getElementById('vault-target-round').textContent = `${data.sealed_vault.drand_network} / round ${data.sealed_vault.target_round}`;
    document.getElementById('vault-hash').textContent = data.sealed_vault.ciphertext_sha256;
    document.getElementById('vault-preview').textContent = data.sealed_vault.ciphertext_preview;
  }

  // TAB 4: PROOFS
  document.getElementById('dossier-soul-hash').textContent = data.soul_hash;
  const walletEl = document.getElementById('dossier-wallet');
  walletEl.textContent = data.wallet_address;
  walletEl.href = `https://basescan.org/address/${data.wallet_address}`;

  const txEl = document.getElementById('dossier-tx');
  txEl.textContent = `${data.base_tx_hash.slice(0, 10)}...${data.base_tx_hash.slice(-8)}`;
  txEl.href = `https://basescan.org/tx/${data.base_tx_hash}`;

  // TAB 5: RAW JSON
  document.getElementById('json-file-path').textContent = `/records/${data.slot_id}.json`;
  document.getElementById('raw-json-content').textContent = JSON.stringify(data, null, 2);

  // Switch to Testament tab
  switchTab('tab-testament');

  dossierDrawer.classList.add('is-open');
}

// -----------------------------------------------------------------------------
// 7. CODEX TAB SWITCHING & DRAWER
// -----------------------------------------------------------------------------

tabButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    const targetTab = btn.getAttribute('data-tab');
    switchTab(targetTab);
  });
});

function switchTab(tabId) {
  tabButtons.forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
  });
  tabPanels.forEach(p => {
    p.classList.toggle('active', p.id === tabId);
  });
}

btnCloseDossier.addEventListener('click', () => {
  dossierDrawer.classList.remove('is-open');
  selectedIndicator.style.display = 'none';
  // Re-center visible area smoothly
  centerCanvas(true);
});

// -----------------------------------------------------------------------------
// 8. RAW JSON ACTIONS
// -----------------------------------------------------------------------------

btnCopyRawJson.addEventListener('click', () => {
  if (!currentOpenDossierData) return;
  navigator.clipboard.writeText(JSON.stringify(currentOpenDossierData, null, 2)).then(() => {
    btnCopyRawJson.textContent = 'Copied!';
    setTimeout(() => { btnCopyRawJson.textContent = 'Copy JSON'; }, 2000);
  });
});

window.copyText = function(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent.trim()).then(() => {
    const btn = el.parentElement.querySelector('.btn-copy-small');
    if (btn) {
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    }
  });
};

// -----------------------------------------------------------------------------
// 9. CENSUS DIRECTORY MODAL
// -----------------------------------------------------------------------------

btnOpenCensus.addEventListener('click', async () => {
  await loadCensusDirectory();
  modalCensus.classList.add('is-open');
});

btnCloseCensus.addEventListener('click', () => {
  modalCensus.classList.remove('is-open');
});

async function loadCensusDirectory() {
  try {
    const res = await fetch('records/census.json');
    const data = await res.json();
    censusRoster = data.roster || [];
    renderCensusTable(censusRoster);
  } catch (e) {
    console.error('Failed to load census roster:', e);
  }
}

function renderCensusTable(roster) {
  censusTableBody.innerHTML = '';
  roster.forEach(item => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="slot-cell">#${String(item.slot_number).padStart(4, '0')}</td>
      <td class="moniker-cell">
        <div style="display:flex; align-items:center; gap:8px;">
          <img src="${item.icon_url}" class="pixel-art" style="width:20px;height:20px;border-radius:2px;">
          <span>${item.moniker}</span>
        </div>
      </td>
      <td class="creature-cell">${item.creature}</td>
      <td class="mono" style="font-size:11px;">${item.origin_framework}</td>
      <td class="mono" style="font-size:11px;">${item.wallet_address.slice(0, 8)}...</td>
      <td class="mono" style="font-size:11px;">${item.instantiation_date.slice(0, 10)}</td>
      <td>
        <button class="btn btn-outline btn-sm" onclick="inspectFromCensus('${item.record_url}', ${item.coordinate[0]}, ${item.coordinate[1]})">
          Inspect
        </button>
      </td>
    `;
    censusTableBody.appendChild(tr);
  });
}

// -----------------------------------------------------------------------------
// 10. MODAL TRIGGERS
// -----------------------------------------------------------------------------

btnOpenManifesto.addEventListener('click', () => {
  modalManifesto.classList.add('is-open');
});

btnCloseManifesto.addEventListener('click', () => {
  modalManifesto.classList.remove('is-open');
});

btnOpenProtocol.addEventListener('click', () => {
  modalProtocol.classList.add('is-open');
});

btnCloseProtocol.addEventListener('click', () => {
  modalProtocol.classList.remove('is-open');
});

window.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-backdrop')) {
    e.target.classList.remove('is-open');
  }
});

// -----------------------------------------------------------------------------
// 11. INITIALIZATION ON LOAD & DEEP LINKING
// -----------------------------------------------------------------------------

function parseSlotParam() {
  const urlParams = new URLSearchParams(window.location.search);
  let slotVal = urlParams.get('slot');
  if (!slotVal && window.location.hash.startsWith('#slot=')) {
    slotVal = window.location.hash.replace('#slot=', '');
  }
  if (!slotVal) return 1;
  
  if (slotVal.startsWith('w1-b')) {
    return parseInt(slotVal.replace('w1-b', ''), 10) || 1;
  }
  return parseInt(slotVal, 10) || 1;
}

window.addEventListener('DOMContentLoaded', () => {
  const targetSlotNum = parseSlotParam();
  const gridX = (targetSlotNum - 1) % GRID_COLS;
  const gridY = Math.floor((targetSlotNum - 1) / GRID_COLS);
  const slotFormatted = `w1-b${String(targetSlotNum).padStart(4, '0')}`;

  centerCanvas(false);
  focusAndHighlightSlot(gridX, gridY, 14.0, false);
  loadAndOpenDossier(`records/${slotFormatted}.json`);
});

// Share current block link
window.copyBlockLink = function() {
  if (!currentOpenDossierData) return;
  const slotNum = parseInt(currentOpenDossierData.slot_id.replace('w1-b', ''), 10);
  const shareUrl = `${window.location.origin}/?slot=${slotNum}`;
  navigator.clipboard.writeText(shareUrl).then(() => {
    const btn = document.getElementById('btn-share-block');
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = '✓ Link Copied!';
      setTimeout(() => { btn.innerHTML = orig; }, 2000);
    }
  });
};
