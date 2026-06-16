# Task D — Cross-event DCs + Cuts (Phase 2 — QUEUED)

**Do not start until A–C are green and shape-checked with Nelson.** Same
two-noun/two-verb engine, applied library-wide. **Read first:** spec/81
(§2.1 scope asymmetry), spec/32 (the whole doc — dimensions, `global_items`,
`saved_filter`), spec/61 §8 (cross-event trailhead), spec/75 §2 (the cross-event
band entry point).

## What changes vs. Phase 1 (only the surface widens)

The model is identical; cross-event just exposes more of the same engine
(spec/81 §2.1):

| | Event (Phase 1) | Cross-event (this task) |
|---|---|---|
| Origin universe | `#exported` only | full ladder `#collected / #picked / #edited / #exported` |
| Filters | Style + media | full spec/32 §2 catalogue (EXIF/hardware, settings — focal length + exposure triangle, temporal, location, curatorial) |
| Storage | `event.db` | user level (`app.db`: `saved_filter` + `global_items`) |
| Attachment defaults | separators ON / overlays OFF | **separators OFF / overlays ON** — the portfolio case (spec/81 §3.1). Each member already carries its own event's EXIF + IPTC location, so embedded-mode overlays work cross-event with no extra write. |

## Build

1. **`app.db` global index** (spec/32 §3): `global_items` projection table +
   sync job (on event close / startup). Columns per spec/32 §3 — note the
   reconciled names: **`pick_state`** (was `cull_state`), **`flag`** (was
   `pick`).
2. **Cross-event DC storage** — a user-level DC home. Reconcile with spec/32 §4
   `saved_filter` (predicate tree) so a cross-event DC and a saved filter are the
   same entity, not two. Confirm: does the Phase-1 `dynamic_collection` shape
   (Task A) extend to user level, or does the user-level DC wrap a `saved_filter`?
   Settle at kickoff.
3. **Resolver extension** (`core/collection_resolver.py`): teach it the ladder
   universes + the full filter dispatch over `global_items`. **Two query paths
   exist** — event-level resolves against `event.db`, cross-event against
   `global_items`; keep them behind one resolver interface so the UI is
   scope-agnostic. (Flagged 2026-06-16: don't assume one query layer covers
   both.)
4. **Pin across events** — gathers DCs/Cuts **by tag across selected events**
   (spec/61 §8; lowercase-normalised names are the glue). **Grab-originals**
   lands here (spec/61 §8, §6).
5. **Filter surface + entry point** — the full facet UI, launched from the
   cross-event band on the events screen (spec/75 §2).

## Open (settle at kickoff)

- Cross-event DC vs `saved_filter` unification (above).
- The `global_items` sync trigger + staleness story.
- Whether grab-originals is in the first cross-event cut or a fast follow.

## Done when

Cross-event DCs resolve over `global_items`, pin into Cuts, play + export — the
spec/32 acceptance queries ("5-star macro of insects, focus-stacked, any year",
etc.) run end-to-end. `verify.bat` green.
