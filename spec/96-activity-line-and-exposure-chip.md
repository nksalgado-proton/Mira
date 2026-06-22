# 96 — Always-on activity line + exposure-chip setting & content

**Status: SHIPPED (Nelson accepted 2026-06-22). Two independent
viewing-feedback changes: (1) the batch progress line is now permanent
and also reports background preview/proxy building; (2) the new
`show_exposure_overlay` roaming setting (default ON) gates the pill in
Picker + Quick Sweep, and the chip carries camera + exposure + file
type + file size. Touches `mira/ui/shell`, `mira/ui/media`,
`mira/ui/pages` (Picker + Quick Sweep), `mira/settings`, and
`mira/picked/exif_compare.py`. No charter-invariant or LOCKED-keymap
impact. Implementation landed in commit
[539596e](https://github.com/nksalgado-proton/Mira/commit/539596e).**

---

## Part 1 — Always-on activity line (background work feedback)

### What exists today

`mira/ui/shell/main_window.py` already mounts `self.batch_line`
(`BatchProgressLine`, `mira/ui/shell/batch_queue.py`) directly below the
menubar, bound to `self.batch_queue` (`BatchJobQueue`). Per spec/84 the
queue carries **both export operations and ingest/card copies**, so the
line already reports those. It **hides when idle** (`_sync` →
`setVisible(False)`).

The gap: **preview/proxy building is invisible.** The proxy builder
(`core.photo_proxy_cache.ProxyBuilder`) runs on its own background thread
and does NOT ride the batch queue, so opening a day (which seeds a big
proxy build) makes the app feel slow with no on-screen explanation.

### Change

1. **Always visible.** `BatchProgressLine` never hides. When nothing is
   running it shows a quiet idle state — "Ready" (muted styling).
2. **Report preview building.** The line also reflects the proxy
   builder's pending count. The builder already exposes
   `pending_count()` (thread-safe). It emits no Qt signal, so the line
   **polls** on a `QTimer` (~400 ms) and re-`_sync`s. Expose a public
   accessor on the UI photo-cache singleton, e.g.
   `photo_cache().proxy_pending_count() -> int` (delegates to
   `self._proxy_builder.pending_count()`); MainWindow hands that callable
   to the line (keep the line decoupled from the cache import).
3. **Message priority** in `_sync` (one line, one message):
   - A batch job running (export / ingest) → today's batch message (it's
     the active foreground-ish operation).
   - else previews pending `> 0` → "Creating previews — responses may be
     slower ({n} left)".
   - else → "Ready".
4. The progress **bar** stays meaningful for batch jobs (per-file
   progress as today); for the previews state, show the count in text
   (no determinate bar needed — an indeterminate/cleared bar is fine).

### Notes / invariants

- Don't block the GUI: the poll only reads an int under the builder's
  lock; no decode on the UI thread.
- The builder is Qt-free (charter inv. 8) — keep it that way; the poll
  lives in the Qt line, not the builder.
- "Ready" must be visually quiet (muted text, no accent) so a permanent
  line isn't noise.

---

## Part 2 — Exposure chip: ON/OFF setting + camera + source type/size

### What exists today

The chip is `PhotoExposureOverlay` (`mira/ui/media/photo_overlay.py`),
filled via `caption_html(exif)` (`mira/picked/exif_compare.py`). It shows
only **shutter · aperture · ISO · focal length**. It is set on the
single-photo views — **Picker** (`picker_page.py`) and **Quick Sweep**
(`quick_sweep_page.py`) — for every photo with usable EXIF (hidden on
videos / missing EXIF), and on grid tiles only for small compare grids
(≤ `GRID_CAPTION_MAX` = 4). **There is no ON/OFF setting** — it is always
on for photos in those views.

### Change

1. **Setting `show_exposure_overlay: bool` (default `True`).** This is a
   viewing preference, not hardware-bound, so it lives in the **roaming
   `Settings`** (`mira/settings/model.py`, via `_u(...)`) — NOT in
   `machine.json` (contrast spec/95's `display_quality`, which was
   hardware-bound). Default `True` preserves today's behavior.
   - Expose it in `mira/ui/base/settings_dialog.py` (Appearance/Display
     tab) as a checkbox/combo. `restart_required: False`.
   - Picker + Quick Sweep read it before setting the overlay: when
     `False`, never show the chip; when `True`, show it on **all photo
     views** (both single views, consistently — for photos with content).

2. **Extend the chip content** to add, beyond the existing exposure
   readout: **camera** + **file type** + **file size (MB)**. Chosen by
   Nelson 2026-06-22 (NOT pixel dimensions, NOT lens). Target shape:

   ```
   <Camera>  ·  1/250s · f/2.8 · ISO 400 · 85mm  ·  RAW · 24.3 MB
   ```

   - **camera:** from the item (`SourceItem.camera_id` in Quick Sweep,
     the Picker item's `camera_id`) or EXIF camera model — whichever is
     populated. Omit the segment if empty.
   - **file type:** map the path suffix to a short label (RAW family
     [`.cr2/.cr3/.nef/.arw/.raf/.dng/…`] → "RAW"; `.jpg/.jpeg` → "JPEG";
     `.heic/.heif` → "HEIF"; else the uppercased extension).
   - **file size:** `os.stat(path).st_size` → "{:.1f} MB" (≥ 1 MB) or
     "{kb} KB" for small files. One stat per shown item — cheap; guard
     missing-file with a blank segment.

3. **Where to compose it.** Keep `exif_compare.py` pure-logic (no
   filesystem). `caption_html(exif)` stays as-is. Add the camera + type +
   size as a separate composition:
   - Either a small pure helper `source_chip_html(camera, type_label,
     size_text)` in `exif_compare.py` (takes already-computed strings),
     with the call sites (Picker / Quick Sweep) doing the `os.stat` +
     extension mapping and passing strings in; OR a tiny UI helper near
     the overlay. Prefer the pure helper + call-site stat so the format
     is unit-testable and `core`/pure modules stay filesystem-free.
   - Final overlay text = camera + `caption_html(exif)` + type/size,
     joined with the existing `·` separators.

---

## Build order

1. **Setting (Part 2).** Add `show_exposure_overlay` to the Settings
   model + dialog. Picker + Quick Sweep honor it (show/hide). Eyeball:
   toggling hides/shows the chip everywhere.
2. **Chip content (Part 2).** Add camera + type + size composition + the
   pure formatter; wire both single views.
3. **Activity line (Part 1).** `proxy_pending_count()` accessor on the
   photo-cache singleton; `BatchProgressLine` always-visible + poll +
   message priority; MainWindow wires the callable.

## Tests

- `show_exposure_overlay=False` → Picker + Quick Sweep never call
  `set_html` with content; `=True` → they do (photo, has EXIF).
- Source formatter: RAW/JPEG/HEIF/other extension → correct label;
  byte→MB/KB formatting; missing camera / missing file → blank segments,
  no crash.
- Final chip string contains camera + exposure + type + size in order.
- `BatchProgressLine`: idle → visible with "Ready"; previews pending > 0
  and no batch job → "Creating previews … ({n} left)"; batch job running
  → batch message wins; the line is never `setVisible(False)`.
- `photo_cache().proxy_pending_count()` reflects the builder queue.

## Acceptance (Nelson eyeball)

- Open a day on the big library: the line reads "Creating previews —
  responses may be slower (N left)" while the builder drains, then
  "Ready".
- Export / ingest still show on the same line (unchanged).
- The exposure chip now shows camera + exposure + RAW/JPEG + size; the
  Settings toggle hides/shows it across all photo views; default ON.
