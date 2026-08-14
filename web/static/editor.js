/**
 * editor.js — Konva.js visual topology + validation-rule editor for ATB.
 * Requires Konva loaded via CDN before this script.
 *
 * Modes:
 *   topology — drag windings, assign relays (original behaviour)
 *   validate — draw/edit measurement rules as dashed lines between nodes
 */

const EC = {
  bg: '#e8ecf2',
  grid: 'rgba(149,166,191,0.22)',
  gridMajor: 'rgba(100,128,170,0.38)',
  core: '#1e2d4a', coreStroke: '#2a3d60',
  coilIdle: '#3a6aaa', coilSel: '#1a9fff', coilActive: '#059669',
  coilPass: '#22c55e', coilFail: '#ef4444',
  nodeFilled: '#1a6fd4', nodeEmpty: '#b8c8e0', nodeActive: '#059669',
  tapIdle: '#2a5a9a', tapActive: '#0284c7', tapSel: '#0284c7',
  leadIdle: 'rgba(37,61,96,0.55)', leadSel: 'rgba(37,99,235,0.8)', leadActive: 'rgba(5,150,105,0.85)',
  labelMuted: '#64748b', labelNormal: '#475569', labelSel: '#18243a',
  relayLabel: '#0284c7', selFill: 'rgba(37,99,235,0.08)', selStroke: '#2563eb',
  ruleLine: 'rgba(37,99,235,0.55)', ruleLineSel: '#1a9fff', ruleLineDisabled: 'rgba(100,116,139,0.3)',
  nodePick: 'rgba(37,99,235,0.15)', nodePickStroke: '#2563eb',
};

// Wire-colour palette + helpers (wireColor/wireColorName/wireColorKind/
// wireStroke/wireSwatchBg) are the single source of truth defined in canvas.js,
// which loads before this file and shares global scope.

class TopoEditor {
  constructor(containerId) {
    this._cid = containerId;
    this._el = document.getElementById(containerId);
    this.config = null;
    this._sel = null;       // { type, side, wIndex, tIndex? }
    this._leads = new Map();
    this._wmeta = new Map(); // key → { cy, coilHalf }
    this._onSelectCb    = null;
    this._onChangeCb    = null;
    this._onRuleSelectCb = null;
    this._onPickPhaseCb  = null;
    this._relays = {};
    this._appState = 'IDLE';

    // ── validate mode state ─────────────────────────────────────────────
    this._mode     = 'topology';  // 'topology' | 'validate'
    this._rules    = [];          // RatioValidationRule[]
    this._ruleSel  = null;        // { ruleId }
    // _activeExcitation: the fixed global excitation segment for all new measurement rules
    //   { node_a, node_b, nominal_voltage }
    this._activeExcitation     = null;
    this._onActiveExcChangedCb = null;
    // _rulePick tracks the current pick flow:
    //   phase: 'exc_a' | 'exc_b' | 'meas_a' | 'meas_b'
    //   pendingExcA, pendingExcB, pendingMeasA: node IDs accumulated so far
    //   targetRuleId: non-null when editing an existing rule
    //   editField: 'excitation' | 'measurement' | null (which segment to replace)
    this._rulePick = null;

    const w = this._el.offsetWidth  || 600;
    const h = this._el.offsetHeight || 500;
    this.stage = new Konva.Stage({ container: containerId, width: w, height: h });

    this._gridLyr  = new Konva.Layer({ listening: false });
    this._wireLyr  = new Konva.Layer({ listening: false });
    this._coreLyr  = new Konva.Layer({ listening: false });
    this._windLyr  = new Konva.Layer();
    this._ruleLyr  = new Konva.Layer({ listening: true });
    this.stage.add(this._gridLyr, this._wireLyr, this._coreLyr, this._windLyr, this._ruleLyr);

    this._drawGrid();
    this._setupInteraction();
  }

  // ── public API ────────────────────────────────────────────────────────────

  onSelect(cb)                  { this._onSelectCb          = cb; }
  onChange(cb)                  { this._onChangeCb          = cb; }
  onRuleSelect(cb)              { this._onRuleSelectCb      = cb; }
  onPickPhase(cb)               { this._onPickPhaseCb       = cb; }
  onActiveExcitationChanged(cb) { this._onActiveExcChangedCb = cb; }

  getActiveExcitation() {
    return this._activeExcitation ? { ...this._activeExcitation } : null;
  }

  setConnectionColor(color) {
    if (!this.config) return;
    if (!this.config.connection_style) {
      this.config.connection_style = { connection_type: 'core_link', line_style: 'solid', line_color: color };
    } else {
      this.config.connection_style.line_color = color;
    }
    this._rebuildAll();
    this._fire('change', this.config);
  }

  getConnectionColor() {
    return this.config?.connection_style?.line_color || EC.leadIdle;
  }

  // Wire-color update. Colours can be multi-stroke (Y/G stripe, White/Clear
  // outline), so rather than mutate a single line we set the value on the config
  // and rebuild the winding — the styled lead + colour-name label are redrawn
  // correctly. Colour changes are one-per-pick (swatch picker), so this is cheap.
  // field: 'wire_color_start' | 'wire_color_end' for windings, 'wire_color' for taps.
  setLeadColor(field, color) {
    if (!this._sel || !this.config) return;
    const { type, side, wIndex, tIndex } = this._sel;

    if (type === 'winding') {
      const w = this.config[side]?.[wIndex];
      if (!w) return;
      w[field] = color;
    } else if (type === 'tap') {
      const tap = this.config[side]?.[wIndex]?.taps?.[tIndex];
      if (!tap) return;
      tap[field] = color;
    } else {
      return;
    }
    this._rebuildWinding(side, wIndex);
    this._fire('change', this.config);
  }

  setActiveExcitation(seg) {
    this._activeExcitation = seg ? { ...seg } : null;
    if (this._mode === 'validate') this._drawRules();
    this._fire('activeExcitationChanged', this._activeExcitation);
  }

  setConfig(cfg) {
    this.config = cfg ? JSON.parse(JSON.stringify(cfg)) : null;
    if (this.config) {
      if (!this.config.ratio_rules) {
        this.config.ratio_rules = this.config.validation_rules || [];
      }
      // Migrate any legacy from_node/to_node rules to segment format
      this.config.ratio_rules = this.config.ratio_rules.map(r =>
        r.excitation_segment ? r : this._migrateRule(r)
      );
    }
    this._rules = this.config?.ratio_rules || [];
    this._sel     = null;
    this._ruleSel = null;
    this._rulePick = null;
    this._leads.clear();
    this._wmeta.clear();
    this._rebuildAll();
    this.fitView();
    this._fire('select', null);
  }

  setLiveState(relays, appState) {
    this._relays   = relays    || {};
    this._appState = appState  || 'IDLE';
    this._applyLive();
  }

  setMode(mode) {
    this._mode             = mode;
    this._rulePick         = null;
    this._activeExcitation = null;
    this._el.style.cursor  = 'default';
    if (mode === 'validate') {
      this._drawRules();
    } else {
      this._ruleLyr.destroyChildren();
      this._ruleLyr.batchDraw();
      this._ruleSel = null;
    }
    this._fire('pickPhase', null);
    this._fire('activeExcitationChanged', null);
  }

  // ── validation rules public API ──────────────────────────────────────────

  setRules(rules) {
    this._rules = rules ? rules.map(r => Object.assign({}, r)) : [];
    if (this.config) this.config.ratio_rules = this._rules;
    if (this._mode === 'validate') this._drawRules();
  }

