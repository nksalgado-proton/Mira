# spec/40 — Mira V1 · Effortless Craft

**The Supreme Implementation Guide for Mira V1.**
Authored 2026-06-04 (Nelson + Claude). Locks the V1 product design after the strategic
pivot of 2026-06-04 (market research + persona reframe + pipeline collapse).

> **Read this before every V1 session. Every V1 design choice traces to a principle in
> here. When code disagrees with this spec, update the spec first to reflect the new
> understanding, then update the code.**

---

## 0. THE NORTH STAR — Effortless Craft

> **Persona 1 makes pro-quality content without effort, gets praise from their audience,
> and feels they *created* something. They come back next event. That is the loop.**

The phrase is **"Effortless craft."** Three dimensions:

1. **Effortless input** — the user does small things (plug in a card, tap a winner, set a
   duration). The tool does the rest. Sliders exist as a fallback for the curious; they
   are never required.
2. **Craft output** — the output looks like *real photography work*, not like a phone
   roll. Tone-corrected. Cropped. Composed. Compiled into a designed slideshow with
   scene cards and collages. The artifact reads as deliberate.
3. **Praise loop** — the audience that watches the share session says *"this is great."*
   The user feels they *created* it (deliberately chosen verb — Mira is a creative
   tool, not a file mover). They come back next trip.

**Brand alignment:** the name *Mira* embeds "craft." *Effortless craft* is the
promise the name carries. Every share is the user living the brand without anyone
having to say it.

**The decision test.** When a V1 feature decision arises, ask: *"Does this serve
effortless craft?"*
- **Yes** = it removes user effort, raises output quality, or sharpens the praise loop.
- **No** = it adds effort, lowers quality, or breaks the praise loop.
- **Unclear** = think again, or defer.

If a feature serves effortless craft only for power users, it does not belong in V1. It
belongs in [Mira X](41-xmc-completion.md).

---

## 1. WHO V1 IS FOR — Persona 1, the new-camera transition user

The user who:

- **Has just bought (or just started using) a real camera.** Interchangeable-lens,
  compact, mirrorless, whatever. They were a phone-only photographer until recently.
- **Still uses their phone too.** Or their partner does. Phone is part of every event.
- **Has developed *some* interest in photography** — enough to commit to a camera, not
  enough to learn craft language. They will not say "shutter speed" or "histogram"
  unprompted.
- **Wants less work, better outcome.** They are *not* trying to become a photographer.
  They are trying to get more out of having committed to a camera.
- **Has to organize and present the result back to people who were there.** Family,
  friends, fellow travellers. The audience is intimate, not anonymous.

### Who Persona 1 is **NOT**

- **NOT the 100%-phone user.** Phone-only humanity has Google Photos / iCloud / Samsung
  Gallery and (mostly) does not buy a Windows desktop tool. Mira is not for them.
- **NOT the wedding / event pro.** That segment is targeted by Aftershoot, Narrative
  Select, Imagen, FilterPixel. Mira is not for them.
- **NOT the serious amateur enthusiast** (Persona 2). They want the wizard, per-style
  AUTO, multi-camera reconciliation, focus stacking, classification queries. That's
  Mira X.

### Why this segmentation is right

Backed by two market-research workflows run 2026-06-04:

- **First pass** (`wf_0298193a-bcb`, 24 sources, 14 confirmed claims): the cull-tool
  market is targeted at wedding pros via AI-automation; the DAM market is owned by
  Mylio + Excire-in-LRC + Helicon-into-LRC; the **multi-day multi-camera nature/wildlife/
  landscape trip-event segment is not the named target of any surveyed cull tool**.
- **Second pass** (`wf_5ec156fc-419`, 29 sources, 13 confirmed claims): phone-first
  reality is validated globally (5B smartphone users, ~1.8–2T mobile photos/year); the
  cell-phone cloud-incumbent landscape is dominated by Google Photos / iCloud / Samsung
  Gallery with documented free-tier-squeeze + vendor-lock-in patterns; Mylio is the
  named offline-first competitor positioning explicitly against cloud lock-in.

Persona 1 sits in the gap: a real-camera user who experiences phone-photo lock-in pain
but is not the target of any current cull / catalogue / processor tool.

---

## 2. THE PRODUCT — 3 phases, end to end

