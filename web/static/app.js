/**
 * app.js — ATB web application
 * State management, WebSocket, REST API, DOM updates.
 * No dependencies. Plain ES2020 JavaScript.
 */

// ── relay assignment manager ──────────────────────────────────────────────────
// Enforces one-relay-per-node: each relay channel may be owned by at most one node.

class RelayAssignmentManager {
  constructor() {
    this._registry  = new Map(); // relay# → nodeDescription
    this._conflicts = [];        // [{relay, nodeA, nodeB}]
  }

  buildFromConfig(config) {
    this._registry.clear();
    this._conflicts = [];
    if (!config) return;
    for (const side of ['primary', 'secondary']) {
      (config[side] || []).forEach((w) => {
        this._reg(w.relay_a, `${w.id} start`);
        this._reg(w.relay_b, `${w.id} end`);
        (w.taps || []).forEach((t, ti) => {
          this._reg(t.relay_b, `${w.id}:tap${ti}`);
        });
      });
    }
  }

  _reg(relay, nodeDesc) {
    if (relay == null) return;
    if (this._registry.has(relay)) {
      this._conflicts.push({ relay, nodeA: this._registry.get(relay), nodeB: nodeDesc });
    } else {
      this._registry.set(relay, nodeDesc);
    }
  }

  isUsed(relay)     { return this._registry.has(relay); }
  getOwner(relay)   { return this._registry.get(relay) || null; }
  getUsedRelays()   { return new Set(this._registry.keys()); }
  getConflicts()    { return [...this._conflicts]; }
  hasConflicts()    { return this._conflicts.length > 0; }
}

const relayManager = new RelayAssignmentManager();

// ── state ────────────────────────────────────────────────────────────────────

const state = {
  wsConnected:           false,
  appState:              'IDLE',
  testMode:              'AUTO',
  selectedTransformerId: null,
  operator:              '',
  transformerList:       [],
  loadedConfig:          null,
  relayStates:           {},
  currentVoltage:        null,
  measurementNoSignal:   false,
  expectedVoltage:       null,
  tolerancePct:          5,
  activePrimary:         null,
  activeSecondary:       null,
  currentStepIndex:      -1,
  totalSteps:            0,
  progressPct:           0,
  stepResults:           [],
  session:               null,
  errorMessage:          null,
  // Batch tracking
  batchSession:          null,
  batchActive:           false,
  // Ratio / excitation
  excitationWindingId:        null,
  appliedVoltage:             null,
  nominalExcitationVoltage:   null,
  ratioFactor:                null,
  loadedWindings:             [],   // [{id, nominal_voltage, can_energize, side}]
};

// ── WebSocket ────────────────────────────────────────────────────────────────

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
let ws = null;
let reconnectTimer = null;
let reconnectDelay = 2000;

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    state.wsConnected = true;
    reconnectDelay = 2000;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    renderConnectionStatus();
  };

  ws.onclose = () => {
    state.wsConnected = false;
    ws = null;
    renderConnectionStatus();
    reconnectTimer = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
      connectWS();
    }, reconnectDelay);
  };

  ws.onerror = () => { /* onclose handles reconnect */ };

  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    dispatchWsEvent(msg);
  };
}

function sendWS(type, data = {}) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type, data }));
  }
}

function dispatchWsEvent(msg) {
  const { type, data } = msg;
  switch (type) {

    case 'snapshot':
      state.appState              = data.app_state;
      state.testMode              = data.test_mode;
      state.selectedTransformerId = data.selected_transformer;
      state.operator              = data.operator;
      state.relayStates           = objKeysToStr(data.relay_states || {});
      state.currentStepIndex      = data.current_step;
      state.totalSteps            = data.total_steps;
      state.progressPct           = data.progress_pct;
      if (data.transformers) {
        state.transformerList = data.transformers.map(t => ({ id: t.id, name: t.name }));
        renderTransformerSelect();
      }
      if (data.batch) {
        state.batchSession = data.batch;
        state.batchActive  = data.batch.active;
        if (state.batchActive) startBatchTimer();
      }
      renderAll();
      if (state.selectedTransformerId) loadConfig(state.selectedTransformerId);
      break;

    case 'app_state':
      state.appState = data.state;
      renderControlButtons();
      renderHeaderState();
      renderCanvas();
      break;

    case 'relay_state_changed':
      state.relayStates = objKeysToStr(data.relays || {});
      renderRelayGrid();
      renderCanvas();
      break;

    case 'relays_cleared':
      state.relayStates = {};
      renderRelayGrid();
      renderCanvas();
      break;

    case 'voltage_updated':
      state.currentVoltage      = data.voltage;
      state.measurementNoSignal = data.no_signal || false;
      renderMeasurement();
      renderCanvas();
      break;

    case 'live_voltages':
      renderVoltageBar(data);
      break;

    case 'relay_comm':
      appendRelayComm(data.entries || []);
      break;

    case 'active_measurement_changed':
      state.activePrimary   = data.from_winding;
      state.activeSecondary = data.to_winding;
      state.expectedVoltage = data.expected_voltage;
      state.tolerancePct    = data.tolerance_pct;
      renderMeasurement();
      renderActivePath();
      renderCanvas();
      break;

    case 'excitation_config':
      state.excitationWindingId      = data.excitation_winding_id;
      state.appliedVoltage           = data.applied_voltage;
      state.nominalExcitationVoltage = data.nominal_excitation_voltage;
      state.ratioFactor              = data.ratio_factor;
      renderRatioPanel();
      renderExcitationSection();
      break;

    case 'test_progress':
      state.currentStepIndex = data.step_index;
      state.totalSteps       = data.total;
      state.progressPct      = data.progress_pct;
      renderProgress();
      break;

    case 'step_result':
      state.stepResults.push(data);
      renderResults();
      break;

    case 'session_started':
      state.session     = data;
      state.stepResults = [];
      _resultExpanded.clear();
      renderSession();
      renderResults();
      break;

    case 'session_ended':
      if (state.session) state.session.overall_pass = data.overall_pass;
      renderSession();
      break;

    case 'animation_state':
      state.activePrimary   = data.active_primary;
      state.activeSecondary = data.active_secondary;
      renderCanvas();
      break;

    // ── Batch / unit events ──────────────────────────────────────────────────

    case 'batch_summary':
      state.batchSession = data;
      state.batchActive  = data.active;
      if (data.active) {
        startBatchTimer();
      } else {
        stopBatchTimer();
        hideUnitResult();
      }
      renderBatchBar();
      renderControlButtons();
      break;

    case 'unit_completed':
      // Update batch stats
      if (data.batch) {
        state.batchSession = data.batch;
        state.batchActive  = true;
        renderBatchBar();
      }
      showUnitResult(data);
      renderControlButtons();
      break;

    case 'unit_skipped':
      if (data.batch) {
        state.batchSession = data.batch;
        renderBatchBar();
      }
      renderControlButtons();
      break;

    case 'test_paused':
      // app_state event handles button state
      break;

    case 'test_resumed':
      hideUnitResult();
      break;

    case 'test_stopped':
      state.batchActive  = false;
      state.batchSession = null;
      stopBatchTimer();
      hideUnitResult();
      state.currentVoltage = null; state.measurementNoSignal = false;
      state.expectedVoltage = null;
      state.activePrimary   = null;
      state.activeSecondary = null;
      state.currentStepIndex = -1;
      state.totalSteps       = 0;
      state.progressPct      = 0;
      state.ratioFactor      = null;
      renderBatchBar();
      renderRatioPanel();
      renderAll();
      break;

    case 'estop_triggered':
      hideUnitResult();
      stopBatchTimer();
      renderBatchBar();
      renderAll();
      break;

    case 'error':
      state.errorMessage = data.message;
      break;

    case 'reset':
      state.activePrimary    = null;
      state.activeSecondary  = null;
      state.currentVoltage = null; state.measurementNoSignal = false;
      state.expectedVoltage  = null;
      state.progressPct      = 0;
      state.currentStepIndex = -1;
      state.relayStates      = {};
      renderAll();
      break;
  }
}

// ── REST API ─────────────────────────────────────────────────────────────────

