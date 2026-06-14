# spec/61 — Share phase: event Cuts (creation + consumption)

**Status:** design **LOCKED 2026-06-11**, Nelson (design-mode session, third
session of 2026-06-11). Supersedes [spec/51](51-share-cuts-vision.md) for the
event-Cut model and surfaces; spec/51 stays in place as the record of the
2026-06-08 brainstorm it always was. **Implementation NOT scheduled — Nelson's
word required.**

Parked for their own design sessions: **cross-event Cuts** (§8), **database
protection** (§9). The short list of questions to settle at implementation
kickoff is §10.

---

## 0. What survives from spec/51

The north star is unchanged: **a Cut is NOT a final slideshow.** It's a
time-budgeted, chronologically-ordered set the user assembles in MC and hands
off to PTE for finishing. No transitions, no music synchronization, no
per-slide effects, no reordering — PTE's job. MC delivers a high-quality
starting point.

Also surviving: the **Cut** vocabulary; export as a **separate verb** (links,
not byte copies; snapshot-in-time; the Cut stays live); time-budget thinking
with the green/amber/red zones; audio from the **user's own library** at
export. Everything else below is the 2026-06-11 redesign.

---

## 1. The model

### 1.1 Built-in Cuts are live queries, not data

Every event always has **#exported** — all exported final files, i.e. exactly
the population that paints the *Exported* watermark (edit-phase lineage, all
four writers: as-you-go, batch, return scan, backfill). The user already knows
the word from the watermark; the Cut and the watermark are the same fact.

#exported is **never created, never deleted, never stale** — it is computed on
demand from lineage, not stored as membership. (Same pattern as the future
#collected / #picked / #edited: the four-rung ladder collected → picked →
edited → exported exists conceptually, but event-Cut creation exposes **only
#exported** as the universe. The other rungs are reserved as alternative
universes for cross-event Cuts, §8.)

Note the distinction Nelson drew: **edited ≠ exported.** A photo can carry
edits and never have been exported. The Share universe is the *exported* rung.

### 1.2 A Cut is a set of exported FILES

Not abstract photos. If a photo was exported twice (a colour and a B&W
version), those are **two distinct pool entries**; the user can pick either or
both. Exported clip files are members the same way — that's how video enters a
Cut (the clip is already final by the time it reaches Share, per spec/56).

Every member file keeps its lineage link back to the original item — which is
what makes "grab the originals" possible later without new machinery (that
feature belongs to cross-event Cuts, §8).

### 1.3 Cuts are zero-byte until handoff

A Cut lives as rows in the database. Creating, editing, deleting Cuts costs
nothing on disk. The directory of links exists **only after the user exports**
(§5.2). Ten experimental Cuts = ten cheap rows, not ten folders.

### 1.4 Relational underneath — "#" is display language only

What the UI calls tags is implemented as classic relational pieces:

- **`cut`** — one row per Cut: tag/name, target + max time, seconds-per-photo,
  filters, default-state choice, music category, pool expression (the recipe
  that built it), created/updated, last-exported.
- **cut membership** — one row per *(cut, exported-file)* pair.
- **Built-ins** — views/queries over existing state (lineage), never rows.

Consequences (and why this beats literal tag-strings): rename = update one
cell; delete a Cut = membership cascades away; delete an exported file = it
falls out of every Cut automatically; the pool algebra (§2 step 2) maps
directly onto native set operations (union / except / intersect).

**Storage placement (CONFIRMED at kickoff, Nelson 2026-06-11):** `cut` +
membership in **event.db** (names are unique per event; members are per-event
files; a Cut lives and dies with its event — already-made exports on disk
remain); **templates at the user level** (cross-event by purpose). The
`cut`/`cut_template` DDL in [spec/53 §2.4](53-user-data-store.md) was shaped
for the spec/51 model and **needs a revision pass** at implementation kickoff
(annotated there).

### 1.5 Names: typed by the user, transformed live, unique per event

