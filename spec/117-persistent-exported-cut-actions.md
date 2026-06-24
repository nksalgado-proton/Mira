# 117 — Persistent post-export actions on an exported Cut (Open folder · Open in PTE)

**Status: PROPOSED (Nelson 2026-06-23). Today "Open folder" + "Open in
PTE" exist ONLY as buttons in the transient export-complete message box
(`share_cuts_page._add_open_buttons`, called once at the end of
`_on_export_cut`). Dismiss that dialog and the actions are gone — the only
way back to the exported bundle (or to launch PTE) is to re-export. But the
Cut already records it has shipped (`last_exported_at`), so an exported Cut
should keep those actions reachable from its own surface. This adds
persistent **Open in PTE** (the primary action) and **Open folder**
actions to the Cut detail / list surface, shown when `last_exported_at` is
set. Touches
`mira/ui/pages/share_cuts_page.py` (+ the cut detail page) and reuses the
existing `mira/shared/pte_launch.py` helpers. No data-model change beyond
optionally remembering the last export folder (§3). Applies to per-event
and cross-event Cuts.**

## 1. The gap

`_add_open_buttons(box, folder, pte_file)` attaches the two actions to the
**post-export `QMessageBox` only**. There is no affordance on an
already-exported Cut: the buttons vanish with the dialog, and the Cut view
shows exported status (`last_exported_at`) without any way to act on it.
The user has to run a full re-export just to reopen the folder or relaunch
PTE — wasteful, and surprising.

## 2. The fix — persistent actions on the Cut surface

When a Cut has `last_exported_at` set, surface, on the Cut detail page (and
optionally as a row action in the Cut list):

- **Open in PTE** (primary) — `open_in_pte(pte_path, pte_file)`. The main
  reason to come back to a shipped Cut: relaunch the slideshow project in
  PTE without re-exporting. Shown under the same gate as today's button:
  `use_pte` on **and** `pte_launch_available(settings.pte_path)` **and** a
  `.pte` exists in the resolved folder.
- **Open folder** (secondary) — `reveal_in_explorer(folder)`. Always
  available for an exported Cut; the fallback when PTE isn't configured or
  the project can't be found.

Both reuse `mira/shared/pte_launch.py` verbatim — this spec only adds where
they're invoked from, no new launch logic.

## 3. Resolving the export folder (the one design point)

The Cut stores **no absolute path** (charter #2), and `_fresh_folder`
disambiguates collisions to `… (2)`, so a re-resolved default can be
ambiguous after multiple exports. Resolution order:

1. **Re-resolve the default** via `resolve_event_cut_target` /
   `resolve_cross_event_cut_target` (same call the exporter used). If that
   exact folder exists, use it — covers the common single-export case.
2. If it doesn't exist (disambiguated, moved, or deleted — see spec/116…
   actually spec on deletion), **fall back** to revealing the parent
   `Cuts/…` folder so the user can still find the bundle, and **hide Open
   in PTE** (no `.pte` to point at).
3. Find the `.pte` for the PTE action by globbing the resolved folder
   (`*.pte`, prefer `slideshow*.pte`); none found → hide Open in PTE.

**Optional (cleaner) alternative:** persist the last export folder as a
**library-relative** path on the Cut row (a new `last_export_relpath`
column, sibling to `last_exported_at`) so step 1 is exact even after
disambiguation. Library-relative keeps charter #2 (no absolute user path
stored). Recommended if we want the `(2)` case to resolve precisely;
otherwise the re-resolve + fallback in steps 1–2 is enough for v1.

## 4. Acceptance

- An exported Cut shows **Open folder** on its detail surface; clicking it
  opens the materialized bundle in Explorer.
- With PTE configured (`use_pte` on + valid `pte_path`) and a `.pte` in the
  folder, **Open in PTE** appears and launches PTE with the project loaded
  — without re-exporting.
- A never-exported Cut shows neither action.
- If the resolved folder is gone (deleted/moved), Open folder degrades to
  the parent `Cuts/…` folder and Open in PTE is hidden — no crash.
- Works for per-event and cross-event Cuts.

## 5. Tests

- `tests/test_exported_cut_actions.py` — actions present iff
  `last_exported_at` is set; Open folder calls `reveal_in_explorer` with
  the resolved target; Open in PTE gated on `use_pte` +
  `pte_launch_available` + a discovered `.pte`; missing-folder degrades to
  parent + hides PTE; cross-event Cut resolves via the cross-event target.
- If §3 optional column is taken: round-trip `last_export_relpath`
  (library-relative) through `mark_cut_exported` / load, and exact-folder
  resolution after a `(2)` disambiguation.