async function apiGet(path) {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function apiPost(path, body = {}) {
  const res = await fetch(`/api${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

async function loadConfig(id) {
  try {
    const [cfg, windings] = await Promise.all([
      apiGet(`/transformers/${encodeURIComponent(id)}`),
      apiGet(`/transformers/${encodeURIComponent(id)}/windings`).catch(() => []),
    ]);
    state.loadedConfig   = cfg;
    state.loadedWindings = windings;
    tfCanvas.setConfig(cfg);
    el('canvas-placeholder').classList.add('hidden');
    renderExcitationSection();
    _populateEnergizeDropdown(cfg);
  } catch {
    tfCanvas.setConfig(null);
    state.loadedWindings = [];
    el('canvas-placeholder').classList.remove('hidden');
    renderExcitationSection();
    _populateEnergizeDropdown(null);
  }
}

async function loadTransformerList() {
  try {
    const list = await apiGet('/transformers');
    state.transformerList = list.map(t => ({ id: t.transformer_id, name: t.name }));
    renderTransformerSelect();
  } catch {}
}

// ── render functions ──────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }

function renderAll() {
  renderConnectionStatus();
  renderHeaderState();
  renderTransformerSelect();
  renderOperatorInput();
  renderControlButtons();
  renderExcitationSection();
  renderRatioPanel();
  renderProgress();
  renderRelayGrid();
  renderMeasurement();
  renderActivePath();
  renderSession();
  renderCanvas();
  renderBatchBar();
}

function renderConnectionStatus() {
  const dot   = el('conn-dot');
  const label = el('conn-label');
  if (state.wsConnected) {
    dot.className    = 'conn-dot online';
    label.textContent = 'Live';
  } else {
    dot.className    = 'conn-dot offline';
    label.textContent = 'Offline';
  }
}

function renderHeaderState() {
  const badge = el('hdr-state');
  badge.textContent = state.appState;
  badge.className   = 'state-badge ' + state.appState.toLowerCase();
}

function renderTransformerSelect() {
  const label = el('tf-select-label');
  const sel = state.transformerList.find(t => t.id === state.selectedTransformerId);
  if (sel) {
    label.textContent = sel.name;
    label.classList.remove('muted');
  } else {
    label.textContent = '— Select —';
    label.classList.add('muted');
  }
}

// ── searchable transformer picker (shared: dashboard select + editor Load) ─────
let _tfPickerCallback = null;

function openTransformerPicker(callback, title) {
  _tfPickerCallback = callback;
  el('tf-picker-title').textContent = title || 'Select Transformer';
  el('tf-picker-search').value = '';
  renderTfPickerList('');
  el('tf-picker-backdrop').classList.remove('hidden');
  el('tf-picker-modal').classList.remove('hidden');
  setTimeout(() => el('tf-picker-search').focus(), 30);
}

function closeTransformerPicker() {
  _tfPickerCallback = null;
  el('tf-picker-backdrop').classList.add('hidden');
  el('tf-picker-modal').classList.add('hidden');
}

function renderTfPickerList(query) {
  const list = el('tf-picker-list');
  const q = (query || '').trim().toLowerCase();
  list.innerHTML = '';
  const items = state.transformerList.filter(t =>
    !q || t.name.toLowerCase().includes(q) || t.id.toLowerCase().includes(q));
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'tf-picker-empty';
    empty.textContent = 'No matching transformers';
    list.appendChild(empty);
    return;
  }
  for (const t of items) {
    const item = document.createElement('div');
    item.className = 'tf-picker-item' + (t.id === state.selectedTransformerId ? ' active' : '');
    const name = document.createElement('span');
    name.className = 'tf-picker-name';
    name.textContent = t.name;
    const id = document.createElement('span');
    id.className = 'tf-picker-id';
    id.textContent = t.id;
    item.appendChild(name);
    item.appendChild(id);
    item.addEventListener('click', () => {
      const cb = _tfPickerCallback;
      closeTransformerPicker();
      cb?.(t.id);
    });
    list.appendChild(item);
  }
}

function bindTransformerPicker() {
  el('tf-picker-search').addEventListener('input', (e) => renderTfPickerList(e.target.value));
  el('tf-picker-close').addEventListener('click', closeTransformerPicker);
  el('tf-picker-backdrop').addEventListener('click', closeTransformerPicker);
  el('tf-picker-search').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeTransformerPicker(); return; }
    if (e.key === 'Enter') {
      const first = el('tf-picker-list').querySelector('.tf-picker-item');
      if (first) first.click();
    }
  });
}

async function selectTransformer(id) {
  state.selectedTransformerId = id || null;
  renderTransformerSelect();
  if (id) {
    sendWS('select_transformer', { transformer_id: id });
    await loadConfig(id);
  } else {
    tfCanvas.setConfig(null);
    el('canvas-placeholder').classList.remove('hidden');
  }
  renderCanvas();
  // New transformer → any expanded step details collapse.
  _resultExpanded.clear();
}

function renderOperatorInput() {
  const input = el('op-input');
  if (document.activeElement !== input) {
    input.value = state.operator || '';
  }
}

function renderExcitationSection() {
  const sec = el('excitation-section');
  const windings = state.loadedWindings || [];
  const hasRatioRules = (state.loadedConfig?.ratio_rules?.length > 0);

  // Show the section only when the loaded config has ratio rules
  if (!hasRatioRules || windings.length === 0) {
    sec.classList.add('hidden');
    return;
  }
  sec.classList.remove('hidden');

  // Rebuild excitation winding dropdown (only when config changed)
  const sel = el('excitation-winding');
  const curVal = sel.value;
  sel.innerHTML = '<option value="">— Select winding —</option>';
  for (const w of windings) {
    if (!w.can_energize) continue;
    const opt = document.createElement('option');
    opt.value = w.id;
    opt.textContent = `${w.id}  (${w.nominal_voltage}V ${w.side})`;
    if (w.id === state.excitationWindingId || w.id === curVal) opt.selected = true;
    sel.appendChild(opt);
  }

  // Ratio preview
  _updateRatioPreview();
}

function _updateRatioPreview() {
  const wid = el('excitation-winding').value;
  const av  = parseFloat(el('applied-voltage').value);
  const badge  = el('ratio-badge');
  const prev   = el('ratio-preview');
  const prevTx = el('ratio-preview-text');

  if (!wid || !av || av <= 0) {
    badge.classList.add('hidden');
    prev.classList.add('hidden');
    return;
  }

  const winding = (state.loadedWindings || []).find(w => w.id === wid);
  if (!winding) { badge.classList.add('hidden'); prev.classList.add('hidden'); return; }

  const ratio = av / winding.nominal_voltage;
  badge.textContent = `×${ratio.toFixed(4)}`;
  badge.classList.remove('hidden');

  // Build per-winding expected voltage table in preview
  const lines = (state.loadedWindings || [])
    .filter(w => w.id !== wid)
    .map(w => `${w.id} (${w.nominal_voltage}V) → ${(w.nominal_voltage * ratio).toFixed(3)}V`)
    .join('  ·  ');
  prevTx.textContent = lines || '—';
  prev.classList.remove('hidden');

  // Keep local state in sync
  state.excitationWindingId      = wid;
  state.appliedVoltage           = av;
  state.nominalExcitationVoltage = winding.nominal_voltage;
  state.ratioFactor              = ratio;

  // Live-push to server via WS
  sendWS('set_excitation', { excitation_winding_id: wid, applied_voltage: av });
}

function renderRatioPanel() {
  const panel = el('ratio-panel');
  if (!state.ratioFactor || !state.appliedVoltage) {
    panel.classList.add('hidden');
    return;
  }

  panel.classList.remove('hidden');

  const factorBadge = el('ratio-factor-badge');
  factorBadge.textContent = `×${state.ratioFactor.toFixed(4)}`;

  const excInfo = el('ratio-exc-info');
  excInfo.textContent =
    `${state.appliedVoltage}V applied / ${state.nominalExcitationVoltage}V nominal`
    + (state.excitationWindingId ? ` (${state.excitationWindingId})` : '');

  const tbody = el('ratio-table-body');
  tbody.innerHTML = '';
  const windings = state.loadedWindings || [];
  const excId    = state.excitationWindingId;

  for (const w of windings) {
    const isExc = w.id === excId;
    const expected = w.nominal_voltage * state.ratioFactor;
    const tr = document.createElement('tr');
    tr.className = isExc ? 'ratio-row-exc' : '';
    tr.innerHTML = `
      <td class="${isExc ? 'ratio-exc-cell' : ''}">${esc(w.id)}${isExc ? ' ⚡' : ''}</td>
      <td class="muted" style="text-align:right">${w.nominal_voltage}V</td>
      <td style="text-align:right;color:${isExc ? 'var(--accent)' : 'var(--glow)'}">
        ${isExc ? esc(String(state.appliedVoltage)) : expected.toFixed(3)}V
      </td>`;
    tbody.appendChild(tr);
  }
}

function renderControlButtons() {
  const s          = state.appState;
  const isTesting  = s === 'TESTING';
  const isPaused   = s === 'PAUSED';
  const isUnitDone = s === 'PASS' || s === 'FAIL';
  const isIdle     = ['IDLE', 'READY', 'ERROR'].includes(s);
  const isManual   = state.testMode === 'MANUAL';

  // Standard run controls
  setVisible('btn-start',  isIdle);
  setVisible('btn-stop',   isTesting || isPaused);
  setVisible('btn-pause',  isTesting);
  setVisible('btn-resume', isPaused);
  setVisible('btn-next',   isPaused && isManual);

  // Unit / batch controls
  setVisible('unit-ctrl-buttons', isUnitDone);
  setVisible('btn-retry-unit',    isUnitDone && s === 'FAIL');

  // Lock inputs during active test or unit-result screen
  const locked = isTesting || isPaused;
  el('tf-select').disabled           = locked || isUnitDone;
  el('op-input').disabled            = locked;
  el('mode-auto').disabled           = locked || isUnitDone;
  el('mode-manual').disabled         = locked || isUnitDone;
  el('excitation-winding').disabled  = locked || isUnitDone;
  el('applied-voltage').disabled     = locked || isUnitDone;

  el('mode-auto').classList.toggle('active',   state.testMode === 'AUTO');
  el('mode-manual').classList.toggle('active', state.testMode === 'MANUAL');

  // Status badge in panel header
  const badge = el('ctrl-status-badge');
  if (isTesting) {
    badge.textContent = '● RUNNING';
    badge.style.color      = 'var(--glow)';
    badge.style.background = 'rgba(0,255,136,0.1)';
    badge.style.border     = '1px solid rgba(0,255,136,0.3)';
    badge.classList.remove('hidden');
  } else if (isPaused) {
    badge.textContent = '⏸ PAUSED';
    badge.style.color      = 'var(--warning)';
    badge.style.background = 'rgba(245,158,11,0.1)';
    badge.style.border     = '1px solid rgba(245,158,11,0.3)';
    badge.classList.remove('hidden');
  } else if (s === 'PASS') {
    badge.textContent = '✓ PASS';
    badge.style.color      = 'var(--glow)';
    badge.style.background = 'rgba(0,255,136,0.1)';
    badge.style.border     = '1px solid rgba(0,255,136,0.3)';
    badge.classList.remove('hidden');
  } else if (s === 'FAIL') {
    badge.textContent = '✗ FAIL';
    badge.style.color      = 'var(--danger)';
    badge.style.background = 'rgba(239,68,68,0.1)';
    badge.style.border     = '1px solid rgba(239,68,68,0.3)';
    badge.classList.remove('hidden');
  } else {
    badge.classList.add('hidden');
  }

  if (!isIdle && !isUnitDone) setVisible('tf-required-hint', false);
  setVisible('progress-section', state.totalSteps > 0);
}

function renderProgress() {
  const step  = Math.max(0, state.currentStepIndex + 1);
  const total = state.totalSteps;
  const pct   = state.progressPct;
  el('step-label').textContent = `${step} / ${total}`;
  el('pct-label').textContent  = `${pct.toFixed(0)}%`;
  el('progress-fill').style.width = `${pct}%`;
  setVisible('progress-section', total > 0);
}

function renderRelayGrid() {
  const { relayStates } = state;
  const isOn = id => Boolean(relayStates[String(id)]);
  const active = [];

  const paint = (rowId, from, to, cls, clickable) => {
    const row = el(rowId);
    if (!row) return;
    if (row.children.length === 0) buildRelayRow(row, from, to, clickable);
    for (let id = from; id <= to; id++) {
      const btn = row.querySelector(`[data-rl="${id}"]`);
      if (!btn) continue;
      const on = isOn(id);
      if (on) active.push(id);
      const classes = ['relay-btn'];
      if (on) classes.push(cls);
      if (clickable) classes.push('clickable');
      if (_ff.active && _ff.order[_ff.index] === id) classes.push('ff-testing');
      if (_ff.results[id] === 'faulty') classes.push('ff-faulty');
      btn.className = classes.join(' ');
    }
  };

  paint('relays-a',    1, 16, 'on-a',    true);   // A1 — start nodes (toggleable)
  paint('relays-b',   17, 32, 'on-b',    true);   // A2 — end / tap nodes (toggleable)
  paint('relays-b2',  37, 40, 'on-b2',   true);   // B  — energizing winding taps (toggleable)
  paint('relays-gate', 33, 36, 'on-gate', false); // gates (firmware-controlled)

  el('relay-count').textContent = `${active.length} active`;
  el('relay-count').style.color = active.length > 0 ? 'var(--glow)' : 'var(--muted)';

  // Mirror active relays into the voltage bar.
  const vb = el('vbar-relays');
  const dot = el('vbar-relays-dot');
  if (vb) {
    if (active.length) {
      vb.textContent = active.map(id => `R${id}`).join(' ');
      vb.classList.remove('idle');
    } else {
      vb.textContent = 'none';
      vb.classList.add('idle');
    }
  }
  if (dot) dot.className = 'vbar-dot' + (active.length ? ' relays-on' : ' off');
}

function buildRelayRow(container, from, to, clickable) {
  container.innerHTML = '';
  for (let id = from; id <= to; id++) {
    const btn = document.createElement('div');
    btn.className   = 'relay-btn' + (clickable ? ' clickable' : '');
    btn.dataset.rl  = id;
    btn.textContent = id;
    if (clickable) {
      btn.title = `Toggle relay ${id}`;
      btn.addEventListener('click', () => onRelayClick(id));
    }
    container.appendChild(btn);
  }
}

// ── manual relay control (dev panel) ──────────────────────────────────────────
function onRelayClick(id) {
  if (_ff.active) return;   // fault finder drives the relays itself
  toggleRelayManual(id);
}

function applyRelayStates(relays) {
  if (relays) state.relayStates = relays;
  renderRelayGrid();
}

function _relayError(e) {
  const msg = String(e).includes(': 409')
    ? 'Blocked — stop the active test/sequence first'
    : 'Relay command failed';
  const rc = el('relay-count');
  const prev = rc.textContent, prevColor = rc.style.color;
  rc.textContent = msg;
  rc.style.color = 'var(--danger)';
  setTimeout(() => { rc.textContent = prev; rc.style.color = prevColor; }, 2500);
}

async function toggleRelayManual(id) {
  const on = Boolean(state.relayStates[String(id)]);
  try {
    const r = await apiPost('/relays/set', { relay_id: id, state: !on });
    applyRelayStates(r.relays);
  } catch (e) { _relayError(e); }
}

async function clearAllRelaysManual() {
  try {
    const r = await apiPost('/relays/clear', {});
    applyRelayStates(r.relays);
  } catch (e) { _relayError(e); }
}

// ── fault finder ──────────────────────────────────────────────────────────────
// Step through every selectable relay (RL1–32) one at a time; the operator marks
// each OK or Faulty (a stuck/dead relay can't be sensed in firmware, so this is
// operator-confirmed). Produces a list of faulty relays at the end.
const _ff = { active: false, order: [], index: 0, results: {} };

function _ffGroup(id) {
  if (id <= 16) return 'A1 · start';
  if (id <= 32) return 'A2 · end/tap';
  return 'B · energizing tap';
}

async function faultFinderStart() {
  if (_relaySeqRunning) { alert('Stop the Diagnostic sequence before running the Fault Finder.'); return; }
  _ff.active = true;
  _ff.index = 0;
  _ff.results = {};
  _ff.order = [];
  // Every selectable relay: A1 (1–16), A2 (17–32) and Group B (37–40).
  for (let id = 1; id <= 32; id++) _ff.order.push(id);
  for (let id = 37; id <= 40; id++) _ff.order.push(id);
  setVisible('fault-finder-panel', true);
  setVisible('ff-summary', false);
  el('btn-fault-finder').disabled = true;
  await _ffEnergizeCurrent();
}

async function _ffEnergizeCurrent() {
  const id = _ff.order[_ff.index];
  el('ff-progress').textContent = `${_ff.index + 1} / ${_ff.order.length}`;
  el('ff-current-relay').textContent = `${id}`;
  el('ff-current-group').textContent = _ffGroup(id);
  _ffRenderResults();
  try {
    const r = await apiPost('/relays/set', { relay_id: id, state: true, exclusive: true });
    applyRelayStates(r.relays);
  } catch (e) { _relayError(e); }
}

function _ffMark(verdict) {
  if (!_ff.active) return;
  _ff.results[_ff.order[_ff.index]] = verdict;
  _ff.index++;
  if (_ff.index >= _ff.order.length) return _ffFinish();
  _ffEnergizeCurrent();
}

function _ffRenderResults() {
  const box = el('ff-results');
  box.innerHTML = '';
  for (const id of _ff.order) {
    const v = _ff.results[id];
    if (!v) continue;
    const chip = document.createElement('span');
    chip.className = 'ff-chip ' + v;
    chip.textContent = `${id}`;
    box.appendChild(chip);
  }
}

async function _ffFinish() {
  const faulty = _ff.order.filter(id => _ff.results[id] === 'faulty');
  const skipped = _ff.order.filter(id => _ff.results[id] === 'skip');
  const summary = el('ff-summary');
  summary.classList.remove('hidden', 'has-faults', 'all-ok');
  if (faulty.length) {
    summary.classList.add('has-faults');
    summary.textContent = `⚠ ${faulty.length} faulty: ` + faulty.map(id => `${id}`).join(', ')
      + (skipped.length ? `  ·  ${skipped.length} skipped` : '');
  } else {
    summary.classList.add('all-ok');
    summary.textContent = `✓ All ${_ff.order.length - skipped.length} tested relays OK`
      + (skipped.length ? `  ·  ${skipped.length} skipped` : '');
  }
  _ffRenderResults();
  await _ffStopHardware();
  _ff.active = false;
  el('btn-fault-finder').disabled = false;
  renderRelayGrid();
}

async function _ffStopHardware() {
  try {
    const r = await apiPost('/relays/clear', {});
    applyRelayStates(r.relays);
  } catch (e) { /* ignore */ }
}

async function faultFinderStop() {
  if (!_ff.active) return;
  await _ffFinish();
}

function bindRelayManual() {
  el('btn-relay-clear').addEventListener('click', clearAllRelaysManual);
  el('btn-fault-finder').addEventListener('click', faultFinderStart);
  el('ff-ok').addEventListener('click',     () => _ffMark('ok'));
  el('ff-faulty').addEventListener('click', () => _ffMark('faulty'));
  el('ff-skip').addEventListener('click',   () => _ffMark('skip'));
  el('ff-stop').addEventListener('click',   faultFinderStop);
}

// Append relay MCU ⇄ PC serial traffic to the console panel.
function appendRelayComm(entries) {
  const box = el('relay-comm-log');
  if (!box || !entries.length) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  const pad = n => String(n).padStart(2, '0');
  for (const e of entries) {
    const line = document.createElement('span');
    let cls = 'rc-line ';
    if (e.dir === 'TX')      cls += 'rc-tx';
    else if (e.dir === 'RX') cls += 'rc-rx' + (/error/i.test(e.text) ? ' rc-err' : '');
    else                     cls += 'rc-info';
    line.className = cls;
    const t  = new Date((e.ts || Date.now() / 1000) * 1000);
    const ts = `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    const arrow = e.dir === 'TX' ? '→' : e.dir === 'RX' ? '←' : '·';
    const tspan = document.createElement('span');
    tspan.className = 'rc-time';
    tspan.textContent = ts;
    line.appendChild(tspan);
    line.appendChild(document.createTextNode(`${arrow} ${e.text}`));
    box.appendChild(line);
  }
  while (box.children.length > 300) box.removeChild(box.firstChild);
  if (atBottom) box.scrollTop = box.scrollHeight;
  const st = el('relay-comm-status');
  if (st) { st.textContent = 'live'; st.style.color = 'var(--glow)'; }
}

function renderVoltageBar(data) {
  const { v1, v2, v1_fresh, v2_fresh, v1_connected, v2_connected,
          v1_overload, v2_overload } = data;

  const v1El   = document.getElementById('vbar-v1');
  const v2El   = document.getElementById('vbar-v2');
  const v1Dot  = document.getElementById('vbar-v1-dot');
  const v2Dot  = document.getElementById('vbar-v2-dot');

  // V1
  if (v1 !== null && v1 !== undefined) {
    v1El.textContent = v1.toFixed(3);
    v1El.className   = 'vbar-value ' + (v1_fresh ? 'fresh' : 'stale');
    v1Dot.className  = 'vbar-dot '   + (v1_fresh ? 'fresh' : 'stale');
  } else if (v1_overload) {
    v1El.textContent = 'OL';
    v1El.className   = 'vbar-value stale';
    v1Dot.className  = 'vbar-dot stale';
  } else {
    v1El.textContent = v1_connected ? '…' : '—';
    v1El.className   = 'vbar-value';
    v1Dot.className  = 'vbar-dot off';
  }

  // V2
  if (v2 !== null && v2 !== undefined) {
    v2El.textContent = v2.toFixed(3);
    v2El.className   = 'vbar-value ' + (v2_fresh ? 'fresh' : 'stale');
    v2Dot.className  = 'vbar-dot '   + (v2_fresh ? 'fresh' : 'stale');
  } else if (v2_overload) {
    v2El.textContent = 'OL';
    v2El.className   = 'vbar-value stale';
    v2Dot.className  = 'vbar-dot stale';
  } else {
    v2El.textContent = v2_connected ? '…' : '—';
    v2El.className   = 'vbar-value';
    v2Dot.className  = 'vbar-dot off';
  }
}

function renderMeasurement() {
  const { currentVoltage, measurementNoSignal, expectedVoltage, tolerancePct } = state;

  const measEl = el('meas-measured');
  const expEl  = el('meas-expected');
  const devEl  = el('meas-deviation');
  const tolEl  = el('meas-tolerance');

  if (measurementNoSignal) {
    measEl.textContent = 'No Signal';
    measEl.className   = 'meas-value muted';
    devEl.textContent  = '—';
    devEl.className    = 'meas-value';
  } else {
    measEl.textContent = currentVoltage != null ? currentVoltage.toFixed(3) : '—';
    if (currentVoltage != null && expectedVoltage != null && expectedVoltage !== 0) {
      const dev = Math.abs(currentVoltage - expectedVoltage) / expectedVoltage * 100;
      devEl.textContent = dev.toFixed(2);
      const ok = dev <= tolerancePct;
      devEl.className  = 'meas-value ' + (ok ? 'success' : 'danger');
      measEl.className = 'meas-value ' + (ok ? 'glow' : 'danger');
    } else {
      devEl.textContent = '—';
      devEl.className   = 'meas-value';
      measEl.className  = 'meas-value';
    }
  }
  expEl.textContent = expectedVoltage != null ? expectedVoltage.toFixed(3) : '—';
  tolEl.textContent = `±${tolerancePct.toFixed(1)}`;
}

function renderActivePath() {
  const { activePrimary, activeSecondary } = state;
  const pathEl = el('active-path');
  if (activePrimary || activeSecondary) {
    pathEl.classList.remove('hidden');
    el('path-from').textContent = activePrimary  || '—';
    el('path-to').textContent   = activeSecondary || '—';
  } else {
    pathEl.classList.add('hidden');
  }
}

function renderSession() {
  const sess = state.session;
  const sesEl = el('session-info');
  if (!sess) { sesEl.classList.add('hidden'); return; }
  sesEl.classList.remove('hidden');
  el('ses-op').textContent     = sess.operator      || '—';
  el('ses-tf').textContent     = sess.transformer_id || '—';
  el('ses-passed').textContent =
    `${sess.passed_steps ?? '?'} / ${sess.total_steps ?? '?'}`;
  if (sess.overall_pass != null) {
    el('ses-passed').className = 'val ' + (sess.overall_pass ? 'glow' : 'danger');
  }
}

// Step indices (step_index) whose detail the operator has tapped open.
const _resultExpanded = new Set();

function renderResults() {
  const list = el('results-list');
  if (!list) return;
  list.innerHTML = '';
  const results = [...state.stepResults].reverse();   // newest first
  if (results.length === 0) {
    list.innerHTML = '<div class="results-empty">Steps appear here as the test runs. Tap a step to see its measurement.</div>';
    el('results-summary').textContent = '';
    return;
  }

  for (const r of results) {
    const passed = r.passed;
    const open   = _resultExpanded.has(r.step_index);

    // The exactly-two measured leads: colour block + its lead number.
    const swatches = _stepLeads(r).map(L => {
      const bg = L.hex ? wireSwatchBg(L.hex) : 'transparent';
      const nm = L.hex ? (wireColorName(L.hex) || '') : 'no colour';
      return `<span class="res-lead" title="${esc(nm)}">` +
               `<span class="swatch res-swatch" style="background:${bg}"></span>` +
               `<span class="res-leadnum">${esc(L.num)}</span>` +
             `</span>`;
    }).join('');

    // Phase badge for the detail row.
    let phase = '<span class="phase-badge na">—</span>';
    if (r.phase_ok === true)  phase = '<span class="phase-badge in">IN-PHASE</span>';
    if (r.phase_ok === false) phase = '<span class="phase-badge out">OUT-OF-PHASE</span>';

    const noSignal = r.error && r.phase_ok !== false;
    const measTxt  = noSignal ? 'No Signal' : `${r.measured_voltage.toFixed(3)} V`;
    const dev = (r.expected_voltage && !noSignal)
      ? (Math.abs(r.measured_voltage - r.expected_voltage) / r.expected_voltage * 100).toFixed(2) + ' %'
      : '—';

    const item = document.createElement('div');
    item.className = 'res-item ' + (passed ? 'pass' : 'fail') + (open ? ' open' : '');
    item.innerHTML =
      `<button class="res-row" type="button">` +
        `<span class="res-num">${r.step_index + 1}</span>` +
        `<span class="res-path">${esc(_stepLabel(r))}</span>` +
        `<span class="res-colors">${swatches}</span>` +
        `<span class="pass-badge ${passed ? 'pass' : 'fail'}">${passed ? 'PASS' : 'FAIL'}</span>` +
        `<span class="res-caret">${open ? '▾' : '▸'}</span>` +
      `</button>` +
      `<div class="res-detail">` +
        `<div class="res-metric"><span>Measured</span><b class="${passed ? 'glow' : 'danger'}">${measTxt}</b></div>` +
        `<div class="res-metric"><span>Expected</span><b class="accent">${r.expected_voltage.toFixed(3)} V</b></div>` +
        `<div class="res-metric"><span>Deviation</span><b>${dev}</b></div>` +
        `<div class="res-metric"><span>Tolerance</span><b class="muted">±${Number(r.tolerance_pct ?? 5).toFixed(1)} %</b></div>` +
        `<div class="res-metric"><span>Phase</span>${phase}</div>` +
        (r.error ? `<div class="res-error">${esc(r.error)}</div>` : '') +
      `</div>`;

    item.querySelector('.res-row').addEventListener('click', () => {
      if (_resultExpanded.has(r.step_index)) _resultExpanded.delete(r.step_index);
      else _resultExpanded.add(r.step_index);
      renderResults();
    });
    list.appendChild(item);
  }

  const passCount = state.stepResults.filter(r => r.passed).length;
  el('results-summary').textContent = `${passCount}/${state.stepResults.length} PASS`;
}

function renderCanvas() {
  if (tfCanvas) {
    tfCanvas.setLiveState({
      appState:        state.appState,
      activePrimary:   state.activePrimary,
      activeSecondary: state.activeSecondary,
      relayStates:     state.relayStates,
      currentVoltage:  state.currentVoltage,
      expectedVoltage: state.expectedVoltage,
    });
  }
  if (topoEditor) {
    topoEditor.setLiveState(state.relayStates, state.appState);
  }
}

// ── Batch bar ─────────────────────────────────────────────────────────────────

let _batchTimerInterval = null;

function startBatchTimer() {
  stopBatchTimer();
  _batchTimerInterval = setInterval(_tickBatchTimer, 1000);
}

function stopBatchTimer() {
  if (_batchTimerInterval) { clearInterval(_batchTimerInterval); _batchTimerInterval = null; }
}

function _tickBatchTimer() {
  if (!state.batchActive || !state.batchSession) { stopBatchTimer(); return; }
  const elapsed = Math.floor(Date.now() / 1000 - state.batchSession.start_time);
  const m = Math.floor(elapsed / 60);
  const s = elapsed % 60;
  const elEl = el('bb-elapsed');
  if (elEl) elEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
}

function renderBatchBar() {
  const batch = state.batchSession;
  if (!batch || !state.batchActive) {
    setVisible('batch-bar', false);
    return;
  }
  setVisible('batch-bar', true);
  el('bb-id').textContent   = (batch.batch_id || '').substring(0, 8).toUpperCase();
  el('bb-unit').textContent = `#${(batch.unit_count || 0) + 1}`;
  el('bb-pass').textContent = batch.pass_count || 0;
  el('bb-fail').textContent = batch.fail_count || 0;
  el('bb-skip').textContent = batch.skip_count || 0;
}

// ── Unit result overlay ───────────────────────────────────────────────────────

function showUnitResult(data) {
  const pass    = data.overall_pass;
  const verdict = pass ? 'PASS' : 'FAIL';

  el('uro-verdict').textContent = verdict;
  el('uro-verdict').className   = `uro-verdict ${pass ? 'pass' : 'fail'}`;

  const dur = (data.duration || 0).toFixed(1);
  el('uro-subtitle').textContent =
    `Unit #${data.unit_number}  ·  ${data.passed_steps}/${data.total_steps} steps  ·  ${dur}s`;

  const failures = data.failures || [];
  const failList = el('uro-fail-list');
  if (failures.length > 0) {
    failList.innerHTML = failures
      .map(f => `✗ ${esc(f.from)} → ${esc(f.to)}: measured ${f.measured.toFixed(3)}V / expected ${f.expected.toFixed(3)}V`)
      .join('\n');
    failList.style.display = 'block';
  } else {
    failList.innerHTML    = '';
    failList.style.display = 'none';
  }

  // Show retry only on FAIL
  setVisible('uro-btn-retry', !pass);

  el('unit-result-overlay').classList.remove('hidden');
}

function hideUnitResult() {
  el('unit-result-overlay').classList.add('hidden');
}

// ── canvas setup ──────────────────────────────────────────────────────────────

let tfCanvas = null;

function initCanvas() {
  const canvasEl = el('tf-canvas');
  tfCanvas = new TransformerCanvas(canvasEl);

  const wrap = el('canvas-wrap');
  const ro = new ResizeObserver(() => {
    const rect = wrap.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) tfCanvas.resize(rect.width, rect.height);
  });
  ro.observe(wrap);

  const rect = wrap.getBoundingClientRect();
  if (rect.width > 0) tfCanvas.resize(rect.width, rect.height);
}

