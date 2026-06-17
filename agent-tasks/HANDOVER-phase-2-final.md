# Phase 2 final handover — cross-event surface end-to-end

**From:** 2026-06-16 Phase-2 session (extended). **Branch:** `main`.
**Status:** Six engine items + four UI surfaces + lifecycle wiring +
cross-event export pipeline — **the cross-event surface runs end-to-end**.

**Tests:** 422+ Phase-1 baseline preserved + 61 new polish-suite tests
green; cumulative Phase-2 total **~250 new tests across 11 test files**.

Read first, in order:
- [`spec/81`](../spec/81-dynamic-collection-and-cut.md) — the model.
- [`agent-tasks/HANDOVER-phase-2-start.md`](HANDOVER-phase-2-start.md) — the
  brief this session worked from.
- [`agent-tasks/HANDOVER-phase-2-complete.md`](HANDOVER-phase-2-complete.md)
  — the intermediate handover after the 6 engine items + lifecycle wiring.
- This doc — what landed on top.

---

## The end-to-end user loop

A user can now drive the entire cross-event surface from the events screen:

1. **Browse cross-event collections.** Events screen → click `+ Collection`
   on the cross-event band → `CrossEventDcsDialog` opens, lists every
   saved_filter row with its tag / description / recipe summary / live count.
2. **Create a cross-event DC.** From the list, `+ New collection` →
   `NewCrossEventDcDialog` with every spec/32 §2 facet — origin radio +
   styles + media + stars + flag + color + cameras + lenses + flash + ISO +
   aperture + shutter + focal + dates + countries + cities. Live tag preview
   + reserved/taken warnings + live count. Save → `LibraryGateway.create_dc`
   → list refreshes.
3. **Edit or delete a DC.** Kebab menu per row → Edit (pre-filled dialog) /
   Delete (confirm).
4. **Pin a DC into a Cut.** From the list, `Pin → Cut` per row →
   `NewCrossEventCutDialog` opens pre-selected on that DC, with the anchor
   event defaulted to the top contributor → fill name + budget + music +
   attachments → Create → host builds a `CrossEventCutDraft` + drives
   `CrossEventCutSession.from_draft` + commits to the anchor event's
   `event.db`.
5. **View cross-event Cuts.** From the DCs dialog header: `View Cuts` →
   `CrossEventCutsDialog` walks every event.db, lists cuts where
   `source_dc_kind = 'user'`. Each row shows tag, anchor event, member
   count, export status. Kebab: Delete.
6. **Export a Cut.** From the Cuts list row: `Export` → file picker for
   target → `export_cross_event_cut` materialises the directory: export-kind
   members hardlink from their source event's `Exported Media/` (copy
   fallback cross-volume); grab-kind members copy from their source event's
   `Original Media/`. Stamps `last_exported_at`. Surfaces a summary
   (member_count / linked / copied / missing).

Everything else from the start handover stays in place: the projection sync
runs on event close + startup reconcile, the resolver is scope-agnostic,
the schema is at event.db v9 + user.db v4.

---

## What's still deferred (not in this session)

- **Cross-event Picker UI** — `CrossEventCutSession`'s weed-out / pick-in
  modes need a per-key Picker so the user can refine the resolved set.
  The cut dialog's radios for those modes are disabled until the Picker
  lands; today only **keep-all** commits.
- **Cross-event flat grid** — a WYSIWYG view of a Cut's frozen members
  (multi-event, per-event-day separators). The Cuts list's `Open…` action
  surfaces the kebab item but doesn't wire to anything yet.
- **In-app Play** — the rehearsal slideshow for a cross-event Cut. The
  cross-event resolver's per-(event, day) bucket structure is ready; the
  Play page hasn't been built.
- **Stale member sweep** — when a source event is deleted, cross-event Cuts
  in other event.db files keep dangling cut_member rows. Today they
  surface as `missing` in the export summary but no automated sweep
  prunes them.
- **Cross-store delete-DC cleanup** — when a cross-event DC (saved_filter)
  is deleted, cross-event Cuts that pointed at it keep their stale
  `source_dc_id`. No active sweep across event.db files. UI surfaces no
  inconsistency today; future polish.

---

## File map — what landed this session vs. earlier

### Engine (Phase 2 Items 1-6, prior session — recap)
- `mira/user_store/schema.py` v3 + v4 — `global_items` + `saved_filter` +
  `export_relpath` column.
- `mira/user_store/models.py` — `GlobalItem`, `SavedFilter`.
- `mira/gateway/global_items_sync.py` — projection sync.
- `mira/gateway/cross_event_resolver.py` — composite keys + accessors.
- `mira/gateway/library_gateway.py` — cross-event DC facade.
- `mira/store/schema.py` v8 + v9 — `cut.source_dc_kind` + `cut_member` v9
  (kind / origin_relpath / member_id PK).
