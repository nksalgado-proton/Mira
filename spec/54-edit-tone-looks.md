# spec/54 — Edit-phase tone redesign (Looks)

**Status:** design locked 2026-06-10, Nelson. Sequencing locked:
**calibration-first** — the data experiment (§5) runs before any UI work,
and its results gate the deferred decisions in §7. Implementation of the
UI layer must not start until the calibration checkpoint passes.

This doc is the durable record of the 2026-06-09/10 design session. It
supersedes the tone-slider model of docs/25 §3 (LRC-vocabulary sliders +
single AUTO) for the photo Edit surface. The crop/rotation side of the
Edit surface is **out of scope** — locked as-is.

---

## 0. Why redesign

The Edit surface exposes six LRC-vocabulary tone sliders (Exposure,
Shadows, Contrast, Highlights, Whites, Blacks) plus Strength, Style,
AUTO toggle and Vibrance. That vocabulary only means something to
someone already fluent in Lightroom. The target user is not.

The current AUTO is one adaptive formula per style — percentile
statistics of the photo's luminance histogram mapped to slider values
through per-style tuning constants (`core/photo_auto.py`), calibrated
against LRC-AUTO pairs. Its known weakness, identified by Nelson: **the
single per-style constant set is fitted "on average"** and is therefore
wrong in different directions for different sub-populations of the same
style (backlit vs evenly-lit vs low-key…).

---

## 1. The north-star principle

> **It is better to give the user choices by offering a coherent set of
> adjustments than to have an illiterate user try to adjust six sliders
> by himself.** (Nelson, 2026-06-10)

The user's mental act changes from *adjusting an image* (technical,
slider-pushing) to *choosing between finished interpretations* (visual,
taste). The photographic expertise moves into the product. Every
sub-decision in this redesign tests against this sentence.

---

## 2. Vocabulary

| Concept | Term | Status |
|---|---|---|
| One named tone option | **Look** | **LOCKED 2026-06-10** (same word EN + pt-BR) |
| The three Looks | **Natural / Brighter / Deeper** | **LOCKED 2026-06-10**; pt-BR: Natural / Mais claro / Mais profundo. Internal keys `natural` / `brighter` / `deeper`. |
| The hidden correction layer | router / A-fit | internal only, never user-visible |
| The visible taste layer | mood / spread | internal only, never user-visible |

---

## 3. The model — two layers

### 3.1 Layer A — the invisible router (correction)

AUTO becomes a **mixture of experts** instead of one average:

- Within each style, the calibration pairs are clustered by the
  original photo's histogram features (the same percentiles the formula
  already reads).
- Each cluster gets its **own fitted constant set** for the existing
  formula (`_TuningConstants` shape — targets, gains, caps).
- At runtime the photo's histogram routes it to its cluster's
  constants. Cluster membership is a function of statistics the engine
  already computes — **the user never sees this layer.**

This directly fixes "average is wrong" with zero new user-facing
complexity. A style whose data shows no cluster structure simply keeps
one fit and loses nothing.

### 3.2 Layer B — the visible Looks (taste)

The user-facing choice is **Original + 3 Looks** per style:

