# spec/81 — The Dynamic Collection / Cut model (two nouns, two verbs)

**Status:** design **agreed** with Nelson 2026-06-16 (design-mode session).
This is the **canonical reference** for how selections are defined, frozen,
played, and handed off. It **revises** the three specs that now disagree with
it — `spec/80`, `spec/61`, `spec/32` (§8) — and is what the coding agents are
handed.

**Implementation gated** (carried forward from spec/80 §4): the real test is
assembling real Cuts (`#long`, `#medium`, `all-time-best`, …) from production
events after the 30-year import. Validate against that before building, and
expect to refine.

Read with: `spec/61` (the event-Cut surfaces — Cuts list, Picker session, flat
grid, separators, audio, export — which all still apply, now understood as
operating on a *materialised DC*), `spec/32` (Dynamic Collections, the
cross-library query entity this generalises), and `spec/80` (the construction
session this supersedes).

---

## 1. The whole model

> **Two nouns — Dynamic Collection and Cut. Two verbs — pin (DC → Cut) and
> export (Cut → directory). That is the entire model.**

Everything below is detail on those four things. spec/80's "live Cut vs pinned
Cut" split is gone: the live thing is now its own noun (the Dynamic
Collection), and a Cut is always frozen. Cleaner, and it removes the conflation
that made spec/80's live/pinned badge necessary.

---

## 2. Dynamic Collection (DC)

A **Dynamic Collection is a formula** — set algebra over operands, plus filters
— that **resolves live** to a set of media files.

- **It is only a definition.** A DC is **not playable, not exportable**, has
  nothing to hand off. It answers "which files," nothing more.
- **Operands** are base universes and other DCs. Per-event the base universe is
  `#exported`; cross-event the universes are the ladder rungs
  `#collected / #picked / #edited / #exported`. Set algebra is **union (`+`),
  difference (`−`), and intersection (`∩`)** — all three available to the user.
  Operators evaluate **left-to-right**, and **grouping is done by nesting a DC
  as an operand** (a saved sub-DC stands in for parentheses — see "composable"
  below), so there are no precedence rules and no bracket UI. **No other boolean
  ops:** symmetric difference is rare and expressible by composition; complement
  is just `#exported − X`.
- **Filters** narrow the resolved set — classification styles (combinable),
  media type, and the EXIF / curatorial / temporal / location dimensions
  catalogued in spec/32 §2.
- **Reusable, and composable.** A DC can be an **operand inside another DC**
  (`all-time-best = all-time-best-macro + all-time-best-wildlife`). The
  built-in `#exported` is itself the base DC of an event.
- **Resolves live.** Change an operand or add matching files and the DC's
  resolution changes the next time it is read. A DC is never a stored member
  set — it is the recipe, evaluated on demand (same pattern as spec/61's
  `#exported`).
