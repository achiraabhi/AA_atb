# Spec: Visual Transformer Editor + Segment‑Pair Rule System

> **Paste this whole file into the other project's Claude chat.** It is a complete,
> implementation‑ready specification of two techniques from an existing transformer
> test‑bench app: (1) a **visual node‑based transformer editor** on a canvas, and
> (2) a **segment‑pair, ratio‑based rule‑making method** with auto‑generation and
> hybrid‑tolerance evaluation. Recreate these faithfully; adapt only the framing if
> the host app differs. Ask me before inventing behavior that isn't described here.

---

## 0. What you are building

A transformer is described by a JSON document. The user builds/edits it **visually
on a canvas** (drag windings, click nodes to assign relays/pins, pick wire colors),
then switches to a **Validate mode** to define **measurement rules** — each rule is a
pair of segments (an *excitation* segment and a *measurement* segment) whose expected
voltage is derived by **ratio**, checked with a **hybrid tolerance**. Rules can be
**auto‑generated** from the topology.

Two techniques to reproduce:
- **A. The visual editor** (topology design).
- **B. The rule‑making method** (segment‑pair ratio rules + auto‑generation + evaluation).

Reference stack in the original: **FastAPI (Python) backend + vanilla JS frontend with
Konva.js** for the editor canvas and a plain 2D `<canvas>` for the live dashboard
diagram. You may use any stack, but keep the **data model and semantics identical**.

> **⚠️ For the UI, build to Appendices A–C at the bottom of this file** — they are the
> authoritative reference: **A** = ASCII wireframes of every screen/panel, **B** = the
> real DOM structure (element IDs + classes), **C** = the CSS layout skeleton (exact
> column widths + flex rules). Sections §5–§6 explain the *behavior*; the appendices
> pin down the *layout*. When in doubt, match the appendices.

---

## 1. Transformer JSON data model

```jsonc
{
  "name": "Multi-Tap Primary 200-250V / Dual Secondary",
  "transformer_id": "multi_tap_200_250",     // stable slug (filename / PK)
  "type": "multi_tap_transformer",
  "rated_power_va": 1000,
  "rated_frequency_hz": 50,
  "notes": "...",

  "connection_style": { "connection_type": "core_link", "line_style": "solid", "line_color": "#1a1a1a" },

  "primary":   [ Winding, ... ],
  "secondary": [ Winding, ... ],

  "auto_matrix": {                 // drives auto rule generation
    "enabled": true,
    "energize_winding": "P1",      // which winding is energized (the reference)
    "energize_tap_index": 3        // null = full winding; N = energize at tap N
  },

  "ratio_rules": [ Rule, ... ]     // the segment-pair rules (see §4)
}
```

### Winding

```jsonc
{
  "id": "P1",                      // unique within the transformer
  "winding_type": "basic_winding", // basic_winding | tapped_winding (cosmetic)
  "start_pin": 1,                  // wire number of the START node (see §3)
  "end_pin": 17,                   // wire number of the END node
  "voltage": 230,                  // nominal voltage at full rated excitation
  "dot_polarity": true,            // winding orientation dot (display + polarity)
  "relay_a": 1,                    // A1 relay for START node (RL1-16, + probe) | null
  "relay_b": 17,                   // A2 relay for END node   (RL17-32, - probe) | null
  "meas_channel": -1,              // legacy ADC channel (-1 = unused)
  "can_energize": true,            // may this winding be the excitation source?
  "wire_color_start": "#2563eb",   // color of the start lead (see palette §3)
  "wire_color_end":   "#dc2626",   // color of the end lead
  "taps": [ Tap, ... ],
  "coords": {}                     // optional saved canvas position
}
```

### Tap

```jsonc
{
  "pin": 18,                       // wire number (== its relay number, see §3)
  "voltage": 200,                  // nominal voltage start→tap at full excitation
  "label": "200V",
  "relay_b": 18,                   // the tap's single relay (see relay model §2)
  "wire_color": "#16a34a",
  "meas_channel": 2                // legacy (ignored)
}
```

A tap has **one** relay (`relay_b`). It is measured **start→tap**: the winding's
start node paired with the tap node.

---

## 2. Relay / hardware model (adapt to your bench, keep the roles)

The physical board routes a voltmeter's **+ probe** and **− probe** to winding nodes.
Groups have **functional meaning** — they are not generic banks:

| Group | Relays | Role |
|-------|--------|------|
| **A1** | RL1–16  | Measurement winding **START** node → voltmeter **+** bus |
| **A2** | RL17–32 | Measurement winding **END / TAP** node → voltmeter **−** bus |
| Gate A | RL33    | firmware auto‑closes when any A1 relay is active |
| Gate B | RL34    | firmware auto‑closes when any A2 relay is active |
| **B**  | RL37–40 | **Energizing winding's TAP** nodes (excitation domain) |
| Gate B′| RL35,36 | firmware auto‑close when any Group‑B relay is active |

