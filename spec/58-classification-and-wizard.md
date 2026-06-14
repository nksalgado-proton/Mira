# spec/58 — Classification: the background pass, the Edit-only surface, and the wizard refresh

**Status:** design LOCKED, Nelson 2026-06-10 (design session, audit-grounded,
all questions closed one by one). Implementation: slices 1–2 + 5 landed
2026-06-10 (schema v2 + classify routine + background triggers + the wizard
refresh); slice 3 landed 2026-06-11 behind v0 ramp values — **thresholds +
colors await Nelson's live calibration** (§5.1); slice 4 follows. Supersedes the
Pick-phase classification surface (the genre chip + Reclassify affordances)
and the lazy classify-on-browse writer wherever they contradict this
document. Sister in spirit to [spec/56](56-video-workshop.md): a capability
that drifted into the wrong phase returns to the phase it serves.

---

## 0. The mistake being corrected

Classification today is written *by* the Pick surface (lazily, per photo, as
the user browses) and shown everywhere *except* where it matters. The audit
(2026-06-10) found: the only live writer is the Pick photo surface; photos
never browsed reach Edit unclassified; backfilled events (spec/57 §4.3
from-Picked / from-Edited) reach Edit with **zero** classifications; the
classifier's confidence score is computed and then dropped; and the genre
chrome sits on two Pick surfaces where it cannot affect anything.

**Classification exists for one purpose: choosing the correction profile in
Edit** (the spec/54 A-router and per-style recipes). Everything in this
design follows from that sentence.

## 1. The classification pass

- **Every captured item — photos AND videos — is classified before the user
  reaches Edit.** Videos need it too: video export renders frames through
  the same style-routed pipeline (spec/55 per-style recipes).
- The pass runs **in the background** after media enters the system (items
  sit in the base long before Edit). It is the **sole writer** of auto
  classification — the Pick surfaces' lazy writers retire with their chrome
  (§2). Backfilled events are covered by construction: the pass keys on
  ingested items, not on Pick visits.
- **The rules chain is unchanged** (audit-confirmed end-to-end): wizard
  answers → `scenarios/user-<genre>.json` → merged ruleset (deterministic
  tier > user scenarios > lens fallback) → `classifier_v2` first-match-wins.
- **RAW-first (locked: "Use the raw").** A RAW+JPEG pair is ONE shot: the
  RAW is classified; its JPEG twin inherits by filename stem. A user
  decision on either applies to the pair. (Re-affirms the pre-fork decision
  the audit found unimplemented.)
- **Confidence is persisted.** The classifier's score (0..1) lands in a new
  `item.classification_confidence` column beside the existing
  `classification_source` / `classification_rules_version` /
  `classification_needs_review`. The score is what the Edit surface colors
  by (§2); `needs_review` stays as the derived boolean.
- **Items born outside the captured set inherit at creation** (Nelson
  2026-06-11): a video snapshot copies its source video's classification
  row; an adopted stack master copies its anchor bracket member's. The
  pass itself stays captured-only — inheritance keeps the Edit badge (§2)
  honest for children the pass never sees.

## 2. The one surface: Edit's Style button

- **The Style button carries the classification.** Its color encodes the
  stored confidence on a **red → green ramp** — the user knows at a glance
  when the profile needs their eye. Thresholds and exact colors are
  **eyeball-calibrated at implementation, not designed here** (locked:
  "calibrate later").
- **Choosing a Style IS the human decision.** The moment the user selects
  any style — *even the currently shown one* — the button flips to a fourth
  color outside the ramp (the "human decided" color) and the item records
  `classification_source='user'`.
- **No classification appears on any surface before Edit.** The Pick photo
  surface's genre chip + Reclassify menu and the video Pick page's
  "genre · Reclassify" chrome (kept in spec/56 slice 2; consciously
  superseded here) retire, along with their lazy `set_classification`
  writers.
- **The dormant style pie stays.** `style_breakdown_last_phase` (both the
  rebuilt and legacy copies) has no UI caller; it is deliberately NOT
  retired — "we may find some use for it in the future."

## 3. Stability — when a classification may change

- **Auto re-classification** (new rules after a wizard re-run, i.e. a
  `classification_rules_version` mismatch) applies **only to items
  untouched in Edit**. *Edited* means the item carries any Edit work the
  user produced — a Style/Look/Filter choice, any adjustment, or an export.
  **Untouched means re-classifiable** (locked).
- `classification_source='user'` is never overwritten by auto
  re-classification — edited or not.
- An edited item's classification produced a visual result the user has
  seen and worked on; it is frozen against rules changes by definition.

## 4. The wizard refresh

The audit found the wizard's 4-phase vocabulary already correct (rewritten
2026-06-07, the day before the fork) — its staleness is elsewhere:

- **Rename sweep:** 18 user-visible **"Mira" → "Mira"** strings
  (welcome title, calibration questions, every genre block's
  "expected setup" hint, capture overview body).
- **Four QSS roles the wizard references exist in neither theme** —
  `BodyText`, `WizardRadio`, `WizardRadioHint`, `WizardWarning` render
  unstyled today. Definitions land in BOTH themes (spec/05 rule).
- **Re-run semantics stay overwrite-on-complete** for the scenario JSONs;
  the blast radius is bounded by §3 — a rules change re-classifies only
  unedited, non-user items. No warning dialog needed.
- The wizard's question bank and the scenario schema are NOT redesigned
  here — the seam to classification is confirmed working and stays.

## 5. Open (deferred, explicitly)

1. Red↔green thresholds + the exact ramp and "human decided" colors —
   **eyeball calibration with Nelson still pending.** The v0 values in
   the field (2026-06-11): discrete QSS bands on the STYLE combo's
   border (a continuous ramp would need inline styles — banned) —
   `low` < 0.55 ≤ `mid` < 0.80 ≤ `high`, unclassified/no-confidence
   reads `low`; colors `{error}` red / `{warning}` amber / `{success}`
   green / `{primary}` blue for `human`. Thresholds live in
   `adjustment_surface.py` (`CONFIDENCE_MID_FROM` / `_HIGH_FROM`);
   band rules in both themes under `ProcessStyleCombo[confidenceBand]`.
2. ~~Whether the confidence value itself surfaces anywhere~~ — landed as
   a live tooltip status line on the Style combo ("Auto-classified —
   confidence N%." / "You decided this style."); veto-able at
   calibration.

## 6. Implementation slices (when Nelson pulls the trigger)

1. **Schema + classify routine** — `classification_confidence` column;
   a reusable classify-items routine carrying the RAW-first stem
   inheritance and the §3 stability guards (skip user-set, skip edited,
   re-classify on rules_version mismatch).
2. **The background pass** — post-ingest classification worker (Collect
   AND the backfill wizard), off the UI thread, quiet.
3. **Edit surface** — Style button confidence color + human-decided flip
   writing `source='user'`; QSS roles in both themes. *(Landed
   2026-06-11: badge + `activated`-backed flip on both Edit pages —
   the workshop scopes segment → source video / snapshot → own row —
   plus inherit-at-creation (§1) and the workshop's default style now
   routing by classification like EditPage. Ramp values v0, §5.1.)*
4. **Pre-Edit removal** — Pick photo surface + video Pick page genre
   chrome retire with their lazy writers.
5. **Wizard refresh** — Mira rename sweep + the four QSS roles.

Targeted tests per slice. Sequencing against spec/56 slices 3–5 stays
Nelson's call at the next checkpoint.
