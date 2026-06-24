# Handoff — activity line + exposure chip (spec/96)

Implement **spec/96** (`spec/96-activity-line-and-exposure-chip.md`) —
read it in full first; it governs. Two independent features; ship in the
build order below.

Branch: **main** (the repo's only branch). Read CLAUDE.md. Update the
spec with the code if you change any detail.

---

## Part 1 — Always-on activity line (preview/proxy feedback)

The export/ingest progress line **already exists**: `self.batch_line`
(`mira/ui/shell/batch_queue.py::BatchProgressLine`) below the menubar,
bound to `self.batch_queue`. It hides when idle and does NOT know about
preview building (the proxy builder runs on its own thread, off the
batch queue).

Do:
1. `mira/ui/media/photo_cache.py`: add a public
   `proxy_pending_count() -> int` on the cache singleton that delegates
   to `self._proxy_builder.pending_count()` (already exists, thread-safe).
2. `mira/ui/shell/batch_queue.py::BatchProgressLine`:
   - Never `setVisible(False)`. Idle shows a quiet **"Ready"** (muted
     styling — no accent).
   - Add a `QTimer` (~400 ms) that re-runs `_sync`.
   - Accept a `previews_pending: Callable[[], int]` (via `bind(...)` or a
     setter) so the line stays decoupled from the cache import.
   - `_sync` message priority (one line):
     1. batch job running → today's batch head/label/progress.
     2. else `previews_pending() > 0` → "Creating previews — responses
        may be slower ({n} left)".
     3. else → "Ready".
   - Keep the determinate bar for batch jobs; for the previews state the
     count lives in the text (indeterminate/cleared bar is fine).
3. `mira/ui/shell/main_window.py`: hand the line the previews callable,
   e.g. `self.batch_line.set_previews_source(photo_cache().proxy_pending_count)`.

Watch-outs: the poll only reads an int under the builder lock — no decode
on the GUI thread. Keep the builder Qt-free (charter inv. 8).

## Part 2 — Exposure chip: setting + camera + type/size

Today: `PhotoExposureOverlay` (`mira/ui/media/photo_overlay.py`) is
filled by `caption_html(exif)` (`mira/picked/exif_compare.py`) showing
only shutter · aperture · ISO · focal. Set in `picker_page.py` and
`quick_sweep_page.py` for photos with EXIF. No ON/OFF setting.

Do:
1. **Setting `show_exposure_overlay: bool = True`** in
   `mira/settings/model.py` (roaming `Settings`, `_u(...)` pattern — NOT
   machine.json; this is a viewing pref, not hardware-bound). Expose a
   checkbox/combo in `mira/ui/base/settings_dialog.py`
   (Appearance/Display tab), `restart_required: False`.
   - Picker + Quick Sweep read `SettingsRepo().load().show_exposure_overlay`
     before filling the overlay: `False` → never show; `True` → show on
     all photo views (both single views), as today.
2. **Content = camera + exposure + file type + file size (MB).** Target:
   `<Camera>  ·  1/250s · f/2.8 · ISO 400 · 85mm  ·  RAW · 24.3 MB`.
   - camera: item `camera_id` (or EXIF model); omit if empty.
   - type: suffix → label (RAW family → "RAW"; jpg/jpeg → "JPEG";
     heic/heif → "HEIF"; else uppercased ext).
   - size: `os.stat(path).st_size` → "{:.1f} MB" (≥1 MB) / "{kb} KB";
     blank if the file is missing.
   - Keep `exif_compare.py` filesystem-free: `caption_html(exif)` stays;
     add a pure `source_chip_html(camera, type_label, size_text)` that
     takes already-computed strings, and do the `os.stat` + extension map
     at the call sites (Picker / Quick Sweep). Final overlay text =
     camera + `caption_html(exif)` + type/size joined with the existing
     `·` separators.

## Tests

- `show_exposure_overlay` False/True gates the chip in both single views.
- Source formatter: RAW/JPEG/HEIF/other → label; byte→MB/KB; missing
  camera / missing file → blank segments, no crash.
- Final chip string = camera + exposure + type + size, in order.
- `BatchProgressLine`: idle → visible "Ready"; previews pending and no
  batch job → "Creating previews … ({n} left)"; batch job running → batch
  message wins; never hidden.
- `photo_cache().proxy_pending_count()` reflects the builder queue.

Run the relevant existing suites (photo cache, settings dialog, any
picker/quick-sweep tests) then full `verify.bat`.

## Commit + push (on main)

```
feat: always-on activity line (incl. preview building) + exposure-chip
      setting & camera/type/size (spec/96)

- batch line is now permanent (quiet "Ready" when idle) and also reports
  background preview/proxy building via photo_cache().proxy_pending_count();
  export/ingest reporting unchanged.
- exposure chip: new show_exposure_overlay setting (roaming, default ON),
  honored in Picker + Quick Sweep; chip now shows camera + exposure +
  file type + file size. exif_compare stays filesystem-free.
```

Then `git push` on `main`.
