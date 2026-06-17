# spec/86 — Event-data filters for cross-event collections

**Status:** design agreed with Nelson 2026-06-17; the **extra task** after the
spec/83+85 filter build shipped. Adds a fifth **"Event"** group to the
cross-event collection dialog so a DC can filter on the *event's own
qualifiers* — type, subtype, scope, participants — and on an **event date
range**. Same opt-in philosophy as everything else: the dialog still opens with
no filters; these appear only when the user adds them.

Builds directly on what just shipped:
- `mira/ui/pages/_filter_family.py` — `FilterDimension`, the group constants +
  `GROUP_ORDER` + `group_label`, and `build_cross_event_catalogue(host)`. This
  spec **adds a group + dimensions** to that catalogue; it does not build new
  machinery.
- `mira/gateway/library_gateway.py` — the `available_*` / `facet_inventory(key)`
  inventory pattern (returns `(value, count)`).
- `mira/gateway/global_items_sync.py` + the `global_items` projection — extended
  here with the event qualifiers.
- The facet picker + adaptive inline editor (spec/83 §3–§4), reused as-is.

Read with: `spec/83` (the two-tier model), `spec/32 §2` (facet catalogue),
`spec/52` (event qualifiers), `spec/81 §2.1` (cross-event surface).

---

## 1. Why
The cross-event DC can today filter on item/EXIF data and on **location**
(country/city) — but **not** on the event's own qualifiers. So "all my
**wildlife trips**", "all **international** trips", "**family occasions**",
"trips from **2015–2018**" are unbuildable, even though the data exists per
event. These are the event-shaped complement to the EXIF facets, and the
date-range one in particular makes many searches **far more efficient** — an
event-level date bound prunes whole events before any item is touched (matters
over a 30-year archive).

## 2. Scope — cross-event only
This group is added to **`build_cross_event_catalogue` only**. The event-scope
dialog (`build_event_scope_catalogue`) stays thin (spec/81 §2.1) — within one
event the qualifiers are constant, so they are not filters there.

## 3. Which qualifiers (the columns that actually exist)
Spec against the surviving `event` table columns, **not** spec/52's retired
Mood/Transport vocabulary:

| Dimension | Source column | Cardinality | Editor |
|---|---|---|---|
| **Event type** | `event_type` (`trip`/`session`/`occasion`/`project`/`unclassified`) | 5 fixed | inline multi |
| **Event subtype** | `event_subtype` (free text, curated presets) | medium | adaptive (picker if > threshold) |
| **Scope** | `experience_type` (e.g. international / domestic) | few | inline multi (or single) |
| **Participants** | `participants` (JSON array: Solo/Couple/Family/Kids/Friends/Colleagues/Client) | small fixed | inline multi |
| **Event date range** | derived `event_start` / `event_end` (§5) | n/a | from/to range |

`duration_value`/`duration_unit` exist but are low-value as a filter — **omit
from v1** unless Nelson wants it (open question §7).

## 4. Projection — push the qualifiers into `global_items`
Per-event qualifiers are the **same for every item of an event**, so denormalise
them into the projection (the model spec/32 §3 already uses for location):
- Add columns to `global_items`: `event_type`, `event_subtype`,
  `experience_type`, `participants`, `event_start`, `event_end`.
- `global_items_sync.py` reads them from the event row (and §5 derives the
  dates) and writes them on every item of that event.
- A **user-store migration** (the gear work landed v4→v5; this is **v5→v6**),
  mirroring that pattern. Existing events backfill on the next sync / reconcile.

## 5. Event date range — derived span, kept beside Capture date
- The `event` table has no explicit start/end; derive **`event_start` /
  `event_end` = min/max of the event's `trip_day` dates**, computed at sync and
  stored on the projection rows.
- The **Event date range** filter (`event_from` / `event_to`) selects items
  whose event **overlaps** the requested window (event_start ≤ to AND event_end
  ≥ from). Overlap, not containment — a trip that straddles the boundary still
  matches.
- **Keep the existing item-level Capture date facet too** — they answer
  different questions (which *events* happened then vs. which *photos* were shot
  then). Both available; user picks. (Decided 2026-06-17.)

## 6. Wiring (reuse, don't rebuild)
- **New group** in `_filter_family.py`: `GROUP_EVENT = "event"`, added to
  `GROUP_ORDER` (place after Curatorial) + a `group_label` entry ("Event").
- **New dimensions** registered in `build_cross_event_catalogue` via the
  existing `host._make_multi` / `_make_single` / `_make_date_range` factories —
  `event_type`, `event_subtype`, `scope`, `participants`, `event_date`.
- **Inventory:** extend `available_*` / `facet_inventory` with count queries for
  `event_type`, `event_subtype`, `experience_type`, and `participants`.
  `participants` is a JSON array → expand with `json_each` for distinct values +
  counts. Subtype flows through the same adaptive editor (inline ≤12, picker
  above), so a big subtype vocabulary is handled for free.
- **Resolver:** `apply_filters` honours the new keys — `event_type` /
  `event_subtype` / `experience_type` as `IN (...)`, `participants` as a JSON
  overlap, `event_from`/`event_to` as the §5 overlap range. Event-level
  predicates are cheap and prune whole events first.

## 7. Slices
1. **Projection + migration** — add the six columns to `global_items`
   (v5→v6 user-store migration); `global_items_sync` writes the qualifiers +
   derived `event_start`/`event_end`; reconcile backfills. Tests.
2. **Inventory queries** — `available_*` / `facet_inventory` for `event_type`,
   `event_subtype`, `experience_type`, `participants` (json_each), with counts.
   Tests.
3. **Resolver** — `apply_filters` honours the new keys incl. the event-date
   overlap. Tests on a multi-event fixture (efficiency: event prune).
4. **The Event group** — `GROUP_EVENT` + dimensions in `_filter_family`; appears
   in the Add-filter menu; rehydrate/edit-flow + live count work. Tests.

## 8. Open questions
- **Duration filter** — include `duration_value`/`unit` in v1, or omit (lean:
  omit)?
- **Scope editor** — `experience_type` as multi-select or single? (Confirm the
  actual vocabulary first.)
- **Participants match** — "any of" (overlap, lean) vs "all of" the selected
  categories.
- **Event date overlap vs containment** — overlap chosen (§5); revisit only if a
  "wholly within" mode is wanted.