// ── control panel events ──────────────────────────────────────────────────────

function bindControlPanel() {
  el('tf-select').addEventListener('click', () => {
    if (el('tf-select').disabled) return;
    openTransformerPicker(selectTransformer, 'Select Transformer');
  });

  el('op-input').addEventListener('input', (e) => {
    state.operator = e.target.value;
    sendWS('set_operator', { operator: state.operator });
  });

  el('mode-auto').addEventListener('click', () => {
    state.testMode = 'AUTO';
    renderControlButtons();
  });
  el('mode-manual').addEventListener('click', () => {
    state.testMode = 'MANUAL';
    renderControlButtons();
  });

  el('excitation-winding').addEventListener('change', _updateRatioPreview);
  el('applied-voltage').addEventListener('input',  _updateRatioPreview);

  // Collapse / expand the excitation setup body
  el('excitation-toggle').addEventListener('change', (e) => {
    setVisible('excitation-body', e.target.checked);
  });

  el('btn-start').addEventListener('click', async () => {
    if (!state.selectedTransformerId) {
      setVisible('tf-required-hint', true);
      el('tf-select').classList.add('input-shake');
      setTimeout(() => {
        setVisible('tf-required-hint', false);
        el('tf-select').classList.remove('input-shake');
      }, 3000);
      return;
    }
    setVisible('tf-required-hint', false);
    // Clear any lingering result overlay
    hideUnitResult();
    try {
      const body = {
        operator: state.operator || '',
        mode: state.testMode,
        excitation_winding_id: state.excitationWindingId || null,
        applied_voltage: state.appliedVoltage || null,
      };
      await apiPost('/test/start', body);
    } catch (e) { console.error(e); }
  });

  el('btn-stop').addEventListener('click', async () => {
    hideUnitResult();
    try { await apiPost('/test/stop'); } catch {}
  });

  el('btn-pause').addEventListener('click', () =>
    apiPost('/test/pause').catch(() => {}));

  el('btn-resume').addEventListener('click', () => {
    hideUnitResult();
    apiPost('/test/resume').catch(() => {});
  });

  el('btn-next').addEventListener('click', () =>
    apiPost('/test/next-step').catch(() => {}));

  el('btn-emergency').addEventListener('click', () =>
    apiPost('/test/emergency-stop').catch(() => {}));

  // ── Unit / batch controls ─────────────────────────────────────────────────

  el('btn-next-unit').addEventListener('click', async () => {
    hideUnitResult();
    state.stepResults = []; _resultExpanded.clear();
    renderResults();
    state.currentVoltage   = null;
    state.expectedVoltage  = null;
    state.activePrimary    = null;
    state.activeSecondary  = null;
    state.currentStepIndex = -1;
    state.totalSteps       = 0;
    state.progressPct      = 0;
    renderProgress();
    renderMeasurement();
    renderActivePath();
    try { await apiPost('/test/next-unit'); } catch (e) { console.error(e); }
  });

  el('btn-retry-unit').addEventListener('click', async () => {
    hideUnitResult();
    state.stepResults = []; _resultExpanded.clear();
    renderResults();
    state.currentVoltage   = null;
    state.expectedVoltage  = null;
    state.currentStepIndex = -1;
    state.totalSteps       = 0;
    state.progressPct      = 0;
    renderProgress();
    renderMeasurement();
    try { await apiPost('/test/retry-unit'); } catch (e) { console.error(e); }
  });

  el('btn-skip-unit').addEventListener('click', async () => {
    try { await apiPost('/test/skip-unit', { reason: 'Operator skipped' }); } catch (e) { console.error(e); }
  });

  el('btn-complete-batch').addEventListener('click', async () => {
    if (!confirm('End this batch session?')) return;
    hideUnitResult();
    try { await apiPost('/test/complete-batch'); } catch (e) { console.error(e); }
  });

  // ── Overlay buttons ───────────────────────────────────────────────────────

  el('uro-btn-next').addEventListener('click', async () => {
    hideUnitResult();
    state.stepResults = []; _resultExpanded.clear();
    renderResults();
    state.currentVoltage   = null;
    state.expectedVoltage  = null;
    state.activePrimary    = null;
    state.activeSecondary  = null;
    state.currentStepIndex = -1;
    state.totalSteps       = 0;
    state.progressPct      = 0;
    renderProgress();
    renderMeasurement();
    renderActivePath();
    try { await apiPost('/test/next-unit'); } catch (e) { console.error(e); }
  });

  el('uro-btn-retry').addEventListener('click', async () => {
    hideUnitResult();
    state.stepResults = []; _resultExpanded.clear();
    renderResults();
    state.currentVoltage   = null;
    state.expectedVoltage  = null;
    state.currentStepIndex = -1;
    state.totalSteps       = 0;
    state.progressPct      = 0;
    renderProgress();
    renderMeasurement();
    try { await apiPost('/test/retry-unit'); } catch (e) { console.error(e); }
  });

  el('uro-btn-skip').addEventListener('click', async () => {
    hideUnitResult();
    try { await apiPost('/test/skip-unit', { reason: 'Operator skipped' }); } catch (e) { console.error(e); }
  });

  el('uro-btn-complete').addEventListener('click', async () => {
    hideUnitResult();
    try { await apiPost('/test/complete-batch'); } catch (e) { console.error(e); }
  });
}

