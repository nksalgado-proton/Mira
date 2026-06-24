# 119 — Cut dialog: style + overlay-field selectors must be real checkboxes

**Status: PROPOSED (Nelson 2026-06-23, third request). The style filters
(Macro / Landscape / …) and the embedded-overlay field selectors (When /
Where / Camera / …) in the Cut composition dialog use `pill_toggle`
QPushButtons, whose checked state is visually ambiguous — the user cannot
tell which are selected. Replace BOTH with real `QCheckBox` widgets, which
carry an OS-native, unmistakable check indicator. The sibling hardware
filter chips (camera / lens) share the identical widget + problem and are
converted in the same pass. Touches `mira/ui/pages/new_recipe_dialog.py`
only. No data-model change.**

## 1. Root cause of the repeated failures

`pill_toggle` (`mira/ui/design/chips.py`) returns a checkable QPushButton
with `objectName="PillToggle"`. But every caller in the Cut dialog
**overwrites** that object name right after:

- `_build_style_row` → `chip.setObjectName("StyleChip")` (line 2648).
- `_build_overlay_box` → `chip.setObjectName("OverlayFieldChip")` (line 3114).

So spec/113's strengthened `#PillToggle:checked` accent-fill rule **never
matched these chips** — their role is `StyleChip` / `OverlayFieldChip`, not
`PillToggle`. Three rounds of "make the active state visible" landed on a
selector that doesn't apply here. A checked QPushButton with a soft tint is
inherently easy to miss; a QCheckBox is not.

## 2. The fix — QCheckBox everywhere a filter/field is multi-selected

Replace the `pill_toggle` instances with `QCheckBox` in:

- **Style filters** (`_build_style_row`, `self._style_chips`).
- **Overlay fields** (`_build_overlay_box`, `self._overlay_field_chips`).
- **Hardware filters** (camera / lens chips, spec/90 §4) — same `pill_toggle`
  pattern, same ambiguity; convert for consistency so the whole Filters /
  Overlays surface reads one way.

Each becomes `QCheckBox(label)`; `setChecked(...)` for the seeded state;
`toggled.connect(...)` to the **same** handlers
(`_on_filter_chip_toggled` / `_on_overlay_field_toggled`). The dicts stay
keyed the same (`{key: QCheckBox}`); every read site already uses
`.isChecked()` (lines 3483, 3553, 3816, 3855, 3925, 4034, 4491), so those
need no change beyond the type. Keep the canonical `OVERLAY_FIELDS`
ordering in `_on_overlay_field_toggled` (stable round-trip regardless of
click order). `_sync_overlay_fields_enabled` keeps disabling the field
checkboxes when overlay mode = Off.

Layout: a horizontal row of checkboxes (or a compact flow) in place of the
pill row. Give them the existing checkbox QSS role used elsewhere in the
dialog (e.g. `DaysTableCheck`, as the Photos / Videos media checkboxes
already do at lines 2663 / 2668) so they match the dialog's other real
checkboxes.

## 3. Cleanup

- Drop the now-unused `pill_toggle` import in `new_recipe_dialog.py` if no
  other caller in the file remains. (Leave the factory itself — other
  dialogs, e.g. the Event Header, still use it legitimately.)
- The retired `#StyleChip` / `#OverlayFieldChip` QSS roles can be removed if
  nothing else references them (verify before deleting).

## 4. Acceptance

- The style filters, overlay fields, and camera/lens filters are real
  checkboxes; a checked one is unmistakable at a glance.
- Selecting/deselecting still drives the same probe / payload behaviour
  (filter pool count, `overlay_fields` round-trip in canonical order, edit
  prefill pre-checks the right boxes).
- Overlay mode = Off greys (disables) the field checkboxes but keeps their
  checks; flipping back to Embedded restores them.
- Works in both per-event and cross-event Cut dialogs.

## 5. Tests

- Update `tests/test_new_recipe_overlay.py` + `tests/test_cut_filter_visibility.py`
  (and any test asserting `QPushButton` / `#PillToggle` for these rows) to
  the QCheckBox type; assert `.isChecked()` reflects selection, canonical
  `overlay_fields` order survives click order, prefill pre-checks, Off
  disables-but-preserves.
- Regress the dialog composition / presentation-payload tests.
