# 129 — Days list: keep the capture-distribution block fully visible; compress the status bars instead

**Status: PROPOSED (Nelson 2026-06-23). In the Days list, each `DayRow` has a
right-side **capture-distribution** block (the `_CaptureSpark` 24-hour
density chart) that should ALWAYS be fully visible. When the dialog is too
narrow, that block is clipped instead — the wrong thing compresses. Cause:
the row is `_left_wrap` (fixed 98px) · `center` (title + status bars,
stretch 1) · separator · `meta_wrap` (spark, fixed 168px), but `center`'s
**minimum width** is too high to yield — every bar row carries a fixed 60px
label + the `StageProgress` track + a fixed 96px count (~156px hard floor).
Below a threshold width `center` can't shrink, the row overflows, and the
rightmost widget (the spark) clips. Fix: make the **status-bars column the
compressible element** (shrink the track, then the labels) and keep the
distribution block fixed + always visible. One layout change in
`mira/ui/pages/days_lists_page.py::DayRow`. No data/behaviour change.**

## 1. The fix

Protect the distribution block; let the bars give up width first.

- **`meta_wrap` (spark) stays fixed at 168px and must never clip.** Keep
  `setFixedWidth(168)`; ensure the row's layout treats it as fixed/reserved
  (it already is).
- **Make `center` genuinely compressible** so it absorbs the width
  reduction:
  - **`StageProgress` track:** give it a small `minimumWidth` (e.g. ~24px)
    and a horizontal size policy that shrinks (`Expanding`/`Ignored`), so the
    track visibly compresses (it already has `addWidget(bar, 1)`).
  - **Lower the per-bar-row hard floor:** the fixed `lab.setFixedWidth(60)`
    and `count_label.setFixedWidth(96)` set ~156px of unshrinkable width per
    row. Allow these to shrink under pressure — e.g. switch to a small
    `setMinimumWidth(0)` with elision on the label, and let the count column
    shrink/elide — so `center`'s minimum width drops well below the spark's.
  - Ensure `center` is added with stretch (it is, `row.addLayout(center, 1)`)
    and its container can shrink below its size hint.
- **Compression order:** the **track** compresses first; only at very narrow
  widths do the label / count elide. The spark never participates.

## 2. Result

- At normal/wide widths: unchanged (bars at full proportions, spark at 168).
- As the dialog narrows: the status-bar **tracks** shrink (then labels/count
  elide if needed); the **capture-distribution block stays fully visible**
  at its fixed width — exactly the desired behaviour.
- No horizontal clipping of the spark at any reasonable width.

## 3. Acceptance

- Narrowing the Days list never clips the right-side distribution chart; the
  status bars compress instead.
- The distribution block keeps its fixed width and full content at all
  widths down to a sensible floor (`_left_wrap` 98 + minimal bars + spark
  168).
- Wide layout is visually unchanged.
- Applies in Pick / Edit / Export DayRow variants (all share this layout).

## 4. Tests

- `tests/test_day_row_layout.py` — at a constrained row width, the spark
  (`meta_wrap`) keeps width 168 and is fully visible while the bars column's
  width is reduced; at wide width both are at full size. (Assert via
  `sizeHint`/`minimumWidth` of the bar track vs the fixed spark, and that
  `center.minimumWidth < meta_wrap.width`.)
- Regress the existing DayRow build tests (Pick/Edit/Export variants render).