// ── tabs ──────────────────────────────────────────────────────────────────────

function bindTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const pane = el(`tab-${btn.dataset.tab}`);
      if (pane) pane.classList.add('active');

      if (btn.dataset.tab === 'dashboard') {
        setTimeout(() => {
          const wrap = el('canvas-wrap');
          const rect = wrap.getBoundingClientRect();
          if (rect.width > 0 && tfCanvas) tfCanvas.resize(rect.width, rect.height);
        }, 50);
      }
      if (btn.dataset.tab === 'logs') loadLogs();
      if (btn.dataset.tab === 'editor') setTimeout(() => {
        initEditor();
        // Always refresh the Generate Rules dropdown with whatever is currently loaded
        const cfg = editorConfig || state.loadedConfig;
        if (cfg) _populateEnergizeDropdown(cfg);
      }, 30);
    });
  });
}

// ── step-result leads (colour + lead number) ────────────────────────────────
// The exactly-two leads a step was tested against, on the MEASURED winding (the
// energizing winding is the reference and is not shown). Each lead is
// { hex, num } — its wire colour and its lead (relay) number.
//   • full winding  → start lead + end lead
//   • a tap         → start lead + the tap's lead
function _findWinding(cfg, id) {
  for (const side of ['primary', 'secondary'])
    for (const w of (cfg[side] || [])) if (w.id === id) return w;
  return null;
}

