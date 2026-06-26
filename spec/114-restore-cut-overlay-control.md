# 114 — Restore the overlay control in the Cut composition dialog

> **Simplified by spec/153 (2026-06-26):** the Off / Embedded / Burn-in
> mode combo described here is **removed**. The overlay control is now just
> the field flags (When / Where / Camera / Exposure) — overlays are on when
> ≥1 flag is checked, off when none; `overlay_mode` is fixed at `embedded`
> internally. Burn-in is retired (no pixel renderer). See spec/153.

**Status: SHIPPED (Nelson 2026-06-22). `NewRecipeContext` gained
`overlay_field_options` / `overlay_mode` / `overlay_fields` (mirrors the
spec/106 music pattern). `NewRecipeDialog._build_overlay_box` renders, in
the Runtime row beside music + aspect: a `RuntimeOverlayModeCombo`
(Off / Embedded / Burn-in → `None`/`"embedded"`/`"burn_in"`) + multi-select
`#OverlayFieldChip` pills (inherit `#PillToggle`, so the spec/113 strong-
checked QSS lights active fields for free). `_on_overlay_field_toggled`
reads checked keys in canonical `OVERLAY_FIELDS` order (stable round-trip
regardless of click order); Off greys the chips but keeps their checks
(flip back to Embedded restores picks). `presentation_payload()` emits
`overlay_mode` + `overlay_fields` when opted in, omits when Off (adapter
reads omission as Off). Hosts: `share_cuts_page._dialog_kwargs` supplies
the `(key, tr(label))` vocab + edit-Cut prefill (`cut_overlay_fields`);
`events_page._pin_cross_event_dc` threads it (and the music sanity-check)
into the cross-event context. 12 tests in `tests/test_new_recipe_overlay.py`
(emit/prefill/clear; canonical order survives click order; Off omits keys;
`create_cut` + `cut_overlay_fields` round-trip; `export_cut` writes embedded
IPTC vs burns pixels; `create_cross_event_cut` persists; cross-event dialog
identical under `INVENTORY_LIBRARY`; untouched-overlay schema exact). Full
`verify.bat` 4508 passed + 24 quarantine (the `test_focus_keeper` flake is
the same pre-existing colocated-Qt-contamination issue; a `tr`-import
regression in `events_page` was caught + fixed pre-commit). Original
proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). Verified: `NewRecipeDialog` has
**no overlay control at all** (`grep overlay` = 0). The export layer can
write overlays — `export_cut` reads `cut.overlay_mode` +
`gateway.cut_overlay_fields(cut)` and applies them as `embedded` (IPTC) or
`burn_in` (pixels), spec/81 §3.1 — but with no UI to set mode/fields,
overlays are never configured, so they are effectively dead in every
export. This restores the control so overlays work, and so spec/107's
`embedded`-overlay → PTE `Text` path has data to consume. Touches
`mira/ui/pages/new_recipe_dialog.py` + a verify pass on the
recipe→cut-draft adapter and `share_cuts_page` wiring. Sibling to the
shipped spec/106 (music); same "dropped Phase-4 field" root cause. No
data-model change (the fields already exist).**

## 1. The gap

`overlay_mode` + `overlay_fields` exist on the Cut and are honoured by the
exporters, and `create_cut`/`update_cut` (+ cross-event) accept them — but
the dialog dropped the control in the spec/90 rework and
`presentation_payload()` omits the fields, so they can never be set or
round-tripped. Result: overlays are off for every Cut, with no way to
enable them.

## 2. The fix

### A. Add an overlay control to `NewRecipeDialog`

In the presentation/runtime row, beside the music + aspect combos:

- **Overlay mode** — **Off** / **Embedded** (metadata, link-pure) /
  **Burn-in** (drawn into the pixels), from `cut.overlay_mode`.
- **Overlay fields** — a small multi-select of the facets to show:
  **camera**, **exposure** (ISO / aperture / shutter / focal length),
  **where** (city / country) — from the overlay-field vocabulary
  `share_cuts_page` already supplies via `_dialog_kwargs`; seeded from the
  cut's current `overlay_fields`. Disabled when mode = Off.

### B. Stop dropping the fields

`presentation_payload()` **emits** `overlay_mode` + `overlay_fields` (off /
empty when mode = Off); `composition()` carries them through.

### C. Verify carry-through (no new plumbing expected)

- `recipe_to_cut_draft` reads `presentation["overlay_mode"]` /
  `["overlay_fields"]` into the draft (add the one-line mappings if
  defaulted away).
- `share_cuts_page` new-Cut + edit-Cut handlers pass them to
  `create_cut`/`update_cut` and the cross-event equivalents (the gateways
  already take the args).

## 3. Acceptance

- The Cut dialog shows an overlay mode + field control; choosing
  Embedded/Burn-in + fields persists and pre-selects on edit (per-event and
  cross-event).
- Exporting an `embedded` Cut writes the where/camera/exposure IPTC; a
  `burn_in` Cut draws them into the pixels (existing export behaviour, now
  reachable).
- Mode = Off → no fields, no overlay, exactly today's behaviour.
- spec/107 then renders `embedded` overlays as per-slide PTE `Text`.

## 4. Tests

- `tests/test_new_recipe_overlay.py` — the control sets `overlay_mode` +
  `overlay_fields` into `composition()["presentation"]`; prefill
  pre-selects; mode = Off clears the fields; end-to-end → `create_cut`
  persists them and `export_cut` writes the overlay.
- Regress the `NewRecipeDialog` composition/presentation schema tests for
  the added fields.