- The user types anything ("Best Macro Shots"); the dialog **transforms live**
  — lowercase, spaces → underscores, accents stripped (Pássaros → passaros),
  anything outside `[a-z0-9_]` dropped, `#` prefixed — and **shows the
  resulting tag as they type**: *tag: #best_macro_shots*. The user learns the
  system by watching it.
- **Uniqueness is per event, checked on the transformed result** (case-blind
  by construction — "Best Macro" and "best macro" collide).
- **One name.** The tag IS the display name everywhere: the Cuts list, pool
  expressions, the export folder. No separate pretty-name aliasing.
- Lowercase normalization is load-bearing: **names are the cross-event glue**
  (§8 gathers "#best_macro_shots from events A, B, C" by name match).

---

## 2. Creating a Cut — one dialog, then one Picker session

Dialog fields, in order:

0. **Load template…** (optional) — pick a saved recipe; pre-fills every
   field below, all still editable (kickoff addition, Nelson 2026-06-11).
1. **Name** — free text + live tag preview (§1.5).
2. **Pool** — boolean algebra over the Cuts that already exist:
   `#exported − #cut_1 + #cut_2` (evaluated left to right). The universe for
   event Cuts is always **#exported**. This is the power move: "everything I
   exported but haven't used yet", "the union of my two favourites", "the long
   version minus the short version" are all one expression, no special
   features. *(Dialog UX for composing the expression — chips and +/− buttons,
   not raw typing — is sketched at implementation kickoff, §10.)*
3. **Filters** on the pool — classification styles, combinable (macro +
   wildlife), **default All**; media type (photo / video). (Camera filter
   dropped at kickoff, Nelson 2026-06-11; other filter kinds may join
   later.)
4. **Default state** — everything remaining in the pool starts **all-picked**
   or **all-skipped** (subtractive vs additive session).
5. **Time** — **target minutes + max minutes** (the budget is MINUTES, not
   slides) and **seconds-per-photo**. Real accounting: each photo costs its
   display seconds, each clip costs its true duration, each separator costs
   one slide (§4). The "≈ N slides, keep ~1 in K" line is shown as rough
   orientation **only when the pool is photo-only** — it is never the actual
   math.
6. **Music category** — a subdirectory of the audio library (§5.3). Stored on
   the Cut; changeable at the moment of use.

**Start →** the **good old Picker opens on the pool** — days list → day grid →
single item → compare, all existing mechanics. Two rules:

- **Separate ledger.** Pick/Skip in a Cut session belongs to *this Cut only*.
  The real Pick-phase decisions are never touched. Same surface, different
  decision store.
- **The live budget line** rides in the same slot the export progress bar
  uses: **green** at/under target, **amber** between target and max, **red**
  over max — updating as picks change.

**Create Cut →** commits the membership (one row per picked file). The Cut
appears in the Cuts list.

**Save as template** — stores the *recipe* (pool expression, filters, default,
target/max, seconds-per-photo, music category), not the result. Replaying a
template in another event re-evaluates the recipe against **that event's**
Cuts — #exported resolves to a totally different set, the names glue it
together, the user finishes with the same Picker session.

**Changing an existing Cut = re-enter the creation session** (dialog
pre-filled → Picker with current membership). There is no second editing
surface.

---

## 3. The Cuts list (Share landing)

Click Share on an event → the **Cuts list**:

- **#exported** always present (it's a query — no one created it, no one can
  delete it).
- User Cuts below: **tag · item count · duration · music category · exported
  status**.
- **New Cut** button → §2.
- Row click → the Cut's flat grid (§5).

Empty state (no user Cuts yet): one sentence hint + the New Cut button. No
modal, no tutorial.

---

## 4. Separator slides

Nelson's slideshow practice: every show gets day separators, hand-authored
today. MC generates them:

- At **every day boundary** in a Cut sits a **separator slide** — a real piece
  of content, not UI chrome. It shows in the grid, plays in the slideshow,
  exports as a file in sequence.