  getSelectedRule() {
    if (!this._ruleSel) return null;
    return this._rules.find(r => r.id === this._ruleSel.ruleId) || null;
  }

  addRule(fromNode, toNode) {
    const rule = this._makeRule(fromNode, toNode);
    this._rules.push(rule);
    if (this.config) this.config.ratio_rules = this._rules;
    this._ruleSel = { ruleId: rule.id };
    this._fire('change', this.config);
    this._fire('ruleSelect', rule);
    if (this._mode === 'validate') this._drawRules();
    return rule;
  }

  deleteRule(ruleId) {
    this._rules = this._rules.filter(r => r.id !== ruleId);
    if (this.config) this.config.ratio_rules = this._rules;
    if (this._ruleSel?.ruleId === ruleId) this._ruleSel = null;
    this._fire('change', this.config);
    this._fire('ruleSelect', null);
    if (this._mode === 'validate') this._drawRules();
  }

  updateRule(ruleId, updates) {
    const rule = this._rules.find(r => r.id === ruleId);
    if (!rule) return;
    Object.assign(rule, updates);
    if (this.config) this.config.ratio_rules = this._rules;
    this._fire('change', this.config);
    if (this._mode === 'validate') this._drawRules();
  }

  startNodePick(field, targetRuleId) {
    let phase, editField = null;
    if (field === 'new_exc') {
      // Explicitly pick a fresh excitation segment (always starts from exc_a)
      phase = 'exc_a';
    } else if (field === 'excitation') {
      phase = 'exc_a'; editField = 'excitation';
    } else if (field === 'measurement') {
      phase = 'meas_a'; editField = 'measurement';
    } else if (field === 'new_from') {
      // If excitation already locked in, go straight to measurement pick
      phase = this._activeExcitation ? 'meas_a' : 'exc_a';
    } else if (field === 'from_node') {
      phase = 'exc_a'; editField = 'excitation';
    } else if (field === 'to_node') {
      phase = 'meas_a'; editField = 'measurement';
    } else {
      phase = this._activeExcitation ? 'meas_a' : 'exc_a';
    }

    this._rulePick = {
      phase,
      pendingExcA:  null,
      pendingExcB:  null,
      pendingMeasA: null,
      targetRuleId: targetRuleId || null,
      editField,
    };
    this._el.style.cursor = 'crosshair';
    this._drawRules();
    this._fire('pickPhase', { phase, targetRuleId: targetRuleId || null, excA: this._activeExcitation?.node_a, excB: this._activeExcitation?.node_b });
  }

  cancelNodePick() {
    this._rulePick = null;
    this._el.style.cursor = 'default';
    if (this._mode === 'validate') this._drawRules();
    this._fire('pickPhase', null);
  }

  resize(w, h) {
    this.stage.width(w);
    this.stage.height(h);
    this._drawGrid();
    if (this.config) { this._rebuildAll(); this.fitView(); }
  }

  fitView() {
    const L  = this._L();
    const sw = this.stage.width(), sh = this.stage.height();
    // Content bounding box: windings span ~±195 from centre plus label room,
    // and the full (possibly extended) core height with padding.
    const contentW = 540;
    const contentH = L.coreH + 80;
    const scale = Math.max(0.25, Math.min(sw / contentW, sh / contentH, 1));
    this.stage.scale({ x: scale, y: scale });
    // Content centre is (cx, H/2) since the core is vertically centred.
    this.stage.position({
      x: sw / 2 - L.cx * scale,
      y: sh / 2 - (L.coreY + L.coreH / 2) * scale,
    });
    this._drawGrid();
    this.stage.batchDraw();
  }

  zoom(dir) {
    const by = dir > 0 ? 1.2 : 0.83;
    const s = this.stage;
    const c = { x: s.width() / 2, y: s.height() / 2 };
    const oldS = s.scaleX();
    const newS = Math.max(0.25, Math.min(4, oldS * by));
    s.scale({ x: newS, y: newS });
    s.position({ x: c.x - (c.x - s.x()) * (newS / oldS), y: c.y - (c.y - s.y()) * (newS / oldS) });
    this._drawGrid();
    s.batchDraw();
  }

  getZoomPct() { return Math.round(this.stage.scaleX() * 100); }

  addWinding(side) {
    if (!this.config) return;
    const list = this.config[side];
    const n = list.length + 1;
    const prefix = side === 'primary' ? 'P' : 'S';
    const bp = side === 'primary' ? n * 2 - 1 : 100 + (n - 1) * 2;
    list.push({
      id: `${prefix}${n}`, winding_type: 'basic_winding',
      start_pin: bp, end_pin: bp + 1,
      voltage: side === 'primary' ? 230 : 115,
      dot_polarity: true, relay_a: null, relay_b: null,
      meas_channel: -1, taps: [], coords: {},
    });
    this._rebuildAll();
    this.fitView();
    this._selectWinding(side, list.length - 1);
    this._fire('change', this.config);
  }

  addTapToSelected() {
    if (!this._sel || this._sel.type !== 'winding') return;
    const { side, wIndex } = this._sel;
    const w = this.config[side][wIndex];
    const ti = w.taps.length;
    w.taps.push({ pin: 0, voltage: 0, label: `T${ti + 1}`, relay_b: null });
    this._rebuildWinding(side, wIndex);
    this._fire('change', this.config);
  }

  deleteSelected() {
    if (!this._sel) return;
    const { type, side, wIndex, tIndex } = this._sel;
    if (type === 'winding') {
      this.config[side].splice(wIndex, 1);
      this._sel = null;
      this._rebuildAll();
      this.fitView();
      this._fire('select', null);
    } else if (type === 'tap') {
      this.config[side][wIndex].taps.splice(tIndex, 1);
      this._sel = null;
      this._rebuildWinding(side, wIndex);
      this._fire('select', null);
    }
    this._fire('change', this.config);
  }

  updateSelected(updates) {
    if (!this._sel) return;
    const { type, side, wIndex, tIndex } = this._sel;
    const obj = type === 'winding'
      ? this.config[side][wIndex]
      : (type === 'tap' ? this.config[side][wIndex].taps[tIndex] : null);
    if (!obj) return;
    Object.assign(obj, updates);
    this._rebuildWinding(side, wIndex);
    this._fire('change', this.config);
  }

  // Wire number shown on a winding's start/end lead. Pin == relay number, so it
  // is derived from the relay live (updates on assignment). The energizing
  // winding's two mains wires have no relay → EN+ / EN-. Returns '' when a normal
  // winding node has no relay yet (nothing to number).
  _wireNumber(winding, which) {
    const isEnergizing = this.config?.auto_matrix?.energize_winding === winding.id;
    if (isEnergizing) return which === 'start' ? 'EN+' : 'EN-';
    const relay = which === 'start' ? winding.relay_a : winding.relay_b;
    return relay != null ? `${relay}` : '';
  }

  // ── layout ────────────────────────────────────────────────────────────────

  _L() {
    const W = this.stage.width(), H = this.stage.height();
    // Core grows with winding count so each winding gets a fixed vertical slot
    // instead of being squeezed into a fixed-height core.
    const count = Math.max(
      (this.config?.primary || []).length,
      (this.config?.secondary || []).length,
      1,
    );
    const SLOT  = 95;                          // vertical space per winding
    const baseH = Math.min(H * 0.72, 360);
    const coreH = Math.max(baseH, count * SLOT);
    const coreY = (H - coreH) / 2;             // centered (negative when taller than stage)
    const cx = W / 2;
    return { W, H, cx, coreX: cx - 20, coreW: 40, coreH, coreY, primaryX: cx - 195, secondaryX: cx + 195 };
  }