function _stepLeads(r) {
  const cfg = state.loadedConfig;
  if (!cfg) return [];
  const w = _findWinding(cfg, r.to_winding);
  if (!w) return [];
  const isEw = cfg.auto_matrix?.energize_winding === w.id;
  const startNum = isEw ? 'EN+' : (w.relay_a != null ? String(w.relay_a) : '—');
  const startHex = w.wire_color_start || w.wire_color || null;

  if (r.to_tap_index != null && w.taps && w.taps[r.to_tap_index]) {
    const t = w.taps[r.to_tap_index];
    return [
      { hex: startHex, num: startNum },
      { hex: t.wire_color || null, num: t.relay_b != null ? String(t.relay_b) : '—' },
    ];
  }
  const endNum = isEw ? 'EN−' : (w.relay_b != null ? String(w.relay_b) : '—');
  const endHex = w.wire_color_end || w.wire_color || null;
  return [
    { hex: startHex, num: startNum },
    { hex: endHex,   num: endNum },
  ];
}

// The measured node's label: "S3" for a full winding, "S3 (T1)" for a tap
// (using the tap's own label when set, else T<n>).
function _stepLabel(r) {
  if (r.to_tap_index == null) return r.to_winding;
  const w = state.loadedConfig && _findWinding(state.loadedConfig, r.to_winding);
  const t = w && w.taps && w.taps[r.to_tap_index];
  const lbl = (t && t.label) ? t.label : `T${r.to_tap_index + 1}`;
  return `${r.to_winding} (${lbl})`;
}

// ── logs tab ──────────────────────────────────────────────────────────────────

let currentLogFile = null;

async function loadLogs() {
  try {
    const data = await apiGet('/logs');
    const list = el('logs-list');
    list.innerHTML = '';
    if (!data.files.length) {
      list.innerHTML = '<div class="muted" style="padding:16px;font-family:var(--font-mono);font-size:12px">No logs yet.</div>';
      return;
    }
    for (const f of data.files) {
      const item = document.createElement('div');
      item.className = 'log-item' + (currentLogFile === f.name ? ' active' : '');
      item.innerHTML = `
        <div class="log-item-name">${esc(f.name)}</div>
        <div class="log-item-size">${(f.size / 1024).toFixed(1)} KB</div>
      `;
      item.addEventListener('click', () => openLog(f.name, item));
      list.appendChild(item);
    }
  } catch { /* no logs dir */ }
}

async function openLog(name, itemEl) {
  currentLogFile = name;
  document.querySelectorAll('.log-item').forEach(i => i.classList.remove('active'));
  if (itemEl) itemEl.classList.add('active');
  el('log-filename').textContent = name;
  try {
    const data = await apiGet(`/logs/${encodeURIComponent(name)}`);
    el('log-content').textContent = data.content;
  } catch {
    el('log-content').textContent = 'Error loading file.';
  }
}

el('logs-refresh').addEventListener('click', loadLogs);

// ── editor tab ────────────────────────────────────────────────────────────────

let topoEditor = null;
let editorConfig = null;
let _editorInited = false;

function makeEmptyConfig() {
  return {
    name: 'New Transformer', transformer_id: 'new_transformer', type: 'unknown',
    rated_power_va: 0, rated_frequency_hz: 50, notes: '',
    auto_matrix: { enabled: true, energize_winding: 'P1' },
    connection_style: { connection_type: 'core_link', line_style: 'solid', line_color: '#1a3d6e' },
    primary: [{ id: 'P1', winding_type: 'basic_winding', start_pin: 1, end_pin: 2, voltage: 230, dot_polarity: true, relay_a: null, relay_b: null, meas_channel: -1, taps: [], coords: {} }],
    secondary: [{ id: 'S1', winding_type: 'basic_winding', start_pin: 100, end_pin: 101, voltage: 115, dot_polarity: true, relay_a: null, relay_b: null, meas_channel: -1, taps: [], coords: {} }],
    tests: [],
    ratio_rules: [],
  };
}

// ── editor mode ──────────────────────────────────────────────────────────────

let _editorMode = 'topology';  // 'topology' | 'validate'

function setEditorMode(mode) {
  _editorMode = mode;
  topoEditor?.setMode(mode);
  el('tool-mode-topology').classList.toggle('active', mode === 'topology');
  el('tool-mode-validate').classList.toggle('active', mode === 'validate');

  if (mode === 'validate') {
    el('inspector-empty').classList.add('hidden');
    el('inspector-winding').classList.add('hidden');
    el('inspector-tap').classList.add('hidden');
    setVisible('inspector-rules-list', true);
    if (editorConfig && !editorConfig.ratio_rules) {
      editorConfig.ratio_rules = editorConfig.validation_rules || [];
    }
    _renderActiveExcitation();
    _updateRulesList();
  } else {
    setVisible('inspector-rules-list', false);
    setVisible('inspector-rule', false);
    setVisible('rule-pick-hint', false);
    el('inspector-empty').classList.remove('hidden');
  }
}

function _renderActiveExcitation() {
  const exc     = topoEditor?.getActiveExcitation();
  const display = el('active-exc-display');
  if (!display) return;
  if (exc && exc.node_a && exc.node_b) {
    display.innerHTML =
      `<div class="active-exc-nodes">` +
        `<span class="rule-node-badge exc-node-badge">${esc(exc.node_a)}</span>` +
        `<span class="node-sep">&#8596;</span>` +
        `<span class="rule-node-badge exc-node-badge">${esc(exc.node_b)}</span>` +
      `</div>` +
      `<div class="active-exc-voltage">${exc.nominal_voltage || 0}V nominal</div>`;
  } else {
    display.innerHTML = '<span class="active-exc-unset">Not set — click Set or a canvas node</span>';
  }
}

function initEditor() {
  if (_editorInited) {
    const wrap = el('editor-konva-wrap');
    if (wrap && topoEditor) {
      const r = wrap.getBoundingClientRect();
      if (r.width > 0) topoEditor.resize(r.width, r.height);
    }
    return;
  }
  _editorInited = true;

  if (!editorConfig) editorConfig = makeEmptyConfig();

  const wrap = el('editor-konva-wrap');
  const cont = el('editor-konva-container');
  const r = wrap.getBoundingClientRect();
  cont.style.width  = (r.width  || 600) + 'px';
  cont.style.height = (r.height || 500) + 'px';

  topoEditor = new TopoEditor('editor-konva-container');

  new ResizeObserver(() => {
    const rr = wrap.getBoundingClientRect();
    if (rr.width > 0) {
      cont.style.width  = rr.width  + 'px';
      cont.style.height = rr.height + 'px';
      topoEditor.resize(rr.width, rr.height);
    }
  }).observe(wrap);

  topoEditor.setConfig(editorConfig);
  _syncMetaToForm(editorConfig);

  topoEditor.onChange((cfg) => {
    editorConfig = cfg;
    if (!editorConfig.ratio_rules) editorConfig.ratio_rules = [];
    _updateToolbarState();
    if (_editorMode === 'validate') _updateRulesList();
  });

  topoEditor.onSelect((sel) => {
    if (_editorMode === 'validate') return; // don't clobber rule inspector
    _updateToolbarState(sel);
    _showInspector(sel);
  });

  topoEditor.onRuleSelect((rule) => {
    if (_editorMode !== 'validate') return;
    el('inspector-empty').classList.add('hidden');
    el('inspector-winding').classList.add('hidden');
    el('inspector-tap').classList.add('hidden');
    _showRuleInspector(rule);
    _updateRulesList();
    _renderActiveExcitation();
  });

  topoEditor.onPickPhase((info) => {
    if (!info) {
      setVisible('rule-pick-hint', false);
      _renderActiveExcitation();
      return;
    }
    const excLabel = info.excA && info.excB ? `${info.excA}↔${info.excB}` : (info.excA || '…');
    const hints = {
      exc_a:  '① EXCITATION — click node A (first terminal of the excitation segment)',
      exc_b:  `② EXCITATION — click node B to complete segment from ${info.excA || '…'}`,
      meas_a: `③ MEASUREMENT — click node A  [excitation: ${excLabel}]`,
      meas_b: `④ MEASUREMENT — click node B to complete from ${info.measA || '…'}`,
    };
    const hint = hints[info.phase];
    if (hint) { el('rule-pick-hint').textContent = `→ ${hint}`; setVisible('rule-pick-hint', true); }
  });

  topoEditor.onActiveExcitationChanged(() => {
    _renderActiveExcitation();
  });

  bindEditorEvents();
  bindInspectorEvents();
  bindRuleInspectorEvents();
}

function _syncMetaToForm(cfg) {
  el('ed-name').value  = cfg.name  || '';
  el('ed-id').value    = cfg.transformer_id || '';
  el('ed-power').value = cfg.rated_power_va || 0;
  el('ed-freq').value  = cfg.rated_frequency_hz || 50;
  el('ed-notes').value = cfg.notes || '';
  el('ed-auto-matrix').checked = cfg.auto_matrix?.enabled ?? false;
  el('ed-energize').value      = cfg.auto_matrix?.energize_winding || '';
  _populateEnergizeDropdown(cfg);
}

function _populateEnergizeDropdown(cfg) {
  const sel = el('ed-energize-sel');
  if (!sel) return;

  // Prefer state.loadedWindings (always in sync with the selected transformer)
  const windings = state.loadedWindings?.length ? state.loadedWindings : (() => {
    const src = cfg || state.loadedConfig;
    return [
      ...(src?.primary   || []).map(w => ({ id: w.id, nominal_voltage: w.voltage, can_energize: w.can_energize, side: 'primary' })),
      ...(src?.secondary || []).map(w => ({ id: w.id, nominal_voltage: w.voltage, can_energize: w.can_energize, side: 'secondary' })),
    ];
  })();

  const curVal = el('ed-energize').value
    || (cfg || state.loadedConfig)?.auto_matrix?.energize_winding
    || '';

  sel.innerHTML = '<option value="">— Select winding —</option>';
  for (const w of windings) {
    if (w.can_energize === false) continue;
    const opt = document.createElement('option');
    opt.value       = w.id;
    opt.textContent = `${w.id}  —  ${w.side === 'primary' ? 'Primary' : 'Secondary'}  ${w.nominal_voltage}V`;
    if (w.id === curVal) opt.selected = true;
    sel.appendChild(opt);
  }
  _populateEnergizeTapDropdown(cfg);
}

