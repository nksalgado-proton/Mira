# Phase 2 handover — cross-event DCs (Task D) **complete**

**From:** 2026-06-16 Phase-2 session (Claude + Nelson).
**Branch:** `main`. **Status:** Six build items + lifecycle wiring shipped.
**Tests:** 408 passing in the focused subset (Phase-1 231 + Phase-2 177).

Read first, in this order:
- [`spec/81`](../spec/81-dynamic-collection-and-cut.md) — the model.
- [`agent-tasks/HANDOVER-phase-2-start.md`](HANDOVER-phase-2-start.md) — the
  brief this session worked from.
- This doc — what landed + what's open.

---

## What landed

### Item 1 — `global_items` projection + `saved_filter` (recommendation #1: wrap saved_filter)
- `mira/user_store/schema.py` v3 adds two tables:
  - `global_items` — per-(event_uuid, item_id) projection. EXIF facets, ladder
    state (`pick_state`/`edit_state`/`has_export`), per-day location, curatorial.
    19 partial indexes — one per facet the cross-event resolver queries.
  - `saved_filter` — the cross-event DC home. Spec/32 §4 predicate-tree
    framing reconciled to spec/81 §2's typed-ref `expr_json` + `filters_json`.
- `mira/gateway/global_items_sync.py` — `project_event` / `sync_event` /
  `drop_event` / `reconcile_all`. Replace-the-slice semantics; atomic.

### Item 2 — Cross-event resolver accessors
- `core/collection_resolver.py` — added `BASE_COLLECTED`/`PICKED`/`EDITED`
  constants + `LADDER_TOKENS`. **Engine unchanged.**
- `mira/gateway/cross_event_resolver.py` — `pack_key`/`unpack_key` for
  composite `event_uuid::item_id` strings + `CrossEventAccessors`
  implementing the four resolver callables against `global_items` +
  `saved_filter`.
- Filter catalogue: full spec/32 §2 — styles, media_type, stars_min,
  color_labels, flag, camera_ids, lens_models, flash, iso/aperture/shutter/
  focal min/max, capture_from/to, country_codes, cities.

### Item 3 — `LibraryGateway`
- `mira/gateway/library_gateway.py` — cross-event facade. DC CRUD against
  `saved_filter`, resolution returning `(event_uuid, item_id)` pairs,
  `dc_show_totals` with per-(event, day) separator counting,
  `dc_operand_inventory` (4 ladder rungs + saved DCs), facet inventories
  (`available_classifications` / `cameras` / `lenses` / `country_codes` /
  `cities` / `color_labels`), sync triggers (`sync_event` / `drop_event` /
  `reconcile_all`).

### Item 4 — Pin-across-events session glue (recommendations #4 + #5)
- **event.db v8** reshape:
  - Dropped FK on `cut.source_dc_id` + added `source_dc_kind` ('event' /
    'user') discriminator.
  - Dropped FK on `cut_member.export_relpath` + added nullable `event_id`.
  - Freeze invariant (spec/81 §5) moves to gateway: `delete_dc` explicitly
    NULLs `source_dc_id` on referencing Cuts.
  - Gateway-enforced cascade for `delete_exported_file` /
    `delete_exported_file_by_relpath` / `clear_lineage` — explicit
    cut_member cleanup for event-scope (event_id IS NULL) members.
- **user.db v4** — added `export_relpath` (LATEST export per item) to
  `global_items`.
- `mira/shared/cross_event_cut_session.py` — `CrossEventSessionFile` carries
  `event_uuid` + `day_bucket = "<event_uuid>::<ISO date>"`;
  `CrossEventCutSession` mirrors `CutSession`'s shape but commits to an
  anchor event.db with `source_dc_kind='user'` + every member's `event_id`
  explicitly set.
- `CrossEventCutDraft` — sibling of `CutDraft` with `filters: dict` (full
  spec/32 §2 catalogue) + `anchor_event_id`.

### Item 5 — Cross-event filter UI + entry point
- `mira/ui/pages/new_cross_event_dc_dialog.py` — `NewCrossEventDcDialog`
  with modular facet widgets (`_MultiSelectFacet`, `_SingleSelectFacet`,
  `_NumberRangeFacet`, `_StarsMinFacet`, `_DateRangeFacet`, `_OriginRadio`).
  Every spec/32 §2 facet shipped. Live count via injected `dc_probe`. Live
  tag preview with reserved/taken warnings.
