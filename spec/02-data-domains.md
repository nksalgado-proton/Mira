# spec/02 — Data domains & the settings discipline

**Registered 2026-05-30 (Nelson).** The gateway discipline and the protection
guarantees are **not event-only**. Mira persists **five data domains**; each
must be reached exclusively through typed gateway access and protected to the same
standard — even where the substrate differs, and even where we cannot build the
domain yet. This doc registers all five with their substrate, protection,
customizability, and **build-status (NOW / LATER / DIRECTION)**, so the intended
direction in every dimension is on record before any of it is implemented.

Nelson's instruction: *"we need these things registered so we know what direction we
want to move in each dimension, even if the movement is not possible at a certain
moment in time."* So this is a direction-of-travel spec, not a build order. Only
Domains 1 and 5 are NOW.

---

## 1. The one protection contract (applies to every domain)

Reused from the one genuinely good part of the legacy persistence (`core/
atomic_journal.py`), made universal:

- **Atomic write-then-rename** — never write the destination file directly.
- **SHA-256 sidecar** — corruption/tamper detection on load.
- **History rotation** — last N versions retained for recovery.
- **External JSON form** for backup/portability (for SQLite domains, a JSON dump).
- **Relative paths** wherever paths appear.
- **Typed access only** — no client reads or writes the bytes directly; everything
  goes through that domain's repository.

The **gateway is an umbrella** over per-domain repositories — event repo · knowledge
repo · rules repo · learning repo · settings repo. One contract, five backends. The
UI never knows which substrate sits behind any of them. If a domain's repo lacks
something a surface needs, we add it to the repo (charter §5.3: fix the model, don't
paper the facade).

---

## 2. Overview

| # | Domain | Holds | Substrate | Scope | Build |
|---|---|---|---|---|---|
| 1 | **Event data** | the 12-entity item model (spec/01) | SQLite per event + JSON backup | per event | **NOW** |
| 2 | **User knowledge** | the wizard's output — personal scenario library + preferences | JSON, app/user area | per install/user | wizard LATER · **protect NOW** |
| 3 | **Classification rules** (+ opt-in hardware) | brand-agnostic refinement rules; *optional* user bodies/lenses + preferred per-style config | shipped assets (read-only) + JSON user layer | shipped + per user | rules exist · user layer **DIRECTION** |
| 4 | **Tone-learning corpus** | (original ↔ LRC-corrected) pairs per style + derived adjustment conclusions | JSON now, small DB if it scales | per user, growing | **DIRECTION** (photo-AUTO seed exists) |
| 5 | **App settings** | every customizable default | JSON, install/user area | per install | **NOW** (enforced from start) |

Domains 2–5 are **app/user-level** (cross-event), unlike the per-event Domain 1. They
live in the user-data area, not inside any one `event.db`.

---

## 3. Domain 1 — Event data

The per-event item model fully specified in `spec/01-model-census.md`; substrate
SQLite (`event.db`) + JSON backup dump. Already in flight as the first build. Listed
here only to place it among its peers. The other four domains are the new registration.

---

## 4. Domain 2 — User knowledge (the wizard's output)

**What.** The first-run wizard (legacy `docs/04-wizard-question-bank.md` +
`docs/07-scenario-schema.md`) converts the user's per-genre shooting habits into
deterministic scenario rules and a **personal scenario library**, plus preferences
(preferred genres/themes, etc.). This is the mechanism by which user knowledge becomes
machine rules — the centerpiece of v1.

**Why it must be protected like event data — arguably more.** A single event is
re-cullable; the wizard knowledge is the **distilled product of the user's invested
time** and feeds *every* journey. Losing it is worse than losing one event. It must
survive reinstall, be backed up externally, and be portable to another install (its
own backup/restore, analogous to event backup/restore).

**Shape (direction-level; carry the existing scenario schema forward cleanly):**
the user's scenario definitions, per-genre wizard answers, preferred_genres/themes,
wizard version + completion state. The classifier (Domain 3) *reads* this; the wizard
UI *writes* it — both only through the knowledge repo.

**Substrate / protection.** App-level JSON in the user-data area, under §1's contract.
**Build:** the wizard itself is LATER (centerpiece, build carefully and twice per
CLAUDE.md) — but the **storage + protection + repo are defined NOW**, so when the
wizard lands it writes into a protected, backed-up home from its first keystroke.

Related: `[[principle_ambiguity_becomes_wizard_question]]` — the wizard is where
classification ambiguity is resolved into per-user deterministic rules.

---

## 5. Domain 3 — Classification rules (+ optional user hardware)

