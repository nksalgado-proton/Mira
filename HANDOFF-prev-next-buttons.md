# Handoff — prev/next buttons → ghost style with Prev/Next label (Option A)

Branch: **main**. Read CLAUDE.md. Small UI-consistency fix; **no big test
run needed** — a quick smoke + an eyeball is enough.

## The problem

The Quick Sweep viewer's prev/next use `nav_arrow()`
(`mira/ui/design/media_nav.py`), which sets `objectName("MediaNavArrow")`
— but **there is no `#MediaNavArrow` rule in `redesign.qss`**, so the
button falls back to the raw native OS button and looks alien next to the
ghost-styled buttons. (The Picker and Editor already use ghost buttons,
but only a bare `‹` / `›` glyph.)

## The fix (Option A — chosen by Nelson)

Make every photo/video prev/next a **ghost-style rounded rectangle with a
chevron + label**: `‹ Prev` and `Next ›`, identical chrome to the other
buttons in the row.

1. `mira/ui/design/media_nav.py`:
   - Replace `nav_arrow(direction)` with a helper that returns a real
     ghost button:
     ```python
     from mira.ui.design.buttons import ghost_button
     def nav_button(direction="left", parent=None):
         if direction not in ("left", "right"):
             raise ValueError(...)
         label = "‹ Prev" if direction == "left" else "Next ›"
         btn = ghost_button(label, parent)
         btn.setCursor(Qt.CursorShape.PointingHandCursor)
         return btn
     ```
     `ghost_button` already sets the `#Ghost` role + styling, so no QSS is
     needed. Drop the old `#MediaNavArrow` objectName entirely (it was
     never styled).
   - Rename `nav_arrow` → `nav_button` and update the export in
     `mira/ui/design/__init__.py` (line ~80 import + line ~146 in
     `__all__`). (Keeping the old name as an alias is fine if you prefer
     less churn, but the name should stop implying a bare arrow.)
2. `mira/ui/pages/quick_sweep_page.py` (~lines 357, 398): use
   `nav_button("left")` / `nav_button("right")` (drop the `nav_arrow`
   import).
3. `mira/ui/pages/picker_page.py` (~289, 349) and
   `mira/ui/pages/editor_page.py` (~356, 380): replace
   `ghost_button("‹")` / `ghost_button("›")` with the same
   `nav_button("left")` / `nav_button("right")` so all three surfaces
   match exactly. Keep the existing tooltips and `clicked` wiring.

The `Filmstrip` class in `media_nav.py` is unrelated — leave it.

## Spec

`spec/63` §MediaNav currently mandates "floating ‹/› arrows … **No text
Previous/Next buttons**". This change deliberately overrides that
(inline ghost `‹ Prev` / `Next ›`, consistent with the rest of the row).
Update that note in spec/63 so spec and code agree.

## Verification (light — do NOT run the full suite for this)

- Quick build/launch and eyeball: Picker, Quick Sweep, and Editor all
  show matching `‹ Prev` / `Next ›` ghost buttons; the Quick Sweep ones
  no longer look like native OS buttons.
- One tiny smoke: `nav_button("left").text()` contains "Prev" and the
  widget is a `QPushButton` with objectName `Ghost`; `nav_button("bad")`
  raises `ValueError`.
- Run only the directly-related test file if one exists (media_nav /
  quick-sweep viewer); no need for the whole `verify.bat`.

## Commit + push (on main)

```
ui: prev/next buttons are ghost-style "‹ Prev" / "Next ›" (consistent)

nav_arrow rendered as an unstyled native button (#MediaNavArrow had no
QSS rule), so Quick Sweep's prev/next looked alien. Replace with a
ghost_button-based nav_button ("‹ Prev" / "Next ›") and use it in Quick
Sweep, Picker, and Editor so all photo/video surfaces match. spec/63
MediaNav note updated (inline labelled buttons, not floating arrows).
```

Then `git push` on `main`.