- `mira/ui/pages/_cross_event_band.py` — added `new_dc_requested` signal +
  ghost `+ Collection` button next to the Search button.
- Wired in `events_page.py` — `_open_new_cross_event_dc` opens the dialog
  with `LibraryGateway` inventories + `dc_probe`, handles save → `create_dc`.

### Item 6 — Grab-originals (recommendation #3: fast follow that landed)
- **event.db v9** — cut_member rebuilt:
  - PK → `(cut_id, member_id)` with `member_id` as content-stable distinguisher.
  - `kind` column ('export' / 'grab') + CHECK exclusivity.
  - `export_relpath` nullable; `origin_relpath` new nullable.
- `CrossEventSessionFile.member_kind` — discriminates export vs grab cells.
- `session_files_from_global_items(rows, keys, *, allow_grab=True)` —
  un-exported items with `origin_relpath` become grab members.
- `picked_members()` returns dicts (not tuples) so export + grab can intermix.
- `set_cut_members` accepts a third shape: `Iterable[dict]` with `kind` /
  `event_id` / `export_relpath` / `origin_relpath`.

### Polish — lifecycle wiring
- `EventGateway` — new `on_close` callable param. Fires before
  `store.close()` so the hook sees a live connection. Failure logged, never
  blocks close.
- `Gateway.open_event(event_id)` — installs `on_close` that runs
  `LibraryGateway.sync_event` so cross-event projection stays current.
- `Gateway.reconcile_global_items()` — startup catch-up. Walks the events
  index, opens each store raw (no recursive hook), syncs, drops stale
  slices. Unopenable events skipped + logged.
- `events_page.py` — `_cross_band.new_dc_requested` connects to
  `_open_new_cross_event_dc` which builds inventories from
  `LibraryGateway`, opens the dialog, on `saved` calls `create_dc`.

---

## Tests landed (177 new in Phase 2)

| File | Tests | Covers |
|---|---:|---|
| `test_user_store.py` (updated) | +8 | global_items + saved_filter schema, v3 migration |
| `test_global_items_sync.py` | 11 | project / sync / drop / reconcile |
| `test_cross_event_resolver.py` | 33 | every ladder rung, full facet catalogue, set algebra, SavedFilter operand, cycle guard |
| `test_library_gateway.py` | 33 | every DC CRUD path, resolve / probe / show_totals, inventories, sync triggers |
| `test_cross_event_cut_session.py` | 23 | session ledger, totals, commit to anchor event, picked_members shape |
| `test_new_cross_event_dc_dialog.py` | 24 | every facet's value composition, live count, tag preview, accept gating, band signal |
| `test_grab_originals.py` | 12 | per-kind CHECK, v8→v9 migration, session grab inclusion, set_cut_members shapes |
| `test_phase2_wiring.py` | 9 | EventGateway on_close hook, Gateway.open_event wiring, reconcile_global_items, events_page → dialog → create_dc |

**Total:** 177 Phase-2 tests; Phase-1 focused subset still 231 green; **408 in the focused subset.**

---

## Open questions, all settled per the recommendations

| # | Question | Recommendation taken |
|---|---|---|
| 1 | Cross-event DC storage | Wrapped `saved_filter` (spec/32 §4) |
| 2 | `global_items` sync trigger | On event close (hook installed in `Gateway.open_event`) + startup reconcile (`Gateway.reconcile_global_items`) |
| 3 | Grab-originals | Fast follow that landed (data model + session integration; export pipeline deferred) |
| 4 | `cut.source_dc_id` reshape | Dropped FK, opaque id + `source_dc_kind` discriminator |
| 5 | `event_id` on cross-event members | Nullable column added to `cut_member` (NULL = legacy event-scope) |

---

## What's NOT in Phase 2 (next session)

These were intentionally out of scope for "the cross-event surface lands" and
are substantial enough to warrant their own session(s):

### Big UI builds
- **Cross-event "New Cut" dialog** — Item 4 has the
  `CrossEventCutSession` engine ready; the dialog that drives it is its own
  surface. Should mirror `mira/ui/pages/new_cut_dialog.py`'s shape with
  cross-event inventories + anchor-event picker.
- **Cross-event Cuts list page** — browse / edit / delete cross-event Cuts
  across the library (the `cut` rows in event.db with
  `source_dc_kind='user'`).
