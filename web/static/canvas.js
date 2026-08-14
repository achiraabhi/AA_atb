/**
 * canvas.js — Industrial transformer diagram renderer (HTML5 Canvas API)
 * No dependencies. Uses requestAnimationFrame for live animation.
 */

const C = {
  bg:          '#0d1117',
  grid:        '#161b22',
  core:        '#1e2d4a',
  coreStroke:  '#2a3d60',
  coilIdle:    '#3a6aaa',
  coilActive:  '#00ff88',
  coilPass:    '#22c55e',
  coilFail:    '#ef4444',
  pinIdle:     '#4a80c4',
  pinActive:   '#00ff88',
  pinFail:     '#ef4444',
  tapIdle:     '#2a5a9a',
  tapActive:   '#00e5ff',
  leadIdle:    '#1e2d4a',
  leadActive:  'rgba(0,255,136,0.35)',
  labelMuted:  '#ffffff',
  labelActive: '#00ff88',
  labelAccent: '#00e5ff',
  textWhite:   '#ffffff',
  particleFill:'#00ff88',
};

// ════════════════════════════════════════════════════════════════════════════
//  WIRE COLOUR PALETTE — single source of truth (shared by canvas.js, editor.js
//  and app.js; these are the only files loaded, in this order, sharing globals).
//  Each colour has a `kind` so the two "special" insulations render truthfully:
//    solid = one flat colour · yg = green/yellow stripe · clear = transparent ·
//    white = needs an outline to be visible on light backgrounds.
// ════════════════════════════════════════════════════════════════════════════
const WIRE_PALETTE = [
  { hex: '#1a1a1a', name: 'Black',  kind: 'solid' },
  { hex: '#7c4a1e', name: 'Brown',  kind: 'solid' },
  { hex: '#dc2626', name: 'Red',    kind: 'solid' },
  { hex: '#ea580c', name: 'Orange', kind: 'solid' },
  { hex: '#eab308', name: 'Yellow', kind: 'solid' },
  { hex: '#16a34a', name: 'Green',  kind: 'solid' },
  { hex: '#2563eb', name: 'Blue',   kind: 'solid' },
  { hex: '#7c3aed', name: 'Violet', kind: 'solid' },
  { hex: '#6b7280', name: 'Grey',   kind: 'solid' },
  { hex: '#65a30d', name: 'Y/G',    kind: 'yg' },
  { hex: '#ffffff', name: 'White',  kind: 'white' },
  { hex: '#cbd5e1', name: 'Clear',  kind: 'clear' },
];
const _WIRE_BY_HEX = Object.fromEntries(WIRE_PALETTE.map(c => [c.hex.toLowerCase(), c]));

function wireColor(hex)     { return hex ? (_WIRE_BY_HEX[String(hex).toLowerCase()] || null) : null; }
function wireColorName(hex) { const c = wireColor(hex); return c ? c.name : ''; }
function wireColorKind(hex) { const c = wireColor(hex); return c ? c.kind : 'solid'; }

// How to STROKE a wire on a canvas. base = main line colour; dash = optional
// overlaid dashed colour (Y/G stripe); outline = optional darker underlay so
// pale wires stay visible; thin = draw narrower (Clear reads as barely-there).
function wireStroke(hex) {
  switch (wireColorKind(hex)) {
    case 'yg':    return { base: '#16a34a', dash: '#eab308', outline: null,      thin: false };
    case 'white': return { base: '#ffffff', dash: null,      outline: '#94a3b8', thin: false };
    case 'clear': return { base: '#e2e8f0', dash: null,      outline: '#94a3b8', thin: true  };
    default:      return { base: hex,       dash: null,      outline: null,      thin: false };
  }
}

// CSS background for a DOM swatch (picker chips, wiring checklist, inspector).
// Swatches always carry a thin border in CSS, so White/Clear stay visible.
function wireSwatchBg(hex) {
  switch (wireColorKind(hex)) {
    case 'yg':    return 'repeating-linear-gradient(45deg,#16a34a 0 5px,#eab308 5px 10px)';
    case 'clear': return 'repeating-linear-gradient(45deg,#eef1f5 0 5px,#cfd6e0 5px 10px)';
    default:      return hex || '#ffffff';
  }
}

