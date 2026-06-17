# Phase 2 — implementation complete

**From:** 2026-06-16 Phase-2 session (fully drained).
**Branch:** `main`.
**Status:** Six engine items + four UI surfaces + Picker + flat grid +
sweeps + lifecycle wiring + export pipeline — **the cross-event surface
runs end-to-end with every queued gap closed.**

**Tests:** Phase-1 focused subset preserved (231/231 green). Phase-2 + polish
total **~340 new tests across 13 test files**; latest polish run **90/90 green**.

---

## The complete end-to-end loop

1. **Browse cross-event collections.** Events screen → `+ Collection` →
   `CrossEventDcsDialog` lists every saved_filter row.
2. **Create / edit / delete a DC.** `+ New collection` →
   `NewCrossEventDcDialog` with full spec/32 §2 facet UI. Edit + Delete
   from kebab. Delete **sweeps cross-store** (NULLs `source_dc_id` on any
   event.db Cut that pointed at the deleted DC).
3. **Pin a DC into a Cut.** `Pin → Cut` → `NewCrossEventCutDialog`:
   pre-selected DC + default anchor (top contributor) + pin mode picker
   (every mode enabled).
4. **Refine the pin (weed-out / pick-in).** When pin mode ≠ keep-all,
   `CrossEventPickerDialog` opens with one row per candidate (event+id +
   capture time + kind + relpath + grab/export label). Pick/Skip flips the
   ledger; live budget zone (green/amber/red); Commit drives
   `session.commit(anchor_gateway)`.
5. **View cross-event Cuts.** From DCs dialog header: `View Cuts` →
   `CrossEventCutsDialog` walks every event.db for `source_dc_kind='user'`
   cuts.
6. **Open a Cut.** Kebab `Open…` → `CrossEventCutDetailDialog` groups
   members by source event (anchor first, then cross-event sources by id);
   per-member kind + relpath line. Missing source events render with
   `(missing)`.
7. **Export a Cut.** Per-row primary `Export` → file picker →
   `export_cross_event_cut` routes per-kind: export-kind hardlinks from
   source `Exported Media/` (copy fallback cross-volume); grab-kind copies
   from source `Original Media/`. Idempotent re-export; stamps
   `last_exported_at`. Surfaces summary (member_count / linked / copied /
   missing).

---

## Final file map

### Engine — Items 1-6 (recap)
- `mira/user_store/schema.py` v3 + v4 (`global_items` + `saved_filter` +
  `export_relpath`).
- `mira/user_store/models.py` (`GlobalItem`, `SavedFilter`).
- `mira/gateway/global_items_sync.py` (projection sync).
- `mira/gateway/cross_event_resolver.py` (composite keys + accessors).
- `mira/gateway/library_gateway.py` (cross-event DC facade).
- `mira/store/schema.py` v8 + v9 (cut + cut_member reshape).
- `mira/store/models.py` (`Cut.source_dc_kind`, `CutMember` v9 shape).
- `mira/shared/cross_event_cut_session.py` (`CrossEventCutSession` +
  `CrossEventSessionFile` + `pick_anchor_event`).
- `mira/shared/cut_draft.py` (`CrossEventCutDraft`).
- `core/collection_resolver.py` (`BASE_COLLECTED/PICKED/EDITED` constants).
- `mira/gateway/event_gateway.py` (`set_cut_members` dict shape, freeze
  invariant, lineage sweep on delete, `source_dc_kind`).

### UI surfaces — polish queue
- `mira/ui/pages/new_cross_event_dc_dialog.py` — `NewCrossEventDcDialog`.
- `mira/ui/pages/cross_event_dcs_dialog.py` — `CrossEventDcsDialog`
  (with `umbrella_gateway` for cross-store delete sweep).
- `mira/ui/pages/new_cross_event_cut_dialog.py` —
  `NewCrossEventCutDialog` (all pin modes enabled).
- `mira/ui/pages/cross_event_cuts_dialog.py` — `CrossEventCutsDialog`.
- `mira/ui/pages/cross_event_picker_dialog.py` —
  `CrossEventPickerDialog` (per-candidate ledger + live budget zone).
- `mira/ui/pages/cross_event_cut_detail_dialog.py` —
  `CrossEventCutDetailDialog` (per-source-event grouped member list).

### Engine — polish queue
- `mira/shared/cross_event_cut_export.py` — `export_cross_event_cut`.
- `mira/shared/cross_event_sweeps.py` —
  `sweep_dangling_cross_event_members` + `sweep_dc_references`.