function _populateEnergizeTapDropdown(cfg) {
  const wid    = el('ed-energize-sel')?.value;
  const tapSel = el('ed-energize-tap');
  const tapFld = el('ed-energize-tap-field');
  if (!tapSel || !tapFld) return;
  tapSel.innerHTML = '<option value="">Full winding</option>';
  // Look up tap data from the full config (loadedConfig or editorConfig)
  const src  = (cfg?.primary?.length || cfg?.secondary?.length) ? cfg : (state.loadedConfig || cfg);
  const all  = [...(src?.primary || []), ...(src?.secondary || [])];
  const winding = all.find(w => w.id === wid);
  if (winding?.taps?.length) {
    tapFld.style.display = '';
    const etIdx = src?.auto_matrix?.energize_tap_index;
    winding.taps.forEach((tap, i) => {
      const opt = document.createElement('option');
      opt.value       = String(i);
      opt.textContent = `Tap ${i}: ${tap.label || ''} (${tap.voltage}V)`;
      if (etIdx != null && i === etIdx) opt.selected = true;
      tapSel.appendChild(opt);
    });
  } else {
    tapFld.style.display = 'none';
  }
}

function _updateToolbarState(sel) {
  sel = sel ?? null;
  const hasSel = sel != null;
  el('tool-add-tap').disabled = !(hasSel && sel.type === 'winding');
  el('tool-delete').disabled  = !hasSel;
}

function _showInspector(sel) {
  if (_editorMode === 'validate') return;  // rule inspector owns the right panel
  el('inspector-empty').classList.toggle('hidden', sel != null);
  el('inspector-winding').classList.add('hidden');
  el('inspector-tap').classList.add('hidden');

  if (!sel) return;

  // The energizing winding is never routed through the relay matrix, so its
  // (and its taps') relay assignment is disabled with an explanatory note.
  const ewId = topoEditor?.config?.auto_matrix?.energize_winding || null;
  const isEnergizing = ewId != null && sel.data && (
    sel.type === 'winding' ? sel.data.id === ewId : sel.wIndex != null &&
      topoEditor?.config?.[sel.side]?.[sel.wIndex]?.id === ewId
  );

  if (sel.type === 'winding') {
    el('inspector-winding').classList.remove('hidden');
    const badge = el('insp-badge');
    badge.textContent = sel.side === 'primary' ? 'PRIMARY' : 'SECONDARY';
    badge.className = 'insp-badge' + (sel.side === 'secondary' ? ' secondary' : '');
    const w = sel.data;
    el('insp-wid').value       = w.id;
    el('insp-voltage').value   = w.voltage;
    const ra = el('insp-relay-a'), rb = el('insp-relay-b');
    el('insp-energize-note').classList.toggle('hidden', !isEnergizing);
    ra.disabled = rb.disabled = isEnergizing;
    if (isEnergizing) {
      ra.textContent = rb.textContent = '— permanent —';
      ra.className = rb.className = 'relay-assign-btn';
    } else {
      ra.textContent = w.relay_a != null ? `${w.relay_a}` : 'None';
      ra.className   = 'relay-assign-btn' + (w.relay_a != null ? ' assigned-a' : '');
      rb.textContent = w.relay_b != null ? `${w.relay_b}` : 'None';
      rb.className   = 'relay-assign-btn' + (w.relay_b != null ? ' assigned-b' : '');
    }
    setColorBtn('insp-wire-color-start', w.wire_color_start || w.wire_color || null);
    setColorBtn('insp-wire-color-end',   w.wire_color_end   || w.wire_color || null);
  } else if (sel.type === 'tap') {
    el('inspector-tap').classList.remove('hidden');
    const t = sel.data;
    el('insp-tap-label').value   = t.label || '';
    el('insp-tap-voltage').value = t.voltage;
    const trb = el('insp-tap-relay-b');
    // Taps of the energizing winding ARE measured — but through Group B
    // (RL37–40), not the A2 group. Only its main wires are external.
    el('insp-tap-energize-note').classList.toggle('hidden', !isEnergizing);
    const grpLbl = el('insp-tap-relay-group');
    if (grpLbl) {
      grpLbl.textContent = isEnergizing ? 'Group B (RL37–40)' : 'A2 (RL17–32)';
      grpLbl.className   = isEnergizing ? 'col-b2' : 'col-b';
    }
    trb.disabled = false;
    trb.textContent = t.relay_b != null ? `${t.relay_b}` : 'None';
    trb.className   = 'relay-assign-btn' + (t.relay_b != null ? ' assigned-b' : '');
    setColorBtn('insp-tap-wire-color', t.wire_color || null);
  }
}

// ── rule inspector ────────────────────────────────────────────────────────────

function _showRuleInspector(rule) {
  setVisible('inspector-rule', rule != null);
  if (!rule) return;
  const exc  = rule.excitation_segment  || {};
  const meas = rule.measurement_segment || {};
  el('insp-rule-exc-a').textContent   = exc.node_a  || '—';
  el('insp-rule-exc-b').textContent   = exc.node_b  || '—';
  el('insp-rule-meas-a').textContent  = meas.node_a || '—';
  el('insp-rule-meas-b').textContent  = meas.node_b || '—';
  el('insp-rule-nom-in').value        = exc.nominal_voltage  ?? 0;
  el('insp-rule-nom-out').value       = meas.nominal_voltage ?? 0;
  el('insp-rule-tolerance').value     = rule.tolerance_percent      ?? 10;
  el('insp-rule-min-delta').value     = rule.minimum_absolute_delta ?? 0.1;
  el('insp-rule-enabled').checked     = rule.enabled !== false;
  _updateRuleRatioDisplay(exc.nominal_voltage, meas.nominal_voltage);
}

function _updateRuleRatioDisplay(nomIn, nomOut) {
  const ni = nomIn  || 0;
  const no = nomOut || 0;
  const disp = el('insp-rule-ratio-display');
  if (ni > 0 && no > 0) {
    const r = no / ni;
    disp.textContent = `${no}V / ${ni}V = ×${r.toFixed(5)}`;
    disp.style.color = 'var(--glow)';
  } else {
    disp.textContent = '— (set nominal voltages above)';
    disp.style.color = '';
  }
}

function _updateRulesList() {
  const rules = editorConfig?.ratio_rules || [];
  const selId = topoEditor?._ruleSel?.ruleId;
  const items = el('rules-list-items');
  const countEl = el('rules-count');
  if (!items) return;
  if (countEl) countEl.textContent = String(rules.length);
  items.innerHTML = '';
  if (rules.length === 0) {
    items.innerHTML = '<div class="rules-empty">No rules yet — select a winding and click <b>Generate Rules</b>, or add one manually.</div>';
    return;
  }
  for (const rule of rules) {
    const item = document.createElement('div');
    item.className = 'rule-list-item' + (rule.id === selId ? ' active' : '')
                   + (rule.enabled ? '' : ' disabled');
    const meas    = rule.measurement_segment || {};
    const nomOut  = meas.nominal_voltage || 0;
    const measLabel = meas.node_a && meas.node_b ? `${meas.node_a}↔${meas.node_b}` : (meas.node_a || '?');
    const voltStr  = nomOut > 0 ? `${nomOut}V` : '';
    item.innerHTML =
      `<span class="rule-item-path">${esc(measLabel)}</span>` +
      (voltStr ? `<span class="rule-item-voltage">${esc(voltStr)}</span>` : '') +
      (!rule.enabled ? `<span class="rule-item-dis">off</span>` : '');
    item.addEventListener('click', () => topoEditor?._selectRule(rule.id));
    items.appendChild(item);
  }
}

function bindRuleInspectorEvents() {
  el('tool-mode-topology').addEventListener('click', () => setEditorMode('topology'));
  el('tool-mode-validate').addEventListener('click', () => setEditorMode('validate'));

  el('btn-set-exc').addEventListener('click', () => {
    if (!topoEditor || _editorMode !== 'validate') return;
    el('rule-pick-hint').textContent = '→ ① EXCITATION — click node A (e.g. the 0V terminal)';
    setVisible('rule-pick-hint', true);
    topoEditor.startNodePick('new_exc', null);
  });

  el('insp-add-rule').addEventListener('click', () => {
    if (!topoEditor || _editorMode !== 'validate') return;
    const exc = topoEditor.getActiveExcitation();
    if (!exc) {
      el('rule-pick-hint').textContent = '→ ① EXCITATION — click node A (define excitation segment first)';
      topoEditor.startNodePick('new_exc', null);
    } else {
      el('rule-pick-hint').textContent = `→ ③ MEASUREMENT — click node A  [excitation: ${exc.node_a}↔${exc.node_b}]`;
      topoEditor.startNodePick('new_from', null);
    }
    setVisible('rule-pick-hint', true);
  });

  el('insp-rule-pick-exc').addEventListener('click', () => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    el('rule-pick-hint').textContent = '→ ① Click the new EXCITATION node A';
    setVisible('rule-pick-hint', true);
    topoEditor.startNodePick('excitation', rule.id);
  });

  el('insp-rule-pick-meas').addEventListener('click', () => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    el('rule-pick-hint').textContent = '→ ① Click the new MEASUREMENT node A';
    setVisible('rule-pick-hint', true);
    topoEditor.startNodePick('measurement', rule.id);
  });

  el('insp-rule-nom-in').addEventListener('input', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    const v = parseFloat(e.target.value) || 0;
    if (!rule.excitation_segment) rule.excitation_segment = { node_a: '', node_b: '', nominal_voltage: 0 };
    rule.excitation_segment.nominal_voltage = v;
    topoEditor.updateRule(rule.id, { excitation_segment: rule.excitation_segment });
    _updateRuleRatioDisplay(v, rule.measurement_segment?.nominal_voltage);
    _updateRulesList();
  });

  el('insp-rule-nom-out').addEventListener('input', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    const v = parseFloat(e.target.value) || 0;
    if (!rule.measurement_segment) rule.measurement_segment = { node_a: '', node_b: '', nominal_voltage: 0 };
    rule.measurement_segment.nominal_voltage = v;
    topoEditor.updateRule(rule.id, { measurement_segment: rule.measurement_segment });
    _updateRuleRatioDisplay(rule.excitation_segment?.nominal_voltage, v);
    _updateRulesList();
  });

  el('insp-rule-tolerance').addEventListener('input', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    topoEditor.updateRule(rule.id, { tolerance_percent: parseFloat(e.target.value) || 10 });
  });

  el('insp-rule-min-delta').addEventListener('input', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    topoEditor.updateRule(rule.id, { minimum_absolute_delta: parseFloat(e.target.value) || 0.1 });
  });

  el('insp-rule-enabled').addEventListener('change', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    topoEditor.updateRule(rule.id, { enabled: e.target.checked });
    _updateRulesList();
  });

  el('insp-rule-delete').addEventListener('click', () => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    const exc  = rule.excitation_segment?.node_a  || '?';
    const meas = rule.measurement_segment?.node_a || '?';
    if (!confirm(`Delete rule ${exc} → ${meas}?`)) return;
    topoEditor.deleteRule(rule.id);
    setVisible('inspector-rule', false);
    _updateRulesList();
  });
}

