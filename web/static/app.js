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
          this._reg(t.relay_a, `${w.id}:tap${ti}+`);
          this._reg(t.relay_b, `${w.id}:tap${ti}-`);
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
      state.currentVoltage = data.voltage;
      renderMeasurement();
      renderCanvas();
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
      state.currentVoltage  = null;
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
      state.currentVoltage   = null;
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
  } catch {
    tfCanvas.setConfig(null);
    state.loadedWindings = [];
    el('canvas-placeholder').classList.remove('hidden');
    renderExcitationSection();
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
  const sel = el('tf-select');
  sel.innerHTML = '<option value="">— Select —</option>';
  for (const t of state.transformerList) {
    const opt = document.createElement('option');
    opt.value       = t.id;
    opt.textContent = t.name;
    if (t.id === state.selectedTransformerId) opt.selected = true;
    sel.appendChild(opt);
  }
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
  let activeCount = 0;

  const rowA = el('relays-a');
  if (rowA.children.length === 0) buildRelayRow(rowA, 1, 16);
  for (let id = 1; id <= 16; id++) {
    const btn = rowA.querySelector(`[data-rl="${id}"]`);
    if (!btn) continue;
    const on = isOn(id);
    if (on) activeCount++;
    btn.className = 'relay-btn' + (on ? ' on-a' : '');
  }

  const rowB = el('relays-b');
  if (rowB.children.length === 0) buildRelayRow(rowB, 17, 32);
  for (let id = 17; id <= 32; id++) {
    const btn = rowB.querySelector(`[data-rl="${id}"]`);
    if (!btn) continue;
    const on = isOn(id);
    if (on) activeCount++;
    btn.className = 'relay-btn' + (on ? ' on-b' : '');
  }

  const rowG = el('relays-gate');
  if (rowG.children.length === 0) buildRelayRow(rowG, 33, 34);
  for (const id of [33, 34]) {
    const btn = rowG.querySelector(`[data-rl="${id}"]`);
    if (!btn) continue;
    const on = isOn(id);
    if (on) activeCount++;
    btn.className = 'relay-btn' + (on ? ' on-gate' : '');
  }

  el('relay-count').textContent = `${activeCount} active`;
  el('relay-count').style.color = activeCount > 0 ? 'var(--glow)' : 'var(--muted)';
}

function buildRelayRow(container, from, to) {
  container.innerHTML = '';
  for (let id = from; id <= to; id++) {
    const btn = document.createElement('div');
    btn.className   = 'relay-btn';
    btn.dataset.rl  = id;
    btn.textContent = id;
    container.appendChild(btn);
  }
}

