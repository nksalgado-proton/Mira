# 31 — Why Relational: the vision behind the core rebuild

**Status: load-bearing rationale (Nelson + Claude, 2026-05-31).** This is the *why* that
[`spec/30-relational-schema-redesign.md`](30-relational-schema-redesign.md) is the *what*. Read this
first to understand
what we are buying and why it is worth re-laying the foundation.

> **The realization (Nelson):** *"We moved to a very powerful relational database without
> properly thinking about how to take advantage of it to make Mira a better solution. Step
> back now — better now than later — think about the relational nature of the entities
> conceptually, and create a database structure that leverages that to provide much faster and
> more reliable code. I don't mind starting over and porting the legacy on a solid foundation."*

The first rebuild attempt moved the bytes into SQLite but kept the **journal mindset** (load the
whole document, filter in Python, structure stuffed into JSON-blob columns) — "journals wearing a
database costume." We were leaving most of the relational engine's value on the table. This
document captures the high-level view of what changes, why it is better code, and how we recreate
the legacy on top of it **without reinventing the wheel.**

---

## 1. We are really moving journal/directory → relational (and *finishing* a half-done migration)

There have been three data models in this project's life:

- **Legacy (works perfectly).** The truth was **scattered**: decisions lived in JSON journals,
  lineage lived in the **folder structure** (`00 - Captured/`, `01 - Culled/` …), ordering lived
  in **filenames**, time lived in **EXIF**. The directory tree *was* the database. Answering
  "what's kept on day 3?" meant walking folders and parsing filenames.
- **First rebuild attempt.** Bytes moved into SQLite, but the access layer still loaded whole
  documents and filtered in Python, with JSON-blob columns carrying structure. A relational store
  used as a key-value/JSON store.
- **Now (this rebuild).** The **database is the single system of record.** Decisions,
  relationships, and lineage are **rows and foreign keys.** The folder tree is **demoted from
  source-of-truth to a rebuildable *projection*** of what the DB says.

The nuance that matters: the directory tree does **not** vanish — Lightroom and the other partner
tools still read folders, and the pipeline still renders them. But the tree stops being
*authoritative*. It is an **output**, regenerated from the DB, not the model. This is the move the
first rebuild only half-made; we are completing it.

---

## 2. Why this is *better code* than the legacy

The legacy is not broken — but it spends enormous effort **defending consistency by hand.** The
relational core makes whole categories of that effort disappear:

- **One source of truth instead of four.** No more "the journal says kept but the folder says
  discarded," no reconciling filenames against EXIF against journals. That entire bug class —
  including the painful Costa Rica `mtime`/lineage bugs — largely stops being *possible*, not just
  fixed.
- **The engine enforces integrity, not our Python.** `ON DELETE CASCADE` removes a video's clips /
  markers / overrides automatically (no orphan-cleanup code to get wrong). `CHECK` constraints
  make illegal states **unrepresentable**: no half-materialized item, no clip that is secretly a
  photo, no two reference cameras. The legacy policed all of that in code, in many places, forever.
- **Queries replace directory walks.** "Kept items on day 3, ordered by corrected time" is one
  indexed `SELECT` instead of a filesystem crawl + filename parse — faster, and it cannot drift
  from reality.
- **Far less plumbing.** A large fraction of legacy code is mechanical: serialize/deserialize
  JSON, walk trees, write-then-rename atomically, infer state from disk. When the DB owns state,
  most of that evaporates. Less code → fewer bugs → cheaper future features.
- **Real transactions.** SQLite gives ACID + WAL crash-safety for free; the legacy hand-rolled
  per-file atomic writes and journal recovery.

**The honest boundary.** This does *not* make the genuinely hard parts better by itself — EXIF
reading, bucket-clustering, sharpness scoring, ffmpeg clip extraction, the classification rules.
Those are hard domain problems and they are *the same*; we **reuse** them. The win is concentrated
in the **data layer and integrity**, which is exactly where the legacy was fragile and verbose.

---

## 3. We can restart from the DB+gateway core and recreate the legacy — not reinventing, but better

The layers stack cleanly, and each gets a different treatment. This is the no-wheel-reinvention
strategy made concrete:

| Layer | Strategy | Why it is *not* reinventing the wheel |
|---|---|---|
| **Schema + gateway** | **Rebuilt** (the new foundation) | The one genuinely new, deliberately-designed layer (spec/30, spec/08) |
| **Pure logic** (`core/`: EXIF, clustering, sharpness, ffmpeg, rules) | **Reused verbatim** | Qt-free, persistence-agnostic, brand-agnostic — it never cared *where* data lived |
| **UI surfaces** (cull, select, process, curate dialogs) | **Ported faithfully** — same dialogs, flow, wording | Only the *data-access calls* are rewired to the gateway; the UX is preserved (Supreme Rule, charter §0) |

So the recreation is mechanical and safe: **reuse the hard logic → port the UI verbatim → rewire
only where data comes from.** The "better code" does not come from changing *what Mira does* —
it comes from the floor it stands on being relational, indexed, and integrity-enforced, so the same
proven behavior is faster, more reliable, and dramatically less glue.

**The gateway is the crucial seam.** It speaks the legacy's *mental model* — "the moments of this
video," "the items in this bucket" — through thin methods backed by real SQL. The ported UI never
knows it is talking to a relational engine; it just gets correct data, fast.

**The cost, stated plainly.** Some rebuild surfaces already built on the old gateway (dashboards,
settings, ingest, the minimal cull loop) get **re-pointed** at the new schema. That re-pointing is
real work — but it is *rewiring*, not redesign, and far better done now than after ten more
surfaces are bolted onto sand.

---

## The bet, in one line

**A small, well-modeled relational core + a thin gateway, then the legacy's proven behavior ported
on top of it** — buying speed, reliability, and a fraction of the maintenance burden, without
gambling on rewriting any of the hard, working logic.

## What governs the work from here

- **The Supreme Rule still governs every unit** (charter §0): we PORT; UI + flow stay identical;
  only data-access calls change; reuse-manifest + Nelson's OK before coding each surface;
  improvements welcome but **proposed first**.
- **The legacy code is the master guide for the UI and the Process** behaviour — it is the
  reference we port from, never a thing we re-imagine.
- **Per-functionality port discipline still applies** — every functionality unit is ported via
  the manifest-first reuse pattern (charter §0), now on the relational-core foundation
  (spec/30's schema).
