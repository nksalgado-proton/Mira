# Handoff — restore the editor's image-rotate buttons (regression)

Branch: **main**. Read CLAUDE.md. Small regression fix — restore a
dropped control; no new backend.

## The bug

The photo editor lost the buttons that rotate the **picture** 90°
(distinct from the crop-box rotate, which still works). The backend is
fully intact:

- `mira/ui/edited/adjustment_surface.py::AdjustmentSurface.rotate_image(delta)`
  rotates the image by ±90°, resets the crop/box to the new frame, calls
  `render_now()`, and fires `self.changed.emit("rotation")`.
- `mira/ui/pages/editor_page.py` already handles `kind == "rotation"` on
  that signal and persists `adj.rotation = int(self._surface._rotation)`
  (photo + video adjustment paths both covered).

But **nothing calls `rotate_image` anymore** — the bottom-panel buttons
that drove it were removed in the "dense control tier" rework
(`a4c2a12`). The method is now dead UI-wise. The crop-box rotate buttons
(`↺ 90°` / `90° ↻` / `Reset`, wired to `_box_rotate`) live in the Crop
group and are NOT the same thing — leave them as-is.

## The fix

Re-add an **image-rotate** control to the editor's bottom adjustment
panel, wired to the existing method:

- Two buttons, clearly labelled so they're not confused with the crop-box
  rotate — e.g. **"Rotate photo ↺"** / **"Rotate photo ↻"** (or a small
  labelled "Rotate photo" group with ↺ / ↻).
  - `↺` → `self._surface.rotate_image(-90)`
  - `↻` → `self._surface.rotate_image(90)`
- Place them in the bottom panel where they used to be (a sensible spot
  is its own small row near Crop/Aspect, but visually separate from the
  Crop group's box-rotate row). Use the surface's existing button factory
  (`self._btn(...)`) / design-system buttons so styling matches.
- No host changes needed: `rotate_image` already emits `changed("rotation")`
  and editor_page already persists it. Just verify the wiring end-to-end
  after adding the buttons (rotate → `adj.rotation` updates → survives
  reload / export).

## Tests

- Clicking the new image-rotate buttons calls `rotate_image(±90)` and the
  surface `_rotation` advances 0→90→180→270→0 (cw) and the reverse (ccw).
- After a rotate, the editor commits `kind == "rotation"` and
  `Adjustment.rotation` reflects the surface `_rotation` (assert via the
  existing adjustment-commit seam).
- The buttons are present in the editor bottom panel (a simple
  widget-exists check guards against another silent drop).

Run the editor/adjustment suites, then full `verify.bat`.

## Spec

Add a one-line note to **spec/59** (edit surface) that the bottom panel
carries an image-rotate (whole-picture 90°) control distinct from the
crop-box rotate, so a future chrome pass doesn't drop it again. Update
with the commit (CLAUDE.md).

## Commit + push (on main)

```
fix(edit): restore the picture-rotate buttons in the editor bottom panel

rotate_image(±90) + adj.rotation persistence were intact, but the buttons
that call rotate_image were dropped in the dense-control-tier rework
(a4c2a12), leaving no way to rotate the whole picture (the crop-box
rotate is separate). Re-add a labelled image-rotate ↺/↻ pair wired to
AdjustmentSurface.rotate_image; host persistence already handles the
changed("rotation") signal.
```

Then `git push` on `main`.