- **Scope-agnostic.** "Event" vs "cross-event" is *only* which operands are in
  range and where the DC is stored — event DCs live in `event.db`, cross-event
  DCs live at the user level (spec/32's `saved_filter` / `global_items`). The
  model is identical at both scopes.

### 2.1 Scope asymmetry — the surface is thinner at the event level

The *model* is identical at both scopes; the **surface exposed at creation
differs deliberately**. Event-level Cut creation is kept small and obvious;
cross-event is the power tool (spec/61 §8: "more a search tool than a
share-selection tool").

| | **Event-level** | **Cross-event** |
|---|---|---|
| **Origin universe** | **`#exported` only** — the one base DC | The full ladder: **`#collected / #picked / #edited / #exported`** — so you can reach what *didn't* finish, not just what did |
| **Filters offered** | A **handful: Style** (classification, combinable) **+ media type** (photo/video). No camera filter (dropped, spec/61 §10). | The **full dimension catalogue (spec/32 §2)**: hardware/EXIF (lens, camera, flash), settings (focal length + the exposure triangle: aperture / shutter / ISO), temporal, location, curatorial. |
| **Stored in** | `event.db` | User level (`saved_filter` / `global_items`) |

Why the cut: at the event level you are assembling a share from what you
already finished, so one universe and two filters is the whole job. Cross-event
is querying a lifetime archive, where the EXIF/settings/location facets are the
point (spec/32 §7: "query, don't label" — the facets are free). Same DC engine
underneath; the creation UI just shows more of it cross-event.

---

## 3. Cut

A **Cut is the only thing you can play or export.** It is **made from a DC**,
with **optional steps** — a **pin** plus a set of **attachments** (§3.1):

1. **A budget-driven pick/skip pass** — the **pin** (§4). Open the DC's
   resolution in the Picker on a **separate decision ledger** (spec/61 §2) and
   skip down until it fits the time budget. Optional.
2. **Attachments** — separators, audio, and overlays (§3.1). All optional.

**Skip the pin and add no attachments, and the Cut is the DC's content
one-to-one** — materialisation is just freezing the DC's current resolution
verbatim.

A Cut **holds frozen members** — plus the **budget** if one was used, plus its
**attachments** (separators, audio, overlays — §3.1). It is **playable as a
rehearsal inside Mira** (full-screen slideshow, spec/61 §5.4), and it **exports
to a directory of links** — member files, audio links, separator images, and
overlay metadata embedded in the files (§3.1) — for PTE (spec/61 §5.2). A Cut is
zero-byte
until export materialises the links (spec/61 §1.3).

### 3.1 Cut attachments — separators, audio, overlays

Attachments are optional decorations on the frozen member set. None of them
change membership, none is a verb (§4), and all are **derived live at
play/export time** (nothing baked into stored state). Choosing them is a Cut
edit. Each has a **settings-driven default**, and the sensible defaults differ
by scope:

| Attachment | What it is | Budget cost | Default: event Cut | Default: cross-event Cut |
|---|---|---|---|---|
| **Separators** | Generated day-boundary slides (spec/61 §4) — orient a single timeline | **One slide each** | ON | OFF (no single timeline to orient) |
| **Audio** | A playlist from the user's library category (spec/61 §5.3) | none | per-Cut | per-Cut |
| **Overlays** | Provenance text drawn on each frame — **when** (date/time), **where** (event/location), **how¹** (hardware: lens/camera/flash), **how²** (settings: aperture/shutter/ISO, focal length). A multi-select over fields Mira already holds (spec/32 §2); "none" is valid. | **none** (sits on existing frames, adds no slide) | OFF | ON (the portfolio case: "how and when it was shot") |

Separators and overlays are **complements, not rivals**: separators are
event-shaped (they structure one event's days), overlays are provenance-shaped
(they travel with each frame across events). Both are available on both scopes;
only the defaults flip.

**Overlay export — two modes, a setting (decided 2026-06-16; default = embedded
metadata).** In-app Play always draws overlays live on the frame
(non-destructive, like separators) regardless of mode. The two export modes:

1. **Embedded metadata → PTE renders (default, link-pure).** PTE AV Studio reads
   metadata *embedded in the photo* (its built-in *Add Text with EXIF and/or
   IPTC* template feature; verified against PTE docs) — there is **no separate
   sidecar file to import.** So Mira's job is to make the fields live in the
   file's EXIF/IPTC: **when / how¹ / how²** are already in the camera EXIF of
   every exported JPEG (PTE renders them for free); **where** is written into the
   file's **IPTC** (City/Country/Sublocation — Mira's location model is already
   IPTC-shaped, spec/32 §2c), ideally at Export phase so Cut members stay
   hardlinks. Export stays **pure links**; PTE does the rendering (font, size,
   placement — spec/61 §0). Mira may ship a starter PTE text template matching
   the chosen fields. The per-Cut field selection drives the in-app rehearsal
   and that template.
2. **Mira-native burn-in (opt-in, self-contained).** Mira renders rendered-photo
   *copies* with the chosen fields drawn into the pixels, using its bundled
   ExifTool + render pipeline (`photo_render` / `process_render`). Works in any
   viewer with no PTE dependency — but those members are **copies, not links**,
   and the look is fixed by Mira. For non-PTE use or shareable bundles.

This is **informational provenance, not a Show profile or a per-slide effect**
(§7) — the structured generalisation of the caption Mira already planned to hand
PTE.

---

## 4. The two verbs

**pin — DC → Cut.** Freezes a DC's live resolution into a Cut's stored members.
The budget pass is *the* pinning operation; when run, membership snapshots at
pin time. When skipped, pin still happens — it just freezes the DC's resolution
as-is.

**export — Cut → directory.** Materialises the Cut as a directory of links
(spec/61 §5.2): linked media files named for chronological sort, rendered
separator images in sequence, an `audio/` subdir of linked songs, and overlay
metadata embedded in the member files (default mode; §3.1).

Separators, audio, and overlays are **not verbs** — they are optional
*attachments* on the frozen member set (§3.1). Do not let any of them become a
third verb; the model stays at exactly two.

---

## 5. Export config & repeatability (the resolved open detail)

The question: does a Cut remember its export config so re-exporting reproduces
the same bundle? **Answer: the Cut carries its *composition*; it does not freeze
its *destination*.**

- **Composition travels with the Cut** — frozen members, budget, and all
  attachments (separators, audio selection, overlay field selection — §3.1).
  Re-export reproduces that bundle exactly. Re-export is repeatable and boring,
  which is the goal.
- **Target directory does NOT freeze into the Cut.** Baking an absolute output
  path into a Cut would recreate the hardcoded-user-path coupling the charter
  forbids (invariant #2) and make the Cut non-portable. Target is
  **environmental**: remember last-used as a one-keystroke default, but it is a
  fresh-but-defaulted choice each export, not part of the Cut's identity.
- **The precise rule:** *re-export reproduces the same bundle content; where it
  lands is defaulted, not frozen.* A Cut is repeatable in **what** it emits, not
  in **where**.

**Members are frozen at pin / materialisation. A Cut never re-queries its DC
live.** If the source DC later re-resolves to a different set, the Cut does not
change. (This is spec/80 §1.5's "parked re-base" stated as a hard rule: a Cut
is a snapshot; re-applying a DC against a Cut is an explicit future action, not
automatic.)

---

## 6. Pacing

Pacing is **not stored anywhere.** Export **back-solves** it from the budget if
the Cut has one (each photo costs its display seconds, each clip its true
duration, each separator one slide — spec/61 §2.5; **overlays add no cost**,
§3.1). No budget → no derived pacing; the Cut is just its ordered members.

---

## 7. Two deletions

Both are Nelson's calls, both deliberate:

- **No Show profile.** Mira does not render slideshows — transitions, music
  sync, and rendering are PTE's job (spec/61 §0) — so there is nothing for a
  show-profile to template. Drop the concept.
- **No maps or collages.** Out of scope, PTE's territory. (spec/61 §4 already
  parked map separators and spec/51's collage authoring; this closes them.)

---

## 8. What this revises

| Spec | Was | Now |
|---|---|---|
| **spec/80** | "A Cut = pool expression + optional refinement," with **live vs pinned Cuts** and a live/pinned badge; "pool," "Dynamic Cut," Show-profile language | The **live formula is its own noun (DC)**; a **Cut is always frozen**. "Pool" → DC; the live/pinned split → DC (live) vs Cut (frozen). The New Cut dialog's 3-way Build mode collapses: you choose/compose a **DC**, then optionally pin + add separators. |
| **spec/61** | The event-Cut model and surfaces | Unchanged in surface and mechanics; a **Cut is now explicitly a materialised DC**. `#exported` is the event's base DC. Export §5.2 amended: composition travels with the Cut, **target is defaulted not frozen** (§5 here). |
| **spec/32** | "Dynamic Collection" = saved cross-library query, with **legacy `cull` / `Curate` / `select` / `kept` vocabulary** in its dimensions and presets | The DC entity here **is** spec/32's, generalised to scope-agnostic set algebra over operands (incl. other DCs) and made the sole live-query noun. Its pipeline-state vocabulary is **reconciled to the locked Collect/Pick/Edit/Export + Pick/Skip terms** (charter): `cull`/`select`/`kept` → the ladder rungs `#collected / #picked / #edited / #exported` and the `'picked'` state. |

Stale-by-reference, to clean on the same pass: spec/53 §2.4 (`cut` /
`cut_template` DDL — already flagged by spec/61) and any spec/80 dialog copy
that names "pool," "Dynamic Cut," or a Show profile.

---

## 9. Open / to validate (the gate)

- **The acceptance test** (from spec/80 §4): build `#long`, `#medium`, and an
  `all-time-best`-style composed DC from real production events; pin each to a
  Cut; confirm play + export feel right. Refine this spec from that, then
  implement.
- **Operator grouping** — resolved: union/difference/intersection all ship,
  evaluated left-to-right, grouping via nested-DC operands (no parentheses).
  Revisit only if users hit an expression nesting genuinely can't express.
- **Cross-event DC storage** — confirm the spec/32 `saved_filter` /
  `global_items` home is the right one once cross-event Cuts get their session
  (spec/61 §8).
- **Last-used target default** — per-Cut, per-event, or app-global? Settle at
  implementation; it is a convenience default, not part of the Cut's identity
  either way (§5).
- **Overlay metadata write** — confirmed PTE reads embedded EXIF/IPTC natively
  (§3.1). Open: write the **where** IPTC fields at Export phase (so Cut members
  stay hardlinks) vs. at Cut time (would force a copy for those members);
  settle the exact IPTC field set; decide whether Mira ships a starter PTE text
  template. Burn-in mode: default font/size/placement/position for the drawn
  text.