- **Cross-event DC list page** — browse / edit / delete saved_filter rows.
  The dialog ships the CREATE path; edit reuses the same dialog with
  `existing` rehydrated.
- **Cross-event flat grid** — the WYSIWYG view of a cross-event Cut's
  members (cross-event members carry `event_id`; flat grid joins per-event
  lineage to render).
- **Cross-event Play + Export** — substantial. Export especially: the
  bytes flow for export-kind members (hardlink from source event's
  Exported Media/) + grab-kind members (copy from source event's
  Original Media/, the actual byte movement Item 6 set up the data model
  for but didn't implement).

### Engine completions
- **Cross-event Cut export pipeline** — Item 6 shipped the data shape
  (cut_member.kind + origin_relpath). The actual export needs:
  - For export-kind members: open the source event's gateway, look up the
    lineage row by `event_id` + `export_relpath`, link the file.
  - For grab-kind members: open the source event's gateway, find the
    original by `event_id` + `origin_relpath` under `Original Media/`,
    copy (not link — cross-volume safety + source event isolation).
  - Cross-volume fallback (charter §5.9 + spec/79).
- **Stale cross-event member sweep** — when an event is deleted or its
  lineage row goes, cross-event Cuts in OTHER event.dbs may still reference
  the dead path. Filter dangling members at read time + a periodic sweep
  is the cleanup discipline.
- **LibraryGateway.delete_dc cross-event cleanup** — when a cross-event DC
  (saved_filter) is deleted, cross-event Cuts that pointed at it keep their
  stale `source_dc_id`. Either sweep across event.db files (expensive) or
  rely on read-time existence checks. Defer until UI surfaces it.

### Smaller polish
- **Cross-event Cut name uniqueness** — currently the per-event `cut.tag`
  uniqueness applies within one event.db. Cross-event Cuts in different
  anchor events can share tags. spec/61 §1.5 says names are the cross-event
  glue — if two events have `#mountains_2024`, those should aggregate. Not
  yet enforced at create time. Worth thinking about for cross-event Cut UX.
- **Edit cross-event DC** — the dialog has `existing` rehydration code +
  tests, but no UI surface launches it. Lands with the cross-event DC list page.
- **Cross-event band**'s `submitted(str)` signal (search) still has no
  backend — that's a separate search infrastructure build, predates this work.

---

## How to wire the next session

1. `git log -1` — confirm you're on the Phase-2 wrap commit.
2. Run the focused subset (~9s):
   ```
   python -m pytest tests/test_store.py tests/test_collection_resolver.py \
     tests/test_cut_session.py tests/test_cut_session_page.py \
     tests/test_cut_export.py tests/test_cut_overlay.py \
     tests/test_gateway_cuts.py tests/test_new_cut_dialog_adapter.py \
     tests/test_cuts_shell.py tests/test_cut_play.py \
     tests/test_dc_detail_page.py tests/test_pool_delete_cascade.py \
     tests/test_exported_watermark.py tests/test_look_strength_foundation.py \
     tests/test_user_store.py tests/test_global_items_sync.py \
     tests/test_cross_event_resolver.py tests/test_library_gateway.py \
     tests/test_cross_event_cut_session.py tests/test_new_cross_event_dc_dialog.py \
     tests/test_grab_originals.py tests/test_phase2_wiring.py -q
   ```
   Expected: **408/408 green**.
3. Launch the app on a real library, click the `+ Collection` button on
   the cross-event band, build a cross-event DC, confirm it lands in
   `saved_filter`. That's the Phase-2-complete acceptance test from the
   user's perspective.
4. Pick the next item from "What's NOT in Phase 2" above.

---

## A note from the Phase-2 session

The recommendation set held up against the code with no flag-backs — every
"pick the recommendation" decision worked end to end. The schema
trajectory (v7→v8→v9 on event.db, v2→v3→v4 on user.db) is heavier than I
expected at kickoff; pre-ship reset will fold these into one DDL. The
table-rebuild dance with `defer_foreign_keys=1` handled both reshapes
cleanly.

The biggest live-app risk is the same as Phase 1's KI-1 — Qt focus / paint
on the cross-event dialog. The dialog has 17 facet widgets, far more
state than the event-scope New Cut dialog. Plan for an in-app walkthrough
on a real library before declaring the cross-event surface
production-ready. The dialog tests cover the value composition + event
flow but can't catch a paint regression.

— Claude, 2026-06-16