**Rules of the matrix:**
- A normal winding measurement closes **one A1 + one A2** relay → `[relay_a, relay_b, 33, 34]`.
- **Energizing winding**: its **main wires are external** (carry mains, never switched,
  start hard‑wired to the + bus) → `relay_a = relay_b = null`. Its **taps** ARE measured,
  through **Group B (37–40)**, closing only the tap's B relay → `[relay_b2, 35, 36]`.
- **Groups A and B are mutually exclusive.** Safety: ≤1 A1, ≤1 A2, ≤1 B at a time.
- Gates are **never sent by the PC** — the firmware closes them automatically.

---

## 3. Node addressing + pin/relay numbering (critical conventions)

**Node IDs** used everywhere in rules:
- `"P1"`          → winding **start** node
- `"P1:end"`      → winding **end** node
- `"P1:tap0"`     → winding's tap at **index 0** (`:tap1`, `:tap2`, …)

**Pin == relay number.** Each wire carries exactly one number, and that number **is**
the relay driving it (assigned together). So the wire on terminal `18` is driven by
relay `18` — nothing to cross‑reference. Pins are **derived from the relay**, shown
read‑only in the editor, and rendered **in line with the wire** on the diagram.

The **energizing winding's two mains wires have no relay**, so they show **`EN+`** and
**`EN-`** instead of a number (they are the only non‑numbered nodes).

**Wire‑color palette** (name → hex; each unique so a name can be printed on the wire):

```
Black #1a1a1a · Brown #7c4a1e · Red #dc2626 · Orange #ea580c · Yellow #eab308
Green #16a34a · Blue #2563eb · Violet #7c3aed · Grey #6b7280 · Y/G #65a30d
White #ffffff · Clear #cbd5e1
```
The editor prints the **color name** on each wire (start/end leads inside the winding;
tap colors in line with the tap, outboard of the node).

---

## 4. The Rule model (segment‑pair, ratio‑based) — technique B

A **Rule** validates one measured winding/tap against the energized reference. It is a
**pair of segments** plus tolerances:

```jsonc
{
  "id": "auto_1a2b3c4d",
  "excitation_segment":  { "node_a": "P1", "node_b": "P1:tap3", "nominal_voltage": 230 },
  "measurement_segment": { "node_a": "S1", "node_b": "S1:end",  "nominal_voltage": 115 },
  "tolerance_percent": 5.0,
  "minimum_absolute_delta": 0.1,   // hybrid-tolerance floor (volts)
  "measurement_type": "AC",
  "critical": true,
  "enabled": true,
  "_label": "P1 @ 230V tap → S1 (115V)"
}
```

- **`excitation_segment`** = which two nodes span the *applied* excitation, and its
  **nominal voltage** at full rated excitation. Usually the same for every rule in a
  transformer (a single global "active excitation").
- **`measurement_segment`** = which two nodes are measured, and the **nominal voltage**
  expected across them at full excitation.
- The **ratio** between the two nominals is what's actually tested — so a reduced
  excitation voltage still validates, because both scale together (see §6).

### Ratio math (the heart of it)

```
ratio_factor = applied_voltage / excitation_nominal_voltage
expected_out = measurement_nominal_voltage * ratio_factor
```

`applied_voltage` is entered by the operator at test time (they apply a **reduced**
voltage in production); `excitation_nominal_voltage` is the rule's excitation‑segment
nominal. Every measurement's expected value scales by the same `ratio_factor`.

### Hybrid tolerance (why it's not just a percentage)

```
deviation = |measured - expected|
window    = max( expected * tolerance_percent/100 , minimum_absolute_delta )
PASS  ⇔  deviation <= window
```

The **`minimum_absolute_delta`** floor keeps *adjacent* small voltages distinguishable
(e.g. an 11 V vs 13 V winding) even at loose percentage tolerances. Always apply the
`max()` of the percentage window and the absolute floor.

Optional extra check: **relative‑ordering validation** — after a full sweep, verify the
measured voltages preserve the nominal ordering (winding@11V measured < winding@13V
measured, …); flag inversions.

---

## 5. The Visual Editor — technique A

Two **modes** toggled in a toolbar: **Topology** (build the transformer) and
**Validate** (make rules). One canvas, one right‑hand **inspector** panel whose
contents swap by selection.

### Canvas rendering (Topology mode)
- A central **core** rectangle; **primary** windings drawn as sine‑coil columns on the
  left, **secondary** on the right.