- **v1 form: plain card** — a generated image in the user's preferred aspect
  ratio (**a setting, default 16:9** — the ratio belongs to the screen shows
  play on, not to the event), carrying the day's **date · location ·
  description** straight from the Collect-phase plan. The plan data the user
  already typed compounds into the handoff for zero extra effort.
- **Derived live, never stored** (same pattern as #exported): rendered at
  play/export time, so a plan edit propagates to every Cut's separators
  automatically. Nothing goes stale.
- **Settings flag, default ON** (`use_separators` conceptually; exact key at
  implementation). Flip it off and separators vanish from grid, play, and
  export.
- **They count in the budget** — one slide (seconds-per-photo) each; the
  dialog shows "includes N separators".
- **Future styles, behind the same setting, not built until missed:**
  first-photo-of-the-day duplicated with the label on it; a map card (place or
  route — touches the parked maps topic). The plain card ships first because
  it is always legible and never embarrasses.

---

## 5. Consuming a Cut

Event-Cut consumption is **export + watch as slideshow. That is it.**
("Slides to print" needs no feature — it's just another Cut, e.g. `#to_print`,
exported to a folder and taken to the shop. **The legacy Print question is
closed: there is no Print feature.**)

### 5.1 The flat grid (WYSIWYG)

Open a Cut → **one flat grid in true show order**, separator slides sitting at
the day boundaries. The grid IS the slideshow, tile for tile — what you see is
what PTE receives and what Play shows.

Deliberately NOT the Picker's day drill-down: the Picker's day hierarchy
serves mass deciding over thousands of undecided items; a Cut is small
(time-budget-bounded), already decided, and consumed as a whole. No colored
day-borders either — border colors mean *decision state* in this app's visual
grammar, and color-coding stops working past a handful of days. The separator
slides themselves are the day orientation.

Top bar: **Play all · Export all**. Per item: open single (existing viewer),
**Export** single; **Play** offered on videos only.

### 5.2 Export — the handoff

Materializes `<event_root>/Cuts/<tag>/` (the `Cuts/` top-level dir is born
with every event per [spec/57](57-folders-and-roundtrip.md)):

- **Linked media files** (NTFS hardlinks, copy fallback — no byte copies),
  named so plain filename sort = chronological show order.
- **Separator images** rendered into sequence (§4).
- **`audio/` subdir** with linked songs (§5.3).
- Export is a **snapshot**; the Cut stays live in the database. Re-export
  after changes → fresh materialization. Renaming a Cut never rewrites
  already-exported folders; the next export uses the new name.

### 5.3 Audio — the user's library, the user's categories

- Setting: `audio_library_path`. Its **subdirectories are the categories** —
  whatever the user named them (`happy/`, `samba/`, `80s/`). MC ships no mood
  vocabulary; the picker lists the subdirs found.
- The Cut **stores its category**; changeable at the moment of use (Play and
  Export both).
- **Playlist build:** list the category's files, read durations, shuffle, sum
  until total ≥ the show's duration, **include the crossing file** (always "a
  bit more" — trim room in PTE), prefix `01_`, `02_`, … for play order. Not
  clever, by design.
- Export drops the playlist as **links into `audio/`**. Play (§5.4) uses the
  same playlist machinery in-app.
- **Graceful absence:** path unset/invalid or category empty → audio quietly
  unavailable; export proceeds without it + small notice. Category shorter
  than the show → copy all of it + notice.
- Precedent: the very first Mira prototype shipped this (outside events).
  The change is that it now travels **with the Cut**.

### 5.4 Play — the rehearsal

Full-screen slideshow of exactly the grid sequence: photos held
seconds-per-photo, clips at true duration, separator slides included, **music
from the Cut's category**. The point: feel the *final show* — timing,
separators, soundtrack — before PTE ever opens.

---

## 6. Decisions that fell out along the way

| Decision | Resolution |
|---|---|
| Print | **Dead as a feature.** A `#to_print` Cut + export covers it (§5). Closes the pending Print design question. |
| Grabbing originals | Belongs to **cross-event Cuts** (§8), not event Cuts. |
| Budget unit | **Minutes, not slides.** Slide count is rough guidance for photo-only pools. |
| Tag case | **Lowercase always**, enforced by the transform (§1.5). |
| Mood vocabulary | None shipped — **the user's subdir names are the categories** (§5.3). |
| Music choice | **Stored on the Cut**, changeable at the moment of use. |
| Separator default | **Settings flag, ON by default** (§4). |
| Aspect ratio | **Setting, default 16:9** — promote to per-Cut only if vertical shows become real. |

---

## 7. What this supersedes in spec/51

| spec/51 said | spec/61 says |
|---|---|
| Fresh event → zero Cuts (§3.2) | **#exported always exists**; built-ins are live queries |
| Tags internal, never typed (§3.1) | **The user types the name**; the tag IS the display name |
| One-by-one walk surface (§3.5) | **The Picker, reused**, separate ledger |
| Single seed filter (§3.4) | **Pool algebra** over existing Cuts |
| Videos toggle (§3.7) | **Media-type filter** (photo/video) |
| No unique name constraint (§6 D) | **Unique per event**, case-blind, checked on the transform |
| Membership = items (§10 `photo_tag`) | **Membership = exported FILES** (lineage-backed) |
| Maps + collages authored items + authoring page (§3.12, §6 F) | Day-marker need covered by **generated separators** (§4); standalone maps/collages authoring **parked** (not dead, not designed) |
| "Cuts" menu-bar entry (§6 B) | Cross-event parked to its own session (§8); the menu-bar design session is still pending anyway |
| Templates = `cut_template` rows + pre-shipped constants (§6 C) | Templates = **saved recipes**; storage home + whether anything ships pre-made → kickoff (§10) |
| Audio "mood" framing (§3.11) | **User's own subdir names**; algorithm itself survives |

Also stale relative to this doc: [spec/53 §2.4](53-user-data-store.md)
(`cut`/`cut_template` DDL) and the spec/53 §4 feature-flag rows that reference
the old model — revision pass at implementation kickoff.

---

## 8. Cross-event Cuts — parked (the trailhead)

Same mechanics, **different entry point, different soul: more a search tool
than a share-selection tool.** What's already known, recorded so the future
session doesn't start cold:

- Alternative universes: **#collected / #picked / #edited / #exported** — for
  finding what *didn't* make the finish line, not just what did.
- Pool gathers **by Cut name across selected events** ("#best_macro_shots from
  events A, B, C" or "from all events of 2024–2026") — §1.5's normalization is
  what makes this trustworthy.
- **Grabbing originals lands here.**
- Entry point TBD (interacts with the pending menu-bar design session).

## 9. Database protection — parked

Nelson 2026-06-11: Cuts being zero-byte means a corrupted/lost database loses
them all. Protection deserves its own design session. (Rolling backups exist
for the user-level store per [spec/53 §3.4](53-user-data-store.md); the
per-event side needs its own answer.)

---

## 10. Implementation-kickoff questions — ALL RESOLVED 2026-06-11

1. **Pool-expression dialog UX** — sketched + approved (chips + +/− buttons
   per existing Cut, ✕ removes a term, live pool count + live filter-match
   count; no raw expression typing). Kickoff amendments: **no camera
   filter**; **styles default All**; **Load template…** added at the top of
   the dialog.
2. **Storage placement** — CONFIRMED: `cut` + membership in `event.db`;
   templates at the user level.
3. **Membership file identity** — the lineage row is the exported-file
   reference.
4. **Pre-shipped templates** — **none.** Templates emerge from use; the
   empty-state hint guides the first Cut.
5. **Exported status on the list row** — `last_exported_at` on the `cut` row.
6. **Shape checkpoints** — per [[feedback_verify_spec_shape_during_integration]],
   the Cuts list, the dialog, the Picker-session chrome, and the flat grid
   each get a "shape matches spec?" confirmation with Nelson as they land.
