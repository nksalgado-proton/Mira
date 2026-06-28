# spec/158 — Cut export: "Only new files" (additive re-export)

**Authored 2026-06-27 (Nelson + Claude).**

## Problem

Re-exporting a Cut offered only two collision choices (spec/148):

1. **Overwrite** — clear the folder and re-link the whole bundle.
2. **Keep both** — write a fresh `<tag> (2)/` folder.

A common workflow has no good fit: the user exported a Cut, kept working
on it (added more picked/exported photos to the Cut), and wants to bring
**only the newly-added files** into the existing export folder — without
re-writing the files already there (and without disturbing a PTE project
built on that folder). Overwrite re-does everything (and wipes a
hand-edited `.pte`); Keep-both spawns a parallel folder. There was no
"export only what's new."

## Decision — third collision option

`_ExportTargetDialog` ("If the folder already exists") gains a third
radio:

> **Only new files — add files not yet exported here**

Selecting it routes `export_cut(..., only_new=True)`. The export lands in
the **same** base folder and writes **only** the Cut members not already
materialized there, leaving every existing file untouched.

The choice is **additive, never destructive** — no overwrite-confirm
prompt fires for it.

## How "already exported" is known — the sidecar manifest

Cut export is otherwise a stateless snapshot. To make "only new" precise
across sequence renumbering, every export now writes a sidecar manifest
into the destination folder:

```
<dest>/.mira-cut-export.json
{
  "version": 1,
  "cut_id": "<cut id>",
  "members": ["Exported Media/…", …],   // member export_relpaths present here
  "max_seq": 17                          // highest NNN_ sequence written
}
```

- Written by **every** export mode (Overwrite / Keep-both / Only-new), so
  a later Only-new run has an accurate baseline.
- **Tied to the folder, not the Cut row.** Deleting the folder correctly
  means "everything is new again"; pointing Only-new at a folder written
  by a *different* Cut (mismatched `cut_id`) is treated as no manifest →
  full export. This is strictly more correct than a per-Cut DB record,
  which would wrongly skip files whose folder was deleted.
- Atomic write-then-rename (charter invariant #6). A write failure logs
  and is swallowed — it never fails the export.
- A dotfile, so it stays out of folder listings / PTE member scans.

## Skip is disk-verified, never manifest-only (data-loss fix)

**Critical (Nelson 2026-06-28).** A member is skipped **only when a file
with its exact show-name is really on disk** (the folder is catalogued
by name minus the `NNN_` sequence prefix, and matches are consumed
1:1). The manifest is a record, **not** the authority for skipping —
anything not verified on disk is copied.

This replaced a loose `endswith("_" + name)` match that wasn't 1:1:
brand-new **Repeated-cluster** members (near-duplicate frames) matched a
*different* same-suffix file already in the folder, so they were marked
"copied" in the manifest and **never actually written** — a silent
data-loss. Driving the skip off a verified, exact, 1:1 on-disk check
means we can never record a file as copied that isn't there, and a
poisoned manifest **self-heals** on the next run (the missing members
fail verification and get copied). `present_members` is rebuilt fresh
each run from genuine skips + actual writes, so a stale entry can't
propagate. Regression: `test_only_new_copies_member_absent_from_disk_despite_manifest`.

## Behaviour

`only_new=True`:

- **No prior manifest for this Cut** (never exported / folder gone /
  re-used by another Cut): degenerates to a normal full export into the
  base folder — every member is new, `skipped == 0`.
- **Prior manifest present** (incremental): members already listed are
  **skipped** (`ExportResult.skipped`); the rest are written, numbered
  continuing from `max_seq` so new files **append** without renaming what
  PTE already references. The manifest is rewritten with the union.

### The `.pte` project — export NEVER writes it; generate ASKS first

**Critical (data-loss guard, Nelson 2026-06-27).** Two rules, after a
bug overwrote a user's hand-edited project during an export they never
asked to touch the `.pte`:

1. **The export never writes the `.pte`.** Previously every Cut export
   auto-generated `slideshow.pte` whenever the global "I use PTE"
   setting was on (and a bug made Only-new *overwrite* it). That auto
   path is **removed**. The project is written **only** when the user
   explicitly clicks **Create PTE project** (export-summary dialog) or
   **Generate PTE** / **Open in PTE** (Cut detail page).
2. **Generate asks before overwriting.** When the explicit generate
   would replace an existing `<cut.tag>.pte`, Mira prompts
   (**Overwrite** / **Cancel**); Cancel leaves the project byte-for-byte
   intact. *Open in PTE* only auto-generates when **no** project exists,
   so it never clobbers one.

Regression tests: `test_export_never_writes_pte` (all three collision
modes) and `test_generate_pte_asks_before_overwriting_existing`.

Likewise, the existing **media files** are skipped by name on disk
(manifest OR `NNN_<name>`), so Only-new never re-links or copies over a
file that may be open/locked (the `WinError 32` "file in use" crash).

### Limitations (incremental sub-case) — by design for v1

The incremental add writes **media members only**. The **title slide,
day-separator cards, and audio playlist are NOT regenerated** — they
belong to the first full bundle. New files append after the last
sequence number, so a photo added *earlier* in the timeline still lands
at the end of the folder order. To refresh separators / opener / audio
or to re-sort, re-run **Overwrite**. The export summary states how many
files were skipped as already-present.

## Scope

Wired through the **per-event** path
(`mira.shared.cut_export.export_cut` ← `ShareCutsPage`). The
**cross-event** exporter (`export_cross_event_cut`) does not support it
yet, so `library_page` constructs the shared dialog with
`allow_only_new=False` — the radio is hidden there rather than offered
as a no-op. Extending Only-new to cross-event Cuts is a future follow-up.

## Touch points

- `mira/shared/cut_export.py` — `only_new` param, sidecar manifest
  read/write helpers, `ExportResult.skipped`, incremental loop guards.
- `mira/ui/pages/share_cuts_page.py` — third radio + `allow_only_new`
  flag on `_ExportTargetDialog`; `ExportChoices.only_new`; summary line;
  `.pte` regenerated in place for Only-new.
- `mira/ui/pages/library_page.py` — `allow_only_new=False` for the
  cross-event flow.
- Tests: `tests/test_cut_export_overwrite.py`
  (`test_only_new_appends_unexported_members`,
  `test_only_new_on_fresh_folder_writes_everything`).
