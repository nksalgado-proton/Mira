# Handoff — bug fixes (2026-06-22)

The edits below are **already applied** to the source files. Your job:
**(1)** review the diffs, **(2)** run the listed test suites, **(3)** fix
anything that fails, **(4)** `commit` + `push`.

Branch: **XMC**.

> Context: these fixes were made in a session whose sandbox could not run
> the tests (the mount was desynced/corrupted). So automated
> verification is still pending — that is your first step.

---

## Bug #1 — Days-list tiles stretch vertically

**Symptom:** with few items (e.g. 1 day), the day card stretches
vertically and fills all available space. Height should be fixed.
General list problem, first noticed in Quick Sweep.

**Cause:** a `QVBoxLayout` distributes leftover space among
`Preferred`-policy widgets; with no trailing stretch the single row fills
the viewport. (`setAlignment(AlignTop)` alone is not enough — same
pattern already solved in `share_cuts_page`.)

**File:** `mira/ui/pages/days_lists_page.py`

- `DayRow.__init__`: after `setMinimumHeight(120)`, added
  `setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)`
  — the card never grows past its `sizeHint`.
- `DaysListsPage._render`: after the loop that adds the `DayRow`s, added
  `self._rows.addStretch(1)` (the `while ... takeAt(0)` at the top of
  `_render` already removes the previous stretch, so it doesn't
  accumulate).

---

## Bug #3 — Quick Sweep grid thumbnails show only the border

**Symptom:** in the Quick Sweep grid, several thumbnails don't render —
only the tile border shows.

**Cause:** `ThumbGrid` builds cells in chunks (50 synchronous, the rest
on `QTimer` ticks). `_apply_thumb_pixmap` poked
`self._thumb_widgets[idx]` directly; if a decode finished **before** the
cell at that index existed (later chunks), the pixmap was lost — and
because the grid's stored `ThumbGridItem` still had `pixmap=None`, the
cell was born empty (border only) when finally built.

**File:** `mira/ui/pages/days_grid_page.py`

- `_apply_thumb_pixmap`: now routes through
  `self._grid.set_pixmap(idx, pixmap)` (guarded by
  `0 <= idx < self._grid.count()`), which **also mutates the stored
  item**, so the pixmap survives the cell's later construction.

---

## Bug #4 — "Export now" should be "Back" in event-context Quick Sweep

**Symptom:** opening Quick Sweep during event creation / Collect, the
grid footer showed "↑ Export now" instead of "Back".

**Cause (regression):** commit `a4c2a12` ("standardize surfaces on flush
rail + title-bar Back/Help …") **removed the grid's own "‹ Back" button**
and moved Back to the app title bar. But event-creation Quick Sweep runs
inside a **modal with no title bar** (and standalone too), so Back
disappeared and only "Export now" was left (which, in paths mode, was a
**no-op**: `_on_export_clicked` returns early when `_eg is None`).

**Decision (Nelson):** distinguish the two modes in the grid footer:
- **Standalone** → a functional "↑ Export now" (copies the kept set to
  the destination folder and finishes).
- **Event context (Collect / new event)** → "‹ Back" (the kept photos
  flow on into the event; no export step).

**File:** `mira/ui/pages/days_grid_page.py`

- New signal `quick_sweep_export_requested = pyqtSignal()`.
- New state `self._qs_footer: Optional[str] = None` in `__init__`.
- `_build_ui`:
  - `self._export_btn.setVisible(False)` by default (only shows in the
    Export phase, via `_apply_phase_chrome`).
  - New `self._qs_export_btn` ("↑ Export now") → emits
    `quick_sweep_export_requested`; hidden by default.
  - New `self._qs_back_btn` ("‹ Back") → emits `back_requested`; hidden
    by default.
- `_apply_phase_chrome`: trailing Quick Sweep footer override — shows
  `_qs_export_btn` if `_qs_footer == "export"`, `_qs_back_btn` if
  `== "back"`; when in QS, hides `_export_btn` and `_new_pass_btn`.
- New method `set_quick_sweep_footer(variant)` (`"export"` / `"back"` /
  `None`) → sets `_qs_footer` and calls `_apply_phase_chrome`.
- `open_for_day`: resets `self._qs_footer = None` and was **reordered**
  so the identity is set **before** `_apply_phase_chrome()` (so the
  chrome reads the final identity).

**File:** `mira/ui/shell/main_window.py`

- In signal wiring: `self.days_grid_page.quick_sweep_export_requested`
  → `self._qs_finalize_via_back` (copies the kept set + finishes).
- `_qs_open_day` (**standalone** branch): after
  `set_phase_identity("collect")`,
  `self.days_grid_page.set_quick_sweep_footer("export")`.
- `_qs_open_day` (**per-event** branch): after
  `set_phase_identity("collect")`,
  `self.days_grid_page.set_quick_sweep_footer("back")`.
- `_run_quick_sweep_first` (new-event modal): after
  `grid_page.set_phase_identity("collect")`,
  `grid_page.set_quick_sweep_footer("back")`.

---

## Bug #2 — no action (intentional)

Moving cross-event Cuts to the Library page (spec/94 Phase 4a-iii) was
reviewed and **kept**. Do not touch.

---

## Verification (run before committing)

```powershell
verify.bat tests\test_days_grid_export_mode.py
verify.bat tests\test_days_grid_size_slider.py
verify.bat tests\test_days_grid_cycle_order.py
verify.bat tests\test_quick_sweep_viewer.py
verify.bat tests\test_quick_sweep_days_list_refresh.py
verify.bat tests\test_quick_sweep_clusters.py
verify.bat tests\test_days_lists_export_now.py
```

If those pass, run the full suite:

```powershell
verify.bat
```

**Review/test watch-outs:**
- `test_days_grid_export_mode.py` assumes, in the `export` phase (via
  `open_for_day(phase="export")`), `_export_btn` **visible** and
  `_new_pass_btn` hidden; and in `pick`, the opposite. The QS override
  only acts when `_qs_footer` is set, so those assertions should still
  hold — confirm.
- The smoke/preview path (`setDay` / `setItemsForPreview`) doesn't call
  `_apply_phase_chrome`; with `_export_btn` now hidden by default, adjust
  any preview test that expected "Export now" visible.
- Manually verify the 3 Quick Sweep flows: standalone (footer "Export
  now" copies + finishes), Collect of an existing event, and new-event
  creation (both with footer "Back").

## Spec

Consider a short note in whichever spec documents the Quick Sweep grid
chrome (likely `spec/70` Phase 3, or wherever `a4c2a12` recorded "Back in
the title bar"): the Quick Sweep grid keeps its own Back/Export in the
footer because it runs in a modal without the app title bar. Update the
spec together with the commit (CLAUDE.md: "Update the spec before (or
with) the code change.").

## Commit + push

Suggested message:

```
fix: Quick Sweep grid footer (Back vs Export), tile height, thumb decode

- days_lists: DayRow fixed-height (Maximum vsize policy) + trailing
  stretch so a single day no longer balloons to the viewport.
- days_grid: route async thumb pixmaps through ThumbGrid.set_pixmap so
  cells built in later chunks keep their decoded thumbnail (was: border
  only on Quick Sweep grids).
- days_grid: Quick Sweep footer — standalone shows functional "Export
  now" (copy + finish), event-context shows "Back". Restores the grid's
  own Back (regressed by a4c2a12 moving Back to the title bar, which the
  QS modal lacks). New quick_sweep_export_requested signal +
  set_quick_sweep_footer; host wires standalone/per-event/wizard.
```

Then: `git push` on branch XMC.
