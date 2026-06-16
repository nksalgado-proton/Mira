# Task C — UI surfaces: DC list, New Cut dialog, Cuts list, flat grid, play

**Owns:** `mira/ui/pages/new_cut_dialog.py`, `mira/ui/pages/share_cuts_page.py`,
`mira/ui/shared/new_cut_dialog_adapter.py`, `mira/ui/shared/pool_detail_page.py`
(→ rename), `mira/ui/shared/cut_detail_page.py`,
`mira/ui/shared/cut_session_page.py`, `mira/ui/shared/cut_play.py`,
`mira/ui/shared/separator_card.py`. **Depends on:** B's gateway seam.
**Read first:** spec/81 (§1–§5), spec/80 §2 (reconciled dialog flow),
spec/61 (§3 Cuts list, §5 consuming).

## The reframing (spec/81)

The dialog today (`new_cut_dialog.py`) has a 3-way **Build mode**
(keep_all / weed_out / pick_in) + a **live/pinned badge** + a **POOL** section.
spec/81 collapses this: **there is no live Cut.** A live result is a saved
**DC**; a **Cut is always frozen**.

## Build

1. **New Cut dialog → "compose a DC, then optionally pin":**
   - **Source section** (rename POOL → "Source / Dynamic Collection"): the
     chips + `+` / `−` / `∩` operator controls compose the DC algebra over the
     operand inventory from B (`#exported` + every existing **DC and Cut**) —
     **all three operators available**, evaluated left-to-right; grouping is by
     adding a saved DC as an operand (no bracket UI — spec/81 §2). Live
     "source: N files" readout via `dc_probe`. Let the composed DC be **named + saved**
     (reuse `core/cut_names.py` live tag transform) so it's reusable as an
     operand.
   - **Pin choice (replaces Build mode):** two trim modes — **Weed out**
     (start all-in) / **Pick in** (start all-out) — plus **Keep all** (pin with
     no session). All three produce a **frozen Cut**. **Remove the live/pinned
     badge.**
   - Keep: Style + media filters (event-level only — no camera filter), Timing &
     Music, separators toggle (default ON), Load/Save template, live match count.
   - **Overlays control (spec/81 §3.1):** a multi-select of provenance fields
     (when / where / how¹ / how²; "none" = off) + a mode toggle
     (embedded / burn-in) defaulting from settings. **Scope-aware defaults:**
     separators ON / overlays OFF for event Cuts; flip for cross-event (Task D).
     Show a one-line hint of what the overlay will read (e.g. "date · location ·
     lens · exposure").
   - Update `new_cut_dialog_adapter.py` + the `cut_info()` payload to emit a DC
     ref/expr + pin mode (drop `build_mode`/`live`).
2. **DC list surface** — DCs are managed/shown **separately** from Cuts (a DC is
   not playable/exportable). Either a tab/section on the Share page or a sibling
   list: shows tag · source expr · live count; actions = edit, use-as-source,
   **Pin → New Cut**. (Confirm placement at the shape checkpoint.)
3. **Cuts list** (`share_cuts_page.py`, spec/61 §3): `#exported` always present
   (it is the base DC, not a row anyone made); user Cuts below with tag · count
   · duration · music · exported status. No live/pinned badge.
4. **Pin session chrome** (`cut_session_page.py`): the Picker reused on the DC's
   resolution, separate ledger, live budget line (green/amber/red) in the
   export-progress slot.
5. **Flat grid + play** (`cut_detail_page.py`, `cut_play.py`,
   `separator_card.py`, spec/61 §5): true show order, separators at day
   boundaries, Play all / Export all, per-item open/export, Play on videos.
   **Play draws overlays live** on each frame (non-destructive, from B's shared
   formatter) when the Cut has overlays on. Export action surfaces the **target
   as a defaulted, editable field** (spec/81 §5) — never reads a stored path off
   the Cut.
6. **Rename** `pool_detail_page.py` → `dc_detail_page.py` and purge "pool" from
   UI identifiers + all user-facing strings (which go through `tr()`).

## Constraints

- One-way deps: UI imports gateway + `core/` only.
- **QSS only**, roles via `setObjectName` present in both `light.qss` +
  `dark.qss`; clickables get border/hover/pressed/disabled + pointing-hand
  cursor (app-level event filter). No inline `setStyleSheet`.
- Every control has a hint (spec/05).

## Done when

- `launch.bat` flows: compose+save a DC → pin (each mode) → Cut appears →
  flat grid in show order with separators → Play rehearsal → Export to a
  defaulted target. `verify.bat` green incl. any UI/model tests.
- No "pool", "build mode", "live Cut", or live/pinned badge anywhere in the UI
  or strings. Shape checkpoints (§README) cleared with Nelson.

## Out of scope

Cross-event entry (the events-screen band) + full filter surface (Task D).