V1 collapses the legacy 7-phase pipeline (Plan → Capture → Cull → Select → Process →
Curate → Distribute) into **3 phases**. Persona 1 can hold this in their head:

| Phase | The Persona 1 story | Teaser (anti-subscription pitch) | Output |
|---|---|---|---|
| **Capture** | *"Get the photos in, throw out the obvious junk."* | **Focus peaking** ON by default, with a one-time tooltip. Functional + educational. | Cull-Kept items (photos + videos) in event store |
| **Select** | *"Pick the keepers and make them look nice."* | **Style alternatives** + simplified slider set. The "subscription tone tool for free" pitch. | Silent export per-item: JPEG@90% / MP4 |
| **Share** | *"Organize and share."* | **Time-budget compilation** + seamless photos + videos + scene cards + collages. The "designed slideshow without paying Adobe" pitch. | Saved compilation + MP4 / folder-of-JPEGs |

Plus capabilities (not phases): event metadata edit · sync-pair TZ calibration · create-extras palette (scene cards + collages) · backup/restore · audit.

---

## 3. PHASE 1 — CAPTURE

### What it does

1. User plugs in an SD card (or selects a phone source).
2. Mira **auto-creates `00 - Captured/`** under the event root and copies every
   file from the source. **No user question.** The CLAUDE.md #9 wipe gate now has its
   verified backup; user may eject the source immediately and trust the system.