- Each winding = a draggable **group** containing: coil, a **start node** (top) and
  **end node** (bottom) circle, **taps** as nodes along the coil, two **lead lines**
  (start/end) drawn in their wire colors, and text labels.
- **Wire number** printed *in line* with each lead = the node's relay number (or
  `EN+`/`EN-` for the energizing mains). **Color name** printed bold on each wire.
- Labels live **inside the winding group** so they move with it on drag and are
  destroyed on rebuild (never orphaned — a real bug we hit: labels kept in a separate
  layer duplicated on move; keep them in the group).
- The dashboard has a **read‑only animated** version of the same diagram (live relay
  glow, current‑flow particles) — **event‑driven redraw only while a test runs**, not a
  perpetual 60 fps loop (perf on weak devices).

### Editor interactions (Topology mode)
- Toolbar: `+ Primary`, `+ Secondary`, `+ Tap`, `Delete`, mode toggle, zoom, `Load`,
  `Save`.
- **Left‑click a winding** → inspector shows id, voltage, relay‑assign buttons (start→A1,
  end→A2), wire‑color dropdowns.
- **Left‑click a node** (start/end/tap) → **relay‑picker popup** filtered to the correct
  group (A1 = 1–16, A2 = 17–32, Group B = 37–40 for energizing taps). Assigning a relay
  **also writes the pin** (`pin == relay`), and the wire number updates **live** (derive
  the displayed number from the relay, not a stale stored pin).
- The **energizing winding** (the one named in `auto_matrix.energize_winding`) shows its
  main‑wire relay buttons as `— permanent —` (no relay), an explanatory note, and its
  **taps** pick from **Group B (37–40)**.
- Drag windings vertically to reposition; taps distribute along the coil.

### Relay‑picker
A popup grid of the group's relay numbers; already‑used relays are shown occupied/
disabled (a live "used‑relay registry" built from the current config prevents
double‑assignment). Selecting writes `relay_a`/`relay_b`/`tap.relay_b` **and** the pin.

---

## 6. Validate mode — the rule‑making workflow (technique B, UI)

The inspector switches to a **rules panel**:
- An **Active Excitation** card at the top: the single global excitation segment
  (node_a ↔ node_b + nominal voltage) that every new rule uses. Set it once.
- A scrollable **Measurement Rules** list (each item: `meas_a↔meas_b` + expected V +
  enabled toggle), a **count pill**, and **+ Add Measurement**.
- Selecting a rule opens a **segment editor**: an EXC section (nodes + nominal), a
  **ratio display**, a MEAS section (nodes + nominal), and **tolerance %** + **min Δ (V)**.

### Node‑pick state machine (how you "draw" a rule by clicking canvas nodes)
Clicking canvas nodes drives a small state machine. Phases:
`exc_a → exc_b` (define the excitation segment once) then, per rule,
`meas_a → meas_b` (define the measured segment). The canvas shows **pick hints**
("① click excitation node A", "③ click measurement node A"), highlights selectable
nodes with rings (blue for excitation phase, green for measurement), and auto‑continues
into `meas_a` for the next rule after one completes. `Esc` cancels a pick.

The picked node IDs (`"P1"`, `"P1:tap3"`, `"S1:end"`, …) are written straight into the
rule's `excitation_segment`/`measurement_segment`. Nominal voltages default from the
winding/tap `voltage`, editable in the inspector.

---

## 7. Auto‑generating rules from topology (the "matrix engine")

When the user clicks **Generate Rules** (or `auto_matrix.enabled`), build the full
measurement sweep from the topology — **no manual rules needed**. Given
`energize_winding` (+ optional `energize_tap_index`):

1. **Excitation reference** = the energizing winding (`node_a = ew`, `node_b = ew:end`
   or `ew:tapN`), nominal = that segment's voltage.
2. **Enumerate measurement points** in this order:
   - **Phase 1 — the energizing winding's own taps** (except the energize tap). Routed
     via **Group B**.
   - **Phase 2 — same‑side other windings**: full winding, then each tap.
   - **Phase 3 — other‑side windings**: full winding, then each tap.
3. For each point emit a Rule with:
   - `excitation_segment` = the reference (from step 1),
   - `measurement_segment` = `{node_a: winding, node_b: winding:end|:tapN, nominal_voltage: point.voltage}`,
   - `tolerance_percent`, `minimum_absolute_delta`, `enabled:true`, a human `_label`.
4. **Auto‑assign relays + pins** at the same time (walk windings; energizing winding first
   → its taps take Group B, its mains take none/`EN±`; each other winding start→A1,
   end→A2, taps→A2; **pin = relay**).

All generated rules are **ratio steps** (`is_ratio_step = true`) so expected voltage
scales with whatever excitation the operator applies at run time (§4 math).

---

## 8. Runtime evaluation (per measurement step)

