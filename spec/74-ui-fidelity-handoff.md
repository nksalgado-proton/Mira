# spec/74 — UI fidelity handoff (for a full-access coding agent)

**Status:** written 2026-06-16 from a read-only follow-up to the spec/73
audit, by a session whose sandbox could **not** run Qt or pytest and whose
file-mount was unreliable. This doc hands the remaining work to a coding agent
running on the host (full filesystem, can launch the app + run `verify.bat`).

**Headline:** most of what looked "left behind" is already built. Of the three
"heavy UI" items, **two are done (verify only, do NOT rebuild)** and the third
is ~90% done (one small piece). Three logic fixes were already applied this
session as uncommitted working-tree edits — **verify they're present, regression
-test them, then commit.** Exact intended changes are embedded below so you can
re-apply any that didn't land.

Read first: `spec/00-charter.md`, `spec/05-ui-standards.md` (QSS roles, no
inline `setStyleSheet`), `Desktop/MiraCrafter Redesign/00-design-system.md`,
and the mockups referenced per item.

---

## §0. Verify (or re-apply) the three uncommitted logic fixes

Run `git status`; you should see modified `core/scan_source.py`,
`core/autofill.py`, `mira/ui/pages/event_header_dialog.py`. Open each and
confirm the change below is present. If any is missing, apply it.

### 0.1 Filename timestamp recovery — `core/scan_source.py`

Imports (add `replace`, add the helper import):

```python
from dataclasses import dataclass, field, replace
...
from core.filename_timestamp import parse_timestamp_from_filename
```

New helper (place just above `def build_scan_result(`):

```python
def _recover_filename_timestamps(
    photos: Sequence["PhotoExif"],
) -> List["PhotoExif"]:
    """Recover EXIF-less capture times from the filename. Photos that
    already carry a timestamp pass through; for the rest, a parseable date
    in the name (IMG_20180224_204237.jpg, IMG-20180224-WA0001.jpg, …)
    becomes the capture time. No match → timestamp stays None (still
    untimestamped). Never falls back to mtime (the copy date)."""
    out: List["PhotoExif"] = []
    recovered = 0
    for p in photos:
        if p.timestamp is None:
            parsed = parse_timestamp_from_filename(Path(p.path).name)
            if parsed is not None:
                p = replace(p, timestamp=parsed.dt)
                recovered += 1
        out.append(p)
    if recovered:
        log.info("scan_source: recovered capture time from filename for "
                 "%d EXIF-less photo(s)", recovered)
    return out
```

Call it as the **first line** of `build_scan_result`'s body:

```python
    photos = _recover_filename_timestamps(photos)
    by_day: Dict[date, List["PhotoExif"]] = {}
```

Covers both entry points (`scan_source()` delegates to `build_scan_result()`).

**Regression test** (`tests/test_scan_source.py`): feed a
`PhotoExif(timestamp=None, path=Path(".../IMG_20180224_204237.jpg"))` and
assert it groups onto the 2018-02-24 day; a date-less name (`violet.jpg`)
stays counted in `untimestamped_count`.

### 0.2 `_outros` day-title leak — `core/autofill.py`

Import:

```python
from core.path_builder import RESERVED_DIR_NAMES
```

In `common_immediate_subdir`, replace the final `return seen.pop()` with:

```python
    name = seen.pop()
    # Never surface Mira's internal bucket folders (_cameras / _phones /
    # _other / _no_timestamp, legacy _outros / _celulares, or phase folders)
    # as a day description. A real user subdir never starts with "_".
    if name.startswith("_") or name in RESERVED_DIR_NAMES:
        return None
    return name
```

(No circular import: `path_builder` imports only `re`, `pathlib`,
`core.models`.)

**Regression test** (`tests/test_autofill.py`): paths under `_outros/`,
`_phones/`, `Original Media/` → `None`; a real folder (`Sintra hike/`) → kept.

### 0.3 New Event header — `mira/ui/pages/event_header_dialog.py`

In `_build_header_bar`, the title/subtitle must be a two-line lockup in both
flows:

```python
    is_new = not self._existing_name
    ...
    title = QLabel(tr("New Event") if is_new else tr("Event Header"))
    ...
    sub = QLabel(
        tr("Set up identity, logistics, and tags.")
        if is_new else self._existing_name
    )
    sub.setObjectName("Sub")
    text_col.addWidget(sub)
```

