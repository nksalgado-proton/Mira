# spec/05 — UI standards (affordances, hints, translation)

**Registered 2026-05-30 (Nelson). Expanded 2026-06-08** with rules that
previously lived only in memory (§3b titled `QGroupBox`, §4d sidebar-vs-menu,
§5.3 Qt overlay `singleShot`, §5.4 cluster routing, §5.5 phase-default
state, §5.6 clear-marks pattern, §5.7 Day Grid back-refresh).

Non-negotiable UI rules every widget admitted to `mira/ui` must satisfy.
The UI is reassembled downstream (charter §4 step 7) from legacy "lego" parts
+ newly authored ones — these standards are the **admission test** for each
part before it is bound to the gateway and let into the clean UI. Several are
carried forward from the legacy app (they are among its good parts); all are
restated here so the clean UI enforces them from its first widget. Charter
§5.8 makes them constitutional. Build-status: **registered, enforce at every
admission.**

---

## 1. Clickable affordance — every clickable control

- **Pointing-hand cursor on hover.** Applied by an app-level event filter
  (`mira/ui/base/clickable_cursor.py`, vendored from legacy) — Qt's QSS `cursor`
  property proved unreliable on Windows, so the cursor is set in code via an event
  filter. A widget type opts in by being listed in `CLICKABLE_TYPES`.
- **A hint (tooltip) describing what a click does**, shown on hover. Mandatory — not
  optional polish.
- **Visual states** — visible border + hover + pressed + disabled — live in QSS
  (`assets/themes/{light,dark}.qss`), added to **both** themes in the same commit. No
  inline `setStyleSheet` in widget code; a widget opts into a styled role via
  `setObjectName("<Role>")`.
- **`QListWidget` rows:** the cursor on a list applies list-wide, not per-row, so any
  list with clickable rows calls `setCursor(QCursor(PointingHandCursor))` in its
  `__init__` (the event filter cannot reach individual rows).

## 2. Editable affordance — every editable field

- **I-beam (text) cursor on hover.**
- **A hint (tooltip)** describing the field's purpose and expected input. Every
  editable field carries one — the same load-bearing discipline as the cursor.

## 3. Hints everywhere

Every interactive widget — clickable or editable — has a tooltip. Treated as
load-bearing as the cursor and theme-role conventions, not as a nice-to-have.
(Carried from `[[ui_editable_fields_need_hints]]`.)

## 3b. Form layout — titled `QGroupBox` over label-beside-input

**Every text field / combo / file picker / spin / similar on a form wraps in a
titled `QGroupBox`. The field name is the group box title — never a stray
`QLabel` placed beside an input.** (Nelson 2026-06-06; carried from
`[[feedback_titled_groupbox_over_label]]`.)

Why:

- The title is **inside** the visual frame of the input, so the label-input
  pairing reads as one unit and never desyncs visually under layout pressure
  (label wrapping, RTL languages, text expansion §4).
- Tooltips attach to the group box (single hint per field, single hover target)
  rather than competing across label + input.
- QSS targets the role uniformly with one selector per role.

QSS roles already shipped in both themes:
- **`FilterRailGroup`** — the compact group box for filter chips / narrow rails.
- **`FormFieldGroup`** — the primary group box for full-form inputs (per-day
  rows, dialog forms, settings panels).

A widget opts in via `setObjectName("FormFieldGroup")` (or `FilterRailGroup`)
on the `QGroupBox`; never inline `setStyleSheet`.

## 4. Translation-ready from the first widget (i18n)