```
1. break-before-make: open all relays
2. close the step's relays (SELECT commands; firmware closes gates)
3. hold energized ≥ ~2 s to settle
4. read the measured voltage (RMS magnitude)
5. expected = measurement_nominal * (applied_voltage / excitation_nominal)
6. PASS ⇔ |measured - expected| <= max(expected * tol%, min_abs_delta)
7. open relays
```

Persist per step: from/to nodes, measured, expected, tolerance, deviation %, pass/fail.
(In the original these land in both CSV/JSON logs and a SQLite `measurements` table.)

---

## 9. Minimal build order (recommendation)

1. **Data model** (§1) + node‑ID convention (§3) + validation of uniqueness/relay‑range.
2. **Editor canvas** (§5): render windings/taps from JSON; drag; node click → relay
   picker → write relay + pin; wire colors + names; live pin = relay.
3. **Rule model + ratio/tolerance engine** (§4) as pure functions (unit‑test them first —
   `compute_ratio`, `compute_expected`, `validate_measurement`).
4. **Validate‑mode UI** (§6): active‑excitation card, rules list, segment editor,
   node‑pick state machine.
5. **Auto‑generation** (§7): the matrix engine + auto relay/pin assignment.
6. **Runtime evaluation** (§8) + persistence.

## 10. Gotchas we actually hit (save yourself the debugging)

- **Wire labels must live in the winding's canvas group**, not a shared layer — else they
  orphan/duplicate on drag.
- **Derive the displayed wire number from the relay live**; don't read a stored pin that
  only updates on Generate Rules (it goes stale, e.g. stuck at an old default).
- **Hybrid tolerance floor is essential** — pure % lets adjacent small windings pass on
  each other's values.
- **Energizing winding is the asymmetric case**: mains wires external (no relay, `EN±`),
  but its **taps are still measured via Group B**. Getting this wrong is the #1 modeling
  error.
- **Groups A and B are mutually exclusive**; enforce in software even though firmware does.
- **Don't run the dashboard animation at 60 fps when idle** — event‑driven redraw only.

---

---

# Appendix A — UI wireframes (build to these)

All screens are **dark‑on‑light, monospace‑labelled**, three fixed regions:
left **META** panel (fixed **220 px**), center **CANVAS** (flex), right **INSPECTOR**
(fixed **248 px**), with a **toolbar** across the top. The inspector's contents swap by
selection **and** by mode.