3. User is dropped into the **Cull surface** against the backup — not against the
   source. Source is untouched and stays untouched until the user explicitly invokes
   the wipe gate (which Persona 1 may never invoke; that's fine).
4. **Fast-Culler shape**: keyboard-fast, mouse-wheel-fast, default = Keep, throw out
   the obvious junk.
5. Optional second source (phone) — same backup pattern, same Cull surface.

### The teaser — focus peaking

- **Green outlines** mark what's in sharp focus. Out-of-focus areas don't get
  highlighted.
- **ON by default** for V1. First-time banner: *"Green outlines show what's in sharp
  focus — anything blurry is usually trash."* Toggle to turn off, for users who find
  it distracting.
- **Functional value**: out-of-focus is by far the biggest junk category Persona 1
  produces. Focus peaking is the single most useful technical cull criterion.
- **Educational value**: Persona 1 learns that focus peaking exists. Real cameras (and
  real photographers) use this. They feel slightly more equipped.
- **Implementation**: already shipped — Laplacian port (commit `227130e`). Just keep
  it ON in the V1 Cull surface.

### Sync-pair TZ calibration

Replaces the current sync-pair-picker + per-camera offset machinery.

- User takes **one phone photo + one camera photo at the same moment** (any moment
  during the event — first shot is fine, doesn't have to be at the start).
- User points Mira at the pair: *"these two are the same moment."*
- Mira reads both timestamps; **camera TZ delta = phone-time − camera-time**.
- Phone is assumed TZ-correct (network-set globally on modern phones). One action.
- Solves "I bought the camera and never set the clock" automatically — the most common
  new-camera-transition error.
- **Full UI design deferred** to implementation; principle is locked.

### Phone ingest mechanism

Three candidates, ranked by V1-readiness:

1. **Cable + USB storage** (phone as removable storage to Windows) — works on Android
   cleanly; on iOS, photo-only but Apple manages it. Most universal. **V1 default.**
2. **Drop-folder + watch** (user copies / AirDrops to a known folder; Mira picks
   up). **V1 supported.**
3. **Local Wi-Fi share** via small companion app. **V1.1+** (scope-heavy for V1).

### Video at Capture

Videos go through the same Cull surface, routed by item kind:

- Player + scrub timeline (existing video Cull machinery from the rebuild).
- Keep / discard, same default.
- **Cull-time snapshot extraction**: pull a still frame from a video — becomes a photo
  item in the store.
- **Cull-time sub-clip extraction**: trim a useful section from a long video — becomes
  a clip item. (Materialization to real bytes happens at Cull-exit, hardlink/stream-
  copy/extract as appropriate — already shipped.)
- **Implementation gap to verify**: the rebuild's `video_cull_page` was flagged in
  memory `project_cull_m2_scope_corrections` as unwired stale pre-relational port.
  Under V1, this is **critical path** — first verification step when implementation
  starts.

### V1 Capture deliberately does NOT include

- The wizard (no auto-open; rules engine uses defaults only)
- Three-named-Culler-surfaces (one Cull surface, Fast-Culler shape, handles all V1)
- Manual focus-stack / exposure-bracket clustering (automatic burst-collapse is fine)
- Multi-camera reconciliation
- The current Plan phase (event metadata edit replaces it, post-fact)

---

## 4. PHASE 2 — SELECT

### What it does

1. Show Cull-Kept items (photos + videos, interleaved chronologically).
2. **Compare 2-up with EXIF-diff highlight**: existing compare grid. EXIF differences
   (shutter, aperture, ISO, focal) highlighted when both photos share a style;
   suppressed when >2 fields differ. Doubles as a teaser — Persona 1 sees "1/60 vs 1/8"
   and learns what those numbers mean over time.
3. **Tap the winner**: one keypress per pair, the other becomes discarded.
4. **Inline tone adjustment** via the *Style alternatives* strip + a few sliders.
5. Press Export → silent JPEG@90% (photos) / MP4 (videos) to user-defined output dir.

### The teaser — style alternatives strip

The mechanic — **the user gets per-classification AUTO without touching a slider**:

1. Mira reads EXIF → infers classification using **default brand-agnostic rules**
   (existing `classifier_v2` engine, no wizard).
2. Default per-class style applies → photo preview shows the per-class AUTO.
3. **Below the photo: a small strip of style alternatives** (5 candidates). One-click
   between them, preview re-renders instantly.
4. **Below the alternatives: 3-4 sliders** for the user who wants to fine-tune:
   - Exposure
   - Highlights / Shadows (one combined slider, or two — implementation choice)
   - Warmth (WB temp simplified)
   - Contrast
5. **Crop + straighten** as a separate small surface. Aspect-ratio presets: **Free /
   1:1 / 16:9 / Match original.** That's it for V1.

### Style naming — Hybrid (locked)

Each alternative shows BOTH a friendly word AND the photographer term:

| Friendly | Photographer term |
|---|---|
| Sharp & punchy | Wildlife |
| Bright & airy | Landscape |
| Soft & natural | Portrait |
| Close + rich | Macro |
| Cool clean | Selfie |

(Or similar. Final wording is implementation polish. The pattern is what matters:
Persona 1 picks the friendly term they understand; they absorb the photographer term
over time without being lectured.)

**Style count: 5** for V1. Astro / Night-long-exposure / Sports / Family / Travel /
Street are deferred — they don't fit Persona 1's typical event mix.

### Video at Select

Videos appear interleaved with photos. Centre-click routes by kind:

- **Photo or snapshot** → standard Select Adjustment Surface.
- **Video / clip** → same surface on a **representative frame** (extracted from the
  middle of the clip, or the first frame, or where the user paused — implementation
  pick). Tone alternatives + crop apply to the whole clip.
- **Light trim**: endpoint adjustment only at V1. No multi-clip editing.

### Silent export

- **Photos** → JPEG @ 90% quality. One default, no picker.
- **Videos** → MP4 with tone + crop + trim baked in. Codec / bitrate defaults set
  during implementation.
- **Destination**: user-defined output dir, set once. First-run prompts the user:
  *"Where do you want your exports to go?"* Default suggestion:
  `<My Pictures>\Mira\<event_name>\`.
- **No format picker, no scope picker, no per-photo dialog.**
- **Re-export overwrites** the prior file. No version history at V1 (simpler mental
  model).

### V1 Select deliberately does NOT include

- The full AdjustmentSurface (10+ sliders, classification-driven clipboard, per-style
  presets manager) — that's Mira X.
- Per-classification user-tuned AUTO (wizard-fed) — defaults only at V1.
- "Copy aspect to all in day" and similar power-user affordances.
- Brackets / focus stacks as user-managed clusters.
- The separate Process Day Grid host page — Process collapses into Select.
- Audio / speed / stabilisation on videos.

---

## 5. PHASE 3 — SHARE

### What it does

1. User triggers Share from the event.
2. **Upfront dialog** — all share choices in one place:
   - **Target duration** (minutes): 5 / 10 / 30 / custom
   - **Per-photo duration** (slider, 3-10s, default 5s)
   - **→ Derived time budget** ("about 60 photos") shown live as the user adjusts
   - **Source**: Select-Kept (default) OR an existing saved compilation (user can fork
     "30-min trip" → "5-min highlights")
   - **Mode**: Add (start empty, pick in) OR Subtract (start full, remove)
3. User presses Start.
4. **Budget-aware pick session**: budget monitor visible at all times ("12 of 60 used"
   or "3:00 / 5:00"). Visual feedback approaching / exceeding budget.
5. **Playback inside Mira** — the most common share moment is the laptop on the
   dinner table. Persona 1 plays it for the family right there.
6. **Save / Export**:
   - **Save the compilation** (named like "Costa Rica — 5min Highlights") — forkable,
     reusable as source for future Share sessions.
   - **Export to MP4 slideshow** (for sending).
   - **Export to folder-of-JPEGs-in-order** (for PowerPoint / email / USB).

### The teaser — time-budget compilation + photos+videos seamless

This is the **killer differentiator**:

- Photo-only slideshow tools are everywhere. Photo+video seamless through cull → select
  → share is the hole in the market.
- Persona 1 mixes photos + cell-phone video in every event. A tool that treats both as
  equal citizens through the whole pipeline is genuinely missing.
- The **time-budget mechanic** is something people pay Animoto / Magisto / Adobe
  Express / Canva for. Free + offline + integrated = strong V1 positioning.

### Transitions and framing

Keep minimal at V1:

- **Transitions**: Cut + Fade. Two options.
- **Framing**: Fit + Fill. Two options.
- Ken Burns / slow-zoom: nice-to-have; V1 ships without it OR ships with one preset
  (decide at implementation).

### Budget math

- **Photo** / **Snapshot** / **Collage** / **Scene card** → per-photo duration (3-10s).
- **Video clip** → its actual clip duration (not per-photo).
- Possibly a 1.2-1.5× multiplier on collages (more to look at) — implementation polish.

### V1 Share deliberately does NOT include

- Curate bases + subsets + portfolio bases (Mira X).
- Closed-events / status pill (Mira X).
- Lifetime catalogue queries (Mira X, possibly never).
- PTE bundle export (PTE-using power users are not Persona 1).
- Music tracks on the slideshow (licensing + scope = V2; V1 ships silent).
- Audience-type selector ("Family / Social / Email / Formal") in the upfront dialog —
  duration alone is enough at V1.

---

## 6. CREATE-EXTRAS PALETTE — Scene cards + Collages

Not a phase. A small palette at the event level, accessible anytime once Select-Kept
items exist. Both produce items that get fed INTO the Share compilation source pool.

### Scene cards (a.k.a. title cards)

- Take an existing photo + overlay text → *"Day 1 — Arrival"*, *"Sunset Cruise"*.
- Flexible content: font / size / position / colour / readability gradient under text.
- **Map separator** as a second mode — V1 = **user-supplied map** (paste in a Google
  Maps screenshot). Auto-route-from-GPS deferred to v1.1+ (needs offline tile data;
  off-charter at V1).
- **Per-day descriptions** (entered in event metadata) **pre-fill** scene-card text
  when the user creates a card for that day. Small but very Persona 1.

### Collages

- 3-5 predefined templates:
  - **2×2 grid** (4 photos)
  - **3-strip horizontal** (3 photos, equal height)
  - **1+2** (one large + two small)
  - **4-asymmetric** (one big square + three skinny)
  - **Tall portrait-mix** (one tall + two/three smaller portraits)
- Drop photos into a template → composed image.
- Becomes a regular item in the event store (new item-kind, extending the existing
  two-kind taxonomy).
- Available in the Share upfront dialog source pool alongside individual photos +
  videos.

### Implementation

- Pillow / Qt painter compositing of existing photos + text / template layout. No new
  heavy tech.
- Storage: one JPEG per item, like any other photo.
- New item-kind tags in the relational store (e.g. `scene_card`, `collage`). Pure
  schema extension — no model change beyond a new enum value.

---

## 7. SIDE CAPABILITIES (not phases)

### Event metadata edit

The current Plan phase machinery does not survive as a phase. Its capabilities get
demoted to **post-fact event metadata editing** accessible from the event header:

- **Per-day description** (e.g. "Arrival in San José", "Cloud forest")
- **Per-event TZ** (set via the sync-pair calibration)
- **Per-event title / cover photo**
- **Per-event location** (free text)

### Backup / Restore

Same shape as Mira X — the event tree + `event.db` round-trips through a JSON
backup format. V1 user-facing flow: *"Back up this event to a single file"* / *"Open
event from a backup file."*

### Audit

The existing consistency-audit machinery applies. V1 surfaces it as *"Check this event"*
— produces a plain-English report ("3 photos are missing", "1 video has no kept
state").

---

## 8. WHAT V1 IS BUILT ON — shared core with Mira X

V1 reuses pure-logic code from [Mira X](41-xmc-completion.md). The seam:

### Shared (no Qt)

- Gateway facade + relational store + schema + repo + JSON dump/load
- Settings system (different defaults per product)
- `classifier_v2` rule engine + brand profiles (V1 uses defaults; X uses
  wizard-customised)
- EXIF readers + photo decoder + focus peaking (Laplacian)
- Atomic write-then-rename + crash-safe journals
- Video machinery (cull-time snapshot/clip, materialization, frame extraction)
- Paths + protect + i18n primitives
- ffmpeg wrappers (export, stitch, trim)

### Different per product

| Area | V1 | Mira X |
|---|---|---|
| UI surfaces | 3-phase, simpler | 7-phase, enthusiast |
| Pipeline orchestration | Capture / Select / Share | Plan / Capture / Cull / Select / Process / Curate / Distribute |
| Settings defaults | Simplification defaults | Enthusiast defaults |
| Wizard | NO (rules engine uses defaults only) | YES (auto-opens on first run) |
| Process surface | Collapsed into Select | Full Process page + AdjustmentSurface + ProcessVideoPage |
| Compilation tool | New time-budget Share | Legacy Curate (bases + subsets) + Distribute |
| Multi-camera reconciliation | NO | YES |
| Closed-events / status pill | NO | YES |
| Third-party tool integration | NO (one-way handoff only) | Possible at X if Nelson wants it |

Repo strategy decided at V1 kickoff:

- **Option A**: monorepo with `mira_core/` (shared) + `mira_x/` (X UI) +
  `mira/` (V1 UI). Simplest for development velocity.
- **Option B**: separate repos with shared package (pip-installable). More friction.

Default: **Option A** unless friction emerges.

---

## 9. DISTRIBUTION MODEL

- **Free** — no paid tier. Aligns with anti-subscription positioning.
- **Donations** — Patreon / Buy Me a Coffee / PayPal / similar. Keeps dev alive without
  compromising the principle.
- **Offline-first** — no cloud, no telemetry. The differentiator vs Google Photos /
  iCloud / Mylio.
- **Windows desktop only at v1.** macOS / Linux deferred.
- **Languages at v1.0**: English (locked). Portuguese (directionally supported on the
  English-barrier axis per `wf_5ec156fc-419`; cross-language ranking not completed; OK
  as v1's second language but final lock pending). All other localizations are v1.1+.

### Website + brand

- **Headline**: *Mira — Effortless craft.* (Or similar; brand asset work happens
  during V1 development.)
- **Pitch**: *"Take photos with your camera. Use your phone. Make something to share.
  No subscription, no cloud, no one selling your trip photos."*
- **Channels**: website + direct download + GitHub releases? + photography forums
  (DPReview, Fred Miranda Nature, etc.). Word-of-mouth from satisfied Persona 1 users
  is the long-game distribution.

---

## 10. THE PRODUCT-LADDER OPTION

Mira X starts as Nelson's personal tool. **If V1 succeeds, X becomes a natural
second product** for users who graduated from V1 — the upgrade path for users who
discover they love photography and want to invest more.

- **Mira** = acquisition + brand, free → Persona 1 / early stage
- **Mira X** = enthusiast track for users who emerged → Persona 2 / committed stage

Cross-sell is natural because both serve the same person at different life stages.
Pricing for X stays open (free? paid? donation-based?) — decision deferred to
post-V1-success.

**Does NOT change current execution.** X is built now for Nelson's personal use. The
"X as product" decision is a deferred strategic option that comes for free as a side
effect of having X be a real installable artifact.

---

## 11. OPEN DESIGN QUESTIONS (decide during implementation, not blocking)

These are deliberately not pinned in this spec. They lock at implementation time
against the *Effortless craft* test.

1. **Style alternative count + naming.** 5 alternatives proposed; Hybrid naming
   pattern proposed. Final wording to lock during Select implementation.
2. **Slider set in Select.** 3 or 4 sliders, which ones, defaults. Exposure + Warmth
   are mandatory; Highlights/Shadows as one or two; Contrast.
3. **Crop UI.** Free / 1:1 / 16:9 / Match — order of presets in the picker.
4. **Style alternatives — which 5.** Wildlife / Landscape / Portrait / Macro / Selfie
   proposed. Confirm during Select implementation against Persona 1's typical event mix.
5. **Cull video surface** — verify port status during Capture implementation; fix or
   re-port as needed.
6. **Sync-pair TZ UI** — picker / dialog / overlay; how the user "tells Mira about
   the pair."
7. **Output dir prompt** — first-run only or also accessible from Settings?
8. **Re-export overwrite confirmation** — first-time-only warning or never warn?
9. **Compare-mode "neither" keybind** (both lose) — exists or not?
10. **Phone ingest UI shape** — drop-folder location, where to expose it.
11. **Collage templates — final 3-5 picks.** 2×2, 3-strip, 1+2 are locked; the other
    1-2 picks decide during Share implementation.
12. **Scene-card text styling defaults.** Font, size, position, opacity, gradient.
13. **Slideshow transitions / framing** — exact picks (Cut + Fade probably; Fit + Fill
    probably; Ken Burns optional).
14. **MP4 export defaults** — codec, bitrate, resolution.
15. **Compilation budget math for collages** — straight per-photo or with multiplier?
16. **Audience-type selector in upfront Share dialog** — V1 omits per current decision;
    revisit if Persona 1 testing surfaces a need.
17. **Per-day-description pre-fill for scene cards** — implementation detail.

---

## 12. WHAT V1 DOES NOT SHIP — explicit non-goals

Naming these protects against scope creep during implementation:

- Wizard auto-open (defaults-only at V1)
- Full AdjustmentSurface (style alternatives + sliders replace it)
- Per-classification user-tuned AUTO
- Three named Culler surfaces (Fast / Camera / Final) — one Cull surface only
- Multi-camera TZ reconciliation
- Focus-stacks / exposure-brackets as user-managed clusters
- Curate bases + subsets + portfolio bases + Collections page
- Closed events / status pill
- Lifetime catalogue queries / cross-event search
- Third-party tool round-trip (LRC, Helicon, Capture One, etc.)
- Video audio editing / speed change / stabilisation
- Multi-clip editing inside one video
- Music tracks on slideshows
- PTE bundle export
- Cloud sync of any kind
- Telemetry of any kind
- Auto-route maps from EXIF GPS
- Multi-language localization beyond En + (maybe) Pt at v1.0
- macOS / Linux builds

---

## 13. CITED BACKING

- **Market research workflow `wf_0298193a-bcb`** (2026-06-04) — broad ecosystem +
  cull-tool segmentation + lifetime-catalogue incumbency analysis. Verdict: commit V1
  to discipline-first cull/select; V2 (catalogue) faces structural incumbency.
- **Market research workflow `wf_5ec156fc-419`** (2026-06-04) — Persona 1 sizing +
  cell-phone cloud-incumbent landscape + language prioritization. Confirmed: phone-
  first reality globally; Samsung Sept 30 2026 OneDrive-sync cutoff as imminent
  refugee event; Mylio as named offline-first competitor (reach unknown); EF EPI has
  self-acknowledged upward bias.
- **Memory entries**: [[project_v1_first_persona1_strategy]], [[project_persona1_market_findings]],
  [[project_two_product_strategy]], [[feedback_localization_is_accessibility_not_market]],
  [[project_marketing_lifetime_catalogue_promise]] (shelved).

---

## 14. WHAT TO DO WITH THIS DOCUMENT

- **Every V1 session reads this before touching code.** Same discipline as the charter.
- **Every V1 feature decision cites a principle here.** "Effortless craft says X."
- **When this document is wrong, update it first, code second.**
- **The companion document** [spec/41 — Mira X completion](41-xmc-completion.md)
  governs the X work that comes before V1.

This is the constitution of V1. It binds every future session.