  // ── rendering ─────────────────────────────────────────────────────────────

  _rebuildAll() {
    this._wireLyr.destroyChildren();
    this._coreLyr.destroyChildren();
    this._windLyr.destroyChildren();
    this._ruleLyr.destroyChildren();
    // Preserve manually-dragged positions across re-renders, but when the
    // winding count changes (add/delete) re-lay everything out to defaults so
    // windings spread evenly across the newly-sized core.
    const curCount = (this.config?.primary || []).length + (this.config?.secondary || []).length;
    const countChanged = curCount !== this._lastCount;
    this._lastCount = curCount;
    const prevMeta = countChanged ? new Map() : new Map(this._wmeta);
    this._leads.clear();
    this._wmeta.clear();

    if (!this.config) {
      [this._gridLyr, this._wireLyr, this._coreLyr, this._windLyr, this._ruleLyr].forEach(l => l.batchDraw());
      return;
    }

    this._drawCore();
    const L = this._L();

    for (const side of ['primary', 'secondary']) {
      const list = this.config[side] || [];
      const count = list.length;
      const spacing = count > 0 ? L.coreH / (count + 1) : L.coreH / 2;
      const coilHalf = Math.min(52, spacing * 0.44);

      list.forEach((w, wIndex) => {
        const key = `${side}-${wIndex}`;
        const defCy = L.coreY + spacing * (wIndex + 1);
        const pm = prevMeta.get(key);
        const cy = pm
          ? Math.max(L.coreY + coilHalf + 8, Math.min(L.coreY + L.coreH - coilHalf - 8, pm.cy))
          : defCy;
        this._wmeta.set(key, { cy, coilHalf });
        this._windLyr.add(this._buildGroup(w, side, wIndex, cy, coilHalf));
        this._buildLeads(w, side, wIndex, cy, coilHalf);
      });
    }

    this._coreLyr.batchDraw();
    this._wireLyr.batchDraw();
    this._windLyr.batchDraw();

    if (this._mode === 'validate') this._drawRules();
  }

  _drawGrid() {
    this._gridLyr.destroyChildren();

    const sw    = this.stage.width();
    const sh    = this.stage.height();
    const scale = this.stage.scaleX();
    const ox    = this.stage.x();
    const oy    = this.stage.y();

    // World-space bounds visible in the viewport
    const wLeft   = -ox / scale;
    const wTop    = -oy / scale;
    const wRight  = (sw - ox) / scale;
    const wBottom = (sh - oy) / scale;

    // Adaptive minor grid step: keep screen-space spacing in 20–80 px range
    let step = 40;
    while (step * scale < 20) step *= 2;
    while (step * scale > 80) step /= 2;

    const majorStep = step * 5;

    // One canvas-pixel equivalent in world units
    const px1  = 1   / scale;
    const px15 = 1.5 / scale;

    // Extend one step beyond viewport edges to avoid edge clipping
    const x0 = Math.floor(wLeft   / step) * step - step;
    const x1 = Math.ceil(wRight   / step) * step + step;
    const y0 = Math.floor(wTop    / step) * step - step;
    const y1 = Math.ceil(wBottom  / step) * step + step;

    for (let x = x0; x <= x1; x += step) {
      const isMajor = Math.round(Math.abs(x) / majorStep) * majorStep === Math.round(Math.abs(x));
      this._gridLyr.add(new Konva.Line({
        points: [x, y0, x, y1],
        stroke: isMajor ? EC.gridMajor : EC.grid,
        strokeWidth: isMajor ? px15 : px1,
        listening: false, perfectDrawEnabled: false,
      }));
    }
    for (let y = y0; y <= y1; y += step) {
      const isMajor = Math.round(Math.abs(y) / majorStep) * majorStep === Math.round(Math.abs(y));
      this._gridLyr.add(new Konva.Line({
        points: [x0, y, x1, y],
        stroke: isMajor ? EC.gridMajor : EC.grid,
        strokeWidth: isMajor ? px15 : px1,
        listening: false, perfectDrawEnabled: false,
      }));
    }

    this._gridLyr.batchDraw();
  }