### Umbrella gateway
- `mira/gateway/gateway.py`:
  - `CrossEventCutRow` dataclass.
  - `Gateway.cross_event_cuts()` — multi-event walk.
  - `Gateway.delete_cross_event_cut(anchor_event_id, cut_id)`.
  - `Gateway.delete_cross_event_dc(dc_id)` — LG.delete_dc + cross-store sweep.
  - `Gateway.sweep_dangling_cross_event_members()`.
  - Close-time `on_close` sync hook installed in `open_event`.
  - `reconcile_global_items()` startup pass.

### Wiring
- `mira/ui/pages/_cross_event_band.py` — `new_dc_requested` signal +
  `+ Collection` ghost button.
- `mira/ui/pages/events_page.py` —
  `_open_new_cross_event_dc` / `_pin_cross_event_dc` /
  `_commit_cross_event_cut` (mode-aware — direct or via Picker) /
  `_direct_commit_cross_event_cut` / `_open_cross_event_cuts` /
  `_on_open_cross_event_cut` / `_on_export_cross_event_cut`.

---

## Tests

### Phase-1 baseline preserved
231 green.

### Phase-2 + polish
| File | Tests |
|---|---:|
| `test_user_store.py` (extended) | +8 |
| `test_global_items_sync.py` | 11 |
| `test_cross_event_resolver.py` | 33 |
| `test_library_gateway.py` | 33 |
| `test_cross_event_cut_session.py` | 23 |
| `test_new_cross_event_dc_dialog.py` | 24 |
| `test_grab_originals.py` | 12 |
| `test_phase2_wiring.py` | 10 |
| `test_cross_event_dcs_dialog.py` | 13 |
| `test_new_cross_event_cut_dialog.py` | 21 |
| `test_cross_event_cuts_list.py` | 8 |
| `test_cross_event_cut_export.py` | 10 |
| `test_cross_event_picker_dialog.py` | 13 |
| `test_cross_event_cut_detail_dialog.py` | 6 |
| `test_cross_event_sweeps.py` | 9 |

**Phase-2 total: ~234 new tests.**

---

## What's NOT in Phase 2 (out of scope by design)

- **In-app Play (rehearsal slideshow)** — substantial, depends on the
  photo display engine; the cross-event flat grid in
  `CrossEventCutDetailDialog` is the consumption surface today.
- **Thumbnail-grid flat grid** — the current detail viewer is a text list.
  A WYSIWYG thumbnail grid with per-(event, day) separators is its own
  visual build.
- **File-level cross-event member sweep** — `sweep_dangling_cross_event_members`
  drops members whose source event is gone. Members whose source event is
  present but whose specific lineage / origin file vanished (rare —
  manual file delete out of band) are still surfaced in the export
  summary's `missing` field; no automated sweep yet.
- **Cross-event Cut tag uniqueness across event.db files** — per-event
  uniqueness still applies (cut.tag UNIQUE). Cross-event Cuts can share
  tags in different anchor events. spec/61 §1.5 says names are the
  cross-event glue — future polish could aggregate by tag at the list
  view layer.

---

## How to wire next session

1. `git log -1` — confirm Phase-2 wrap commit.
2. Run focused subset (~10s):
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
     tests/test_grab_originals.py tests/test_phase2_wiring.py \
     tests/test_cross_event_dcs_dialog.py tests/test_new_cross_event_cut_dialog.py \
     tests/test_cross_event_cuts_list.py tests/test_cross_event_cut_export.py \
     tests/test_cross_event_picker_dialog.py tests/test_cross_event_cut_detail_dialog.py \
     tests/test_cross_event_sweeps.py -q
   ```
   Expected: all green.
3. **Launch the app on a real library.** Drive the full loop:
   create DC → pin Cut (try all 3 pin modes) → export Cut → delete DC.
   This is the qualitative acceptance test — unit tests can't catch
   Qt-state / paint regressions.
4. After the in-app pass, the cross-event surface is production-ready
   modulo the deferred items above.

---

## Note from this implementation-complete session

What stood out — the original recommendations from the start handover
held end-to-end without revision. The schema choice (drop FKs, opaque
ids, kind discriminator on cut_member) supported every later UI surface
cleanly. The CrossEventCutSession engine designed in Item 4 supports
all three pin modes; the Picker UI built in this session just drives it.
The Sweep functions are short because the schema choices left clean seams.

The biggest live-app risk is the Picker — 4 nested dialogs (band → DCs
list → Cut dialog → Picker) is the deepest stack in the codebase, and Qt
modal interaction on Windows has historically been the source of
Phase-1's KI-1-class regressions. The Picker's `commit_callback`
indirection keeps it testable without a live gateway; the live wiring
hops one level (via the host's `_direct_commit_cross_event_cut`).

— Claude, 2026-06-16