And the window title (computed after `self._existing_name` is set):

```python
    self.setWindowTitle(
        tr("Event Header") if self._existing_name else tr("New Event"))
```

**Verify** by opening the New Event flow: header reads "New Event" + subtitle,
not a lonely "Event Header".

---

## §1. Item A — Editor crop drag handles → ALREADY DONE, verify only

**Do not rebuild.** `mira/ui/edited/crop_overlay.py` (≈669 lines) is already an
aspect-ratio-locked, draggable crop rectangle: corner/edge handle hit radii,
`mousePressEvent`/`mouseMoveEvent` drag state (`_DragMode`, `_drag_anchor`),
aspect lock via `set_aspect_ratio()` / `core.aspect_ratio`, and a rotation
handle for free-angle straighten. spec/65 §3.8's "paint-only" note is stale.

Verify in a running Editor (Surface 08): toggle Crop on; corner-drag holds the
ratio; the aspect Select re-locks; rotation handle snaps on commit; the photo's
green state border (design-system §5a) is never overwritten by the crop chrome.
Mockup: `surface-08-editor.html`.

---

## §2. Item B — Look-preset previews → ALREADY DONE (richer than mockup)

**Do not rebuild.** The "mini preview per look" intent is met by the **Look
grid** (`mira/ui/edited/look_grid.py`, key **`G`**): a 2×2 of *this* photo
rendered under Original/Natural/Brighten/Deeper via the real engine
(`look_params_from_natural` + `core.photo_render.apply_params`) — real renders,
not approximations. The inline toolbar keeps text pills + `L`/`Shift+L` cycle +
Strength slider, by design.

Verify: load a photo, press `G`, confirm four live renders, clicking applies.
**Optional nicety (not a gap):** if Nelson wants per-pill thumbnails inline,
render each at ~28–32 px via `look_grid._tile_pixmap` and `setIcon` on the Look
buttons.

---

## §3. Item C — Brand layer → ~90% DONE; build the About dialog

Already built: `mira/ui/design/brand.py` (`MiraMark` gradient tile + white
viewfinder/spark from `assets/icons/mira-mark.svg`; `_Wordmark` painting
`M✦ıra`; `MiraLogo(tile_size, wordmark_pt, tagline=False)` with
`TAGLINE = "See the keepers."`) and `mira/ui/design/title_bar.py` hosting
`MiraLogo(tile_size=24)`.

**The one gap:** the tagline is never surfaced (`tagline=True` unused; no
splash/About; grep finds "See the keepers" only in `brand.py`).

Build:
1. An **About dialog** — small `QDialog` (reuse `mira/ui/design/dialogs.py`
   `MessageDialog` chrome if it fits) showing `MiraLogo(tile_size=48,
   tagline=True)`, the app version, and a one-line description. Wire an
   **"About Mira"** action into the Help menu (built in `main_window`). All
   strings via `tr()`.
2. Optional: surface `MiraLogo(tagline=True)` in the first-run wizard welcome
   (`mira/ui/wizard/`).
3. No inline `setStyleSheet` in widget modules (spec/05); any new QSS roles
   must exist in both `assets/themes/light.qss` + `dark.qss`.
4. Validate: Help → About Mira shows the `M✦ıra` lockup + tagline in both
   themes; the title-bar logo is unchanged.

Mockup: `Desktop/MiraCrafter Redesign/mira-logo.html`.

---

## §4. Close-out

1. `verify.bat` green, including the new §0 regression tests.
2. Items A + B confirmed by a quick Editor session (no rebuild).
3. Item C About dialog reachable from Help, both themes, `tr()`-wrapped.
4. **Commit** the §0 fixes + §3 work (the previous session could not commit —
   its sandbox mount was serving truncated copies, so committing there risked
   capturing broken files; that is why this is left to you).
5. Mark these as **done** in spec/65 + spec/73 so they stop reappearing on
   punch lists: crop drag handles (§3.8), Look-preview intent (§2.3/§3.8),
   Days-Lists bulk Pick/Skip-all (spec/73 Tier-1 #4), New-Cut match-count
   binding (spec/73 #2). Also fold this handoff's findings back so the next
   audit starts from the current truth, not the 2026-06-13 punch list.

Companion: `VISUAL-AUDIT-2026-06-16.md` (repo root) has the full per-surface
audit and the "verified already-built" list behind these conclusions.