  _drawCore() {
    const L = this._L();
    const { coreX, coreY, coreW, coreH } = L;
    this._coreLyr.add(new Konva.Rect({ x: coreX, y: coreY, width: coreW, height: coreH, fill: EC.core, stroke: EC.coreStroke, strokeWidth: 2, cornerRadius: 4 }));
    for (let i = 1; i <= 8; i++) {
      const y = coreY + (coreH / 9) * i;
      this._coreLyr.add(new Konva.Line({ points: [coreX + 5, y, coreX + coreW - 5, y], stroke: EC.coreStroke, strokeWidth: 1, opacity: 0.5 }));
    }
    this._coreLyr.add(new Konva.Text({ x: coreX, y: coreY + coreH + 6, text: 'CORE', fontSize: 10, fontFamily: 'IBM Plex Mono, monospace', fill: EC.labelMuted, width: coreW, align: 'center' }));
    this._coreLyr.add(new Konva.Text({ x: L.primaryX - 70, y: coreY - 22, text: '◀ PRIMARY', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', fill: '#3a6aaa' }));
    this._coreLyr.add(new Konva.Text({ x: L.secondaryX - 32, y: coreY - 22, text: 'SECONDARY ▶', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace', fill: '#3a6aaa' }));
  }

  _coilPoints(coilHalf, side, turns = 5) {
    const amp = 18, dir = side === 'primary' ? -1 : 1, steps = turns * 32;
    const pts = [];
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      pts.push(dir * amp * Math.sin(t * turns * Math.PI * 2), -coilHalf + t * coilHalf * 2);
    }
    return pts;
  }

  _buildGroup(winding, side, wIndex, cy, coilHalf) {
    const L = this._L();
    const colX = side === 'primary' ? L.primaryX : L.secondaryX;
    const isSel = this._sel?.type === 'winding' && this._sel?.side === side && this._sel?.wIndex === wIndex;
    const lDir = side === 'primary' ? -1 : 1;

    const g = new Konva.Group({
      x: colX, y: cy, draggable: true,
      dragBoundFunc: (pos) => ({ x: colX, y: Math.max(L.coreY + coilHalf + 8, Math.min(L.coreY + L.coreH - coilHalf - 8, pos.y)) }),
      id: `winding-${side}-${wIndex}`,
    });

    const selW = 105, selH = (coilHalf + 16) * 2;
    g.add(new Konva.Rect({
      x: side === 'primary' ? -(18 + 68) : -18, y: -selH / 2,
      width: selW, height: selH,
      fill: isSel ? EC.selFill : 'transparent',
      stroke: isSel ? EC.selStroke : 'transparent',
      strokeWidth: 1, cornerRadius: 4, name: 'sel-bg',
    }));

    g.add(new Konva.Line({
      points: this._coilPoints(coilHalf, side),
      stroke: isSel ? EC.coilSel : EC.coilIdle, strokeWidth: isSel ? 5 : 4,
      lineCap: 'round', lineJoin: 'round', name: 'coil',
      shadowBlur: isSel ? 6 : 0, shadowColor: EC.coilSel,
    }));

    // Start node (top / relay_a)
    const startNode = new Konva.Circle({
      x: 0, y: -coilHalf, radius: 7,
      fill: winding.relay_a != null ? EC.nodeFilled : EC.nodeEmpty,
      stroke: '#c0ccdd', strokeWidth: 1, name: 'start-node', hitStrokeWidth: 10,
    });
    startNode.on('mouseover', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = 'crosshair';
      startNode.radius(9); startNode.stroke(EC.nodePickStroke); startNode.strokeWidth(2);
      this._windLyr.batchDraw();
    });
    startNode.on('mouseout', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = this._rulePick ? 'crosshair' : 'default';
      startNode.radius(7); startNode.stroke('#c0ccdd'); startNode.strokeWidth(1);
      this._windLyr.batchDraw();
    });
    g.add(startNode);
    // End node (bottom / relay_b)
    const endNode = new Konva.Circle({
      x: 0, y: coilHalf, radius: 7,
      fill: winding.relay_b != null ? EC.nodeFilled : EC.nodeEmpty,
      stroke: '#c0ccdd', strokeWidth: 1, name: 'end-node', hitStrokeWidth: 10,
    });
    endNode.on('mouseover', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = 'crosshair';
      endNode.radius(9); endNode.stroke(EC.nodePickStroke); endNode.strokeWidth(2);
      this._windLyr.batchDraw();
    });
    endNode.on('mouseout', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = this._rulePick ? 'crosshair' : 'default';
      endNode.radius(7); endNode.stroke('#c0ccdd'); endNode.strokeWidth(1);
      this._windLyr.batchDraw();
    });
    g.add(endNode);

    const lx = side === 'primary' ? -(18 + 68) : (18 + 8);
    const lw = 58;
    const la = side === 'primary' ? 'right' : 'left';
    // id + voltage: placed inside the two leads, toward the core (opposite the
    // outer relay badges). Coil oscillates away from the core, so this gap is clear.
    const ilw = 160;
    const ilx = side === 'primary' ? 12 : -(12 + ilw);
    g.add(new Konva.Text({ x: ilx, y: -9, text: winding.id, fontSize: 13, fontFamily: 'IBM Plex Mono, monospace', fill: isSel ? EC.labelSel : EC.labelNormal, width: ilw, align: 'center', name: 'wid-lbl' }));
    g.add(new Konva.Text({ x: ilx, y: 7, text: `${winding.voltage}V`, fontSize: 10, fontFamily: 'IBM Plex Mono, monospace', fill: EC.labelMuted, width: ilw, align: 'center' }));

    // Wire-colour names — printed INSIDE the winding, just under the start lead
    // and just above the end lead. They live in the winding group, so they move
    // with it on drag and are destroyed with it on rebuild (no ghost labels).
    const wcw = 110;
    const wcx = side === 'primary' ? 14 : -(14 + wcw);
    const startColor = winding.wire_color_start || winding.wire_color || null;
    const endColor   = winding.wire_color_end   || winding.wire_color || null;
    g.add(new Konva.Text({
      name: 'wire-color-start', text: wireColorName(startColor),
      x: wcx, y: -coilHalf + 4, width: wcw, align: 'center',
      fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', fontStyle: 'bold',
      fill: EC.labelNormal, listening: false,
    }));
    g.add(new Konva.Text({
      name: 'wire-color-end', text: wireColorName(endColor),
      x: wcx, y: coilHalf - 13, width: wcw, align: 'center',
      fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', fontStyle: 'bold',
      fill: EC.labelNormal, listening: false,
    }));

    // Wire number = the node's relay number (pin == relay), derived live so it
    // updates the moment a relay is (re)assigned. The energizing winding's mains
    // wires carry no relay, so they read EN+ / EN- instead. Placed IN LINE with
    // the lead (leads sit at y = ±coilHalf) and lit while the relay is energized.
    const startTxt = this._wireNumber(winding, 'start');
    const endTxt   = this._wireNumber(winding, 'end');
    if (startTxt) {
      const on = winding.relay_a != null && Boolean(this._relays[String(winding.relay_a)]);
      g.add(new Konva.Text({ x: lx, y: -coilHalf - 6, text: startTxt, fontSize: 11, fontStyle: 'bold', fontFamily: 'IBM Plex Mono, monospace', fill: on ? EC.nodeActive : EC.relayLabel, width: lw, align: la, name: 'rl-a' }));
    }
    if (endTxt) {
      const on = winding.relay_b != null && Boolean(this._relays[String(winding.relay_b)]);
      g.add(new Konva.Text({ x: lx, y: coilHalf - 6, text: endTxt, fontSize: 11, fontStyle: 'bold', fontFamily: 'IBM Plex Mono, monospace', fill: on ? EC.nodeActive : EC.relayLabel, width: lw, align: la, name: 'rl-b' }));
    }

    winding.taps.forEach((tap, ti) => this._addTap(g, tap, ti, winding.taps.length, coilHalf, side, wIndex));

    g.on('click tap', (e) => {
      const nm = e.target.name();
      if (this._mode === 'validate') {
        let nodeId = null;
        if (nm === 'start-node') nodeId = winding.id;
        else if (nm === 'end-node') nodeId = `${winding.id}:end`;
        if (nodeId) { this._handleNodePick(nodeId); e.cancelBubble = true; }
        return;
      }
      if (nm === 'tap-node') return;
      this._selectWinding(side, wIndex);
      e.cancelBubble = true;
    });

    g.on('dragmove', () => {
      const m = this._wmeta.get(`${side}-${wIndex}`);
      if (m) m.cy = g.y();
      this._updateLeads(`${side}-${wIndex}`, g.y(), coilHalf, side);
      if (this._mode === 'validate') this._drawRules();
    });

    return g;
  }

  _addTap(g, tap, ti, total, coilHalf, side, wIndex) {
    const isSel = this._sel?.type === 'tap' && this._sel?.side === side && this._sel?.wIndex === wIndex && this._sel?.tIndex === ti;
    const frac = (ti + 1) / (total + 1);
    const tapY = -coilHalf + frac * coilHalf * 2;
    const tapX = side === 'primary' ? -(18 + 10) : (18 + 10);
    const on = tap.relay_b != null && Boolean(this._relays[String(tap.relay_b)]);

    // Tap connector — drawn like a main winding lead (same colour-kind treatment:
    // Y/G stripe, White/Clear outline) but a touch thinner (3px vs the leads' 4px)
    // so taps read as first-class wiring, not faint hairlines.
    const tapPts = [0, tapY, tapX, tapY];
    if (tap.wire_color) {
      const s = wireStroke(tap.wire_color);
      if (s.outline) g.add(new Konva.Line({ name: `tap-line-${ti}`, points: tapPts, stroke: s.outline, strokeWidth: 5, lineCap: 'round', listening: false }));
      g.add(new Konva.Line({ points: tapPts, stroke: s.base, strokeWidth: s.thin ? 2 : 3, lineCap: 'round', listening: false }));
      if (s.dash) g.add(new Konva.Line({ points: tapPts, stroke: s.dash, strokeWidth: 3, lineCap: 'round', dash: [6, 6], listening: false }));
    } else {
      const hint = on ? 'rgba(2,132,199,0.6)' : 'rgba(42,90,154,0.45)';
      g.add(new Konva.Line({ name: `tap-line-${ti}`, points: tapPts, stroke: hint, strokeWidth: 2.5, lineCap: 'round', listening: false }));
    }

    // Everything about a tap sits IN LINE with the tap wire (y = tapY), laid out
    // outward from the node:   ●——  [pin] [colour] [label]
    const out = side === 'primary' ? -1 : 1;
    const ta  = side === 'primary' ? 'right' : 'left';
    // Column x for a run of `w`-wide fields starting `gap` px outboard of `from`.
    const col = (from, gap, w) => (out < 0 ? from - gap - w : from + gap);

    const pw = 26, cw = 46, lw2 = 58;
    const px  = col(tapX, 5, pw);                      // pin  — nearest the node
    const ccx = col(out < 0 ? px : px + pw, 5, cw);    // colour name
    const tlx = col(out < 0 ? ccx : ccx + cw, 6, lw2); // tap label

    // Wire number = the tap's relay number (pin == relay), derived live.
    if (tap.relay_b != null) {
      g.add(new Konva.Text({
        x: px, y: tapY - 5, width: pw, align: ta, text: `${tap.relay_b}`,
        fontSize: 10, fontStyle: 'bold', fontFamily: 'IBM Plex Mono, monospace',
        fill: on ? EC.tapActive : EC.relayLabel, listening: false,
      }));
    }
    // Colour name — same line as the tap.
    g.add(new Konva.Text({
      name: `tap-color-${ti}`, text: wireColorName(tap.wire_color),
      x: ccx, y: tapY - 5, width: cw, align: ta,
      fontSize: 8, fontFamily: 'IBM Plex Mono, monospace', fontStyle: 'bold',
      fill: EC.labelNormal, listening: false,
    }));

    const node = new Konva.Circle({
      x: tapX, y: tapY, radius: 5,
      fill: isSel ? EC.tapSel : (on ? EC.tapActive : EC.tapIdle),
      stroke: isSel ? EC.tapSel : (on ? EC.tapActive : '#4a6a9a'),
      strokeWidth: 1.5, name: 'tap-node', hitStrokeWidth: 12,
    });
    node.on('mouseover', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = 'crosshair';
      node.radius(8); node.stroke(EC.nodePickStroke); node.strokeWidth(2);
      this._windLyr.batchDraw();
    });
    node.on('mouseout', () => {
      if (this._mode !== 'validate') return;
      this._el.style.cursor = this._rulePick ? 'crosshair' : 'default';
      node.radius(5);
      node.stroke(isSel ? EC.tapSel : (on ? EC.tapActive : '#4a6a9a'));
      node.strokeWidth(1.5);
      this._windLyr.batchDraw();
    });
    g.add(node);

    // Tap label — outermost on the tap's line (pin and colour sit inboard of it).
    const label = tap.label || `${tap.voltage}V`;
    g.add(new Konva.Text({ x: tlx, y: tapY - 5, text: label, fontSize: 9, fontFamily: 'IBM Plex Mono, monospace', width: lw2, align: ta, fill: isSel ? EC.tapSel : (on ? EC.tapActive : EC.labelMuted) }));

    node.on('click tap', (e) => {
      if (this._mode === 'validate') {
        const windId = this.config?.[side]?.[wIndex]?.id;
        if (windId) this._handleNodePick(`${windId}:tap${ti}`);
        e.cancelBubble = true;
        return;
      }
      this._selectTap(side, wIndex, ti);
      e.cancelBubble = true;
    });
  }