- **Original** — no tone adjustment at all. A first-class, pickable
  choice (Nelson: "quite a few times with LRC I decide to just leave
  the photo as shot"). Successor of the AUTO-off state.
- **Natural** (working name) — the A-routed fitted correction, bias
  zero. The anchor of the model.
- **Two further Looks** — the same A-routed correction plus a
  **designed bias** (direction: brighter/airier, deeper/moodier,
  punchier — final directions chosen per style after the calibration
  contact sheets exist).

Every Look remains **fully photo-adaptive**: the A-layer computes the
correction for *this* photo, the Look's bias shifts it in a taste
direction. A Look is not a fixed recipe — two photos under the same
Look get different absolute adjustments.

**Counts are locked at 3 Looks + Original** (2×2 grid geometry, §4.2;
also: three well-separated options beat five subtle ones — choice
paralysis is the slider problem, quieter).

### 3.3 Why bias, not independent fits

The pair data encodes ONE aesthetic — LRC AUTO as corrected by
Nelson's eye (manual exposure fixes where LRC missed). It cannot
calibrate a "moody" target; LRC AUTO is never moody. So:

- Layer A is **fitted from data** (the pairs).
- Layer B is **designed** as a principled spread around the fitted
  center, judged on contact sheets.
- If a Look ever deserves its own empirical loop, the path is to
  create a per-Look pair set (process the same originals in LRC with
  that develop intent) and fit it with the same machinery.

### 3.4 The convergence guard

The correction formula is conditional — a well-exposed photo gets
near-zero correction under every rulebook. The Look biases must
therefore include **character components that differ even at zero
correction** (contrast/vibrance flavor), otherwise the chooser shows
four identical tiles on good photos and reads as broken.

**Spread eyeball verdict (Nelson 2026-06-10):** confirmed — with the
v0 biases the Brighter/Deeper tiles are clearly distinct; the
smallest gap is Original↔Natural. That gap is *faithful* (the fitted
Natural tracks Nelson's own gentle targets), so it is accepted, not
fixed in tone math. UI option for later: when Natural ≈ Original the
grid may say "already well exposed". Appeal confirmed "case by case"
— the per-photo chooser hypothesis holds.

**Bias magnitudes — v1 (in-app checkpoint, Nelson 2026-06-10):** the
v0 spread (±0.30–0.35 EV) read as "a bit too bright / a bit too dark
— I would always choose Natural", failing the appeal test in the
live chooser. Brightness push halved, character components kept:
Brighter = (+0.18 EV, shadows +8, whites +6, contrast −4, vibrance
+8); Deeper = (−0.15 EV, blacks −10, contrast +10, highlights −5,
vibrance +10). ``tools/calibrate_looks.py SPREADS`` is the source;
``export`` regenerates the shipped data module.

---

## 4. The user-facing surface

The whole tone story becomes three CHOICES — **zero sliders**
(locked Nelson 2026-06-10, superseding the earlier
Intensity + Vibrance plan):

| Control | Question it answers | Form |
|---|---|---|
| **Style** | What is this photo? | dropdown (kept; classifier-seeded, user-correctable) |
| **Look** | How should it feel? | cycle + 2×2 grid (§4.2) |
| **Filter** | What should it become? | chooser (§8; set design pending) |

### 4.1 Where the sliders' jobs went

- **Intensity** ("I like it, but less") → absorbed by calibrated
  restraint: the Looks are fitted to Nelson's own gentle targets, and
  each creative filter ships at ONE curated strength (Panasonic
  pick-and-shoot grammar). The engine keeps the intensity degree of
  freedom internally (``compute_look_params(intensity=…)``, fixed at
  1.0) — if the filter design phase proves on contact sheets that a
  specific filter needs an amount control, that returns as an
  evidence-gated per-filter decision, not a standing slider.
- **Vibrance** → absorbed by choice: each Look carries its own color
  character (§3.4), and the filter set covers the axis discretely —
  a color-forward member (Vivid/Pop), a Muted/Faded member, B&W at
  the far end.

### 4.2 The chooser — cycle + grid

Two zoom levels of the same choice:

- **Resting state:** a compact control (dropdown or cycle buttons) —
  change it, watch the photo change.
- **The grid moment:** a **2×2 grid of THIS photo** — Original + the 3
  Looks, each tile rendered with the photo's own adaptive correction.
  All four tiles are **clickable choices** (Original included). Enter,
  click the winner, leave.

### 4.3 What survives unchanged

- **Compare-with-original** toggle (`\`) — still wanted.
- **Crop / box rotation / 90° image rotation / aspect** — untouched,
  out of scope.
- The **`Params` engine** (`core/photo_render.apply_params`) — Looks
  compile to `Params` under the hood. The math layer does not change
  (filters will EXTEND it with new primitives, §8).

### 4.4 What dies

- The six tone sliders (Exposure, Shadows, Contrast, Highlights,
  Whites, Blacks) — gone from the photo Edit surface.
- The AUTO toggle — absorbed by Original-as-a-Look.
- Strength-as-AUTO-scaler — gone (its ugliest behavior, silently
  overwriting hand-tuned sliders, dies with the sliders; its "how
  much" job is absorbed per §4.1).
- The Vibrance slider — absorbed per §4.1 (zero-sliders lock).
- Manual tone fine-tuning as a product capability (photo surface).
  Accepted consciously: this is the first streamlining move landing in
  XMC itself rather than waiting for the MC carve-down — §1 is the
  reason.

---

## 5. Calibration plan (runs FIRST)

### 5.1 The data

`D:\Photos\Compare LRC Auto correction` — 7 style folders, **499
pairs** (census 2026-06-10: General 23, Landscape 29, Macro 155,
Portrait 45, Selfie 19, Wildlife-Action 13, Wildlife-Static 215).
Fit-phase clustering landed at k=3 for macro + wildlife, k=2
elsewhere (portrait's k=3 third cluster overfitted on validation —
refit at k=2, 2026-06-10).

- Pairing patterns, in priority order: `<stem>` + `<stem>-2.JPG`
  (primary, the expanded set); `<stem>.RW2/.HEIC` + `<stem>.JPG`
  (legacy); `<stem>.JPG` + `<stem>(1).JPG` (legacy GoPro).
- Ground truth = **LRC AUTO as corrected by Nelson** (manual exposure
  fixes where LRC missed). The target is Nelson's judgment; LRC is
  scaffolding.
- `_catalog` and `_compare_runs` folders are not data.

### 5.2 The harness

`tools/calibrate_looks.py` (Mira) — ported from Mira's
`tools/compare_auto.py` with an audit pass, extended:

1. **sweep** — decode every pair (originals at the app-faithful
   ≤1280 px the live AUTO sees), extract original histogram features,
   current-fit residuals vs LRC, and the LRC correction vector per
   pair → `pairs.json` (decode once, analyze many).
2. **analyze** — per-style residual structure (does the single fit
   miss in systematic directions?), clustering on feature space
   (scipy, no new deps), per-cluster correction-vector report.
3. **fit** (phase 2, after evidence) — per-cluster constant
   optimization against the pair targets; held-out validation;
   contact sheets per cluster.
4. **spread** (phase 3) — design the Look biases; contact sheets are
   the judge, Nelson's eye is the metric.

### 5.3 Checkpoints

- After **analyze**: evidence checkpoint with Nelson — cluster
  structure exists / doesn't, per style. Gates per-cluster fitting and
  the §7 deferred decisions ("until we see the results").
- After **fit**: contact-sheet eyeball per style.
- After **spread**: the Look set per style + naming session.

---

## 6. Persistence + export (design level)

- The `Adjustment` row's tone payload becomes the **choice**, not the
  resolved numbers: style, look, creative_filter (zero-sliders lock —
  no intensity/vibrance columns). Crop columns unchanged. Resolved
  `Params` are recomputed deterministically from the choice + the
  photo (same as render).
- **Clean break** (locked 2026-06-10): no event databases with tone
  edits worth preserving exist. Schema migrates per the standard
  migration policy; old `params_json` tone state is dropped; crop /
  rotation / aspect survive untouched.
- Export compiles choice → A-routed correction → bias × intensity →
  `Params` → existing engine pipeline. A photo with no row exports as
  Natural at 100 (the default), mirroring today's fresh-AUTO behavior.
- The vestigial `Adjustment.auto_on` / `strength` columns are
  retired/repurposed by the same migration.

---

## 7. Deferred decisions — ALL RESOLVED 2026-06-10 (post-calibration)

| # | Decision | Resolution (Nelson 2026-06-10) |
|---|---|---|
| 1 | **Video surface fate.** | **Looks on video, uncalibrated.** The same chooser ships on EditVideoPage immediately; the photo-fitted constants run on tonemapped frames, accepted as better than nothing. The slider grid dies everywhere. Video-specific calibration remains a backlog item; when it lands it only swaps the constants, not the UI. |
| 2 | **Copy/Paste Adjustments.** | **Killed.** Copy / Paste / Undo Paste buttons and the module-level clipboard are removed. Re-picking a Look elsewhere is two clicks; bulk consistency can return later as "apply to scope" if ever missed. |
| 3 | **Vocabulary lock.** | **"Look"** (EN + pt-BR, same word) — see §2. |
| 4 | **Look names + directions.** | **Natural / Brighter / Deeper**, uniform across styles (no per-style flavoring for now); pt-BR Natural / Mais claro / Mais profundo. v0 bias magnitudes accepted (§3.4). |

---

## 8. Creative filters + versions-as-exports (locked 2026-06-10)

Second design session (Nelson 2026-06-10, same day). With the sliders
gone, Edit gains a **creative layer** on top of the corrective one:

- **Creative filters** — a *distinct Mira-designed set* (NOT
  Panasonic recreations; Panasonic's Creative Filters are the
  inspiration only). B&W, Sepia + a curated family with strong
  identities. Where Looks "correct", filters deliberately
  *un-correct*: distinctive, playful, transformative.
- **Pipeline order (locked):** A-correction → Look bias × intensity →
  creative filter. The filter transforms the *corrected* photo;
  "Deeper + Sepia" is a legal recipe. ``filter`` is NULL = none.
- **Versions = exports.** No first-class version entity. The user
  dials a recipe, exports, re-dials, exports again — each export is a
  committed JPEG. Share groups a photo's exports via the existing
  export lineage and a Cut picks WHICH file it uses (per-Cut version
  choice — Share-slice work, see spec/51).
- **Lineage snapshot:** every export records its full recipe (style /
  look / creative_filter / crop) AND the resolved Params it rendered
  with. Append-only archival — the live adjustment row stays
  choice-only, one source of truth (Nelson delegated, call made
  2026-06-10: no resolved values on the live row; full snapshot on
  lineage instead).
- **Sequencing:** the Looks slice lands first (it removes the
  sliders); the ``filter`` column + lineage snapshot ship in the same
  v2 migration so no third migration is needed. The filter SET then
  gets its own design phase — engine primitives (tint / split-tone /
  B&W mixing — ``Params`` cannot express Sepia today), candidate
  recipes on contact sheets, eyeball loop, naming session (filter
  names are user-visible vocabulary: tr() + locked, like Looks).

## 9. Relationship to other specs

- Supersedes the docs/25 §3 tone-slider model for **both** Edit
  surfaces — photo AND video (§7 #1 resolved: Looks on video,
  uncalibrated). The slider grid leaves the product.
- `spec/03-schema.md` gains the Adjustment tone-payload change when
  the UI slice lands (migration, not column abuse).
- `spec/41-xmc-completion.md`: this redesign replaces the Edit-phase
  polish items that assumed the slider surface.