function bindInspectorEvents() {
  // start_pin / end_pin are NOT editable — they are the wire numbers, assigned
  // automatically alongside the relays by Generate Rules (pins 1 & 2 are always
  // the energizing winding's mains wires; every pin from 3 up maps 1:1 onto a relay).
  const windingFields = [
    { id: 'insp-wid',       field: 'id',        parse: v => v },
    { id: 'insp-voltage',   field: 'voltage',   parse: Number },
  ];
  windingFields.forEach(({ id, field, parse }) => {
    el(id).addEventListener('input', (e) => {
      if (topoEditor?._sel?.type === 'winding')
        topoEditor.updateSelected({ [field]: parse(e.target.value) });
    });
  });

  // tap.pin is the wire number — auto-assigned, not typed (see above).
  const tapFields = [
    { id: 'insp-tap-label',   field: 'label',   parse: v => v },
    { id: 'insp-tap-voltage', field: 'voltage', parse: Number },
  ];
  tapFields.forEach(({ id, field, parse }) => {
    el(id).addEventListener('input', (e) => {
      if (topoEditor?._sel?.type === 'tap')
        topoEditor.updateSelected({ [field]: parse(e.target.value) });
    });
  });

  el('insp-relay-a').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'winding') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].relay_a;
    showRelayPicker('A', cur, (val) => {
      // pin == relay: keep the stored wire number in sync with the assignment.
      topoEditor.updateSelected({ relay_a: val, start_pin: val });
      el('insp-relay-a').textContent = val != null ? `${val}` : 'None';
      el('insp-relay-a').className = 'relay-assign-btn' + (val != null ? ' assigned-a' : '');
    }, e.currentTarget);
  });
  el('insp-relay-b').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'winding') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].relay_b;
    showRelayPicker('B', cur, (val) => {
      topoEditor.updateSelected({ relay_b: val, end_pin: val });
      el('insp-relay-b').textContent = val != null ? `${val}` : 'None';
      el('insp-relay-b').className = 'relay-assign-btn' + (val != null ? ' assigned-b' : '');
    }, e.currentTarget);
  });

  el('insp-tap-relay-b').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'tap') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].taps[sel.tIndex].relay_b;
    // The energizing winding's taps are measured through Group B (RL37–40),
    // every other winding's taps through A2 (RL17–32).
    const ewId = topoEditor?.config?.auto_matrix?.energize_winding || null;
    const isEwTap = ewId != null &&
      topoEditor.config[sel.side][sel.wIndex].id === ewId;
    showRelayPicker(isEwTap ? 'B2' : 'B', cur, (val) => {
      topoEditor.updateSelected({ relay_b: val, pin: val });   // pin == relay
      el('insp-tap-relay-b').textContent = val != null ? `${val}` : 'None';
      el('insp-tap-relay-b').className = 'relay-assign-btn' + (val != null ? ' assigned-b' : '');
    }, e.currentTarget);
  });

  // Wire-colour buttons open the swatch picker; on pick, set the lead colour and
  // refresh the button. Colour kind (Y/G stripe, White/Clear) is rendered by the
  // shared palette helpers (canvas.js).
  const bindColorBtn = (id, field) => {
    el(id).addEventListener('click', (e) => {
      const cur = el(id).dataset.hex || null;
      showColorPicker(cur, (hex) => {
        topoEditor?.setLeadColor(field, hex);
        setColorBtn(id, hex);
      }, e.currentTarget);
    });
  };
  bindColorBtn('insp-wire-color-start', 'wire_color_start');
  bindColorBtn('insp-wire-color-end',   'wire_color_end');
  bindColorBtn('insp-tap-wire-color',   'wire_color');

  el('insp-add-tap').addEventListener('click', () => { topoEditor?.addTapToSelected(); });
  el('insp-delete-winding').addEventListener('click', () => { topoEditor?.deleteSelected(); });
  el('insp-delete-tap').addEventListener('click', () => { topoEditor?.deleteSelected(); });
}

function bindEditorEvents() {
  el('tool-add-primary').addEventListener('click', () => { topoEditor?.addWinding('primary'); });
  el('tool-add-secondary').addEventListener('click', () => { topoEditor?.addWinding('secondary'); });
  el('tool-add-tap').addEventListener('click', () => { topoEditor?.addTapToSelected(); });
  el('tool-delete').addEventListener('click', () => { topoEditor?.deleteSelected(); });

  el('ed-zoom-in').addEventListener('click',  () => { topoEditor?.zoom(1);  _updateZoomLabel(); });
  el('ed-zoom-out').addEventListener('click', () => { topoEditor?.zoom(-1); _updateZoomLabel(); });
  el('ed-zoom-fit').addEventListener('click', () => { topoEditor?.fitView(); _updateZoomLabel(); });


  ['ed-name', 'ed-id', 'ed-power', 'ed-freq', 'ed-notes'].forEach(id => {
    el(id).addEventListener('input', _syncMetaFromForm);
  });

  el('ed-energize-sel').addEventListener('change', () => {
    const src = (editorConfig?.primary?.length || editorConfig?.secondary?.length)
      ? editorConfig : state.loadedConfig;
    _populateEnergizeTapDropdown(src);
    _syncMetaFromForm();
  });
  el('ed-energize-tap').addEventListener('change', _syncMetaFromForm);

  el('ed-generate-rules-btn').addEventListener('click', async () => {
    // Use dashboard-selected transformer if editor hasn't been explicitly loaded
    const activeCfg = (editorConfig?.primary?.length || editorConfig?.secondary?.length)
      ? editorConfig : state.loadedConfig;
    const tid = activeCfg?.transformer_id || state.selectedTransformerId;
    const wid = el('ed-energize-sel').value;
    if (!tid) { alert('Select a transformer on the Dashboard first.'); return; }
    if (!wid) { alert('Select an energize winding first.'); return; }

    const tapRaw = el('ed-energize-tap').value;
    const tapIdx = tapRaw !== '' ? parseInt(tapRaw) : null;
    const tol    = parseFloat(el('ed-gen-tolerance').value) || 5.0;

    const statusEl = el('ed-gen-status');
    statusEl.textContent = 'Generating…';
    statusEl.className   = 'ed-gen-status';

    try {
      const result = await apiPost(
        `/transformers/${encodeURIComponent(tid)}/generate-rules`,
        { excitation_winding_id: wid, energize_tap_index: tapIdx,
          tolerance_percent: tol, save: true }
      );

      // Merge generated rules into whichever config is active
      if (!editorConfig || (!editorConfig.primary?.length && !editorConfig.secondary?.length)) {
        editorConfig = activeCfg ? JSON.parse(JSON.stringify(activeCfg)) : makeEmptyConfig();
      }
      // Apply the auto-assigned relays returned by the backend
      if (result.primary)   editorConfig.primary   = result.primary;
      if (result.secondary) editorConfig.secondary = result.secondary;
      editorConfig.ratio_rules  = result.rules;
      editorConfig.auto_matrix  = { enabled: false, energize_winding: wid, energize_tap_index: tapIdx };
      el('ed-auto-matrix').checked = false;
      el('ed-energize').value      = wid;
      if (topoEditor) topoEditor.setConfig(editorConfig);

      statusEl.textContent = `${result.count} rules generated`;
      statusEl.style.color = 'var(--glow)';

      // Switch to validate mode so the user sees the generated rules
      setEditorMode('validate');
      _updateRulesList();

      // Generation saves to disk — refresh the dashboard if this is the selected one
      if (tid === state.selectedTransformerId) {
        await loadConfig(state.selectedTransformerId);
        renderCanvas();
      }
    } catch (e) {
      statusEl.textContent = 'Error: ' + e;
      statusEl.style.color = 'var(--danger)';
    }
  });

  el('editor-save-btn').addEventListener('click', async () => {
    _syncMetaFromForm();
    // Validate relay assignments before saving
    relayManager.buildFromConfig(editorConfig);
    if (relayManager.hasConflicts()) {
      const lines = relayManager.getConflicts()
        .map(c => `Relay ${c.relay}: assigned to both "${c.nodeA}" and "${c.nodeB}"`)
        .join('\n');
      el('editor-error').textContent = 'Cannot save — duplicate relay assignments:\n' + lines;
      el('editor-error').classList.remove('hidden');
      return;
    }
    if (!editorConfig.tests) editorConfig.tests = [];
    try {
      await apiPost('/transformers', { data: editorConfig });
      el('editor-save-btn').textContent = '✓ Saved';
      setTimeout(() => { el('editor-save-btn').textContent = '💾 Save'; }, 2500);
      el('editor-error').classList.add('hidden');
      await loadTransformerList();
      // Refresh the dashboard view if this transformer is the selected one
      if (editorConfig.transformer_id === state.selectedTransformerId) {
        await loadConfig(state.selectedTransformerId);
        renderCanvas();
      }
    } catch (e) {
      el('editor-error').textContent = String(e);
      el('editor-error').classList.remove('hidden');
    }
  });

  el('editor-load-btn').addEventListener('click', () => {
    openTransformerPicker(loadTransformerIntoEditor, 'Load Transformer into Editor');
  });

  // Escape cancels an active node-pick
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && topoEditor?._rulePick) {
      topoEditor.cancelNodePick();
    }
  });
}

async function loadTransformerIntoEditor(id) {
  if (!id) return;
  try {
    const cfg = await apiGet(`/transformers/${encodeURIComponent(id)}`);
    if (!cfg.ratio_rules) cfg.ratio_rules = cfg.validation_rules || [];
    editorConfig = cfg;
    topoEditor?.setConfig(cfg);
    _syncMetaToForm(cfg);
    el('editor-error').classList.add('hidden');
    if (_editorMode === 'validate') _updateRulesList();
  } catch (e) {
    alert('Could not load transformer: ' + e);
  }
}

function _syncMetaFromForm() {
  if (!editorConfig) return;
  editorConfig.name               = el('ed-name').value;
  editorConfig.transformer_id     = el('ed-id').value;
  editorConfig.rated_power_va     = Number(el('ed-power').value) || 0;
  editorConfig.rated_frequency_hz = Number(el('ed-freq').value)  || 50;
  editorConfig.notes              = el('ed-notes').value;
  const wid    = el('ed-energize-sel').value || el('ed-energize').value;
  const tapRaw = el('ed-energize-tap').value;
  const tapIdx = tapRaw !== '' ? parseInt(tapRaw) : null;
  el('ed-energize').value = wid;
  editorConfig.auto_matrix = {
    enabled:            el('ed-auto-matrix').checked,
    energize_winding:   wid,
    energize_tap_index: tapIdx,
  };
  if (topoEditor) topoEditor.config = editorConfig;
}

function _updateZoomLabel() {
  if (topoEditor) el('ed-zoom-pct').textContent = topoEditor.getZoomPct() + '%';
}

// ── relay picker ──────────────────────────────────────────────────────────────

let rpCallback = null;

function showRelayPicker(group, currentVal, callback, anchorEl) {
  rpCallback = callback;

  // Rebuild the occupied-relay registry from the current config
  relayManager.buildFromConfig(editorConfig);
  const usedRelays = relayManager.getUsedRelays();

  const picker   = el('relay-picker');
  const backdrop = el('rp-backdrop');
  const btnsEl   = el('rp-buttons');

  // Group B (RL37–40) is the energizing winding's TAP group — a different pool
  // from the A2 relays (RL17–32) used by normal windings' end/tap nodes.
  const RANGES = { A: [1, 16], B: [17, 32], B2: [37, 40] };
  const NAMES  = {
    A:  'A1 · Start node',
    B:  'A2 · End / Tap node',
    B2: 'Group B · Energizing tap',
  };
  const range = RANGES[group] || RANGES.B;
  const name  = NAMES[group]  || NAMES.B;
  el('rp-title').textContent = `${name}  (${range[0]}–${range[1]})`;

  btnsEl.innerHTML = '';
  for (let rl = range[0]; rl <= range[1]; rl++) {
    const isCurrent  = currentVal === rl;
    const isOccupied = usedRelays.has(rl) && !isCurrent;
    const btn = document.createElement('button');
    let cls = 'rp-btn';
    if (isCurrent)  cls += group === 'A' ? ' sel-a' : ' sel-b';
    if (isOccupied) cls += ' rp-occupied';
    btn.className   = cls;
    btn.textContent = rl;
    btn.disabled    = isOccupied;
    if (isOccupied) {
      btn.title = `Occupied by ${relayManager.getOwner(rl)}`;
    } else {
      btn.addEventListener('click', () => { rpCallback(rl); closeRelayPicker(); });
    }
    btnsEl.appendChild(btn);
  }

  if (anchorEl) {
    const rect = anchorEl.getBoundingClientRect();
    picker.style.left = `${rect.left}px`;
    picker.style.top  = `${rect.bottom + 4}px`;
  }

  picker.classList.remove('hidden');
  backdrop.classList.remove('hidden');
}