  _buildLeads(w, side, wIndex, cy, coilHalf) {
    const L = this._L();
    const colX = side === 'primary' ? L.primaryX : L.secondaryX;
    const coreEx = side === 'primary' ? L.coreX : L.coreX + L.coreW;
    const isSel = this._sel?.type === 'winding' && this._sel?.side === side && this._sel?.wIndex === wIndex;
    const onA = w.relay_a != null && Boolean(this._relays[String(w.relay_a)]);
    const onB = w.relay_b != null && Boolean(this._relays[String(w.relay_b)]);
    const fallback = this.config?.connection_style?.line_color || EC.leadIdle;
    const startColor = w.wire_color_start || w.wire_color || fallback;
    const endColor   = w.wire_color_end   || w.wire_color || fallback;

    // Styled leads (Y/G stripe, White/Clear outline). Selection is shown by the
    // coil glow + sel-bg rect. NOTE: colour-name labels live inside the winding
    // group (see _buildGroup) so they travel with it on drag.
    const top = this._leadNodes([colX, cy - coilHalf, coreEx, cy - coilHalf], startColor, onA);
    const bot = this._leadNodes([colX, cy + coilHalf, coreEx, cy + coilHalf], endColor, onB);
    this._wireLyr.add(...top, ...bot);
    this._leads.set(`${side}-${wIndex}`, { top, bot, coilHalf });
  }

  // Build the 1–3 Konva lines that render one lead honouring its colour kind:
  //   solid  → one line;   Y/G → green base + yellow dashed overlay;
  //   White  → grey underlay + white line;   Clear → grey underlay + pale thin line.
  _leadNodes(points, hex, active) {
    if (active) {
      return [new Konva.Line({ points, stroke: EC.leadActive, strokeWidth: 4, lineCap: 'round' })];
    }
    const s = wireStroke(hex || EC.leadIdle);
    const nodes = [];
    if (s.outline) nodes.push(new Konva.Line({ points, stroke: s.outline, strokeWidth: 6, lineCap: 'round', listening: false }));
    nodes.push(new Konva.Line({ points, stroke: s.base, strokeWidth: s.thin ? 3 : 4, lineCap: 'round' }));
    if (s.dash) nodes.push(new Konva.Line({ points, stroke: s.dash, strokeWidth: 4, lineCap: 'round', dash: [6, 6], listening: false }));
    return nodes;
  }

  _updateLeads(key, cy, coilHalf, side) {
    const leads = this._leads.get(key);
    if (!leads) return;
    const L = this._L();
    const colX = side === 'primary' ? L.primaryX : L.secondaryX;
    const coreEx = side === 'primary' ? L.coreX : L.coreX + L.coreW;
    const topPts = [colX, cy - coilHalf, coreEx, cy - coilHalf];
    const botPts = [colX, cy + coilHalf, coreEx, cy + coilHalf];
    (leads.top || []).forEach(n => n.points(topPts));
    (leads.bot || []).forEach(n => n.points(botPts));
    this._wireLyr.batchDraw();
  }

  // ── validation rule rendering ─────────────────────────────────────────────