function renderMeasurement() {
  const { currentVoltage, expectedVoltage, tolerancePct } = state;

  const measEl = el('meas-measured');
  const expEl  = el('meas-expected');
  const devEl  = el('meas-deviation');
  const tolEl  = el('meas-tolerance');

  measEl.textContent = currentVoltage  != null ? currentVoltage.toFixed(3)  : '—';
  expEl.textContent  = expectedVoltage != null ? expectedVoltage.toFixed(3) : '—';
  tolEl.textContent  = `±${tolerancePct.toFixed(1)}`;

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

function renderResults() {
  const tbody = el('results-tbody');
  tbody.innerHTML = '';
  const results = [...state.stepResults].reverse();
  for (const r of results) {
    const tr = document.createElement('tr');
    const passed = r.passed;
    tr.innerHTML = `
      <td class="muted">${r.step_index + 1}</td>
      <td>${esc(r.from_winding)}<span class="muted"> → </span>${esc(r.to_winding)}</td>
      <td style="text-align:right;color:${passed ? 'var(--glow)' : 'var(--danger)'}">${r.measured_voltage.toFixed(3)}V</td>
      <td style="text-align:right;color:var(--muted)">${r.expected_voltage.toFixed(3)}V</td>
      <td style="text-align:center"><span class="pass-badge ${passed ? 'pass' : 'fail'}">${passed ? 'PASS' : 'FAIL'}</span></td>
    `;
    tbody.appendChild(tr);
  }
  const passCount = state.stepResults.filter(r => r.passed).length;
  el('results-summary').textContent = results.length > 0
    ? `${passCount}/${results.length} PASS`
    : '';
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
  el('tf-select').addEventListener('change', async (e) => {
    const id = e.target.value;
    state.selectedTransformerId = id || null;
    if (id) {
      sendWS('select_transformer', { transformer_id: id });
      await loadConfig(id);
    } else {
      tfCanvas.setConfig(null);
      el('canvas-placeholder').classList.remove('hidden');
    }
    renderCanvas();
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
    state.stepResults = [];
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
    state.stepResults = [];
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
    state.stepResults = [];
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
    state.stepResults = [];
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
      if (btn.dataset.tab === 'editor') setTimeout(initEditor, 30);
    });
  });
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
  el('ed-auto-matrix').checked = cfg.auto_matrix?.enabled ?? true;
  el('ed-energize').value      = cfg.auto_matrix?.energize_winding || 'P1';
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

  if (sel.type === 'winding') {
    el('inspector-winding').classList.remove('hidden');
    const badge = el('insp-badge');
    badge.textContent = sel.side === 'primary' ? 'PRIMARY' : 'SECONDARY';
    badge.className = 'insp-badge' + (sel.side === 'secondary' ? ' secondary' : '');
    const w = sel.data;
    el('insp-wid').value       = w.id;
    el('insp-voltage').value   = w.voltage;
    el('insp-start-pin').value = w.start_pin;
    el('insp-end-pin').value   = w.end_pin;
    el('insp-relay-a').textContent = w.relay_a != null ? `RL${w.relay_a}` : 'None';
    el('insp-relay-a').className   = 'relay-assign-btn' + (w.relay_a != null ? ' assigned-a' : '');
    el('insp-relay-b').textContent = w.relay_b != null ? `RL${w.relay_b}` : 'None';
    el('insp-relay-b').className   = 'relay-assign-btn' + (w.relay_b != null ? ' assigned-b' : '');
  } else if (sel.type === 'tap') {
    el('inspector-tap').classList.remove('hidden');
    const t = sel.data;
    el('insp-tap-label').value   = t.label || '';
    el('insp-tap-pin').value     = t.pin;
    el('insp-tap-voltage').value = t.voltage;
    el('insp-tap-relay-a').textContent = t.relay_a != null ? `RL${t.relay_a}` : 'None';
    el('insp-tap-relay-a').className   = 'relay-assign-btn' + (t.relay_a != null ? ' assigned-a' : '');
    el('insp-tap-relay-b').textContent = t.relay_b != null ? `RL${t.relay_b}` : 'None';
    el('insp-tap-relay-b').className   = 'relay-assign-btn' + (t.relay_b != null ? ' assigned-b' : '');
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
  el('insp-rule-type').value          = rule.measurement_type       || 'AC';
  el('insp-rule-critical').checked    = !!rule.critical;
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
  if (!items) return;
  items.innerHTML = '';
  if (rules.length === 0) {
    items.innerHTML = '<div style="padding:6px 2px;font-family:var(--font-mono);font-size:10px;color:var(--muted)">No measurement rules yet — set excitation then add measurements</div>';
    return;
  }
  for (const rule of rules) {
    const item = document.createElement('div');
    item.className = 'rule-list-item' + (rule.id === selId ? ' active' : '');
    const meas    = rule.measurement_segment || {};
    const exc     = rule.excitation_segment  || {};
    const nomOut  = meas.nominal_voltage || 0;
    const nomIn   = exc.nominal_voltage  || 0;
    const measLabel = meas.node_a && meas.node_b ? `${meas.node_a}↔${meas.node_b}` : (meas.node_a || '?');
    const ratioStr  = nomIn > 0 && nomOut > 0 ? `${nomOut}/${nomIn}V` : (nomOut > 0 ? `${nomOut}V` : '');
    item.innerHTML =
      `<span class="rule-item-meas">${esc(measLabel)}</span>` +
      (ratioStr ? `<span class="rule-item-voltage">${esc(ratioStr)}</span>` : '') +
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

  el('insp-rule-type').addEventListener('change', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    topoEditor.updateRule(rule.id, { measurement_type: e.target.value });
  });

  el('insp-rule-critical').addEventListener('change', (e) => {
    const rule = topoEditor?.getSelectedRule();
    if (!rule) return;
    topoEditor.updateRule(rule.id, { critical: e.target.checked });
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
  const windingFields = [
    { id: 'insp-wid',       field: 'id',        parse: v => v },
    { id: 'insp-voltage',   field: 'voltage',   parse: Number },
    { id: 'insp-start-pin', field: 'start_pin', parse: Number },
    { id: 'insp-end-pin',   field: 'end_pin',   parse: Number },
  ];
  windingFields.forEach(({ id, field, parse }) => {
    el(id).addEventListener('input', (e) => {
      if (topoEditor?._sel?.type === 'winding')
        topoEditor.updateSelected({ [field]: parse(e.target.value) });
    });
  });

  const tapFields = [
    { id: 'insp-tap-label',   field: 'label',   parse: v => v },
    { id: 'insp-tap-pin',     field: 'pin',     parse: Number },
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
      topoEditor.updateSelected({ relay_a: val });
      el('insp-relay-a').textContent = val != null ? `RL${val}` : 'None';
      el('insp-relay-a').className = 'relay-assign-btn' + (val != null ? ' assigned-a' : '');
    }, e.currentTarget);
  });
  el('insp-relay-b').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'winding') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].relay_b;
    showRelayPicker('B', cur, (val) => {
      topoEditor.updateSelected({ relay_b: val });
      el('insp-relay-b').textContent = val != null ? `RL${val}` : 'None';
      el('insp-relay-b').className = 'relay-assign-btn' + (val != null ? ' assigned-b' : '');
    }, e.currentTarget);
  });

  el('insp-tap-relay-a').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'tap') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].taps[sel.tIndex].relay_a;
    showRelayPicker('A', cur, (val) => {
      topoEditor.updateSelected({ relay_a: val });
      el('insp-tap-relay-a').textContent = val != null ? `RL${val}` : 'None';
      el('insp-tap-relay-a').className = 'relay-assign-btn' + (val != null ? ' assigned-a' : '');
    }, e.currentTarget);
  });
  el('insp-tap-relay-b').addEventListener('click', (e) => {
    const sel = topoEditor?._sel;
    if (!sel || sel.type !== 'tap') return;
    const cur = topoEditor.config[sel.side][sel.wIndex].taps[sel.tIndex].relay_b;
    showRelayPicker('B', cur, (val) => {
      topoEditor.updateSelected({ relay_b: val });
      el('insp-tap-relay-b').textContent = val != null ? `RL${val}` : 'None';
      el('insp-tap-relay-b').className = 'relay-assign-btn' + (val != null ? ' assigned-b' : '');
    }, e.currentTarget);
  });

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

  ['ed-name', 'ed-id', 'ed-power', 'ed-freq', 'ed-notes', 'ed-energize'].forEach(id => {
    el(id).addEventListener('input', _syncMetaFromForm);
  });
  el('ed-auto-matrix').addEventListener('change', _syncMetaFromForm);

  el('editor-save-btn').addEventListener('click', async () => {
    _syncMetaFromForm();
    // Validate relay assignments before saving
    relayManager.buildFromConfig(editorConfig);
    if (relayManager.hasConflicts()) {
      const lines = relayManager.getConflicts()
        .map(c => `RL${c.relay}: assigned to both "${c.nodeA}" and "${c.nodeB}"`)
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
    } catch (e) {
      el('editor-error').textContent = String(e);
      el('editor-error').classList.remove('hidden');
    }
  });

  el('editor-load-btn').addEventListener('click', async () => {
    const id = state.selectedTransformerId;
    if (!id) { alert('Select a transformer on the Dashboard first.'); return; }
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
  });

  // Escape cancels an active node-pick
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && topoEditor?._rulePick) {
      topoEditor.cancelNodePick();
    }
  });
}

function _syncMetaFromForm() {
  if (!editorConfig) return;
  editorConfig.name               = el('ed-name').value;
  editorConfig.transformer_id     = el('ed-id').value;
  editorConfig.rated_power_va     = Number(el('ed-power').value) || 0;
  editorConfig.rated_frequency_hz = Number(el('ed-freq').value)  || 50;
  editorConfig.notes              = el('ed-notes').value;
  editorConfig.auto_matrix = {
    enabled: el('ed-auto-matrix').checked,
    energize_winding: el('ed-energize').value,
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

  const range = group === 'A' ? [1, 16] : [17, 32];
  el('rp-title').textContent = `RL${range[0]}–RL${range[1]}`;

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

document.addEventListener('DOMContentLoaded', () => {
  initCanvas();
  bindTabs();
  bindControlPanel();
  loadTransformerList();
  connectWS();
});