// Draw a wire segment on a 2D context, honouring its kind. Restores dash state.
function strokeWire2D(ctx, x1, y1, x2, y2, hex, width) {
  const s = wireStroke(hex);
  const w = s.thin ? Math.max(1, width - 1.5) : width;
  ctx.lineCap = 'round';
  if (s.outline) {                       // darker underlay for pale wires
    ctx.setLineDash([]);
    ctx.strokeStyle = s.outline;
    ctx.lineWidth = w + 2;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.strokeStyle = s.base;
  ctx.lineWidth = w;
  ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
  if (s.dash) {                          // yellow dashes over green → Y/G stripe
    ctx.setLineDash([5, 5]);
    ctx.strokeStyle = s.dash;
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.lineCap = 'butt';
}

class TransformerCanvas {
  constructor(canvasEl) {
    this.el     = canvasEl;
    this.ctx    = canvasEl.getContext('2d');
    this.config = null;

    // live state (updated from app.js)
    this.appState       = 'IDLE';
    this.activePrimary  = null;
    this.activeSecondary = null;
    this.relayStates    = {};
    this.currentVoltage = null;
    this.expectedVoltage = null;

    // particles
    this.particles  = [];
    this.animFrame  = null;
    this._lastActive = false;

    this._startLoop();
  }

  // ── public API ───────────────────────────────────────────────────────

  setConfig(cfg) {
    this.config = cfg;
  }

  setLiveState({ appState, activePrimary, activeSecondary, relayStates, currentVoltage, expectedVoltage }) {
    this.appState        = appState        ?? this.appState;
    this.activePrimary   = activePrimary   ?? null;
    this.activeSecondary = activeSecondary ?? null;
    this.relayStates     = relayStates     || {};
    this.currentVoltage  = currentVoltage  ?? null;
    this.expectedVoltage = expectedVoltage ?? null;
  }

  resize(w, h) {
    this.el.width  = w;
    this.el.height = h;
  }

  // ── animation loop ───────────────────────────────────────────────────

  _startLoop() {
    const loop = () => {
      this._render();
      this.animFrame = requestAnimationFrame(loop);
    };
    this.animFrame = requestAnimationFrame(loop);
  }

  stop() {
    if (this.animFrame) cancelAnimationFrame(this.animFrame);
  }

  // ── rendering ────────────────────────────────────────────────────────

  _render() {
    const { el, ctx } = this;
    const W = el.width, H = el.height;
    if (W === 0 || H === 0) return;

    ctx.clearRect(0, 0, W, H);
    this._drawBg(W, H);

    if (!this.config) {
      this._drawPlaceholder(W, H);
      return;
    }

    this._computeLayout(W, H);
    this._drawGrid(W, H);

    // Scale the diagram (core + windings + particles) to fit when the core has
    // been extended past the canvas. Overlays below stay at screen scale.
    const s = this.diagramScale || 1;
    ctx.save();
    if (s !== 1) {
      ctx.translate(W / 2, H / 2);
      ctx.scale(s, s);
      ctx.translate(-W / 2, -H / 2);
    }
    this._drawCore();
    this._drawAllWindings();
    this._updateParticles();
    this._drawParticles();
    ctx.restore();

    this._drawVoltage(W, H);
    this._drawPassFail(W, H);
  }

  _drawBg(W, H) {
    this.ctx.fillStyle = C.bg;
    this.ctx.fillRect(0, 0, W, H);
  }

  _drawGrid(W, H) {
    const ctx = this.ctx;
    ctx.strokeStyle = C.grid;
    ctx.lineWidth = 1;
    const step = 40;
    ctx.beginPath();
    for (let x = 0; x <= W; x += step) { ctx.moveTo(x, 0); ctx.lineTo(x, H); }
    for (let y = 0; y <= H; y += step) { ctx.moveTo(0, y); ctx.lineTo(W, y); }
    ctx.stroke();
  }

  _computeLayout(W, H) {
    this.W = W;
    this.H = H;
    this.coreX  = W / 2 - 15;
    this.coreW  = 30;

    // Core grows with winding count so windings keep a fixed vertical slot
    // instead of cramming into a fixed-height core.
    const count = Math.max(
      (this.config?.primary   || []).length,
      (this.config?.secondary || []).length,
      1,
    );
    const SLOT  = 80;                          // vertical space per winding
    const baseH = H * 0.72;
    this.coreH  = Math.max(baseH, count * SLOT);
    this.coreY  = (H - this.coreH) / 2;        // centered (negative when extended)

    // When the extended core is taller than the canvas, scale the diagram down
    // so it stays fully visible (HUD overlays are drawn unscaled).
    this.diagramScale = Math.min(1, (H * 0.92) / this.coreH);

    this.colLX  = this.coreX - 130;  // primary col center x
    this.colRX  = this.coreX + this.coreW + 130; // secondary col center x
  }

  _drawCore() {
    const { ctx, coreX, coreY, coreW, coreH } = this;

    // main core rectangle
    ctx.fillStyle = C.core;
    ctx.strokeStyle = C.coreStroke;
    ctx.lineWidth = 2;
    this._roundRect(coreX, coreY, coreW, coreH, 4);
    ctx.fill();
    ctx.stroke();

    // lamination lines
    const lamCount = 8;
    ctx.strokeStyle = C.coreStroke;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.6;
    for (let i = 1; i <= lamCount; i++) {
      const y = coreY + (coreH / (lamCount + 1)) * i;
      ctx.beginPath();
      ctx.moveTo(coreX + 4, y);
      ctx.lineTo(coreX + coreW - 4, y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // CORE label
    ctx.fillStyle = C.labelMuted;
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('CORE', coreX + coreW / 2, coreY + coreH + 16);

    // section labels
    ctx.fillStyle = C.labelMuted;
    ctx.font = '11px "JetBrains Mono", monospace';
    ctx.textAlign = 'right';
    ctx.fillText('PRIMARY', this.colLX + 20, coreY - 12);
    ctx.textAlign = 'left';
    ctx.fillText('SECONDARY', this.colRX - 20, coreY - 12);
    ctx.textAlign = 'left';
  }

  _drawAllWindings() {
    const { config, coreY, coreH } = this;
    const primaries   = config.primary   || [];
    const secondaries = config.secondary || [];

    const assignY = (list) => {
      const count = list.length;
      const spacing = coreH / (count + 1);
      return list.map((w, i) => ({
        winding: w,
        cy: coreY + spacing * (i + 1),
        h:  Math.min(spacing * 0.78, 160),
      }));
    };

    for (const { winding, cy, h } of assignY(primaries))   this._drawWinding(winding, 'left',  cy, h);
    for (const { winding, cy, h } of assignY(secondaries)) this._drawWinding(winding, 'right', cy, h);
  }

  _drawWinding(w, side, cy, h) {
    const { ctx, coreX, coreW, colLX, colRX } = this;
    const cx     = side === 'left' ? colLX : colRX;
    const coreEx = side === 'left' ? coreX : coreX + coreW;
    const y1 = cy - h / 2;
    const y2 = cy + h / 2;

    const isActive = w.id === this.activePrimary || w.id === this.activeSecondary;
    const coilColor = this._windingColor(w, isActive);
    const lw        = isActive ? 5 : 3;

    // lead lines (coil → core) — drawn in the winding's configured wire colours.
    // Start lead uses wire_color_start, end lead uses wire_color_end (each
    // falling back to the generic wire_color, then the idle default). When the
    // winding is active the lead glows in its own colour so live feedback stays.
    // Leads drawn in the winding's configured wire colours, honouring each
    // colour's kind (Y/G stripe, Clear thin/outlined, White outlined).
    const startWire = w.wire_color_start || w.wire_color || null;
    const endWire   = w.wire_color_end   || w.wire_color || null;
    if (isActive) { ctx.shadowBlur = 10; ctx.shadowColor = wireStroke(startWire || C.leadIdle).base; }
    strokeWire2D(ctx, cx, y1, coreEx, y1, startWire || C.leadIdle, lw);
    strokeWire2D(ctx, cx, y2, coreEx, y2, endWire   || C.leadIdle, lw);
    ctx.shadowBlur = 0;

    // coil
    ctx.strokeStyle = coilColor;
    ctx.lineWidth = isActive ? 6 : 4;
    if (isActive) {
      ctx.shadowColor = coilColor;
      ctx.shadowBlur  = 14;
    }
    this._drawCoilPath(cx, y1, y2, side, 5);
    ctx.shadowBlur = 0;

    // start pin
    const startPinColor = this._pinColor(w, 'start', isActive);
    this._drawPin(cx, y1, startPinColor, isActive);

    // end pin
    const endPinColor = this._pinColor(w, 'end', isActive);
    this._drawPin(cx, y2, endPinColor, isActive);

    // tap nodes
    const tapCount = (w.taps || []).length;
    const outDir   = side === 'left' ? -1 : 1;   // outward — away from the core
    const NODE_OFF = 34;                          // tap node distance out from coil
    w.taps && w.taps.forEach((tap, i) => {
      const tapY  = y1 + (y2 - y1) * ((i + 1) / (tapCount + 1));
      const nodeX = cx + outDir * NODE_OFF;       // node pulled out from the coil
      const tapActive = tap.relay_b != null && this.relayStates[String(tap.relay_b)];
      const tapWire  = tap.wire_color || null;
      const tapColor = tapActive ? C.tapActive : (tapWire ? wireStroke(tapWire).base : C.tapIdle);

      // straight connector: tap node → coil, drawn like a main lead (colour +
      // kind) but a touch thinner (2.5px vs the leads' 3px) so taps read clearly.
      if (tapActive) {
        ctx.setLineDash([]);
        ctx.strokeStyle = 'rgba(0,229,255,0.6)';
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(nodeX, tapY); ctx.lineTo(cx, tapY); ctx.stroke();
      } else {
        strokeWire2D(ctx, nodeX, tapY, cx, tapY, tapWire || 'rgba(120,140,170,0.5)', 2.5);
      }

      // tap → core connection: dotted coloured line
      ctx.lineWidth = tapActive ? 2 : 1;
      ctx.lineCap = 'round';
      ctx.setLineDash([1, 5]);
      ctx.beginPath(); ctx.moveTo(cx, tapY); ctx.lineTo(coreEx, tapY); ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineCap = 'butt';

      // tap node (sits out from the coil)
      ctx.fillStyle = tapColor;
      ctx.beginPath();
      ctx.arc(nodeX, tapY, 6, 0, Math.PI * 2);
      ctx.fill();
      if (tapActive) {
        ctx.shadowColor = C.tapActive;
        ctx.shadowBlur  = 8;
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      // In line with the tap wire, laid out outward from the node:
      //   ●—— [pin] [label]
      ctx.textAlign = side === 'left' ? 'right' : 'left';

      // wire number = the tap's relay number (pin == relay) — nearest the node
      let outX = nodeX + outDir * 8;
      if (tap.relay_b != null) {
        ctx.fillStyle = tapActive ? C.tapActive : C.labelMuted;
        ctx.font = 'bold 10px "JetBrains Mono", monospace';
        ctx.fillText(`${tap.relay_b}`, outX, tapY + 3);
        outX += outDir * 24;
      }

      // tap label — outboard of the wire number
      const tapLabel = tap.label || `${tap.voltage}V`;
      ctx.fillStyle = tapActive ? C.tapActive : C.labelMuted;
      ctx.font = '10px "JetBrains Mono", monospace';
      ctx.fillText(tapLabel, outX, tapY + 3);
    });

    // winding label — placed between the coil and the core, inside the two leads
    ctx.font = `${isActive ? 'bold ' : ''}12px "JetBrains Mono", monospace`;
    ctx.fillStyle = isActive ? C.labelActive : C.labelMuted;
    const labelX = (cx + coreEx) / 2;
    ctx.textAlign = 'center';
    ctx.fillText(w.id, labelX, cy - 6);
    ctx.font = '11px "JetBrains Mono", monospace';
    ctx.fillText(`${w.voltage}V`, labelX, cy + 10);

    // Wire numbers = the node relay numbers (pin == relay), derived live. The
    // energizing winding's mains wires have no relay → "EN+" / "EN-".
    // Drawn IN LINE with the lead they label; lit while that relay is energized.
    const isEnergizing = this.config?.auto_matrix?.energize_winding === w.id;
    const startTxt = isEnergizing ? 'EN+' : (w.relay_a != null ? `${w.relay_a}` : '');
    const endTxt   = isEnergizing ? 'EN-' : (w.relay_b != null ? `${w.relay_b}` : '');
    ctx.font = 'bold 11px "JetBrains Mono", monospace';
    ctx.textAlign = side === 'left' ? 'right' : 'left';
    ctx.textBaseline = 'middle';
    if (startTxt) {
      const rlOn = w.relay_a != null && this.relayStates[String(w.relay_a)];
      ctx.fillStyle = rlOn ? C.pinActive : C.labelMuted;
      ctx.fillText(startTxt, side === 'left' ? cx - 2 : cx + 2, y1);
    }
    if (endTxt) {
      const rlOn = w.relay_b != null && this.relayStates[String(w.relay_b)];
      ctx.fillStyle = rlOn ? C.pinActive : C.labelMuted;
      ctx.fillText(endTxt, side === 'left' ? cx - 2 : cx + 2, y2);
    }
    ctx.textBaseline = 'alphabetic';

    ctx.textAlign = 'left';
  }

  _drawCoilPath(cx, y1, y2, side, turns) {
    const dir   = side === 'left' ? -1 : 1;
    const amp   = 20;
    const steps = turns * 36;
    const ctx   = this.ctx;

    ctx.beginPath();
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const y = y1 + t * (y2 - y1);
      const x = cx + dir * amp * Math.sin(t * turns * Math.PI * 2);
      if (i === 0) ctx.moveTo(x, y);
      else          ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  _drawPin(x, y, color, isActive) {
    const ctx = this.ctx;
    ctx.fillStyle = color;
    if (isActive) {
      ctx.shadowColor = color;
      ctx.shadowBlur  = 10;
    }
    ctx.beginPath();
    ctx.arc(x, y, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = isActive ? 'rgba(255,255,255,0.25)' : 'rgba(0,0,0,0.4)';
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.shadowBlur = 0;
  }

  _windingColor(w, isActive) {
    if (!isActive) return C.coilIdle;
    if (this.appState === 'PASS') return C.coilPass;
    if (this.appState === 'FAIL') return C.coilFail;
    return C.coilActive;
  }

  _pinColor(w, which, isActive) {
    const relay = which === 'start' ? w.relay_a : w.relay_b;
    const on    = relay != null && this.relayStates[String(relay)];
    if (on) return this.appState === 'FAIL' ? C.pinFail : C.pinActive;
    // Tint the pin with the lead's configured wire colour when present.
    const wire = which === 'start'
      ? (w.wire_color_start || w.wire_color)
      : (w.wire_color_end   || w.wire_color);
    if (wire) return wire;
    return isActive ? C.pinActive : C.pinIdle;
  }

  // ── particles ────────────────────────────────────────────────────────

  _updateParticles() {
    const active = this.activePrimary != null && this.activeSecondary != null;

    if (!active) {
      if (this._lastActive) this.particles = [];
      this._lastActive = false;
      return;
    }

    // spawn initial particles
    if (!this._lastActive) {
      this.particles = Array.from({ length: 6 }, (_, i) => ({
        progress: i / 6,
        speed: 0.007 + Math.random() * 0.004,
      }));
    }
    this._lastActive = true;

    const { colLX, colRX, H } = this;
    const midY = H / 2;

    for (const p of this.particles) {
      p.progress = (p.progress + p.speed) % 1;
      p.x = colLX + (colRX - colLX) * p.progress;
      p.y = midY;
    }
  }

  _drawParticles() {
    if (!this.particles.length) return;
    const ctx = this.ctx;
    ctx.shadowColor = C.particleFill;
    ctx.shadowBlur  = 10;
    ctx.fillStyle   = C.particleFill;
    for (const p of this.particles) {
      if (p.x == null) continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.shadowBlur = 0;
  }

  // ── overlays ─────────────────────────────────────────────────────────

  _drawVoltage(W, H) {
    if (this.currentVoltage == null) return;
    const ctx = this.ctx;
    const bw = 150, bh = 32;
    const bx = W / 2 - bw / 2;
    const by = H - 50;

    ctx.fillStyle = '#161b22';
    ctx.strokeStyle = 'rgba(0,255,136,0.3)';
    ctx.lineWidth = 1;
    this._roundRect(bx, by, bw, bh, 4);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = C.coilActive;
    ctx.font = 'bold 16px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText(`${this.currentVoltage.toFixed(3)} V`, W / 2, by + 21);
    ctx.textAlign = 'left';
  }

  _drawPassFail(W, H) {
    const s = this.appState;
    if (s !== 'PASS' && s !== 'FAIL') return;
    const ctx = this.ctx;
    const color = s === 'PASS' ? C.coilPass : C.coilFail;
    const bw = 200, bh = 60;
    const bx = W / 2 - bw / 2;
    const by = H / 2 - bh / 2;

    ctx.fillStyle = s === 'PASS' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    this._roundRect(bx, by, bw, bh, 8);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = color;
    ctx.font = 'bold 28px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText(s, W / 2, H / 2 + 10);
    ctx.textAlign = 'left';
  }

  _drawPlaceholder(W, H) {
    const ctx = this.ctx;
    ctx.fillStyle = '#374151';
    ctx.font = '13px "JetBrains Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText('Select a transformer to view diagram', W / 2, H / 2);
    ctx.textAlign = 'left';
  }

  // ── util ─────────────────────────────────────────────────────────────

  _roundRect(x, y, w, h, r) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
}