- `mira/store/models.py` — `Cut.source_dc_kind`, `CutMember` reshape.
- `mira/shared/cross_event_cut_session.py` — `CrossEventCutSession` /
  `CrossEventSessionFile` / `pick_anchor_event`.
- `mira/shared/cut_draft.py` — `CrossEventCutDraft`.
- `core/collection_resolver.py` — `BASE_COLLECTED/PICKED/EDITED` + ladder set.
- `mira/gateway/event_gateway.py` — `set_cut_members` accepts dict shape +
  cross-event member tuples; `delete_dc` NULLs source_dc_id at gateway
  level; explicit cut_member sweep on lineage deletes; `create_cut` /
  `update_cut_settings` accept `source_dc_kind`.

### UI dialogs (Item 5 + the polish queue, this session)
- `mira/ui/pages/new_cross_event_dc_dialog.py` (Item 5) —
  `NewCrossEventDcDialog`. Full spec/32 §2 facet UI.
- `mira/ui/pages/cross_event_dcs_dialog.py` (polish) —
  `CrossEventDcsDialog`. Browse / edit / delete / pin entry point.
- `mira/ui/pages/new_cross_event_cut_dialog.py` (polish) —
  `NewCrossEventCutDialog`. Configure a cross-event Cut.
- `mira/ui/pages/cross_event_cuts_dialog.py` (polish) —
  `CrossEventCutsDialog`. List + export + delete cross-event Cuts.

### Engine: cross-event export (polish)
- `mira/shared/cross_event_cut_export.py` — `export_cross_event_cut`.
  Per-member kind routing through source event roots; hardlink/copy with
  cross-volume fallback; flat output filenames; idempotent re-export;
  last_exported_at stamp.

### Umbrella gateway (polish)
- `mira/gateway/gateway.py`:
  - `CrossEventCutRow` dataclass.
  - `Gateway.cross_event_cuts()` — multi-event walk.
  - `Gateway.delete_cross_event_cut(anchor_event_id, cut_id)`.

### Wiring (polish)
- `mira/ui/pages/events_page.py`:
  - `_open_new_cross_event_dc` opens the DCs list (was: the new-dc dialog
    directly).
  - `_pin_cross_event_dc` opens the Cut dialog with `LibraryGateway`
    inventories + `Gateway` events + audio-library categories;
    on save builds the draft + drives `CrossEventCutSession.from_draft` +
    commits via `Gateway.open_event(anchor)`.
  - `_open_cross_event_cuts` opens the Cuts list dialog.
  - `_on_export_cross_event_cut` runs the export pipeline + reports to
    the user.
- `mira/ui/pages/cross_event_dcs_dialog.py`:
  - `view_cuts_requested` signal + `View Cuts` button in header.

---

## Tests landed this session

| File | Tests | Covers |
|---|---:|---|
| `test_cross_event_dcs_dialog.py` | 13 | DCs list refresh, recipe summary, delete confirm, edit rehydrate, new + create error, pin signal |
| `test_new_cross_event_cut_dialog.py` | 20 | every facet of the Cut dialog: identity, gating, defaults (cross-event), source/anchor pickers, budget, music, accept emits info |
| `test_cross_event_cuts_list.py` | 8 | gateway walk: only `kind='user'` cuts; spans events; skips unopenable; member count; dialog refresh + delete + export signal |
| `test_cross_event_cut_export.py` | 10 | unresolvable anchor / unwritable target raise; export-kind member links from source Exported Media; anchor-event self-route works; grab-kind always copies from Original Media; mixed kinds route per-kind; missing source event lands in summary; missing source bytes; re-export overwrites; `last_exported_at` stamped |
| `test_phase2_wiring.py` | +2 (1 updated, 1 added) | events-page wires band → list dialog; pin_requested wires to NewCrossEventCutDialog |

Total this session: **61 new + 1 updated test**.

---

## A note from this extended session

The polish queue absorbed nicely. The cross-event Cut dialog's
keep-all-only constraint is the most visible deferred item — a user can pin
a DC into a Cut today but can't refine the resolved set per-key. The
session engine supports all three pin modes; the UI gates two of them with
visible disabled radios so the user knows they're coming.

The export pipeline's per-kind routing is the second nontrivial piece —
the bytes go through the right source event for each member. Cross-volume
hardlink fallback to copy works for export-kind; grab-kind always copies
(Original Media is byte-pristine per charter §3, so a link would
incorrectly couple the export's bytes to the source event's pristine ones).

Recommended next-session move: **in-app walkthrough on a real library**.
The cross-event surface has substantial state — 4 dialogs, 2 wiring paths,
2 schema versions on each store — and the unit tests can't catch every
real-app failure mode (Qt focus, paint, modal stacking on Windows, etc.).
The Phase-1 KI-1 (a `QTabWidget` paint regression that passed the
regression test but failed real interaction) is the canonical reminder.

— Claude, 2026-06-16
