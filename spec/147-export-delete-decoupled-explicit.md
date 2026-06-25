# 147 — Export surface: decouple intent from deletion; explicit Export now / Delete now

**Status: PROPOSED (Nelson 2026-06-23, design agreed). Today the Export
surface conflates **ship intent** with **file deletion**: marking an item
"Set aside" (red) causes its `Exported Media/` file to be **deleted** on the
next "Export now" run (the confirm reads "Render N · Delete M files"), and a
"legacy flat-cell auto-delete on X" path means even un-selecting a single
cell can erase its export. The verbs ("Export all" / "Drop all" / "Export
now") don't communicate any of this. Redesign so **deletion is always
explicit** and intent never deletes: `Export now` renders only the green
**Will export** set; a new parallel `Delete now` deletes only the red **Set
aside** set; both are deliberate, confirmed runs with **live counts**.
Individual delete lives in the preview viewer. The delete confirm **warns**
when files are used in Cuts and **cleans up** both event and cross-event Cut
membership so nothing dangles. Touches `mira/ui/pages/days_grid_page.py`,
`mira/ui/pages/days_lists_page.py`, `mira/ui/shell/main_window.py`,
`mira/ui/exported/batch.py`, `mira/ui/exported/preview_dialog.py`, and the
gateways (event + library). No new render path.**

## 1. Principle — intent ≠ deletion

- **Will export** (green) / **Set aside** (red) are **intent only** (border
  click; bulk toggles). Setting "Set aside" **never deletes** the existing
  exported file — it just excludes the item from a fresh render.
- Deletion is a **separate, explicit, confirmed** action. Remove the
  "legacy flat-cell auto-delete on X" path and the delete branch from the
  "Export now" batch run.

## 2. Two parallel run verbs (with live counts)

Per toolbar — **Days Grid (per-day)** and **Days List (all-days)** — four
buttons:

- **Mark all to export** — bulk-set every item to Will export (intent).
- **Set all aside** — bulk-set every item to Set aside (intent).
- **Export now · N** — renders the **N** green Will-export items that lack a
  file into `Exported Media/`. **Deletes nothing.**
- **Delete now · M** — deletes the **M** red Set-aside items' exported files
  (per-file via `delete_exported_file_by_relpath`). **Renders nothing.**

(Replaces "Export all" / "Drop all" / "Export now". The bulk-intent toggles
are renamed; "Export now" is now render-only; "Delete now" is new.)

- **Live count** on the button face (`Export now · 12`, `Delete now · 3`),
  in the hint, and in the confirm modal; updates as intents change.
- **Hints:** Export now → *"Renders the N items marked Will export (green)."*
  Delete now → *"Deletes the M exported files marked Set aside (red)."*
- **Zero state:** count 0 → button disabled, hint *"Nothing marked Will
  export" / "Nothing marked Set aside."*
- Each run shows a brief confirm naming the count before acting.

## 3. Individual level (preview viewer)

Parallel single-item verbs in the export preview viewer:

- **Export this** — render this one item (must be Will export).
- **Delete this** — delete this one exported file (must be Set aside),
  confirmed.

## 4. Delete confirm: warn + clean up Cut usage

Before deleting (Delete now or Delete this), compute how many Cuts use the
file(s) and **warn**; on confirm, **clean up** so nothing dangles:

- **Event Cuts** (event.db `cut_member`, `event_id IS NULL`) — already
  cascade-deleted by `delete_exported_file_by_relpath`. The frame is removed.
- **Cross-event Cuts** (library `user_store.cut_member`, `event_id` =
  this event) — **new:** the delete also reaches the **library DB** and
  removes those member rows, so no cross-event Cut is left with a missing
  frame.
- **Count** is assembled from **both** DBs (event + library) for the warning:
  *"These files are used in N event Cuts and M cross-event Cuts — deleting
  removes them from all of them."*

## 5. Wipe-and-redo + convenience

- The full re-do flow falls out of the verbs: **Set all aside → Delete now**
  (all gone), then **Mark all to export → Export now** (rebuilt).
- Optional one-click **Event menu → "Delete exported media…"** (strong
  confirm) for the whole-event wipe — the in-app version of the test script
  (`clear_test_event_exports.py`): drop `Exported Media/` files + lineage
  rows + reset `edit_exported`, with a backup. (Optional; the verbs already
  cover it.)

## 6. Acceptance

- Marking an item Set aside (or un-selecting) **never** deletes its file.
- **Export now** only renders the Will-export set; **Delete now** only
  deletes the Set-aside set; each shows its live count and a confirm; both
  disable at 0.
- Deleting a file warns when it's in any Cut(s) (event + cross-event) and,
  on confirm, removes it from **all** of them (no dangling, including
  cross-event).
- The preview viewer has Export this / Delete this with the same scope.
- Wipe-and-redo works via the verbs; the optional Event-menu wipe also works.

## 7. Tests

- `tests/test_export_intent_no_delete.py` — Set aside / un-select does not
  delete any `Exported Media/` file; the "Export now" run renders green-only
  and deletes nothing.
- `tests/test_delete_now_scope.py` — Delete now removes only Set-aside files
  (count = M), Export now renders only Will-export (count = N); zero-state
  disables each.
- `tests/test_delete_cut_usage_warn_cleanup.py` — the confirm count sums
  event + cross-event Cut usage; on confirm, event `cut_member` AND library
  `cut_member` rows for the file are removed (no dangling cross-event member).
- `tests/test_export_buttons_counts.py` — button faces/hints carry the live
  counts and update on intent change.
