# 134 — Configurable photo-viewer overlay (When / Where / Camera / Exposure), like cuts

**Status: PROPOSED (Nelson 2026-06-23). The info overlaid on the Picker photo
view is **fixed** — a `PhotoExposureOverlay` pill fed a hard-coded exposure
caption (`mira.picked.exif_compare.caption_html` / `exposure_for_chip`). It
should be **user-configurable** with the **same field vocabulary as cut
overlays** (When / Where / Camera / Exposure). Add a Settings control that
picks which fields show on the photo viewer; the Picker (and the Editor photo
view, which has no overlay today) compose the pill from the selected fields
via the existing `core.cut_overlay` model. Touches `mira/settings/model.py`
(new setting), `mira/ui/base/settings_dialog.py` (the control),
`mira/ui/pages/picker_page.py` + `mira/ui/edited/adjustment_surface.py` (read
the setting + compose), and a small item→`FrameProvenance` resolver. Reuses
`core/cut_overlay.py`. No data-model change beyond one settings field.**

## 1. Reuse the cut overlay vocabulary

`core/cut_overlay.py` already defines the field set and the composer:
`OVERLAY_FIELDS = (FIELD_WHEN, FIELD_WHERE, FIELD_HOW1, FIELD_HOW2)` —
**When** (date/time), **Where** (city/country), **Camera** (how1: camera /
lens / flash), **Exposure** (how2: aperture / shutter / ISO / focal) — with
`compose_overlay_lines(fields, FrameProvenance)`. The photo viewer overlay
will drive off the **same** keys + composer, so cuts and the viewer speak one
vocabulary (and the spec/119 checkbox control style is shared).

## 2. The setting

- Add to `Settings` (`mira/settings/model.py`):
  `viewer_overlay_fields: list[str]` — the selected `OVERLAY_FIELDS` keys for
  the photo viewer. **Default `["how2"]`** (exposure only) so today's
  behaviour is unchanged until the user opts in. `[]` = overlay off.
- App-level (machine/user settings), not per-event — it's a viewing
  preference.

## 3. The Settings control

In the Settings dialog, a **"Photo viewer overlay"** row: multi-select
**checkboxes** (per spec/119, not pill toggles) labelled **When / Where /
Camera / Exposure**, mapped to `when / where / how1 / how2`, seeded from
`viewer_overlay_fields`, written back in canonical `OVERLAY_FIELDS` order.
A short hint: "Choose what to show over photos in the Picker and Editor."

## 4. Compose + render in the viewers

- **Item → `FrameProvenance` resolver** (shared helper): build a
  `FrameProvenance` for the on-screen item from its EXIF (camera, lens,
  aperture, shutter, ISO, focal) plus the event's *where* (city / country
  from the item's day). The Picker already reads EXIF (`read_exif_batch` /
  `_exif_cache`) and `exif_compare` already extracts exposure — extend that
  to a full `FrameProvenance` rather than an exposure-only caption.
- **`when` uses the TZ/clock-CORRECTED time**, matching cuts exactly:
  `when = item.capture_time_corrected or item.capture_time_raw` (the cut's
  `frame_provenance` does the same, event_gateway.py:1693). Never the raw
  EXIF `DateTimeOriginal` directly — the whole correction pipeline exists so
  the corrected time is the one shown. Raw is only the fallback when no
  correction was applied.
- **Picker:** replace the fixed caption feeding `PhotoExposureOverlay` with
  `compose_overlay_lines(settings.viewer_overlay_fields, prov)` →
  `overlay.set_html(...)`; empty selection (or no data) → `set_html("")`
  (hidden). Re-read the setting on settings change so the overlay updates
  live.
- **Editor:** add the same overlay pill to the Editor photo view
  (`AdjustmentSurface`) — it has none today — driven by the same setting +
  composer, so both viewers behave identically.

## 5. Acceptance

- A Settings control lets the user choose When / Where / Camera / Exposure
  for the photo viewer; the Picker overlay shows exactly those fields for the
  current photo (composed from its EXIF + the day's where).
- The Editor photo view shows the same configurable overlay.
- Default (`["how2"]`) reproduces today's exposure pill; clearing all hides
  the overlay; changing the setting updates both viewers without restart.
- Cut overlays are unaffected (shared vocabulary, separate setting).

## 6. Tests

- `tests/test_viewer_overlay_setting.py` — the setting round-trips the
  selected keys in canonical order; default is `["how2"]`.
- `tests/test_viewer_overlay_compose.py` — the item→`FrameProvenance`
  resolver fills the fields from EXIF + day where; **`when` comes from
  `capture_time_corrected` (not raw EXIF) and falls back to
  `capture_time_raw` only when corrected is absent**; `compose_overlay_lines`
  with `["when","how2"]` yields date + exposure lines; `[]` → empty (overlay
  hidden); a field with no data is omitted gracefully.
- `tests/test_picker_overlay_reads_setting.py` — the Picker pill reflects the
  setting (exposure-only default; when+where+camera+exposure when all
  checked); updates on settings change. Editor parity assertion.