  _drawRules() {
    this._ruleLyr.destroyChildren();
    if (!this.config) { this._ruleLyr.batchDraw(); return; }

    const phase = this._rulePick?.phase;

    // Selectable-indicator rings on every node during an active pick
    if (this._rulePick) {
      // Blue rings for excitation phase, green rings for measurement phase
      const isMeasPhase = (phase === 'meas_a' || phase === 'meas_b');
      const ringFill    = isMeasPhase ? 'rgba(34,197,94,0.12)'  : EC.nodePick;
      const ringStroke  = isMeasPhase ? '#22c55e'               : EC.nodePickStroke;

      for (const side of ['primary', 'secondary']) {
        (this.config[side] || []).forEach((w, wIndex) => {
          const meta = this._wmeta.get(`${side}-${wIndex}`);
          if (!meta) return;
          const { cy, coilHalf } = meta;
          const L = this._L();
          const colX = side === 'primary' ? L.primaryX : L.secondaryX;
          for (const ny of [cy - coilHalf, cy + coilHalf]) {
            this._ruleLyr.add(new Konva.Circle({
              x: colX, y: ny, radius: 11,
              fill: ringFill, stroke: ringStroke,
              strokeWidth: 1, dash: [3, 2], listening: false, opacity: 0.6,
            }));
          }
          w.taps.forEach((_, ti) => {
            const total = w.taps.length;
            const tapY = cy - coilHalf + (ti + 1) / (total + 1) * coilHalf * 2;
            const tapX = side === 'primary' ? colX - 28 : colX + 28;
            this._ruleLyr.add(new Konva.Circle({
              x: tapX, y: tapY, radius: 9,
              fill: ringFill, stroke: ringStroke,
              strokeWidth: 1, dash: [3, 2], listening: false, opacity: 0.6,
            }));
          });
        });
      }

      // Highlight already-chosen pending nodes
      const pending = [];
      if (this._rulePick.pendingExcA)  pending.push({ id: this._rulePick.pendingExcA,  color: '#1a9fff' });
      if (this._rulePick.pendingExcB)  pending.push({ id: this._rulePick.pendingExcB,  color: '#1a9fff' });
      if (this._rulePick.pendingMeasA) pending.push({ id: this._rulePick.pendingMeasA, color: '#22c55e' });
      for (const { id, color } of pending) {
        const pos = this._getNodePos(id);
        if (pos) {
          this._ruleLyr.add(new Konva.Circle({
            x: pos.x, y: pos.y, radius: 13,
            fill: color + '33', stroke: color,
            strokeWidth: 2, listening: false,
          }));
        }
      }

      // Draw in-progress excitation segment if both ends known
      if (this._rulePick.pendingExcA && this._rulePick.pendingExcB) {
        const pa = this._getNodePos(this._rulePick.pendingExcA);
        const pb = this._getNodePos(this._rulePick.pendingExcB);
        if (pa && pb) {
          this._ruleLyr.add(new Konva.Line({
            points: [pa.x, pa.y, pb.x, pb.y],
            stroke: '#1a9fff', strokeWidth: 2, dash: [5, 3], listening: false, opacity: 0.7,
          }));
        }
      }
    }

    // ── Persistent active excitation segment (global reference) ──────────
    if (this._activeExcitation) {
      const aeA = this._getNodePos(this._activeExcitation.node_a);
      const aeB = this._getNodePos(this._activeExcitation.node_b);
      if (aeA && aeB) {
        this._ruleLyr.add(new Konva.Line({
          points: [aeA.x, aeA.y, aeB.x, aeB.y],
          stroke: '#1a9fff', strokeWidth: 5,
          lineCap: 'round', listening: false, opacity: 0.9,
          shadowBlur: 14, shadowColor: '#1a9fff',
        }));
        for (const pos of [aeA, aeB]) {
          this._ruleLyr.add(new Konva.Circle({
            x: pos.x, y: pos.y, radius: 12,
            fill: 'rgba(26,159,255,0.18)', stroke: '#1a9fff',
            strokeWidth: 2.5, listening: false,
            shadowBlur: 10, shadowColor: '#1a9fff',
          }));
        }
        const midX = (aeA.x + aeB.x) / 2;
        const midY = (aeA.y + aeB.y) / 2;
        this._ruleLyr.add(new Konva.Text({
          x: midX - 56, y: midY - 17,
          text: `ACTIVE EXC  ${(this._activeExcitation.nominal_voltage || 0).toFixed(0)}V`,
          fontSize: 9, fontFamily: 'IBM Plex Mono, monospace',
          fill: '#1a9fff', listening: false, fontStyle: 'bold',
        }));
      }
    }

    // Draw each committed rule: blue excitation segment + green measurement segment
    for (const rule of this._rules) {
      const exc  = rule.excitation_segment  || {};
      const meas = rule.measurement_segment || {};

      const excA  = this._getNodePos(exc.node_a);
      const excB  = this._getNodePos(exc.node_b);
      const measA = this._getNodePos(meas.node_a);
      const measB = this._getNodePos(meas.node_b);

      const isSel      = this._ruleSel?.ruleId === rule.id;
      const isDisabled = !rule.enabled;
      const alpha      = isDisabled ? 0.35 : 1;

      // Excitation segment — blue
      const excColor  = isSel ? '#1a9fff' : '#2a6fd4';
      if (excA && excB) {
        this._ruleLyr.add(new Konva.Line({
          points: [excA.x, excA.y, excB.x, excB.y],
          stroke: excColor, strokeWidth: isSel ? 3 : 2,
          lineCap: 'round', listening: false, opacity: alpha,
        }));
        for (const pos of [excA, excB]) {
          this._ruleLyr.add(new Konva.Circle({
            x: pos.x, y: pos.y, radius: 9,
            fill: 'rgba(42,111,212,0.15)', stroke: excColor,
            strokeWidth: isSel ? 2 : 1.5, listening: false, opacity: alpha,
          }));
        }
        // EXC label on the segment
        this._ruleLyr.add(new Konva.Text({
          x: (excA.x + excB.x) / 2 - 18, y: (excA.y + excB.y) / 2 - 8,
          text: `EXC ${(exc.nominal_voltage || 0).toFixed(0)}V`,
          fontSize: 8, fontFamily: 'IBM Plex Mono, monospace',
          fill: excColor, listening: false, opacity: alpha,
        }));
      }

      // Measurement segment — green
      const measColor = isSel ? '#00ff88' : '#22c55e';
      if (measA && measB) {
        this._ruleLyr.add(new Konva.Line({
          points: [measA.x, measA.y, measB.x, measB.y],
          stroke: measColor, strokeWidth: isSel ? 3 : 2,
          lineCap: 'round', listening: false, opacity: alpha,
        }));
        for (const pos of [measA, measB]) {
          this._ruleLyr.add(new Konva.Circle({
            x: pos.x, y: pos.y, radius: 9,
            fill: 'rgba(34,197,94,0.12)', stroke: measColor,
            strokeWidth: isSel ? 2 : 1.5, listening: false, opacity: alpha,
          }));
        }
        this._ruleLyr.add(new Konva.Text({
          x: (measA.x + measB.x) / 2 - 18, y: (measA.y + measB.y) / 2 - 8,
          text: `MEAS ${(meas.nominal_voltage || 0).toFixed(0)}V`,
          fontSize: 8, fontFamily: 'IBM Plex Mono, monospace',
          fill: measColor, listening: false, opacity: alpha,
        }));
      }

      // Ratio connector: curved dashed line between segment midpoints (clickable)
      if (excA && excB && measA && measB) {
        const excMid  = { x: (excA.x  + excB.x)  / 2, y: (excA.y  + excB.y)  / 2 };
        const measMid = { x: (measA.x + measB.x) / 2, y: (measA.y + measB.y) / 2 };
        const mx = (excMid.x + measMid.x) / 2;
        const my = (excMid.y + measMid.y) / 2;
        const dx = measMid.x - excMid.x;
        const dy = measMid.y - excMid.y;
        const len = Math.sqrt(dx * dx + dy * dy) || 1;
        const bend = Math.min(len * 0.25, 55);
        const cpx = mx - (dy / len) * bend;
        const cpy = my + (dx / len) * bend;
        const relColor = isSel ? EC.ruleLineSel : (isDisabled ? EC.ruleLineDisabled : EC.ruleLine);

        const relLine = new Konva.Line({
          points: [excMid.x, excMid.y, cpx, cpy, measMid.x, measMid.y],
          stroke: relColor, strokeWidth: isSel ? 1.5 : 1,
          dash: [7, 5], tension: 0.5, lineCap: 'round',
          listening: true, hitStrokeWidth: 14,
          id: `rule-line-${rule.id}`,
        });
        relLine.on('click tap', (e) => { this._selectRule(rule.id); e.cancelBubble = true; });
        this._ruleLyr.add(relLine);

        // Ratio label at curve apex
        const nomIn  = exc.nominal_voltage  || 0;
        const nomOut = meas.nominal_voltage || 0;
        const ratio  = nomIn > 0 ? (nomOut / nomIn).toFixed(4) : '?';
        const lt = nomIn > 0 && nomOut > 0
          ? `${nomOut}/${nomIn}V = ×${ratio}`
          : `${exc.node_a || '?'}→${meas.node_a || '?'}`;
        this._ruleLyr.add(new Konva.Text({
          x: cpx - 54, y: cpy - 9,
          text: lt, fontSize: 9, fontFamily: 'IBM Plex Mono, monospace',
          fill: relColor, width: 108, align: 'center', listening: false, opacity: alpha,
        }));
      }
    }

    this._ruleLyr.batchDraw();
  }