function closeRelayPicker() {
  el('relay-picker').classList.add('hidden');
  el('rp-backdrop').classList.add('hidden');
  rpCallback = null;
}

el('rp-close').addEventListener('click', closeRelayPicker);
el('rp-backdrop').addEventListener('click', closeRelayPicker);
el('rp-clear').addEventListener('click', () => { if (rpCallback) rpCallback(null); closeRelayPicker(); });

// ── wire-colour swatch picker ────────────────────────────────────────────────

// Refresh a colour-assign button (swatch + name), remembering the hex on the DOM.
function setColorBtn(id, hex) {
  const btn = el(id);
  if (!btn) return;
  btn.dataset.hex = hex || '';
  const sw = btn.querySelector('.swatch');
  const nm = btn.querySelector('.cname');
  if (sw) sw.style.background = hex ? wireSwatchBg(hex) : 'transparent';
  if (nm) nm.textContent = hex ? (wireColorName(hex) || '—') : '—';
}

let cpCallback = null;

function showColorPicker(currentHex, callback, anchorEl) {
  cpCallback = callback;
  const grid = el('cp-grid');
  grid.innerHTML = '';
  for (const c of WIRE_PALETTE) {
    const sel = currentHex && String(currentHex).toLowerCase() === c.hex.toLowerCase();
    const b = document.createElement('button');
    b.className = 'cp-swatch' + (sel ? ' sel' : '');
    b.innerHTML = `<span class="swatch" style="background:${wireSwatchBg(c.hex)}"></span>` +
                  `<span class="cp-name">${esc(c.name)}</span>`;
    b.addEventListener('click', () => { const cb = cpCallback; closeColorPicker(); if (cb) cb(c.hex); });
    grid.appendChild(b);
  }
  const picker = el('color-picker');
  const backdrop = el('cp-backdrop');
  if (anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    picker.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - 250))}px`;
    picker.style.top  = `${r.bottom + 4}px`;
  }
  picker.classList.remove('hidden');
  backdrop.classList.remove('hidden');
}

function closeColorPicker() {
  el('color-picker').classList.add('hidden');
  el('cp-backdrop').classList.add('hidden');
  cpCallback = null;
}

el('cp-close').addEventListener('click', closeColorPicker);
el('cp-backdrop').addEventListener('click', closeColorPicker);

// ── serial port assignment modal ────────────────────────────────────────────

function openPortsModal() {
  el('ports-modal').classList.remove('hidden');
  el('ports-backdrop').classList.remove('hidden');
  scanPorts();
}

function closePortsModal() {
  el('ports-modal').classList.add('hidden');
  el('ports-backdrop').classList.add('hidden');
}

async function scanPorts() {
  const status = el('ports-status');
  status.textContent = 'Scanning…';
  try {
    const { ports } = await apiGet('/serial/ports');
    fillPortSelect('ports-relay', ports, 'relay');
    await refreshDmms();
    status.textContent = ports.length
      ? `${ports.length} port${ports.length === 1 ? '' : 's'} found`
      : 'No serial ports detected';
  } catch (e) {
    status.textContent = 'Scan failed';
    console.error(e);
  }
}

// Populate the V1/V2 UNI-T meter selectors (by serial).
async function refreshDmms() {
  let data;
  try { data = await apiGet('/serial/dmms'); }
  catch { return; }
  const meters = data.meters || [];
  for (const target of ['v1', 'v2']) {
    const sel = el(`dmm-${target}`);
    if (!sel) continue;
    const cur = data[target];                 // serial currently on this channel
    sel.innerHTML = '';
    if (!meters.length) {
      const o = document.createElement('option');
      o.value = ''; o.textContent = '— no UT61B+ found —';
      sel.appendChild(o);
      continue;
    }
    for (const m of meters) {
      const o = document.createElement('option');
      o.value = m.serial || '';
      let label = m.serial || '(no serial)';
      if (m.assigned) label += `  [${m.assigned.toUpperCase()}]`;
      o.textContent = label;
      sel.appendChild(o);
    }
    sel.value = cur || (meters[0] && meters[0].serial) || '';
  }
}

async function assignDmm(target) {
  const serial = el(`dmm-${target}`).value;
  const status = el('ports-status');
  if (!serial) { status.textContent = 'No meter to assign'; return; }
  status.textContent = `Assigning ${target.toUpperCase()} → ${serial}…`;
  try {
    await apiPost('/serial/dmm', { target, serial });
    status.textContent = `${target.toUpperCase()} → meter ${serial}`;
    refreshDmms();
  } catch (e) {
    status.textContent = `${target.toUpperCase()} assign failed (meter busy?)`;
    console.error(e);
  }
}

function fillPortSelect(selectId, ports, target) {
  const sel = el(selectId);
  const prev = sel.value;
  sel.innerHTML = '';
  if (!ports.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '— none —';
    sel.appendChild(opt);
    return;
  }
  for (const p of ports) {
    const opt = document.createElement('option');
    opt.value = p.device;
    let label = p.device;
    if (p.type) label += `  · ${p.type}`;
    if (p.assigned) label += `  [${p.assigned.toUpperCase()}]`;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  // Preselect, in order: prior choice → port already on this target →
  // first detected port of the matching kind → first port.
  const wantType   = target === 'relay' ? 'relay' : 'voltmeter';
  const onTarget   = ports.find(p => p.assigned === target);
  const freeMatch  = ports.find(p => p.type === wantType && !p.assigned);
  sel.value = (prev && ports.some(p => p.device === prev)) ? prev
            : onTarget   ? onTarget.device
            : freeMatch  ? freeMatch.device
            : ports[0].device;
}

async function assignRelay() {
  const port = el('ports-relay').value;
  const baud = parseInt(el('ports-baud').value, 10) || 115200;
  const status = el('ports-status');
  if (!port) { status.textContent = 'Pick a port first'; return; }
  status.textContent = `Assigning Relay → ${port}…`;
  try {
    await apiPost('/serial/relay', { port, baud });
    status.textContent = `Relay board connected on ${port}`;
    refreshRelayStatus();
    scanPorts();
  } catch (e) {
    status.textContent = 'Relay assign failed — port busy or not a relay';
    console.error(e);
  }
}

async function assignPort(target) {
  const sel  = el(target === 'v1' ? 'ports-v1' : 'ports-v2');
  const port = sel.value;
  const baud = parseInt(el('ports-baud').value, 10) || 115200;
  const status = el('ports-status');
  if (!port) { status.textContent = 'Pick a port first'; return; }
  status.textContent = `Assigning ${target.toUpperCase()} → ${port}…`;
  try {
    await apiPost('/serial/voltmeter', { target, port, baud });
    status.textContent = `${target.toUpperCase()} connected on ${port}`;
    scanPorts();
  } catch (e) {
    status.textContent = `${target.toUpperCase()} failed — port busy or wrong baud`;
    console.error(e);
  }
}

function bindPortsModal() {
  el('btn-ports').addEventListener('click', openPortsModal);
  el('ports-close').addEventListener('click', closePortsModal);
  el('ports-backdrop').addEventListener('click', closePortsModal);
  el('ports-scan').addEventListener('click', scanPorts);
  el('ports-assign-relay').addEventListener('click', assignRelay);
  el('ports-assign-dmm-v1').addEventListener('click', () => assignDmm('v1'));
  el('ports-assign-dmm-v2').addEventListener('click', () => assignDmm('v2'));
}

// ── utilities ─────────────────────────────────────────────────────────────────

function setVisible(id, visible) {
  const e = el(id);
  if (e) e.classList.toggle('hidden', !visible);
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

function objKeysToStr(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) out[String(k)] = v;
  return out;
}

// ── boot ──────────────────────────────────────────────────────────────────────

// ── relay board connect/status ──────────────────────────────────────────────
async function refreshRelayStatus() {
  try {
    const s = await apiGet('/hardware/status');
    const connected = s.relay_controller === 'CONNECTED';
    const btn = el('btn-relay-connect');
    const st  = el('relay-comm-status');
    if (btn) btn.classList.toggle('hidden', connected);
    if (st && !connected) { st.textContent = 'disconnected'; st.style.color = 'var(--danger)'; }
  } catch { /* server not ready */ }
}

function bindRelayConnect() {
  const btn = el('btn-relay-connect');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const st = el('relay-comm-status');
    btn.disabled = true;
    if (st) { st.textContent = 'connecting…'; st.style.color = 'var(--warning)'; }
    try {
      const r = await apiPost('/hardware/relay/connect', {});
      if (st) { st.textContent = 'connected ' + (r.port || ''); st.style.color = 'var(--glow)'; }
      btn.classList.add('hidden');
    } catch (e) {
      if (st) { st.textContent = 'no relay found'; st.style.color = 'var(--danger)'; }
    } finally {
      btn.disabled = false;
      refreshRelayStatus();
    }
  });
}

// ── relay diagnostic sequence ─────────────────────────────────────────────────
let _relaySeqRunning = false;
let _relaySeqTimer   = null;

function _resetRelaySeqBtn() {
  _relaySeqRunning = false;
  if (_relaySeqTimer) { clearTimeout(_relaySeqTimer); _relaySeqTimer = null; }
  const btn = el('btn-relay-seq');
  if (btn) { btn.textContent = '🔧 Diagnostic'; btn.disabled = false; }
}

function bindRelayDiagnostic() {
  const btn = el('btn-relay-seq');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    if (_ff.active) { alert('Stop the Fault Finder before running the Diagnostic sequence.'); return; }
    if (_relaySeqRunning) {
      btn.disabled = true;
      try { await apiPost('/relays/sequence/stop'); } catch {}
      _resetRelaySeqBtn();
      return;
    }
    btn.disabled = true;
    try {
      const r = await apiPost('/relays/sequence');
      _relaySeqRunning = true;
      btn.textContent = '⏹ Stop Sequence';
      btn.disabled = false;
      // Auto-revert the button when the run should be finished (dwell × steps).
      const ms = (r.steps || 0) * (r.dwell_ms || 1000) + 500;
      _relaySeqTimer = setTimeout(_resetRelaySeqBtn, ms);
    } catch (e) {
      _resetRelaySeqBtn();
      console.error(e);
      alert('Relay sequence could not start.\n\nSelect a transformer that has assigned relays, and make sure no test is running.');
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initCanvas();
  bindTabs();
  bindControlPanel();
  bindTransformerPicker();
  bindPortsModal();
  bindRelayConnect();
  bindRelayDiagnostic();
  bindRelayManual();
  loadTransformerList();
  connectWS();
  refreshRelayStatus();
  setInterval(refreshRelayStatus, 5000);   // surface the Connect button if it drops
});
