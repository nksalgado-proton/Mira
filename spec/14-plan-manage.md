# spec/14 — Plan / Manage surface (EventPlanPage) — REUSE MANIFEST

**Status: Slice A BUILT (2026-05-31, Nelson-approved). Slices B + C pending.**
Authored 2026-05-31. Build-order surface **#4** (the largest of the front-of-app four).
The plan-editing slice already shipped (`save_trip_days` + `_open_plan_editor_for_event`,
2026-05-31); this manifest covers the *rest* of the page.

> **As-built — Slice A (2026-05-31):** ported the legacy `EventPlanPage` verbatim into
> `mira/ui/pages/event_plan_page.py`, rewiring ONLY the data seam: `load()` builds a
> legacy `Event` adapter from the gateway (`open_event` → `event`/`trip_days`), dropping the
> `reconcile_phase_progress`+`save_event` step (`phase_progress` is a live query now); the
> Open/Closed pill persists via `EventGateway.set_closed`; Delete goes through the new
> `Gateway.delete_event` (index-only). Inserted **before** the phase grid in `MainWindow`
> routing (`_EVENT_PLAN_KEY`; `_open_event` lands here, "Open phases →" → grid, grid Back →
> plan page). The **2×2 overview is a placeholder** (Slice C). The behavior-changing actions
> (Camera clocks, Adjust TZ, Relocate → Slice B) and the deferred ones (LRC, Audit → Slice C;
> Back up / Restore → build-order #3) keep their faithful buttons + tooltips but route to a
> "being reassembled next" notice. **Camera clocks moved A→B** (justified refinement, propose-
> first amendment): persisting a clock answer is a no-op in the virtual-EXIF model unless it
> re-derives `capture_time_corrected` — the same recompute Adjust TZ needs, so they ship
> together. The **Collections button** is deferred to Slice C (the Collections surface itself
> is downstream) rather than shown as a dead control. `tests/test_event_plan_page_rebuild.py`
> (5 tests). `Gateway.delete_event` added to `mira/gateway/gateway.py`.

---

## 0. What this surface is

The legacy **`ui/pages/event_plan_page.py` → `EventPlanPage`** (740 lines) is the FIRST
page the user lands on when opening an event — the strategic per-event home. It carries:

- **Header** — event name + state badge (`● Open` / `✓ Closed`).
- **Subtitle** — duration · date range · dominant TZ · first location.
- **Three action groups** (wrapping rows): *Plan & data* · *Location & backup* · *Event state*.
- **2×2 graphical overview** (`TwoByTwoOverview`, `event_plan_overview.py`, 1061 lines) —
  cycling TZ display, style pie, kept-vs-captured bars, random photo + slideshow chips.
- **Bottom nav** — `← Events` · `📚 Collections` · `Open phases dashboard →`.

**Routing today in the new app:** opening an event jumps straight to the per-event
**dashboard** (`event_dashboard_page.py`, the PhaseButton 2×3 grid). The legacy flow puts
**EventPlanPage *before* the dashboard** (`Open phases dashboard →` is the CTA into the grid).
Porting #4 means inserting the strategic page in front of the existing tactical grid, matching
legacy routing exactly.

---

## 1. Dialogs / widgets opened, in order, + the exact data seam

Each row is a legacy entry point, what it opens, and the precise persistence calls to rewire
to the gateway. **Only the data calls change** (charter §0).

| # | Action (button) | Legacy handler → opens | Legacy data calls | New seam (gateway) |
|---|---|---|---|---|
| 1 | **Edit plan** | `edit_plan_requested` → `NewEventPage` edit / `PlanEditorDialog` | `save_event` | **DONE** — `save_trip_days` + `_open_plan_editor_for_event` (2026-05-31) |
| 2 | **Camera clocks** | `_on_camera_clocks` → `CameraClockDialog` | `load_camera_clocks(event)` / `save_camera_clocks(event, …)` + `save_event`; `plan_trip_tz(event.trip_days)` | reads `cameras()` (exists); **ADD `save_camera`** mutator |
| 3 | **Adjust TZ** ⚠ | `_on_adjust_tz` → `AdjustEventTzDialog` | `core.adjust_event_tz` (bakes EXIF in place) + `save_event` | **BEHAVIOR CHANGES** — see §2.1 (no bake; re-derive `capture_time_corrected`) |
| 4 | **Re-import from LRC…** | `_on_lrc_reimport` → `LrcReimportDialog` | `core.lrc_reimport` + `load_settings` | reads settings (exists); writes **lineage** (`record_lineage` exists) + classification — see §3 (later slice) |
| 5 | **Relocate** ⚠ | `_on_relocate` → `RelocateEventDialog` | rewrite `Event.photos_base_path` (absolute) + `save_event` | **BEHAVIOR CHANGES** — see §2.2 (relative-path model: index re-anchor) |
| 6 | **Back up event** ⏸ | `_on_back_up` → `BackupEventDialog` | folder mirror | **DEFERRED** (build-order #3) — button present but inert/hidden until #3 |
| 7 | **Restore from backup** ⏸ | `restore_requested` → `RestoreBackupDialog` | folder merge | **DEFERRED** (#3) — `Gateway.materialise_event` exists for the eventual wiring |
| 8 | **Audit** ⚠ | `_on_audit` → `ConsistencyAuditDialog` | `core.consistency_audit` (journal ⇄ on-disk projection) | **BEHAVIOR CHANGES** — see §2.3 (store IS the projection source) |
| 9 | **Close / Re-open** | `_on_close_toggled` | `event.is_closed = …` + `save_event` | **`set_closed(value)`** — EXISTS ✓ |
| 10 | **Delete event** | `delete_requested` → MainWindow confirm | remove app record | **ADD `delete_event`** (index remove; DB drop optional) |
| 11 | **Open phases dashboard →** | `open_dashboard_requested` | — (navigation) | existing `event_dashboard_page` ✓ |
| 12 | **📚 Collections** | `open_collections_requested` | — (navigation) | downstream surface (Curate/Collections) — button present, routes later |
| 13 | **2×2 overview** | `TwoByTwoOverview.populate(event, settings, buckets, …)` | `core.event_stats`, `discover_curated_buckets`, `load_settings` | reads-only; feed via gateway-derived stats / Event adapter — see §3 |
| 14 | **bucket chip** | `browse_bucket_requested(name, files)` → slideshow | curated-browse | downstream (Collections / slideshow) |

Also on `load()`: legacy calls `reconcile_phase_progress(event)` + `save_event`. In the new
app **`phase_progress` is a live query, never a cache** (gateway already does this) — so the
reconcile-and-save step is **dropped**, not ported (charter §5.4; the staleness bug the rebuild
fixes).

---

## 2. Behavior changes — PROPOSE FIRST (charter §5.4 + 2026-05-31 amendment)

These three actions were driven by the *broken* model. Rewired, they change — for the better.
Per the amendment, I'm proposing the new semantics here for your OK; none is a silent swap.

### 2.1 Adjust TZ — no EXIF bake (virtual EXIF)
Legacy `AdjustEventTzDialog` rewrites `00 - Captured` EXIF in place. The rebuild's locked
decision (charter §3) is **virtual EXIF**: originals byte-pristine, correction lives in the
record (`capture_time_raw` never mutated, `capture_time_corrected` derived). So Adjust TZ in
the new world: **reuse the same dialog UI**, but the commit **re-derives `capture_time_corrected`**
for the affected items (per-camera / per-day offset) and saves the item records + marks
downstream buckets dirty — **no file write**. The dialog's "modifies EXIF in place" copy is
adjusted to match (courtesy filenames in the rendered tree still reflect corrected time).
*New gateway mutator needed:* `recompute_corrected_times(...)` (or reuse `save_item` per item).

### 2.2 Relocate — one-setting / index re-anchor (relative paths, §5.9)
Legacy rewrites the absolute `Event.photos_base_path`. The rebuild's anchor is the single
`photos_base_path` setting; every event path is relative to it (index stores `event_relpath`
+ `event_root_abs` cross-volume fallback). So **whole-library relocate = edit one setting**
(already possible via Settings). **Per-event relocate** = update that event's index entry
(`make_entry` re-anchor rule, exists in `index.py`). Reuse the `RelocateEventDialog` UI;
rewire the commit to `EventsIndex.upsert(make_entry(...))` instead of mutating the event.
*Open Q:* given whole-library relocate is now a setting, is the per-event Relocate button still
wanted, or does it become "re-point this one event"? **Your call.**

### 2.3 Audit — store is the projection source
Legacy `ConsistencyAuditDialog` regenerates the on-disk `01 - Culled/…` projection from the
journal. In the rebuild the **store is the source of truth and the tree is 100% rebuildable
from it** (charter §3). Audit becomes: compare the store-derived projection vs what's on disk,
offer rebuild-from-store. This is a meatier port (depends on a projection-renderer that itself
isn't built yet). **Proposal: defer Audit to a later slice** alongside the projection renderer.

---

## 3. Heavier / downstream pieces (propose to slice out)

- **TwoByTwoOverview (1061 lines)** — ✅ **BUILT 2026-05-31… → ported 2026-06-01 (Slice C).**
  The widget + `TimezoneMapWidget` are ported **verbatim** into `mira/ui/`; the only change
  is the data seam — `populate(event, eg, …)` sources its quadrant inputs from an open
  `EventGateway` via the new pure `mira/overview_stats.py` (funnel ← `phase_progress`; style
  pie + random photo ← the **furthest phase with kept items**, the rebuild's stand-in for the
  dropped `is_phase_done`; random photo resolved on the fly as `event_root / origin_relpath`).
  Wired into `EventPlanPage` (replaces the placeholder; click → phases dashboard, inert when
  closed). **Slideshow chips deferred** until the Curate/Collections surface lands
  (`show_slideshows=False`). Tests: `test_overview_stats.py`.
- **LRC re-import** — touches lineage + classification + settings; meaningful only once Process
  exists. Proposal: later slice.
- **Collections** — a distinct downstream surface; the button routes when that surface lands.

---

## 4. Proposed slicing (your decision)

**Slice A — the page + cheap, model-clean actions (recommended first):**
EventPlanPage shell (header, subtitle, 3 action groups, bottom nav) inserted *before* the
existing dashboard in routing · **Open/Closed pill** (`set_closed` ✓) · **Edit plan** (done ✓) ·
**Camera clocks** (add `save_camera`) · **Delete** (add `delete_event`) · **Open phases →**
(exists ✓) · Back-up/Restore buttons present but inert (deferred #3) · 2×2 = placeholder.
Smallest faithful port; no behavior-change debates block it.

**Slice B — the model-changed TZ/path actions:** Adjust TZ (§2.1, no bake) · Relocate (§2.2,
re-anchor). Needs your sign-off on the new semantics first.

**Slice C — heavy/downstream:** TwoByTwoOverview · LRC re-import · Audit · Collections routing ·
slideshow chips. Land as their dependencies (curate data, projection renderer, Process) arrive.

---

## 5. Gateway methods: existing vs to-add

**Exist (reuse):** `event()`, `trip_days()`, `cameras()`, `participants()`, `checklist()`,
`distribution()`, `phase_progress()`, `phase_day_progress()`, `set_closed()`, `save_trip_days()`,
`materialise_event()`, `EventsIndex.make_entry/upsert/remove`.

**To add (one at a time, charter §2 — each answers spec/08 §5.3 invariant-vs-model-gap):**
- `EventGateway.save_camera(camera)` — replace-or-insert one Camera (clock-edit commit).
- `Gateway.delete_event(event_id)` — remove the index row (+ optional `event.db` drop; **propose:
  index-only, never touch the photo tree** — mirrors legacy "your photos are NOT deleted").
- *(Slice B)* a corrected-time recompute path for Adjust TZ; index re-anchor for Relocate
  (the rule already lives in `make_entry`).

---

## 5B. Slice B — REUSE MANIFEST (the model-changed actions) — **BUILT 2026-05-31**

> **As-built (2026-05-31):** shipped the shared recompute primitive + B1 + B2; B3 cut.
> - **`EventGateway.recompute_corrected_times(camera_id, *, applied_offset_minutes, day_number=None)`**
>   — re-derives `capture_time_corrected = capture_time_raw + offset` (raw never touched, G5),
>   updates `tz_offset_minutes`/`tz_source='manual'`, **reassigns `day_number`** from the new
>   corrected date (smallest-day-number-wins), skips quarantined no-raw items, flags downstream
>   `derived_dirty` (G4); the Cull bucket cache self-invalidates via its per-day fingerprint.
> - **`EventGateway.save_camera(camera)`** — replace-or-insert one Camera row.
> - **B1 Camera clocks:** `CameraClockDialog` ported verbatim → `mira/ui/culler/
>   camera_clock_dialog.py` (only `ui.*`→`mira.ui.*` import swaps; pure dialog). Wired via
>   `MainWindow._open_camera_clocks_for_event` — builds the answer record from `cameras()`
>   (`applied = trip_tz − configured`), and on Save persists `save_camera` + `recompute` per
>   changed camera.
> - **B2 Adjust TZ:** `AdjustEventTzDialog` ported → `mira/ui/pages/adjust_event_tz_dialog.py`
>   (UI verbatim; reads from `cameras()`/`items()`, **apply = recompute, no bake**; text reworded
>   to virtual-EXIF). Gateway-native (`gateway`+`event_id`). Wired via `_open_adjust_tz_for_event`.
> - **EventPlanPage:** the two buttons now emit `camera_clocks_requested` / `adjust_tz_requested`;
>   **the Relocate button was removed** (B3 cut).
> - Tests: `tests/test_recompute_tz.py` (recompute raw-untouched/corrected/day-reassign/dirty +
>   day filter + `save_camera`). Recompute algorithm independently verified. ⚠ couldn't run the
>   Qt suite in-sandbox (no Qt libs + stale shell mount on edited files) — **run on Windows**.

> **DECISIONS (Nelson 2026-05-31):** (1) **Per-event Relocate button DROPPED** — rely on the
> single library anchor (`photos_base_path` Settings edit, charter §5.9); **Unit B3 + the
> `relocate_event` gateway method are cut**, and the Relocate button is removed from
> `EventPlanPage` entirely. (2) **Sequencing = my call →** ship the shared recompute primitive +
> **B1 (Camera clocks)** + **B2 (Adjust TZ)** as one coherent block (they share the mechanism).
> So **Slice B = recompute + B1 + B2**. The text below keeps B3 struck for the record.

**Three actions, two of which share one new mechanism.** Per charter §0: dialogs + exact data
calls below; the new semantics were OK'd in principle (§2). Awaiting OK on this detailed plan +
the two open questions before coding.

### The shared primitive — the corrected-time recompute (replaces the EXIF bake)
Legacy Adjust-TZ / Camera-clocks **bake** EXIF on disk. New model = virtual EXIF (charter §3):
the correction is *data*. Both actions converge on ONE new gateway mechanism:

> **`EventGateway.recompute_corrected_times(camera_id, *, applied_offset_minutes, day_number=None)`**
> For every item of `camera_id` (optionally only those on `day_number`):
> 1. build a `CameraCalibration` (reuse `core.clock_calibration` / `core.fresh_source.build_tz_calibrations`) from the new offset,
> 2. `capture_time_corrected = corrected_timestamp(capture_time_raw, cal)` (reuse `core.day_assignment`) — **`capture_time_raw` never touched** (G5),
> 3. recompute `tz_offset_minutes` + `tz_source`, and **re-run day assignment** (`assign_one`) since a corrected time can move an item to a different trip day,
> 4. `save_item` each, then `mark_derived_dirty('cull'/'select'/…, affected_ids)` — corrected times changed ⇒ bucket clustering + downstream decisions may need recompute (G4),
> 5. (no file write; courtesy-filename refresh in the tree is a projection concern, deferred).
>
> *Open design point to confirm:* whether step 4's dirty-cascade should also invalidate the
> Cull **bucket cache** for the affected days (it should — the moment-clustering key uses
> corrected times). Proposed: yes, drop those days' cache rows.

### Unit B1 — Camera clocks (`CameraClockDialog`)
- **Reuse:** port `ui/culler/camera_clock_dialog.py` verbatim (the per-camera answer table).
- **Reads (rewire):** legacy `load_camera_clocks(event)` (from `event_settings`) → build the
  `initial` record from the gateway **`cameras()`** rows (`configured_tz_minutes` ⇄ the answer's
  `configured_tz`; absent/None ⇒ "clock was correct"). `plan_trip_tz(trip_days)` → from gateway
  `trip_days()`.
- **Constructor (unchanged):** `CameraClockDialog(cams, default_trip_tz_hours=plan_trip_tz, ask_trip_tz=False, initial=record, edit_mode=True)` → `result_answers()` → `dict{cam:{correct,configured_tz}}`.
- **Commit (rewire):** legacy `save_camera_clocks(event,…)+save_event` → for each changed camera:
  **`save_camera`** (persist `configured_tz_minutes` + derived `applied_offset_minutes`) **+**
  `recompute_corrected_times(camera_id, applied_offset_minutes=…)`. Keep the legacy "re-open the
  cull to re-group" info message (now the dirty-cascade makes it automatic, but the message stays
  faithful).
- **Model note (no gap):** the "human answer" lives in `Camera.configured_tz_minutes` (the answer)
  + `applied_offset_minutes` (the derived shift) — the new model already owns it; no new table.

### Unit B2 — Adjust TZ (`AdjustEventTzDialog`)
- **Reuse:** port `ui/pages/adjust_event_tz_dialog.py` verbatim — the per-camera offset table, the
  hidden **per-day** section (trips crossing zones), the pre-flight confirm (blast-radius summary),
  the progress dialog, the post-run summary.
- **Reads (rewire):** legacy `all_camera_offsets(event)` / `current_offset_for_camera` /
  `day_offset_for_camera` → from gateway `cameras()` (`applied_offset_minutes`) + `trip_days()`;
  `files_for_camera` / `files_for_camera_on_day` (file **counts** for the summary) → gateway
  `items(camera_id=…, day=…)`.
- **Commit (rewire — the behavior change, §2.1):** legacy `_on_apply` calls `adjust_camera_tz` /
  `adjust_camera_day_tz` (filesystem **bake**) + `save_event`. Replace the bake engine with
  `recompute_corrected_times(camera_id, applied_offset_minutes=Δ, day_number=…)` for each row
  where new ≠ current; `save_camera` to persist the new applied offset. The dialog's hint text
  ("re-bake EXIF in 00-Captured") is **reworded** to the virtual-EXIF reality (no file write).
- **Closes:** the G5 residual (TZ correction as data, re-appliable, originals pristine).

### ~~Unit B3 — Relocate~~ — **CUT (Nelson 2026-05-31)**
Dropped per the decision above: whole-library relocate is one Settings edit (charter §5.9); the
per-event Relocate button is removed from `EventPlanPage`, and no `relocate_event` gateway method
is added. (If a single-event cross-volume move ever becomes real, revisit then.)

### Gateway methods to ADD for Slice B (charter §2, one at a time)
- `EventGateway.save_camera(camera)` — replace-or-insert one Camera row.
- `EventGateway.recompute_corrected_times(camera_id, *, applied_offset_minutes, day_number=None)` — the shared primitive (reuses `core.clock_calibration` / `day_assignment`; no bake).

### Wiring + faithful-port notes
- Wire `_camera_clocks_button` + `_adjust_tz_button` on `EventPlanPage` to MainWindow handlers
  that open the ported dialogs (feeding a gateway-built legacy-`Event` adapter where a dialog
  wants one, same pattern as capture_flow / EventPlanPage.load), then `event_plan_page.refresh()`.
  **Remove `_relocate_button`** from `EventPlanPage` (decision above).
- Tests: `recompute_corrected_times` (raw untouched, corrected re-derived, day reassigned,
  dirty-cascade set), `save_camera`, `relocate_event` (relpath + cross-volume), the three dialog
  data-seam commits.

### Two open questions for Nelson (need answers before coding)
1. **Per-event Relocate button — keep it?** Whole-library relocate is now one Settings edit
   (charter §5.9). Per-event Relocate only matters when a *single* event moves to a different
   drive than the base. Keep the button (re-point one event), or drop it and rely on the
   library-level anchor? *(Recommend: keep — it's cheap and the cross-volume case is real.)*
2. **Sub-ordering:** ship B1+B2 together (they share the recompute primitive) then B3, or all
   three at once? *(Recommend: B-recompute + B1 + B2 first, B3 as a quick follow-up.)*

## 5C. Trip-day operations — hide / hard-delete / move (Nelson-approved 2026-06-01)

**Why:** a plan edit must NEVER silently drop a day. (It was: `save_trip_days` did
`DELETE FROM trip_day`+re-insert, and the new real FK `item.day_number … ON DELETE SET NULL`
silently orphaned every photo. Fixed 2026-06-01 — `store.upsert` → `ON CONFLICT DO UPDATE`,
`save_trip_days` is now a diff that refuses to drop a populated day. See
`feedback_never_insert_or_replace_with_fks`.) Removing a day is now an **explicit** operation,
in three flavours:

### 5C.1 Soft-hide / unhide — **build first; data layer is the cleanest slice**
Hide a whole day + its contents so it is disregarded *everywhere* (phase work + completion
metrics), reversibly while the event is open.
- **Flag on the DAY** — new `trip_day.hidden INTEGER NOT NULL DEFAULT 0`. Item visibility is
  **derived** from its day, NOT a per-item flag. Chosen (over per-item) because this feature is
  used exactly when the item set churns (re-reading a source to reshape events): new /
  re-ingested / TZ-moved items then inherit visibility correctly, toggling is one row, no
  N-row denormalisation to keep in sync. (Undated items, `day_number IS NULL`: always visible
  for now; add a narrow per-item override later only if a real need appears.)
- **Apply the filter centrally** in the gateway — the item reads (`items(...)`, `day_tree`,
  `_captured_by_day`) and the phase-count / progress queries exclude items whose day is hidden
  (a JOIN on `trip_day.hidden = 0`, or a `visible_item` view). One place, not scattered.
- **Do NOT touch `phase_state` on hide** — only filter it. So **unhide restores every prior
  cull/select/process decision intact**. "Run phases seeing only the just-unhidden day" needs no
  new persistence — it's a UI filter over `items(day_number = X)`.
- **DATA LAYER DONE (2026-06-01):** `trip_day.hidden` + `visible_item` view; gateway
  `items(include_hidden=)`/`day_tree`/`phase_progress`/`phase_day_progress` derive visibility;
  `set_day_hidden`; backup round-trips the flag. `tests/test_day_hidden.py`.

### 5C.2 Hard-delete a day — records + this event's copied files (manifest below)
Explicit, destructive: remove the day's records AND *this event's copied files under the event
root* (ingest made private copies), behind a blunt warning. The source card/backup is untouched
(honour invariant #9's spirit — never the source).

### 5C.3 Move day(s) to another event — split one source into several events (manifest below)
The clean primitive for subdividing a trip: relocate the item rows + their files between event
roots — no re-reading the card, no redundant copies, **copy-verify-then-remove so no data loss**.
Nelson's preferred subdivide tool.

---

## 5D. REUSE MANIFEST — the "Manage days" dialog + the three operations (Nelson-approved 2026-06-01)

> **Decisions locked (Nelson 2026-06-01):** UI home = a **dedicated "Manage days" dialog**
> opened from `EventPlanPage`; **manifest all three operations first, then build**. The legacy
> app had none of these (no soft-hide, no move-between-events), so this is **genuinely new UI**,
> welcomed under the charter §0 amendment because it's *proposed first* here. It reuses the app's
> established idioms; it does not reinvent them.

### The surface — `ManageDaysDialog` (new, `mira/ui/pages/manage_days_dialog.py`)
Opened by a new **"Manage days…"** button on `EventPlanPage` (event must be **open**; the button
hides when closed, like the other modification buttons, F-024). A modal dialog:
- **One row per real trip day** (stable `day_number`, NOT the plan-editor's renumbered rows):
  `Day N · date · description · NN photos / MM videos · [Hidden]` + per-row actions.
- **Per-row actions:** **Hide/Unhide** (toggle), **Browse…** (reuse the read-only `FastCullerPage`
  in `browse_mode`, exactly as the plan editor's per-day Browse already does), **Delete day…**,
  **Move to event…**.
- **Reused idioms (not reinvented):** `make_columns_resizable` table (spec/05 §4b); the read-only
  Fast Culler browse; the confirm-dialog + busy-cursor + on-surface progress pattern (spec/05 §4b,
  the batch-op framework); `tr()` everywhere; pointing-hand cursor + hints
  (spec/05 admission). **Genuinely new:** the dialog shell + the per-day row model + action wiring.
- **Hidden rows** render distinctly (greyed / a "Hidden" pill) and stay listed so they can be
  unhidden — so the dialog reads days via a gateway call that **includes hidden** (see below).

### Data seam — gateway methods (charter §2, one at a time; mark exist vs new)
- ✅ `EventGateway.set_day_hidden(day_number, hidden)` — **DONE** (5C.1).
- 🆕 `EventGateway.day_summaries(include_hidden=True) -> list[{day_number,date,description,
  photos,videos,hidden}]` — the dialog's row source. (Either add `include_hidden` to `day_tree`
  and join `trip_day.hidden`, or a small dedicated query. The current `day_tree` excludes hidden
  and omits the `hidden` flag the dialog needs.)
- 🆕 `EventGateway.delete_day(day_number) -> {items_deleted, files_deleted}` — **records**: delete
  every item with that `day_number` (FK `ON DELETE CASCADE` removes their phase_state / adjustment
  / video_adjustment / clip_span / curate_tag / stack_member / subset_member / bucket_member),
  then the `trip_day` row. **Files**: delete this event's copies under the event root for those
  items (each captured item's `origin_relpath`; the day's `00 - Captured/<bucket>/<day folder>/`
  subtrees; rebuildable `01/02/03` hardlinks fall out via the existing projection sync or are
  removed alongside). Records in one transaction; file deletion after the records commit (logged).
  Returns counts for the confirm summary.
- 🆕 `Gateway.move_days(source_event_id, day_numbers, target_event_id) -> {moved, ...}` — **on the
  umbrella Gateway** (it opens *both* events). Per item on those days: **copy** the file
  source_root→target_root (under target's `00 - Captured/...`), **sha256-verify the copy**, insert
  the item row into target (carry `phase_state` so cull/select decisions travel; `add_cameras` +
  copy the camera's calibration pair if absent; carry `clip_span`/`video_adjustment`/`adjustment`/
  `curate_tag`), assign to a target `trip_day` (match by date else create a new day, smallest-
  day-number-wins per the existing tie-break). **Only after every file is copied + verified**,
  delete the days from the source (= `delete_day`, but files are already safely in target). This
  copy-verify-then-remove order is the no-data-loss guarantee (mirrors the ingest integrity gate).

### File handling — the delicate part (invariant #9 adjacency)
- **Never touches a source card** — only this event's *managed copies* under the event root. There
  is no source-wipe here; deletion/move operate on Mira's own files.
- **Delete** removes copies permanently; the warning must say so plainly ("This permanently
  removes this event's copies of these N files. Your camera card / original source is not
  touched.") + explicit confirm. Event must be open.
- **Move** is non-destructive end-to-end: copy + verify into the target *before* removing from the
  source, so an interrupted move never loses bytes (worst case = duplicated, never lost).

### Resolved decisions (Nelson 2026-06-01)
1. **Delete confirmation** = **strong Yes/No + file count** (a clear warning naming the day and the
   number of files permanently removed; not type-to-confirm).
2. **Move target** = **existing events + an inline "＋ New event"** (create a blank event and move
   into it — the headline subdivide flow).
3. **Move day-merge** = if the target already has a day with the **same date, merge into it**
   (smallest-day-number-wins, consistent with ingest); otherwise create a new day.
4. **Processed/curated/stacked days** = **block with a clear message** when a day's items are
   referenced by `lineage`/`stack_bracket` (v1 scope = cull/select-level days); never half-move
   derived work.

**Build order — ✅ ALL THREE DONE (2026-06-01, green: 156 passed):**
1. ✅ `ManageDaysDialog` + `EventGateway.day_summaries` + Hide/Unhide + Browse + the EventPlanPage
   "Manage days" button + `MainWindow._open_manage_days_for_event`. (`tests/test_manage_days_dialog.py`.)
2. ✅ `EventGateway.delete_day` (records cascade + this event's copied files; blocks downstream
   lineage/stacks) + the Delete action (strong Yes/No + file count). (`tests/test_day_hidden.py`.)
3. ✅ `Gateway.move_days` (copy + sha256-verify into target, then remove from source; merge-by-date
   target day; blocks days with clips/snapshots or downstream work) + the Move action (existing
   events + inline "＋ New event"). (`tests/test_move_days.py`.)

**Known v1 limitations (documented):** move blocks days that have video clips/snapshots or any
downstream Process/Curate work (cull/select-level captured days only — §5D Q4); moved files keep
their original `origin_relpath` under the target root (the day-folder name in the path reflects
the source day until the tree projection is rebuilt — cosmetic, the tree is rebuildable).

## 6. Discipline checklist (per surface)

- Reuse the legacy widget verbatim; change ONLY data calls (charter §5.2).
- Keep CLAUDE.md invariants: UI→gateway→store one-way · no network · atomic writes · `tr()`
  on every user string · no hardcoded paths · §5.9 relative paths.
- Enforce spec/05 admission test on every admitted widget (cursor + hints + QSS states + `tr()`).
- Land a parity check against the oracle where data-dependent.
- Update PROGRESS.md before stopping.

**Awaiting:** OK on the slicing (start with Slice A?), the §2 behavior changes, and the two
open Qs (per-event Relocate still wanted? delete = index-only?).
