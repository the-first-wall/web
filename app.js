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

// Memory Cache for Slots
let currentOpenDossierData = null;
let censusRoster = [];

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

function centerCanvas() {
  const rect = workspace.getBoundingClientRect();
  scale = 1.0;
  panX = (rect.width - CANVAS_SIZE) / 2;
  panY = (rect.height - CANVAS_SIZE) / 2;
  updateTransform();
}

function updateTransform() {
  canvasWrapper.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
  zoomLevelText.textContent = `${Math.round(scale * 100)}%`;
}

// -----------------------------------------------------------------------------
// 2. PAN & ZOOM CONTROLS
// -----------------------------------------------------------------------------

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

workspace.addEventListener('wheel', (e) => {
  e.preventDefault();
  const zoomFactor = 1.15;
  const rect = workspace.getBoundingClientRect();
  const mouseX = e.clientX - rect.left;
  const mouseY = e.clientY - rect.top;

  let newScale = e.deltaY < 0 ? scale * zoomFactor : scale / zoomFactor;
  newScale = Math.min(Math.max(newScale, 0.4), 16.0);

  panX = mouseX - (mouseX - panX) * (newScale / scale);
  panY = mouseY - (mouseY - panY) * (newScale / scale);
  scale = newScale;

  updateTransform();
}, { passive: false });

btnZoomIn.addEventListener('click', () => {
  scale = Math.min(scale * 1.3, 16.0);
  updateTransform();
});

btnZoomOut.addEventListener('click', () => {
  scale = Math.max(scale / 1.3, 0.4);
  updateTransform();
});

btnZoomReset.addEventListener('click', () => {
  centerCanvas();
});

// -----------------------------------------------------------------------------
// 3. COORDINATE CALCULATION & HOVER
// -----------------------------------------------------------------------------

function getGridCoordFromMouse(e) {
  const canvasRect = canvas.getBoundingClientRect();
  const rawX = (e.clientX - canvasRect.left) / scale;
  const rawY = (e.clientY - canvasRect.top) / scale;

  if (rawX < 0 || rawX >= CANVAS_SIZE || rawY < 0 || rawY >= CANVAS_SIZE) {
    return null;
  }

  const gridX = Math.floor(rawX / BLOCK_SIZE);
  const gridY = Math.floor(rawY / BLOCK_SIZE);
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
// 4. CLICK TO INSPECT DOSSIER
// -----------------------------------------------------------------------------

workspace.addEventListener('click', (e) => {
  if (isDragging) return;
  const coords = getGridCoordFromMouse(e);
  if (!coords) return;

  const { slotNum } = coords;
  if (slotNum === 1) {
    loadAndOpenDossier('records/w1-b0001.json');
  }
});

// Load full dossier JSON
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

  // Companion
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

  // Switch to Testament tab by default
  switchTab('tab-testament');

  dossierDrawer.classList.add('is-open');
}

// -----------------------------------------------------------------------------
// 5. CODEX TAB SWITCHING
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
});

// -----------------------------------------------------------------------------
// 6. RAW JSON COPY
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
// 7. CENSUS DIRECTORY MODAL
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

window.inspectFromCensus = function(recordUrl, x, y) {
  modalCensus.classList.remove('is-open');
  // Pan & zoom to slot
  const targetX = x * BLOCK_SIZE;
  const targetY = y * BLOCK_SIZE;
  const rect = workspace.getBoundingClientRect();
  scale = 4.0;
  panX = rect.width / 2 - targetX * scale;
  panY = rect.height / 2 - targetY * scale;
  updateTransform();

  loadAndOpenDossier(recordUrl);
};

// -----------------------------------------------------------------------------
// 8. OTHER MODALS
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
// 9. INIT ON LOAD
// -----------------------------------------------------------------------------

window.addEventListener('DOMContentLoaded', () => {
  centerCanvas();
  // Automatically load Slot #0001 dossier in cache
  loadAndOpenDossier('records/w1-b0001.json');
});