v1 ships **En + Pt**; **Es** in v1.1 (CLAUDE.md invariant #5). To keep translation a
*fill-in-the-catalog* task forever, never a rewrite, the new UI obeys:

- **Every user-visible string goes through `tr()`** — labels, button text,
  window/dialog titles, **tooltips/hints**, placeholder text, menu items, status
  messages, error text. No raw literal reaches `setText` / `setToolTip` /
  `setPlaceholderText` / `setWindowTitle` / `setTitle` / `addItem` / … .
- **No concatenation of translated fragments.** One full sentence per `tr()` with
  positional placeholders (`tr("Exported %1 photos to %2").arg(n).arg(name)`), because
  word order differs across languages.
- **Plurals via Qt's plural form** (`tr("%n photo(s)", "", n)`), never a manual
  `if n == 1`.
- **Layouts tolerate text expansion** — Pt/De run ~30% longer than En; no fixed-width
  labels that clip, let widgets size to content.
- **Locale-aware formatting** for dates/numbers via Qt locale, not hand-built strings.
- **No translatable text baked into images/icons.**
- **Catalog workflow:** strings extracted to `.ts`, translated, compiled to `.qm`; a
  CI/lint check flags raw literals reaching user-visible setters (CLAUDE.md asks for
  this — wire it for the new UI from the start).

## 4b. Interaction requirements — **every dialog and surface** (Nelson 2026-05-30)

These are **global** (Nelson: *"requirements … observed for other dialogs as well"*),
not page-specific. They first surfaced on `create_event_page` but bind on every new-app
surface and on every reused legacy dialog before it is admitted (§6).

- **Never a silent lag — busy cursor.** Any operation that can stall the UI for a
  perceptible moment wraps the blocking call in a wait cursor
  (`QApplication.setOverrideCursor(Qt.WaitCursor)` … `restoreOverrideCursor()` in a
  `finally`); for a *synchronous* call, `processEvents()` once after setting it so it
  paints before the loop blocks. A frozen window with a normal cursor reads as a crash.
- **Prefer off-thread + progress** for anything that can run longer than ~1 s (a big
  card scan, an ingest copy, an export, a backup mirror): a worker + determinate
  progress, window stays responsive. The wait cursor is the *minimum*; progress is the
  target for long jobs. **Use the one helper — `mira.ui.base.progress.run_with_progress`** —
  do not hand-roll threads/dialogs per surface (Nelson: *"I don't want to repeat this in
  every new surface"*). It runs the work off-thread behind a modal progress dialog and
  returns `(ok, result_or_error)`; the work takes a `progress(done, total, message)`
  callback and touches no widgets. Engines expose a `progress` callback param for it
  (e.g. `run_ingest(..., progress=...)`). Already applied to the ingest scan + copy.
- **Tables/lists with variable content are user-resizable** — columns set
  `QHeaderView.ResizeMode.Interactive` (drag any column); the trailing column stretches
  to fill so there's no dead gap. Content must never be unreadably clipped with no way
  to widen it.
- **Per-day timezone cascades forward.** Wherever a surface collects a timezone per trip
  day (the ingest plan, the plan editor, the adjust-event-TZ / camera-clock dialogs),
  changing one day's timezone propagates to **all following days** (trips share a zone
  until a border crossing — set once, override only at the change). Guard against the
  re-entrant signals a programmatic set fires.

## 4c. Layout robustness + High-DPI — **every surface** (Nelson 2026-05-31)

Nelson's concern (verbatim spirit): *the dread of building the Nuitka exe, running it on the
notebook, and finding the buttons eat the whole window so the photos can't be seen — and that
nothing can be done because it should have been designed in from the start.* This makes layout
robustness a **first-class, designed-in property**, enforced per widget as it's admitted — not
patched at the end. (Note: the Nuitka exe renders **identically** to `python -m mira.ui`
from source — there is no layout surprise that appears only in the exe, so testing from source
on the notebook fully de-risks it.)

**The design target (frozen 2026-05-31, Nelson):** **1920×1080 at 125–150% Windows scaling.**
Effective working area floor ≈ **1280×720** (at 150%). Every surface must be usable and
photo-first at that effective size. Design to this floor; everything larger is free.

**Rules (admission-tested):**
- **The photo/content canvas gets the stretch; chrome is bounded.** On any photo-display
  surface the image area takes `stretch=1`; toolbars/button rows are fixed/minimum height and
  never expand to crowd the canvas. Verify the photo is the dominant region at the 1280×720
  floor. (Reinforces §5 "predefined zones — TOP+BOTTOM only".)
- **Nothing clips with no escape.** A page whose content can exceed the viewport height lives in
  a `QScrollArea` (or its action rows wrap), so a small window scrolls instead of truncating
  controls or the canvas. Button strips that can overflow horizontally must wrap or scroll.
- **No fixed pixel sizes that don't scale.** Avoid hardcoded `setFixedSize`/large fixed widths
  for anything holding text or chrome; let widgets size to content (also serves §4 text
  expansion). Spacing/margins modest so they don't dominate at the floor resolution.
- **High-DPI is configured at the app entry.** `QGuiApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)`
  is set **before** the `QApplication` is created (`mira/ui/app.py`) so 1.25/1.5 scale
  factors render proportionally instead of rounding (rounding is what makes chrome jump/overflow
  at 125–150%). Qt6 High-DPI pixmap scaling is on by default; don't disable it.
- **Type sizing in QSS uses point sizes (`pt`), not large hardcoded `px`**, so the OS scale
  factor applies. (Per-user font-size/family customization is a parked decision — spec/15 §4b —
  but the *baseline* must already scale correctly with the OS.)
- **Verification is part of done.** Each surface is checked at the target — run
  `python -m mira.ui` on the notebook (or resize the window to ~1280×720) and confirm the
  canvas is dominant and no control is clipped. Recorded in the unit's definition-of-done
  (spec/15 §3).

## 4d. Chrome rules — sidebar vs menu

**When a navigation / action set could live in EITHER a sidebar OR a menu bar,
pick the menu bar.** (Nelson 2026-06-06; carried from
`[[feedback_maximize_canvas_space]]`.)

A sidebar costs 200+ px of permanent canvas; a menu bar costs ~30 px and a
click. The canvas matters more — MC is a photography tool, photo surfaces need
photo dominance.

**Scope — narrow.** This is specifically about the sidebar-vs-menu choice. It
does NOT mean "minimize all chrome everywhere":

- Slim header strips that carry load-bearing identity / state context are fine
  — they earn their pixels.
- Filter / search rows at the top of a panel are fine — one row relating to
  the content below.
- Per-surface toolbars (pick surface, edit surface) are fine — they're the
  surface's own controls, not a navigation channel.
- Modals, popovers, dialogs are free at rest.

**Concrete history (2026-06-06):** spec/46 Slice 1 cycled three sidebar designs
(sticky rail, contextual collapsible, menu-button variant) before Nelson said
"kill the sidebar entirely, use menus". Net canvas gain ≈ 240+ px per surface
— visible immediately on a real event. Don't re-litigate.

## 5. Carry-forward conventions (unchanged, restated for the clean UI)

### 5.1 QSS is the single source of visual treatment

(Legacy docs/16.) Roles via `setObjectName`; light + dark themes always updated
together; no inline `setStyleSheet` in widget code.

Recent additions to the role catalog (the QSS files are the canonical list;
this is a discoverability aid):

- **Video Picker (surface 11)** — `#VideoTransport` (the strip card under the
  stage), `#VideoScrubber` (position slider), `#VideoTime` (bold tabular time
  readout), `#VideoVolume` (volume slider), `#VideoMuteToggle` (clickable mute
  button with the line-icon family's `GLYPH_VOLUME` / `GLYPH_VOLUME_MUTED`
  glyphs; carries a dynamic `[muted="true"]` selector for the dimmed state),
  `#VideoSpeed` (speed selector — extends the base `QComboBox`),
  `#VideoDurationChip` / `#VideoVisitedEye` / `#VideoExportedChip` /
  `#VideoBigPlay` (stage overlays riding the PhotoViewport). Every clickable
  role carries hover · pressed · disabled affordances + pointing-hand cursor
  per §1.

### 5.2 Keyboard nav + fullscreen on every photo-display surface

(`[[feedback_keyboard_and_fullscreen_required]]`) — minimum viable, not
polish. Arrows + Space + Esc for navigation; F / F11 for fullscreen.

### 5.3 Loading overlays use `QTimer.singleShot(0, work)` (Windows Qt quirk)

(`[[feedback_qt_overlay_pattern_defer_via_singleshot]]`.) Windows Qt won't
paint a child overlay while the originating click handler is still on the
stack. The ONLY reliable pattern:

```
1. show the overlay
2. QTimer.singleShot(0, do_work)
3. return from the click handler
```

The handler returns → Qt's event loop drains → the overlay paints → only THEN
`do_work` fires on the next tick. Without the `singleShot`, the work blocks
the loop and the user sees a frozen unchanged window.

Tests can bypass via an instance-level flag that fires `do_work` synchronously.

### 5.4 Cluster routing — clusters open in grid, units in single

(`[[design_rule_clusters_open_in_grid]]`, Nelson 2026-06-04 + extended
2026-06-06.) Applies to Day Grid clicks AND Previous/Next nav buttons. Both
route by item kind, identically across every Pick / Edit surface:

- **Cluster** (focus/exposure bracket, burst, video-moments) → opens in **grid
  mode**.
- **Unit** (individual photo, individual video, snapshot) → opens in **single
  mode** (photo or video viewer).

The two nav buttons live in the SAME positions on every cull/pick surface
(`populate_nav_row(with_buckets=False)`) and step through the day's flat
(cluster + unit) item list. Bucket-level edge buttons retired.

**Terminology: cluster, not bucket** at the user-facing layer. (Internal code
still uses `bucket_*` table names for historical schema reasons.)

### 5.5 Phase-default state — `default_state_for` reader, no untouched indicator

(`[[feedback_phase_default_state_is_wired]]` +
`[[feedback_no_untouched_status_users_see_default]]`, Nelson 2026-06-05.)

- **Route un-decided items through `default_state_for(settings, phase)`.**
  NEVER hardcode `'skipped'`. Consumers (pills, pick_pool_ids, sync_picked,
  navigator fold) all read through the single resolver.
- **Untouched items render in the phase-default colour.** No neutral rings,
  no separate "untouched" state shown to the user. `cell_color_for_item(...,
  default_state=...)` is the single colour resolver.

User-mental-model contract: "I made the P/D call, it sticks; if I didn't call
it, the system applied my phase default."

### 5.6 Clear-marks button pattern ("Start a new pass…")

(`[[feedback_clear_marks_button_pattern]]`, Nelson 2026-06-09.) For any phase
that tracks per-item visited ticks (✓ marks):

- `gateway.clear_visited_for_phase(phase)` clears the ticks for a phase.
- The navigator widget exposes a `show_clear_marks_button` flag (opt-in per
  consumer).
- A signal fires when clicked; the host page wires a handler that calls the
  gateway clear and reloads the day grid.

Replicate the same triplet (flag + signal + handler) for any new phase that
gains ✓ ticks. Don't reinvent.

### 5.7 Day Grid back-refresh — touched-items set across surface session

(`[[feedback_back_refresh_track_touched_items]]`, Nelson 2026-06-08.) Day-grid
surfaces that arrow-step between items reassign `_current_bucket` on every
step — so a naive "refresh current bucket on Back" only catches the LAST
bucket the user touched.

Required pattern (reuse across every Day Grid surface):

- **Per-session `_items_touched_in_surface: set[str]`** — accumulates item ids
  the user touched anywhere during the surface session.
- **On Back, refresh the UNION of buckets** the touched items belong to (not
  just `_current_bucket`).
- **Mirror DB visited state into the in-memory cell** before reproject — so
  the freshly-set ✓ tick shows even before the day grid re-renders from the
  DB.

Used by `PickHostPage`; must also be used by `EditHostPage` (and any future
Day-Grid host).

## 6. Where this binds in the rebuild

This list is the **admission test** for any widget admitted to `mira/ui`
— newly authored OR ported from legacy (charter §5.2). Before a widget is
bound to the gateway and admitted, it must pass:

- §1 — pointing-hand cursor on clickables; visible border + hover + pressed +
  disabled states in BOTH QSS themes.
- §2 — I-beam on editables.
- §3 — tooltip on every interactive widget.
- §3b — form inputs in titled `QGroupBox` (FilterRailGroup / FormFieldGroup
  role), no label-beside-input.
- §4 — every user-visible string (including the tooltip) through `tr()`; no
  fragment concatenation; plurals via Qt's plural form.
- §4b — busy cursor on any lag; off-thread + progress for >1s via
  `run_with_progress`; user-resizable tables; per-day TZ cascade where
  applicable.
- §4c — photo-first canvas (image area `stretch=1`, chrome bounded), nothing
  clips with no escape, no fixed pixel sizes for chrome, type sizing in `pt`.
  Verified at 1280×720 floor before signoff.
- §4d — sidebar-vs-menu choice goes to menu.
- §5 — QSS role discipline (light + dark together); keyboard + fullscreen on
  photo surfaces; `singleShot(0)` for overlays; cluster routing convention;
  `default_state_for` reader + phase-default colouring; clear-marks pattern
  for ✓-tick phases; touched-items set for Day Grid back-refresh.

The cursor event-filter, the QSS role catalog, the `tr()` discipline, the
titled-`QGroupBox` roles, the `run_with_progress` helper, the
`default_state_for` reader, and the touched-items pattern are all vendored
into `mira/ui/base` at the very start of the UI reassembly so every
subsequent widget inherits them for free.