  _getNodePos(nodeId) {
    if (!nodeId || !this.config) return null;
    const colon  = nodeId.indexOf(':');
    const windId = colon >= 0 ? nodeId.substring(0, colon) : nodeId;
    const suffix = colon >= 0 ? nodeId.substring(colon + 1) : 'start';
    const L = this._L();

    for (const side of ['primary', 'secondary']) {
      const list   = this.config[side] || [];
      const wIndex = list.findIndex(w => w.id === windId);
      if (wIndex < 0) continue;
      const w    = list[wIndex];
      const meta = this._wmeta.get(`${side}-${wIndex}`);
      if (!meta) continue;
      const { cy, coilHalf } = meta;
      const colX = side === 'primary' ? L.primaryX : L.secondaryX;

      if (suffix === 'start') return { x: colX, y: cy - coilHalf };
      if (suffix === 'end')   return { x: colX, y: cy + coilHalf };
      if (suffix.startsWith('tap')) {
        const ti = parseInt(suffix.substring(3));
        if (!isNaN(ti) && ti < w.taps.length) {
          const total = w.taps.length;
          const tapY  = cy - coilHalf + (ti + 1) / (total + 1) * coilHalf * 2;
          const tapX  = side === 'primary' ? colX - 28 : colX + 28;
          return { x: tapX, y: tapY };
        }
      }
      return { x: colX, y: cy };
    }
    return null;
  }

  _handleNodePick(nodeId) {
    if (!this._rulePick) {
      if (!this._activeExcitation) {
        // No active excitation yet: auto-start excitation pick
        this._rulePick = {
          phase: 'exc_b', pendingExcA: nodeId,
          pendingExcB: null, pendingMeasA: null,
          targetRuleId: null, editField: null,
        };
        this._drawRules();
        this._fire('pickPhase', { phase: 'exc_b', excA: nodeId });
      } else {
        // Active excitation set: auto-start measurement pick
        this._rulePick = {
          phase: 'meas_b', pendingMeasA: nodeId,
          pendingExcA: null, pendingExcB: null,
          targetRuleId: null, editField: null,
        };
        this._drawRules();
        this._fire('pickPhase', { phase: 'meas_b', measA: nodeId, excA: this._activeExcitation.node_a, excB: this._activeExcitation.node_b });
      }
      return;
    }

    const p = this._rulePick;

    if (p.phase === 'exc_a') {
      p.pendingExcA = nodeId;
      p.phase = 'exc_b';
      this._drawRules();
      this._fire('pickPhase', { phase: 'exc_b', excA: nodeId });

    } else if (p.phase === 'exc_b') {
      p.pendingExcB = nodeId;
      if (p.targetRuleId && p.editField === 'excitation') {
        // Update excitation segment of existing rule only
        const rule = this._rules.find(r => r.id === p.targetRuleId);
        if (rule) {
          rule.excitation_segment = {
            node_a: p.pendingExcA, node_b: nodeId,
            nominal_voltage: this._inferSegmentVoltage(p.pendingExcA, nodeId),
          };
          this._fire('change', this.config);
          this._fire('ruleSelect', rule);
        }
        this._endPick();
        this._drawRules();
      } else {
        // Commit as the global active excitation, auto-transition to meas_a
        this._activeExcitation = {
          node_a: p.pendingExcA, node_b: nodeId,
          nominal_voltage: this._inferSegmentVoltage(p.pendingExcA, nodeId),
        };
        this._fire('activeExcitationChanged', this._activeExcitation);
        p.phase      = 'meas_a';
        p.pendingExcA = null;
        p.pendingExcB = null;
        this._drawRules();
        this._fire('pickPhase', {
          phase: 'meas_a',
          excA: this._activeExcitation.node_a,
          excB: this._activeExcitation.node_b,
        });
      }

    } else if (p.phase === 'meas_a') {
      p.pendingMeasA = nodeId;
      p.phase = 'meas_b';
      this._drawRules();
      this._fire('pickPhase', { phase: 'meas_b', measA: nodeId, excA: this._activeExcitation?.node_a, excB: this._activeExcitation?.node_b });

    } else if (p.phase === 'meas_b') {
      if (p.targetRuleId && p.editField === 'measurement') {
        // Update measurement segment of existing rule only
        const rule = this._rules.find(r => r.id === p.targetRuleId);
        if (rule) {
          rule.measurement_segment = {
            node_a: p.pendingMeasA, node_b: nodeId,
            nominal_voltage: this._inferSegmentVoltage(p.pendingMeasA, nodeId),
          };
          this._fire('change', this.config);
          this._fire('ruleSelect', rule);
        }
        this._endPick();
        this._drawRules();
      } else {
        // Complete new measurement rule using active excitation
        const exc = this._activeExcitation || { node_a: '', node_b: '', nominal_voltage: 0 };
        const rule = this._makeRule(exc.node_a, exc.node_b, p.pendingMeasA, nodeId);
        this._rules.push(rule);
        if (this.config) this.config.ratio_rules = this._rules;
        this._ruleSel = { ruleId: rule.id };
        this._fire('change', this.config);
        this._fire('ruleSelect', rule);
        // Auto-continue in meas_a phase for the next measurement
        p.phase       = 'meas_a';
        p.pendingMeasA = null;
        this._drawRules();
        this._fire('pickPhase', {
          phase: 'meas_a',
          excA: exc.node_a,
          excB: exc.node_b,
        });
      }
    }
  }

  _endPick() {
    this._rulePick = null;
    this._el.style.cursor = 'default';
    this._fire('pickPhase', null);
  }

  _makeRule(excA, excB, measA, measB) {
    return {
      id: Math.random().toString(36).substr(2, 9),
      excitation_segment:  {
        node_a: excA, node_b: excB,
        nominal_voltage: this._inferSegmentVoltage(excA, excB),
      },
      measurement_segment: {
        node_a: measA, node_b: measB,
        nominal_voltage: this._inferSegmentVoltage(measA, measB),
      },
      tolerance_percent:      10.0,
      minimum_absolute_delta: 0.1,
      measurement_type: 'AC',
      critical: true,
      enabled:  true,
    };
  }

  _inferSegmentVoltage(nodeA, nodeB) {
    const vA = this._getNodeAbsoluteVoltage(nodeA);
    const vB = this._getNodeAbsoluteVoltage(nodeB);
    const diff = Math.abs(vA - vB);
    if (diff > 0) return diff;
    // Fallback: full winding voltage if both are 0 (start-to-start edge case)
    return this._findWinding(nodeA)?.voltage || 0;
  }

  _getNodeAbsoluteVoltage(nodeId) {
    if (!nodeId) return 0;
    const winding = this._findWinding(nodeId);
    if (!winding) return 0;
    if (!nodeId.includes(':')) return 0;  // start node = 0 V reference
    const suffix = nodeId.split(':')[1];
    if (suffix === 'end') return winding.voltage || 0;
    if (suffix.startsWith('tap')) {
      const ti = parseInt(suffix.substring(3));
      return winding.taps?.[ti]?.voltage || 0;
    }
    return 0;
  }

