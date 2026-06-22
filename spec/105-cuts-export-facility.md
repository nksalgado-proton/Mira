# 105 — Cuts export facility: roots model, volume-aware layout, originals, link-vs-copy

**Status: PROPOSED (Nelson 2026-06-22, design discussion folded in).
Extends the Cut export (`mira/shared/cut_export.py::export_cut` +
`mira/shared/cross_event_cut_export.py` + the share/library export dialogs)
with: a coherent roots model (one root by default, separable for the
catalog/media split), a volume-aware `<root>/<event>/<cut>` layout that
keeps hardlinks working wherever an event physically lives, a real home for
cross-event Cuts, an optional `Original Media/` subdir, and an explicit
hardlink-vs-copy choice. The `audio/` subdir already exists and is kept.
Touches `mira/settings/model.py`, `mira/shared/cut_export.py`,
`mira/shared/cross_event_cut_export.py`, the export dialog in
`mira/ui/pages/share_cuts_page.py` (`_ExportTargetDialog`) and the
`library_page` caller; relates to the first-run wizard and the spec/94
`validate_root` health hook. Charter invariant #2 (no absolute path stored
on the Cut) is preserved — roots are settings, the per-export target is
recomputed and offered as a default, never frozen onto the Cut.**

## 0. What already exists (do not rebuild)

`export_cut` today: hardlinks each member (NTFS hardlink, `shutil.copy2`
fallback — `_link_or_copy`), numbered `NNN_name.ext`; renders opener + day
separators into the sequence; builds an `audio/` subdir from a playlist
(`audio_library.build_playlist(tracks, show_s)`) chosen by the Cut's
`music_category` and sized to the show's total duration; writes overlays.
Default target `<event_root>/Cuts/<tag>/`. `export_cross_event_cut` already
resolves each member to its source-event root and hardlinks-or-copies into
a caller-supplied `target` (no default home). The export dialog is a
target-folder picker only.

So **#4 (audio subdir, style + duration) is already done** and the
hardlink-with-copy-fallback primitive exists — this spec organises the
roots/layout around them and adds the originals + explicit copy controls.

## 1. The roots model — one root by default, separable for the power user

Mira already has (spec/76) a single user-chosen **`library_root`** with the
`.mira/` machinery (settings, `mira.db`, events index, lock) inside it; the
first-run wizard picks it. Events are stored under **`photos_base_path`**.

- **Default them equal.** First-run sets `photos_base_path = library_root`,
  so the common install is ONE root on ONE volume — hardlinks always work,
  and the Cuts home is unambiguous. This is the assumed, encouraged layout.
- **Keep them separable (advanced).** Do NOT weld them. A serious
  photographer may run the catalog/media split — small, fast, frequently
  backed-up metadata (`library_root`/`.mira`) on the internal SSD, bulk
  photo media (`photos_base_path`) on a big external drive / NAS. That is
  Mira's target user; forcing one drive would hurt them. External / imported
  events (`event_root_abs`, the index's deliberate cross-volume escape
  hatch) stay supported too.
- **Optional `cuts_export_root`** (new setting): an explicit override for
  "put all my slideshows here." Blank = the volume-aware default below.

```python
# mira/settings/model.py
cuts_export_root: str = _u(
    "Root for exported Cuts, as <root>/<event>/<cut>. Blank = a Cuts/ "
    "folder on the same volume as each event (keeps hardlinks).", "")
```

## 2. Layout — volume-aware so hardlinks survive wherever an event lives

Hardlinks only work within one volume. The default target therefore lands
on the *event's own volume*, computed per export (still "defaulted, not
frozen" — the dialog shows it and the user can override):

- **`cuts_export_root` set** → `<cuts_export_root>/<event slug>/<cut slug>/`
  (and `<cuts_export_root>/Cross-event/<cut slug>/`). Honoured verbatim
  even if it crosses volumes — §5 then copies, with the §6 notice.
- **`cuts_export_root` blank (default):**
  - Per-event cut, event on the **same volume as `library_root`** →
    `<library_root>/Cuts/<event slug>/<cut slug>/`. One discoverable home,
    same volume, links work.
  - Per-event cut, event on a **different volume** (an `event_root_abs`
    external event) → `<event_root>/Cuts/<cut slug>/` — the event's own
    volume, so links still work.
  - **Cross-event cut** → `<library_root>/Cuts/Cross-event/<cut slug>/`.
    Members span several event roots / possibly volumes, so some members
    copy regardless — inherent and fine.

Slugs via `core/cut_names.py` (add an event-name sanitiser). `_fresh_folder`
`(2)` disambiguation stays — re-export never overwrites a prior one. Show
files stay flat at the cut-folder root (`NNN_…`); `audio/` and (new)
`Original Media/` are subdirs, so the folder a slideshow tool opens keeps
its shape.

## 3. Export the originals → `Original Media/` subdir (#3)

Add `include_originals: bool = False` to both export functions + a dialog
checkbox ("Also export the original files"). When on:

- Resolve each photo/video member's source via the existing
  `SessionFile.source_item_id` → the Item's `origin_relpath` (bracket
  exports already resolve to the merged-stack master, spec/57). Inject the
  resolution as a callable (`original_resolver`, source_item_id →
  origin_relpath) so `cut_export.py` stays Qt-free; the default queries the
  gateway. The cross-event path already has each member's source-event root,
  so it resolves `Original Media/<origin_relpath>` against that root.
- Place `event_root/<origin_relpath>` into `<dest>/Original Media/`,
  filename preserved, collisions deduped (`_2`, like ingest). Members with
  no source (separators, opener, audio, missing) are skipped; a missing
  source file goes in `result.missing`, never a crash.
- Originals go through the SAME link/copy switch as §5 (links by default).

Add originals counts to `ExportResult`.

## 4. Audio subdir — keep (#4)

Already implemented (§0). Only change: route its placement through the §5
switch so the copy flag also forces audio copies. Playlist still sizes to
the show total (`build_playlist(tracks, show_s)`), unchanged.

## 5. Hardlink by default, copy on request (#5)

Add `copy_mode: bool = False` + a dialog checkbox ("Make independent copies
instead of links"). One placement helper for media, originals, AND audio:

```python
def _place(src, dst, *, force_copy):
    if force_copy:
        shutil.copy2(src, dst); return False        # copied
    try:
        os.link(src, dst); return True              # linked
    except OSError:
        shutil.copy2(src, dst); return False        # cross-volume → copy
```

- Default (`copy_mode=False`): hardlink, copy-fallback on `OSError`. With
  the §2 volume-aware default, normally-ingested events always link;
  cross-volume members copy automatically.
- `copy_mode=True`: force `copy2` everywhere — independent files the user
  can move/keep without the event. Burn-in overlay members are copies
  regardless (rendered), as today.

## 6. Dialog + cross-volume honesty

`_ExportTargetDialog` (and the `library_page` caller) gain:

- The default line reflecting the §2 computed target.
- **☐ Also export the original files** (`include_originals`).
- **☐ Make independent copies instead of links** (`copy_mode`).
- A **cross-volume notice**: when the chosen target is not on the same
  volume as the event's media (e.g. a `cuts_export_root` on another drive,
  or an inherently multi-volume cross-event Cut), the dialog states "These
  will be copied, not linked, because the target is on a different drive" —
  so a slow/space-heavy export is never a surprise.
- The completion summary reports linked/copied/originals/audio/missing.

## 7. Related guard — surface cross-volume events (not a hard rule)

Lean on the spec/94 Phase 5c `validate_root` hook: flag events whose
resolved root is on a different volume than `library_root`, so a user who
*wants* everything on one drive can find and fix the stragglers. This is a
visibility aid, not enforcement — `event_root_abs` stays a supported choice.

## 8. Acceptance

- Fresh install: `photos_base_path == library_root`; a per-event Cut
  exports to `<library_root>/Cuts/<event>/<cut>/` with `NNN_…` show files,
  an `audio/` playlist, and (when chosen) an `Original Media/` subdir — all
  hardlinks.
- A catalog/media split (media on another volume): the same Cut defaults to
  `<event_root>/Cuts/<cut>/` (media's volume) and still hardlinks.
- A cross-event Cut exports to `<library_root>/Cuts/Cross-event/<cut>/`;
  same-volume members link, off-volume members copy, and the dialog says so.
- `cuts_export_root` set: everything lands under it as `<root>/<event|
  Cross-event>/<cut>/`; the copy notice fires when it's off-volume.
- `copy_mode=True` forces independent copies for media + originals + audio.

## 9. Tests

- `tests/test_cut_export_target.py` — volume-aware resolution: same-volume
  event → `<library_root>/Cuts/<event>/<cut>`; simulated off-volume event →
  `<event_root>/Cuts/<cut>`; cross-event → `<library_root>/Cuts/Cross-event/
  <cut>`; `cuts_export_root` set → under it.
- `tests/test_cut_export_originals.py` — `include_originals=True` lands each
  member's `origin_relpath` in `Original Media/` (deduped); missing source →
  `missing`, no crash; separators/audio not duplicated there. Covers both
  `export_cut` and `export_cross_event_cut`.
- `tests/test_cut_export_copy_mode.py` — same-volume `copy_mode=False` →
  hardlinks (`st_nlink > 1`); `copy_mode=True` → independent files
  (`st_nlink == 1`) for media, originals, audio.
- Regress existing `export_cut` / cross-event tests (audio, separators,
  overlays, `_fresh_folder`).

## 10. Open questions

1. **Subdir naming.** `Original Media/` (matches the captured tree) vs
   lowercase. Proposed: `Original Media/`.
2. **Show files flat vs `Show/` subdir.** Keep flat (no change to the
   slideshow-tool contract) — proposed.
3. **First-run wording.** How prominently to expose the advanced
   catalog/media split in the first-run wizard vs burying it in Settings.
   Proposed: one root by default, split only via an advanced Settings field.