### A1 — Editor screen (whole tab)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ TOOLBAR                                                                            │
│  WINDINGS [+ Primary][+ Secondary] │ NODE [+ Tap] │ [✕ Delete] │                   │
│  MODE [⬡ Topology][⚡ Validate] │ VIEW [＋][－][⊡] 100%      …spacer…  [📂 Load][💾 Save]│
├───────────────┬──────────────────────────────────────────────┬─────────────────────┤
│ META  (220px) │              CANVAS  (flex, Konva)           │ INSPECTOR  (248px)  │
│               │                                              │                     │
│ Name  [_____] │      P1                    S1                │  swaps by selection │
│ ID    [_____] │    EN+ ─┐┌coil┐        ┌coil┐┌─ 1            │  + mode:            │
│ Pow[__] Fq[__]│         ││    │═ core ═│    ││               │   · empty state     │
│ Notes [_____] │    EN- ─┘│    │        │    │└─ 17           │   · winding (A2)    │
│               │          └────┘        └────┘               │   · tap     (A3)    │
│ ── Generate ──│           │200V:18      │115V:23             │   · rules   (A4)    │
│ Energize [▼]  │           └tap           └end                │   · segment (A5)    │
│ Tap      [▼]  │                                              │                     │
│ Tol(%)  [ 5 ] │                                              │                     │
│ [ Generate ]  │        "Click to select · Drag winding to    │                     │
│               │         reposition · Scroll/Alt+drag zoom"   │                     │
└───────────────┴──────────────────────────────────────────────┴─────────────────────┘
```
- Toolbar buttons are small mono chips; `MODE` buttons are a toggle (one active).
- Primary windings render on the **left** of the core, secondary on the **right**.
- Each winding: sine **coil**, **start node** (top) + **end node** (bottom) circles,
  **taps** as circles along the coil, **lead lines** in wire colors, and text: the
  **wire number** in line with each lead (or `EN+`/`EN-`), the **color name** bold on
  the wire, plus id/voltage in the center gap.
- A floating **hint pill** sits bottom‑center of the canvas.

### A2 — Winding inspector (Topology mode, a winding selected)

```
┌ INSPECTOR (248px) ─────────────┐
│ [PRIMARY]  Winding             │  badge: blue=primary, cyan=secondary
│ ID       [ P1____________ ]    │
│ Voltage  [ 230___________ ]    │
│ Start node → A1 (1–16)         │
│  [    1    ] [ Blue     ▼]     │  ← relay-assign button (shows number) + color
│ End node → A2 (17–32)          │
│  [   17    ] [ Red      ▼]     │
│ ⚡ Energizing note (only if     │  (main wires show "— permanent —", no relay)
│    this is the energize winding)│
│ ───────────────────────────────│
│ [ + Add Tap ]     [ Delete ]   │  ← sticky bottom action row
└────────────────────────────────┘
```

### A3 — Tap inspector (a tap selected)

```
┌ INSPECTOR ─────────────────────┐
│ [TAP]  Tap Node                │
│ Label    [ 200V_________ ]     │
│ Voltage  [ 200__________ ]     │
│ Tap node → A2 (17–32)          │  label switches to "Group B (37–40)" for an
│  [   18    ]                   │  energizing-winding tap
│ Wire Color [ Green      ▼]     │
│ ───────────────────────────────│
│ [ Delete Tap ]                 │
└────────────────────────────────┘
```

### A4 — Validate mode, nothing selected (rules list)

```
┌ INSPECTOR (Validate) ──────────┐
│ ┌ EXC  Active Excitation [Set]┐│  card, blue tint
│ │  P1  ↔  P1:tap3    230V     ││  (or "Not set — click Set/a node")
│ └─────────────────────────────┘│
│ Measurement Rules         [ 7 ]│  ← header + count pill
│ ┌─────────────────────────────┐│
│ │▎S1 ↔ S1:end          115V   ││  active row = accent left-bar
│ │ S2 ↔ S2:end           24V ⋅off│ disabled row dimmed, "off" tag
│ │ P1 ↔ P1:tap0          200V  ││
│ │ …                           ││
│ └─────────────────────────────┘│  (list fills height, scrolls)
│ [ + Add Measurement ]          │
└────────────────────────────────┘
```

### A5 — Validate mode, a rule selected (segment editor, shown ABOVE the list)

```
┌ Segment Rule ──────────── [✓ On]┐
│ ┌ EXC Excitation ──── [Change] ┐│  blue-tint section
│ │  P1   ↔   P1:tap3            ││  node badges
│ │  Nominal Voltage [ 230 ]     ││
│ └──────────────────────────────┘│
│           ×0.50                  │  ← live ratio (measNom/excNom) pill
│ ┌ MEAS Measurement ── [Change] ┐│  green-tint section
│ │  S1   ↔   S1:end             ││
│ │  Nominal Voltage [ 115 ]     ││
│ └──────────────────────────────┘│
│ Tolerance(%) [ 5 ]  Min Δ(V)[0.1]│
│ [ Delete Rule ]                  │
└──────────────────────────────────┘
```

### A6 — Node-pick state machine (how a rule is "drawn" by clicking canvas nodes)

```
      ┌──────────── SET EXCITATION  (once per transformer) ────────────┐
      │   click node ─► exc_a        click node ─► exc_b               │
      └───────────────────────────────┬───────────────────────────────┘
                                       ▼
      ┌──────────────── PER MEASUREMENT RULE (repeat) ────────────────┐
      │   click node ─► meas_a       click node ─► meas_b             │
      │   → writes rule.measurement_segment, auto-continues to meas_a  │
      └───────────────────────────────────────────────────────────────┘
   • Canvas highlights pickable nodes with rings: BLUE = excitation phase,
     GREEN = measurement phase.  A hint bar shows "① click node A…", etc.
   • Esc cancels the active pick.
   • Picked node IDs ("P1", "P1:tap3", "S1:end") go straight into the segment.