**What.** The refinement-rules engine (legacy `core/classifier_v2.py` + the JSON under
`assets/refinement_rules.json`, `assets/brand_profiles/`, `assets/body_profiles/`).
First-match-wins, brand-agnostic; infers photo **style** from normalized EXIF (camera
settings) and user data.

**The hardware principle (refined 2026-05-30 — FLAG).** The system is **not
user-hardware-dependent**: a fresh user with any modern ILC + any lens gets correct
classification with **zero** gear data. That stays load-bearing (`[[feedback_no_lens_
registry]]`). **Refinement:** if the user *elects* to share their current hardware
(bodies + lenses) and the configuration they prefer for each photo style, that
**optional** layer reduces classification uncertainty substantially. So:

- Hardware data is **banned as a dependency**, **permitted as opt-in enrichment**.
- The enrichment must **degrade gracefully** — the classifier is fully correct with the
  layer absent; it never becomes a required input.
- Rules stay **brand-agnostic** (query normalized concepts, never brand tag names);
  brand-specific reading stays in brand-profile methods. Unchanged.

**Not a separated expert system.** We deliberately do **not** mandate the classic
KB / rule-base / inference-engine separation. Rules and facts may co-locate. What we
*do* enforce is the access discipline + protection: both the shipped rules and the
optional user-hardware layer are reached through the rules repo.

**Two sub-stores.** (a) **Built-in rules** = read-only shipped assets, versioned; the
`rules_version` stamp invalidates the per-item auto-classify cache (spec/01 §A).
(b) **User layer** = JSON per user (opt-in hardware + preferred per-style config),
protected like Domain 2.

**Build:** rules exist (carry forward). The user-hardware opt-in layer is
**DIRECTION**. Complements, not replaces, the brand-agnostic path and the
ambiguity→wizard-question pattern. Related: `[[project_brand_aware_method_library]]`.

---

## 6. Domain 4 — Tone-learning corpus

**What.** The system that picks photo **tone adjustments per style** from data pairs of
(**original photo**, **LRC-corrected photo**). The corpus of pairs and the conclusions
derived from it should **grow in parallel** — more pairs → refined per-style adjustment
models. This is the "photo AUTO" calibration, with a video analogue already noted.

**Shape (direction-level).** Per style: a set of training pairs (references to the
original + corrected images, plus the extracted adjustment deltas) and **derived
conclusions** — the per-style adjustment model that Process AUTO applies. Versioned so
a Process AUTO result can record which model version produced it.

**Substrate / protection.** JSON now; promote to a small dedicated store if the corpus
scales (DQ3). Behind the learning repo, under §1's contract. The corpus is user data —
back it up.

**Build:** **DIRECTION.** A photo-AUTO seed exists today; this formalizes the
growing-corpus ambition. The video tone-AUTO calibration is the same machinery applied
to video frames. Related: `[[backlog_video_adjustment_calibration]]`.

---

## 7. Domain 5 — App settings

**What.** **One application Settings class** holding **every** customizable default the
app uses — no magic numbers scattered through code. Persisted to JSON in the
install/user-data area under §1's contract. (Charter §5.7 makes this constitutional.)

**Customizability tiers** (all defaults live in the class regardless of tier):
- **User-customizable** — surfaced in a **tabbed Settings dialog**.
- **App-managed** — the app changes them; not in the dialog, but **hand-editable in the
  JSON** if ever needed.

**Examples already seen in the census that belong here as *defaults*:** cull/select
default-state, default aspect ratio, slideshow per-tier seconds/max, preferred
genres/themes seed, Process default source dir, backup-on-quit root, calibration mode.
**Boundary:** settings hold app-wide *defaults*; an event may *override* some of these
per-event, and those overrides live in the **event store**, not in settings. Keep that
boundary clean.

**Build:** **NOW** — part of the foundation, before the UI rebuild, so every new module
reads its defaults from the Settings repo from day one.

---

## 8. Open decisions (resolve as each domain is built)

- **DQ1 — User-knowledge portability:** its own app-level backup/restore, or bundled
  with event backups? *Lean: its own, optionally included in an event bundle.*
- **DQ2 — Rules user-layer:** per-install vs per-user-profile; and how the opt-in is
  surfaced (a dedicated wizard step?).
- **DQ3 — Tone corpus substrate threshold:** at what size does JSON → a small DB?
- **DQ4 — Settings versioning:** schema_version + migration for the settings JSON (we
  own it now, so we own its migrations).
- **DQ5 — Physical layout:** where Domains 2/4/5 live in the user-data area and how they
  relate to the per-event stores (and to `core/settings.py` `user_data_dir()`, still the
  single source of truth for user-data locations).