  _migrateRule(r) {
    const fromNode = r.from_node || '';
    const toNode   = r.to_node   || '';
    const nomIn    = r.nominal_input_voltage  || 0;
    const nomOut   = r.nominal_output_voltage || 0;
    return {
      id: r.id || Math.random().toString(36).substr(2, 9),
      excitation_segment:  this._legacyNodeToSegment(fromNode, nomIn),
      measurement_segment: this._legacyNodeToSegment(toNode,   nomOut),
      tolerance_percent:      r.tolerance_percent      ?? 10.0,
      minimum_absolute_delta: r.minimum_absolute_delta ?? 0.1,
      measurement_type: r.measurement_type || 'AC',
      critical: r.critical !== undefined ? r.critical : true,
      enabled:  r.enabled  !== undefined ? r.enabled  : true,
    };
  }

  _legacyNodeToSegment(nodeId, nominalVoltage) {
    if (!nodeId) return { node_a: '', node_b: '', nominal_voltage: 0 };
    if (!nodeId.includes(':') || nodeId.endsWith(':end')) {
      const windId = nodeId.split(':')[0];
      return { node_a: windId, node_b: `${windId}:end`, nominal_voltage: nominalVoltage };
    }
    // tap node — segment runs from winding start to the tap
    const windId = nodeId.split(':')[0];
    return { node_a: windId, node_b: nodeId, nominal_voltage: nominalVoltage };
  }

  _findWinding(nodeId) {
    if (!nodeId) return null;
    const windId = nodeId.split(':')[0];
    for (const side of ['primary', 'secondary']) {
      const w = (this.config?.[side] || []).find(w => w.id === windId);
      if (w) return w;
    }
    return null;
  }

  _selectRule(ruleId) {
    this._ruleSel = { ruleId };
    this._drawRules();
    const rule = this._rules.find(r => r.id === ruleId);
    this._fire('ruleSelect', rule || null);
  }

  // ── topology selection ─────────────────────────────────────────────────────

  _selectWinding(side, wIndex) {
    const prev = this._sel;
    this._sel = { type: 'winding', side, wIndex };
    this._refreshHighlights(prev);
    this._fire('select', { type: 'winding', side, wIndex, data: this.config[side][wIndex] });
  }

  _selectTap(side, wIndex, tIndex) {
    const prev = this._sel;
    this._sel = { type: 'tap', side, wIndex, tIndex };
    this._refreshHighlights(prev);
    this._fire('select', { type: 'tap', side, wIndex, tIndex, data: this.config[side][wIndex].taps[tIndex] });
  }

  _clearSelection() {
    const prev = this._sel;
    this._sel = null;
    this._refreshHighlights(prev);
    this._fire('select', null);
  }

  _clearRuleSelection() {
    this._ruleSel = null;
    this._drawRules();
    this._fire('ruleSelect', null);
  }

  _refreshHighlights(prev) {
    const keys = new Set();
    if (prev) keys.add(`${prev.side}-${prev.wIndex}`);
    if (this._sel) keys.add(`${this._sel.side}-${this._sel.wIndex}`);
    for (const k of keys) {
      const i = k.lastIndexOf('-');
      this._rebuildWinding(k.substring(0, i), parseInt(k.substring(i + 1)));
    }
  }

  _rebuildWinding(side, wIndex) {
    const key  = `${side}-${wIndex}`;
    const meta = this._wmeta.get(key);
    const w    = this.config?.[side]?.[wIndex];
    if (!meta || !w) return;
    const { cy, coilHalf } = meta;

    this._windLyr.findOne(`#winding-${side}-${wIndex}`)?.destroy();
    const old = this._leads.get(key);
    if (old) { (old.top || []).forEach(n => n.destroy()); (old.bot || []).forEach(n => n.destroy()); this._leads.delete(key); }

    this._windLyr.add(this._buildGroup(w, side, wIndex, cy, coilHalf));
    this._buildLeads(w, side, wIndex, cy, coilHalf);
    this._windLyr.batchDraw();
    this._wireLyr.batchDraw();
  }

  // ── live state ────────────────────────────────────────────────────────────

  _applyLive() {
    if (!this.config) return;
    for (const side of ['primary', 'secondary']) {
      (this.config[side] || []).forEach((w, wIndex) => {
        const g = this._windLyr.findOne(`#winding-${side}-${wIndex}`);
        if (!g) return;
        const onA = w.relay_a != null && Boolean(this._relays[String(w.relay_a)]);
        const onB = w.relay_b != null && Boolean(this._relays[String(w.relay_b)]);
        const on  = onA || onB;
        const isSel = this._sel?.type === 'winding' && this._sel?.side === side && this._sel?.wIndex === wIndex;

        const coil = g.findOne('.coil');
        if (coil) {
          coil.stroke(on ? EC.coilActive : (isSel ? EC.coilSel : EC.coilIdle));
          coil.strokeWidth(on ? 3 : (isSel ? 2.5 : 2));
          coil.shadowBlur(on ? 14 : (isSel ? 6 : 0));
          coil.shadowColor(on ? EC.coilActive : EC.coilSel);
        }
        const sn = g.findOne('.start-node');
        if (sn) sn.fill(onA ? EC.nodeActive : (w.relay_a != null ? EC.nodeFilled : EC.nodeEmpty));
        const en = g.findOne('.end-node');
        if (en) en.fill(onB ? EC.nodeActive : (w.relay_b != null ? EC.nodeFilled : EC.nodeEmpty));
      });
    }
    this._windLyr.batchDraw();
  }

  // ── interaction ───────────────────────────────────────────────────────────

  _setupInteraction() {
    this.stage.on('click tap', (e) => {
      if (e.target === this.stage) {
        if (this._mode === 'validate') {
          if (this._rulePick) this.cancelNodePick();
          else this._clearRuleSelection();
        } else {
          this._clearSelection();
        }
      }
    });

    this.stage.on('wheel', (e) => {
      e.evt.preventDefault();
      const by = e.evt.deltaY < 0 ? 1.15 : 0.87;
      const oldS = this.stage.scaleX();
      const newS = Math.max(0.25, Math.min(4, oldS * by));
      const ptr = this.stage.getPointerPosition();
      const mpt = { x: (ptr.x - this.stage.x()) / oldS, y: (ptr.y - this.stage.y()) / oldS };
      this.stage.scale({ x: newS, y: newS });
      this.stage.position({ x: ptr.x - mpt.x * newS, y: ptr.y - mpt.y * newS });
      this._drawGrid();
      this.stage.batchDraw();
    });

    let pan = false, panStart = null;
    this.stage.on('mousedown', (e) => {
      if (e.evt.button === 1 || (e.evt.button === 0 && e.evt.altKey)) {
        pan = true;
        panStart = { sx: this.stage.x(), sy: this.stage.y(), mx: e.evt.clientX, my: e.evt.clientY };
        this._el.style.cursor = 'grab';
        e.evt.preventDefault();
      }
    });
    this.stage.on('mousemove', (e) => {
      if (!pan) return;
      this.stage.position({ x: panStart.sx + e.evt.clientX - panStart.mx, y: panStart.sy + e.evt.clientY - panStart.my });
      this._drawGrid();
      this.stage.batchDraw();
    });
    this.stage.on('mouseup', () => {
      pan = false;
      this._el.style.cursor = this._rulePick ? 'crosshair' : 'default';
    });
  }

  _fire(evt, data) {
    if (evt === 'select'                && this._onSelectCb)          this._onSelectCb(data);
    if (evt === 'change'                && this._onChangeCb)          this._onChangeCb(data);
    if (evt === 'ruleSelect'            && this._onRuleSelectCb)      this._onRuleSelectCb(data);
    if (evt === 'pickPhase'             && this._onPickPhaseCb)       this._onPickPhaseCb(data);
    if (evt === 'activeExcitationChanged' && this._onActiveExcChangedCb) this._onActiveExcChangedCb(data);
  }
}
