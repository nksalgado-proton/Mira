# spec/12 — New Event (plan-only event creation)

**Build-order #2 (PROGRESS "Build order — CORRECTED to the Mira pipeline").** The
second event-creation entry point alongside spec/10's "Create Event from Photos". Where
ingest *imports photos and assembles items*, New Event creates a **plan-only** event — name
+ start date + an optional trip-day plan, **no items**. Photos arrive later (via Create
Event from Photos, or a per-event Cull import).

As-built 2026-05-31. Reused from the legacy `ui/pages/new_event.py` (`NewEventPage`); only
the data seam is rewired (charter §5.2 — [[feedback_reuse_legacy_ui_dont_recreate]]).

---

## 1. The surface (reused verbatim)

`mira/ui/pages/new_event_page.py` — `NewEventPage(QWidget)`:

- **Event name** (`QLineEdit`) + **Start date** (`QDateEdit`, calendar popup, floor 2000).
- **Edit plan…** → the ported `PlanEditorDialog` (`mira/ui/base/plan_editor_dialog.py`),
  seeded with the current pending days; on Apply, replaces the pending plan and snaps the
  form's start date to the plan's earliest. Create-only, so the dialog gets `event=None`
  (no on-disk photos to gate Remove-day against).
- **Import plan from folder…** → derive a plan skeleton from an already-organised
  `Dia N - description` tree (the revisit-a-past-trip case). Brain-only: reads folder names
  + samples EXIF dates, mutates nothing.
- **Cancel** / **Create**.
- The TZ-mismatch heads-up + "where the event lives" message are preserved (docs/14).
- spec/05 admission: pointing-hand cursor + tooltip on every button, hints on both fields.

Signals: `event_created(event_id)`, `cancelled()`.

## 2. The data seam (the only change from legacy)

| Legacy | New |
|---|---|
| `data.event_store.save_event(Event)` | build `m.EventDocument` (Event + trip_days, **no items**) |
| `core.event_service.create_folder_structure` | *dropped* — the tree is a rebuildable projection (charter §3); `create_event` only `mkdir`s `event_root` + writes `event.db` |
| `core.settings.load_settings()["photos_base_path"]` | `Gateway.photos_base_path()` |
| legacy `home_timezone` setting | `Gateway.settings.load().home_timezone` |
| `name_collision` querying the legacy store | matches from `Gateway.list_events()` → the pure-UI `confirm_name_collision` |
| — | **`Gateway.create_event(doc, event_root)`** (the same call ingest uses) |

`event_root = photos_base_path / sanitize_folder_name(name)`. The id is a fresh `uuid4().hex`;
`created_at`/`updated_at` are UTC-now ISO.

**Legacy → store `TripDay` mapping** (the plan editor works in `core.models.TripDay`):
`day_number` / `description` / `location` pass through; `date` → ISO string; `tz_offset`
(hours, may be `None`) → `tz_minutes = round(tz_offset*60)` (or `None`). Mirrors the ingest
engine's day-row construction (spec/10 §4 step 4).

## 3. Guards

- **No name** → warn, refocus the field, abort.
- **No `photos_base_path`** → warn ("set it in Settings first"), abort (no event root to
  resolve — charter §5.9). *(Legacy prompted for the path inline; the rebuild surfaces the
  same requirement but defers the path-set to the reused Settings dialog.)*
- **Name collision** → the shared-folder warning; default No.
- **OSError** during `create_event` → non-blocking warning, abort.

## 4. Shell wiring

`MainWindow` registers `NewEventPage` under `ENTRY_NEW_EVENT` (replacing its placeholder).
Navigating to the entry calls `clear_for_create()` (fresh form). On **`event_created`** the
shell **opens the new event directly on its per-event dashboard** — a new event is always
open, so it follows the same landing rule as activating a card (matches legacy
`MainWindow._on_event_created`, Nelson 2026-05-29), rather than dropping the user back on the
events list to hunt for an empty card. `_on_event_created` is shared with Create-from-Photos
(both land on the new event's dashboard); the modal Create-from-Photos path defers this
routing until after `exec()` returns. On **`cancelled`** the shell returns to the events list.

## 5. Land straight in the editable plan table (plan-edit slice of build-order #4)

New Event exists to author a plan, so after creation the shell drops the user **straight into
the editable plan table** rather than leaving them on the dashboard (Nelson 2026-05-31). The
plan-editing slice of build-order #4 (Plan/Manage) was pulled forward to make this real; the
*rest* of that page (Adjust TZ / Camera Clocks / Relocate / Delete / Audit / Open-Closed pill)
is still build-order #4.

- **`MainWindow._open_plan_editor_for_event(event_id)`** — the single plan-edit entry,
  mirroring legacy `MainWindow._open_plan_editor_for_event`. Reuses the ported
  `PlanEditorDialog`; converts store `TripDay` → legacy `core.models.TripDay` on the way in
  and back on the way out (`tz_minutes` ⇄ `tz_offset` hours); on Apply persists via the
  gateway and refreshes the per-event dashboard + the events list. `event=None` keeps the
  dialog's filesystem remove-day gate off — the gateway mutator is the real safety net.
- Two callers: **New Event create** (`_on_new_event_created` = open the event + then open the
  plan editor on top) and the **Plan tile** on the per-event dashboard
  (`_on_phase_activated('plan')`). Create-from-Photos does **not** auto-open it (its wizard
  already collected the plan).
- **`EventGateway.save_trip_days(days)`** (new mutator) — replace-all (DELETE + re-insert) in
  one transaction with `PRAGMA defer_foreign_keys = ON`, so the editor's full renumbered
  `1..N` set is written in any order. `item.day_number → trip_day` is enforced at commit: a
  removal that would orphan items raises `sqlite3.IntegrityError`, which the caller surfaces
  as "move the photos off that day first" and the transaction rolls back. Plan-only events
  (no items) edit freely.

**Known limitation (deferred to full build-order #4):** `PlanEditorDialog.get_trip_days`
renumbers days `1..N` by date, so re-ordering days by date on an event that *already has
items* can shift which `day_number` an item maps to (the item↔day remap is not yet handled).
Safe for plan-only events (the New Event case) and for pure field edits that don't reorder or
remove dated days; a removal that orphans items is rejected outright.

## 6. Gate

`tests/test_new_event_page.py` (5 tests): create materialises a plan-only event that
round-trips through the gateway (correct name/start_date, `event.db` under `<base>/<name>`,
zero items, form reset); the plan carries through with `tz_offset`→`tz_minutes`; no-name and
no-base are rejected; a declined name-collision blocks the second create.

`tests/test_plan_editor_flow.py` (5 tests): `save_trip_days` replaces the set / shrinks a
plan-only event / rejects removing a day with items (rollback leaves the plan intact); New
Event create lands on the per-event dashboard with the editor's plan persisted; the Plan tile
seeds the editor with the event's days (store→legacy `tz` conversion).

## 7. Not in scope here

`create_event` materialises the `event.db` only — the pipeline folder tree (`00 - Captured`
…) is a projection rendered when items exist (charter §3), so a plan-only event has an empty
tree until photos are ingested. Editing an existing event's plan is the Plan/Manage surface
(build-order #4, spec TBD), not this page.