```

### Relay-picker popup (shared, Topology mode)

Clicking a relay-assign button opens a popup grid of the group's numbers
(A1 = 1–16, A2 = 17–32, Group B = 37–40). Numbers already used elsewhere in the
config are shown **occupied/disabled** (a live used-relay registry). Selecting writes
`relay_a`/`relay_b`/`tap.relay_b` **and** the matching pin, and the canvas number
updates immediately.

---

# Appendix B — DOM structure (editor tab)

Real element IDs + classes from the reference app. Reproduce this tree (color
`<option>` lists trimmed to a comment). JS binds everything by these IDs.

```html
<div id="tab-editor" class="tab-pane">
 <div class="topo-chrome">

  <!-- TOOLBAR -->
  <div class="topo-toolbar">
   <span class="tb-section">WINDINGS</span>
   <button id="tool-add-primary"   class="tb-btn primary-tb">+ Primary</button>
   <button id="tool-add-secondary" class="tb-btn secondary-tb">+ Secondary</button>
   <span class="tb-sep"></span>
   <span class="tb-section">NODE</span>
   <button id="tool-add-tap" class="tb-btn" disabled>+ Tap</button>
   <span class="tb-sep"></span>
   <button id="tool-delete" class="tb-btn danger-tb" disabled>✕ Delete</button>
   <span class="tb-sep"></span>
   <span class="tb-section">MODE</span>
   <button id="tool-mode-topology" class="tb-btn mode-tb active">⬡ Topology</button>
   <button id="tool-mode-validate" class="tb-btn mode-tb validate-tb">⚡ Validate</button>
   <span class="tb-sep"></span>
   <span class="tb-section">VIEW</span>
   <button id="ed-zoom-in" class="tb-btn icon-tb">＋</button>
   <button id="ed-zoom-out" class="tb-btn icon-tb">－</button>
   <button id="ed-zoom-fit" class="tb-btn icon-tb">⊡</button>
   <span id="ed-zoom-pct" class="tb-zoom-pct">100%</span>
   <div style="flex:1"></div>
   <button id="editor-load-btn" class="tb-btn">📂 Load</button>
   <button id="editor-save-btn" class="tb-btn glow-tb">💾 Save</button>
  </div>

  <div class="topo-main">

   <!-- LEFT: metadata + Generate Rules -->
   <aside class="topo-meta">
    <div class="panel-hdr"><span class="panel-title">Transformer</span></div>
    <div class="topo-meta-fields">
     <div class="field"><label class="label">Name</label><input id="ed-name" class="input"></div>
     <div class="field"><label class="label">ID</label><input id="ed-id" class="input"></div>
     <div class="field-row">
      <div class="field"><label class="label">Power (VA)</label><input id="ed-power" type="number" class="input"></div>
      <div class="field"><label class="label">Freq (Hz)</label><input id="ed-freq" type="number" class="input"></div>
     </div>
     <div class="field"><label class="label">Notes</label><textarea id="ed-notes" class="input"></textarea></div>
     <div class="gen-rules-section">
      <div class="gen-rules-hdr"><span class="gen-rules-title">Generate Rules</span></div>
      <div class="field"><label class="label">Energize Winding</label>
        <select id="ed-energize-sel" class="input input-sm"><option value="">— Select winding —</option></select></div>
      <div class="field" id="ed-energize-tap-field" style="display:none"><label class="label">Energize Tap</label>
        <select id="ed-energize-tap" class="input input-sm"><option value="">Full winding</option></select></div>
      <div class="field"><label class="label">Tolerance (%)</label>
        <input id="ed-gen-tolerance" type="number" class="input input-sm" value="5"></div>
      <button id="ed-generate-rules-btn" class="btn btn-sm gen-rules-btn">Generate Rules</button>
      <div id="ed-gen-status" class="ed-gen-status hidden"></div>
     </div>
    </div>
    <div id="editor-error" class="editor-error hidden"></div>
   </aside>

   <!-- CENTER: Konva canvas -->
   <section class="topo-canvas-section">
    <div id="editor-konva-wrap" class="topo-konva-wrap">
     <div id="editor-konva-container" class="topo-konva-container"></div>
     <div id="rule-pick-hint" class="rule-pick-hint hidden"></div>
     <div class="topo-canvas-hint">Click to select · Drag winding to reposition · Scroll/Alt+drag to zoom/pan</div>
    </div>
   </section>

   <!-- RIGHT: inspector (one child visible at a time) -->
   <aside class="topo-inspector">

    <div id="inspector-empty" class="inspector-empty">
     <div class="inspector-empty-icon">⬡</div>
     <div class="inspector-empty-text">Select a winding or tap<br>to view properties</div>
    </div>

    <!-- winding inspector (Topology) -->
    <div id="inspector-winding" class="inspector-panel hidden">
     <div class="inspector-hdr"><span id="insp-badge" class="insp-badge">PRIMARY</span>
       <span class="panel-title">Winding</span></div>
     <div class="insp-fields">
      <div class="field"><label class="label">ID</label><input id="insp-wid" class="input"></div>
      <div class="field"><label class="label">Voltage (V)</label><input id="insp-voltage" type="number" class="input"></div>
      <div id="insp-energize-note" class="hidden">⚡ energizing winding…</div>
      <div class="field"><label class="label">Start node → <span class="col-a">A1 (1–16)</span></label>
       <div style="display:flex;gap:6px">
        <button id="insp-relay-a" class="relay-assign-btn" data-group="A" style="flex:1">None</button>
        <select id="insp-wire-color-start" class="input" style="flex:0 0 90px"><!-- color options --></select>
       </div></div>
      <div class="field"><label class="label">End node → <span class="col-b">A2 (17–32)</span></label>
       <div style="display:flex;gap:6px">
        <button id="insp-relay-b" class="relay-assign-btn" data-group="B" style="flex:1">None</button>
        <select id="insp-wire-color-end" class="input" style="flex:0 0 90px"><!-- color options --></select>
       </div></div>
     </div>
     <div class="insp-actions">
      <button id="insp-add-tap" class="btn btn-sm">+ Add Tap</button>
      <button id="insp-delete-winding" class="btn btn-sm btn-danger">Delete</button>
     </div>
    </div>

    <!-- segment-rule editor (Validate, a rule selected) -->
    <div id="inspector-rule" class="inspector-panel hidden">
     <div class="inspector-hdr"><span class="insp-badge insp-badge-rule">RULE</span>
      <span class="panel-title">Segment Rule</span>
      <label class="insp-enabled-row"><input id="insp-rule-enabled" type="checkbox" checked><span class="label">On</span></label>
     </div>
     <div class="insp-fields">
      <div class="segment-section segment-exc-section">
       <div class="segment-section-hdr"><span class="segment-badge exc-badge">EXC</span>
        <span class="segment-section-title">Excitation Segment</span>
        <button id="insp-rule-pick-exc" class="btn btn-sm">Change</button></div>
       <div class="segment-nodes-row"><span id="insp-rule-exc-a" class="rule-node-badge">—</span>
        <span class="node-sep">↔</span><span id="insp-rule-exc-b" class="rule-node-badge">—</span></div>
       <div class="field"><label class="label">Nominal Voltage (V)</label>
        <input id="insp-rule-nom-in" type="number" class="input input-sm"></div>
      </div>
      <div class="segment-ratio-row"><span id="insp-rule-ratio-display" class="segment-ratio-formula">—</span></div>
      <div class="segment-section segment-meas-section">
       <div class="segment-section-hdr"><span class="segment-badge meas-badge">MEAS</span>
        <span class="segment-section-title">Measurement Segment</span>
        <button id="insp-rule-pick-meas" class="btn btn-sm">Change</button></div>
       <div class="segment-nodes-row"><span id="insp-rule-meas-a" class="rule-node-badge">—</span>
        <span class="node-sep">↔</span><span id="insp-rule-meas-b" class="rule-node-badge">—</span></div>
       <div class="field"><label class="label">Nominal Voltage (V)</label>
        <input id="insp-rule-nom-out" type="number" class="input input-sm"></div>
      </div>
      <div class="field-row">
       <div class="field"><label class="label">Tolerance (%)</label><input id="insp-rule-tolerance" type="number" class="input"></div>
       <div class="field"><label class="label">Min Δ (V)</label><input id="insp-rule-min-delta" type="number" class="input"></div>
      </div>
     </div>
     <div class="insp-actions"><button id="insp-rule-delete" class="btn btn-sm btn-danger" style="width:100%">Delete Rule</button></div>
    </div>

    <!-- rules list (Validate, default) -->
    <div id="inspector-rules-list" class="inspector-rules-list hidden">
     <div id="active-exc-card" class="active-exc-card">
      <div class="active-exc-hdr"><span class="segment-badge exc-badge">EXC</span>
       <span class="active-exc-title">Active Excitation</span>
       <button id="btn-set-exc" class="btn btn-sm">Set</button></div>
      <div id="active-exc-display" class="active-exc-display">
       <span class="active-exc-unset">Not set — click Set or a canvas node</span></div>
     </div>
     <div class="insp-sub-hdr"><span>Measurement Rules</span><span id="rules-count" class="insp-sub-count">0</span></div>
     <div id="rules-list-items" class="rules-list-items"></div>
     <button id="insp-add-rule" class="btn btn-sm add-rule-btn">+ Add Measurement</button>
    </div>

    <!-- tap inspector -->
    <div id="inspector-tap" class="inspector-panel hidden">
     <div class="inspector-hdr"><span class="insp-badge insp-badge-tap">TAP</span>
      <span class="panel-title">Tap Node</span></div>
     <div class="insp-fields">
      <div class="field"><label class="label">Label</label><input id="insp-tap-label" class="input"></div>
      <div class="field"><label class="label">Voltage (V)</label><input id="insp-tap-voltage" type="number" class="input"></div>
      <div id="insp-tap-energize-note" class="hidden">⚡ energizing tap → Group B…</div>
      <div class="field"><label class="label">Tap node → <span id="insp-tap-relay-group" class="col-b">A2 (17–32)</span></label>
       <button id="insp-tap-relay-b" class="relay-assign-btn" data-group="B">None</button></div>
      <div class="field"><label class="label">Wire Color</label><select id="insp-tap-wire-color" class="input"><!-- color options --></select></div>
     </div>
     <div class="insp-actions"><button id="insp-delete-tap" class="btn btn-sm btn-danger">Delete Tap</button></div>
    </div>

   </aside>
  </div>
 </div>
</div>
```

**Mode/selection → which inspector child is visible:**

| State | visible child |
|-------|---------------|
| Topology, nothing selected | `#inspector-empty` |
| Topology, winding selected | `#inspector-winding` |
| Topology, tap selected | `#inspector-tap` |
| Validate, nothing selected | `#inspector-rules-list` |
| Validate, rule selected | `#inspector-rule` **and** `#inspector-rules-list` (editor above list) |

---

# Appendix C — CSS layout skeleton (fixed widths matter)

```css
/* editor tab fills its pane, column of [toolbar][main] */
.topo-chrome { display:flex; flex-direction:column; height:100%; overflow:hidden; }
.topo-toolbar { display:flex; align-items:center; gap:6px; padding:6px 10px;
                border-bottom:1px solid var(--border); flex-shrink:0; }
.tb-section { font-family:mono; font-size:10px; font-weight:700; text-transform:uppercase; }

/* three regions: 220 | flex | 248 */
.topo-main { display:flex; flex:1; min-height:0; overflow:hidden; }
.topo-meta { width:220px; flex-shrink:0; border-right:1px solid var(--border);
             display:flex; flex-direction:column; overflow:hidden; }
.topo-canvas-section { flex:1; min-width:0; display:flex; flex-direction:column;
             overflow:hidden; position:relative; }
.topo-konva-container { width:100%; height:100%; }        /* Konva mounts here */
.topo-inspector { width:248px; flex-shrink:0; border-left:1px solid var(--border);
             display:flex; flex-direction:column; overflow:hidden; }

/* inspector panels: header (fixed) + scroll fields + sticky action row */
.inspector-panel { display:flex; flex-direction:column; flex:1; overflow:hidden; }
.inspector-hdr   { display:flex; align-items:center; padding:8px 12px; border-bottom:1px solid var(--border); }
.insp-fields     { padding:10px 12px; overflow-y:auto; flex:1; display:flex; flex-direction:column; gap:8px; }
.insp-fields .relay-assign-btn { width:100%; text-align:left; }
.insp-actions    { padding:10px 12px; border-top:1px solid var(--border); display:flex; gap:6px; flex-shrink:0; }
.insp-actions .btn { flex:1; }

/* relay-assign button colors: green when an A1 relay is set, blue for A2 */
.relay-assign-btn { width:100%; padding:7px 10px; border:1px solid var(--border); border-radius:6px; }
.relay-assign-btn.assigned-a { border-color:var(--glow);   color:var(--glow); }   /* A1 */
.relay-assign-btn.assigned-b { border-color:var(--accent); color:var(--accent); } /* A2 */

/* validate mode: EXC section blue-tinted, MEAS green-tinted, ratio pill between */
.segment-exc-section  { background:rgba(37,99,235,0.07); border:1px solid rgba(37,99,235,0.22); border-radius:6px; padding:8px 10px; }
.segment-meas-section { background:rgba(5,150,105,0.07); border:1px solid rgba(5,150,105,0.22); border-radius:6px; padding:8px 10px; }
.segment-ratio-formula{ color:var(--glow); background:rgba(5,150,105,0.08); border:1px solid rgba(5,150,105,0.22); padding:3px 10px; border-radius:4px; }
.inspector-rules-list { flex:1 1 auto; min-height:120px; overflow-y:auto; padding:10px 12px 12px; border-top:1px solid var(--border); }
.rule-list-item       { display:flex; gap:6px; padding:6px 8px 6px 10px; border:1px solid var(--border); border-left:3px solid transparent; border-radius:4px; cursor:pointer; }
.rule-list-item.active{ border-left-color:var(--accent); background:rgba(2,132,199,0.07); color:var(--accent); }

/* theme variables (light "blueprint" theme) */
:root{ --bg:#e8ecf2; --panel:#f3f5f9; --card:#fff; --border:#cdd3df; --muted:#7b8baa;
       --text:#18243a; --primary:#2563eb; --accent:#0284c7; --glow:#059669;
       --warning:#d97706; --danger:#dc2626; }
```

Notes that make it look right:
- Everything is **IBM Plex Mono / Sans**, small (10–13 px), lots of thin 1 px borders.
- Panel titles/section headers: **bold, uppercase, letter-spaced, dark**.
- The canvas background is the light `--bg`; windings/labels are dark/colored.
- Badges: `PRIMARY` blue, `SECONDARY`/`TAP` cyan, `EXC` blue, `MEAS` green.

---

*End of spec. If the host project's domain differs (not transformers), keep the
**techniques** — visual node editor writing a JSON graph, segment‑pair ratio rules with
hybrid tolerance, and topology‑driven auto‑generation — and remap the vocabulary.*
