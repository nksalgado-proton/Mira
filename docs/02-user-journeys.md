# 02 — User Journeys

> **Status: Phase 1 draft.** Skeletons of the journeys the app must support. Each journey will be filled in (steps, decision points, non-negotiables, nice-to-haves) over the requirements phase. Personas referenced are P1 / P2 / P3 from `01-personas.md`.

A user journey is **end-to-end goal-oriented**, not a feature list. "Make a slideshow" is a journey; "configure white balance" is not. The personas should be able to walk each journey without reading documentation.

The first eight are the load-bearing journeys for v1. Three more are catalogued for later.

---

## J1 — First-run setup (the centerpiece of v1)

**Goal:** A user just installed the app. They want to get from "I clicked the icon for the first time" to "the app understands how I shoot well enough to classify my photos correctly" in **under 20 minutes** for the full path, **under 5 minutes** if they accept defaults.

This is **not** a typical onboarding wizard. It is the mechanism by which user knowledge becomes machine rules. Time invested here pays off in every subsequent journey.

**Triggers:** First launch. Re-entered from Settings when the user wants to extend or refine their profile.

**Personas:** All three. The wizard's question phrasing must work for any modern ILC user — that is the v1 requirement, not "P1 only with patch-release expansion."

### Why the wizard exists

The fundamental constraint discovered in Phase 0: **camera custom-mode slots (C1, C2, C3-x) cannot drive classification because EXIF does not record which slot a photo was taken in.** Classification has to work on the EXIF fields that *are* recorded (aperture, focal length, focus mode, AF area, ISO, drive mode, Photo Style, white balance, etc.).

So the app does not ask "what camera do you own and which slot is each scenario assigned to?" It asks "**how do you actually shoot?**" — genre by genre, in EXIF-grounded terms. The answers build classification rules.

### Key steps

1. **Welcome + privacy statement.** "Nothing leaves your machine. No telemetry, no internet check-ins, no cloud."
2. **Data folder + photos base folder.** Pick where user data lives; pick the root where the user already keeps photos.
3. **Camera bodies.** Add 1–N. For each, brand + model. v1 ships with built-in capability profiles for as many bodies as the author can fill confidently (starting with Lumix G9 MkI + MkII at full coverage). Bodies without a built-in profile fall through to a stub + "please review" markers — but the wizard's EXIF-pattern path still works regardless. Brand identification is used to localize the wizard's terminology (e.g., "Photo Style" for Panasonic, "Film Simulation" for Fuji, "Picture Control" for Nikon).
4. **Lenses + accessories.** Same built-in-or-stub pattern. Mark primary use per lens ("wildlife", "macro", etc.).
5. **Photographic focus.** Multi-select: wildlife, macro, landscape, portrait, street, sports, travel, family, video, other.
6. **Per-genre EXIF-pattern questionnaire.** This is the core. For each genre the user selected, a short series of EXIF-grounded questions. Examples:
   - **Wildlife:** "What AF mode do you use most — AF-S, AF-C, or manual?" / "What's your typical aperture range — wide open, f/5.6–f/8, smaller?" / "Burst mode default — single, low, high?" / "Photo Style — Standard, Vivid, custom?" / "ISO range you're comfortable with?"
   - **Macro:** "Focus mode — autofocus, manual, or both?" / "Do you use focus bracketing on a tripod, focus stacking handheld, or both?" / "Flash setup — on-camera, off-camera with trigger, ambient only?" / "Working aperture range?"
   - **Landscape:** "Tripod or handheld?" / "ND or grad filters often?" / "Photo Style — Standard, Scenery, custom?" / "Aperture range you favor?"
   - **Portrait:** "Lens choices — short tele primes, zooms, mixed?" / "Photo Style — Portrait, Standard, custom?" / "Background separation — wide open or stopped down?"
   - **Etc.** Questions are short, multiple-choice where possible, "skip / I don't know" always available.
7. **Generate personal scenario library.** From the answers, the app builds 3–10 scenario profiles (one per chosen genre, plus a "General" fallback). Each scenario is an EXIF-pattern matcher plus reference-card content. The user can review, edit, or reject any of them before saving.
8. **Done.** Next-step pointer: "Ready to create your first event" or "Print your first reference card."

### Non-negotiables

- Every step has a "skip" option except the data folder (technical necessity).
- "I don't know" / "skip this question" is always available inside the questionnaire. Missing answers produce broader scenario rules — less precise classification, but still functional.
- Unknown body / unknown lens never blocks progress.
- All choices reversible later via Settings → Wizard re-entry.
- The wizard itself ships localized in v1's shipping languages (En + Pt).
- The wizard produces a JSON profile under `%LOCALAPPDATA%/{AppName}/` that is hand-editable for power users.

### Nice-to-haves

- Detect camera bodies from EXIF of an existing photo folder if the user points at one.
- "Sample my photos" mode: user points the wizard at a folder of their existing photos; the wizard infers some EXIF-pattern answers and asks the user to confirm/refine.
- Re-entry from Settings to *add* a new genre without re-walking the existing genres.

### Open questions for Phase 1

- Do we ship a small set of editable built-in scenario templates per genre that the wizard can copy from, or build scenarios fresh every time from user answers?
- How many genres can the wizard handle without becoming a slog? (Probably soft-cap at 5–6 selected in one pass; rest added later via re-entry.)
- Does the wizard write camera-side guidance ("for this scenario, set your G9 to: M mode, f/8, ISO 200, AF-S, Photo Style Standard") — or does it only build classification rules?

---

## J2 — Adding a new camera, lens, or accessory

**Goal:** The user bought new gear. Tell the system about it. Minutes, not hours.

**Triggers:** User opens Gear page; user imports photos from a body the system doesn't recognize.

**Personas:** All three.

**Key steps:**
1. Brand picker → Model picker (typeahead). Includes traditional ILCs (Panasonic, Sony, Fuji, Canon, Nikon, Olympus/OM, Pentax, Leica) **plus action cams** (GoPro Hero series, DJI Osmo Action, Insta360 — registered as body entries; v1 treats them as just another camera source for the import + bucket pipeline; action-cam-specific features like GoPro highlight detection are deferred to v2). If model not listed, "I have a body that's not here" → free-text.
2. Confirm or override capability profile (sensor size, max ISO, focus modes, custom mode slots, video).
3. **Set the body's current timezone.** Used by the J4 pre-trip checklist to generate the per-camera timezone-set reminder. Default: user's home timezone. Editable any time.
4. For lenses: add to gear; assign to one or more bodies; mark primary use ("wildlife", "macro", "travel").
5. For flash / accessories: less structured; name + notes.
6. Offer to set up scenarios that use the new gear.

**Non-negotiables:**
- Unknown gear works. Profile fills with stubs and "please review" markers.
- The user can edit any field of any profile any time without losing per-photo history.

**Nice-to-haves:**
- Pre-built capability profiles for the top ~50 bodies across the v1 brands.
- Lens registry editable as JSON for power users.

---

## J3 — Configuring (and refining) a scenario

**Goal:** Edit a scenario that was generated by the wizard, or hand-author a new one. The scenario then drives reference-card output and EXIF-pattern classification.

**Triggers:** Edit from the Scenarios page; add new from the Scenarios page; re-enter the wizard.

**Personas:** P1 (deep customization). The wizard handles most of what P2/P3 would do — manual scenario editing is a power-user path.

### What a scenario is

A scenario is an **EXIF-pattern profile + reference-card content**. It is *not* a custom-mode slot mapping. Slot numbers (C1, C2, C3-x) may be recorded as informational metadata but are not the scenario's identity.

### What a scenario contains

- **Identity:** name + genre + free-text description. Optional: custom-mode slot reference (e.g., "C1") for the user's own bookkeeping.
- **EXIF expectations** (the load-bearing part for classification):
  - Aperture range
  - Focal-length range
  - ISO range
  - Focus mode (AF-S / AF-C / MF)
  - AF area mode (if recorded)
  - Drive mode (single / burst-low / burst-high)
  - Photo Style (Standard / Scenery / Portrait / Vivid / custom)
  - White balance (auto / daylight / custom)
  - Image format (RAW / JPEG / RAW+JPEG)
  - Lens (one or more — a scenario can match any of several lenses)
- **Physical setup** (reference-card content): lens, accessories, flash, tripod, filter.
- **Software settings** (reference-card content): the actual camera settings the user wants the camera in. Descriptive — not enforced by the app, since v1 is reference-only on the camera side.
- **Field adjustments** (reference-card content): a short list of "things you tweak in the field" with the dial/button hints.
- **Rationale + tips** (reference-card content): free-text why this scenario, common mistakes, "what to watch out for."

### How a scenario classifies a photo

When a photo is imported, the app reads its EXIF and tries to match each scenario's EXIF expectations. The best match wins (with a confidence score). If no scenario matches strongly, the photo falls into "General." Tie-breakers and edge cases are handled in the Refinement Rules engine that v2_design.md §11 sketches.

**Classification quality scales with user discipline.** A user who always shoots full auto produces low-discriminating EXIF — everything tends to land in "General." A user who shoots in deliberate modes with deliberate Photo Style choices produces high-discriminating EXIF — classification gets accurate.

### Non-negotiables

- Every field is editable. Every field except name and genre is optional.
- A scenario can match multiple bodies and multiple lenses.
- Scenarios stored as JSON in the user data folder. Hand-editable. Never lost on app upgrade.
- Editing a scenario does not retroactively reclassify already-imported photos unless the user explicitly requests it.

### Nice-to-haves

- "Sample my photos" mode: point a scenario-editor at a folder of photos the user considers representative of that scenario; app suggests EXIF expectations from what it sees.
- "Validate against gear" — flag a scenario that references a lens the user doesn't own.
- "Test against existing photos" — show what fraction of an existing photo set this scenario would match (before / after an edit).
- Print/export the scenario as a reference card (PDF + PWA — J8).

---

## J4 — Preparing for an event

**Goal:** An event is coming up — a trip, a single-day session, a planned shoot, anything that produces photos worth organizing. Plan what to bring, what to set up on the cameras, what to remember on departure morning.

**Triggers:** User clicks "New Event" on the Dashboard.

**Personas:** P1 (any scale of event), P2 (often shorter / casual events), P3 (any scale).

**Important:** v1 has **one event type only.** No Trip / Session distinction; fields are optional and the user fills in what's relevant.

**Key steps:**
1. Event name (required). Date range: start required; end optional (defaults to single day).
2. Locations (optional): free text or short list of named places.
3. Itinerary (optional, meaningful only for multi-day events): one row per day with location + notes + timezone if it changes.
4. Scenarios for this event: pick from the user's scenario library. The app suggests scenarios based on the user's typical practice if hints are available.
5. Gear list: built automatically from the selected scenarios. User can add accessories. Last review before packing.
6. **Pre-departure checklist** — auto-generated from the gear list, the trip's first-day timezone, and the user's standing pre-trip habits. Includes:
   - **One timezone-set item per registered camera body** (mandatory): *"Set timezone on Lumix G9 MkI to UTC-6 (Costa Rica)"*, etc. Generated from gear list + trip first-day timezone. Covers all cameras including GoPro / action cams. **Catching this pre-trip avoids the camera-clock-mismatch mess later** (see J5 timezone detection).
   - SD card format / clear.
   - Batteries charged.
   - Copyright info set on cameras.
   - Settings audit (per scenario).
   - User-customizable additional items.
7. **Export Event for Transfer — metadata bundle** (optional, for users with a separate field notebook). At the end of event preparation, the user clicks "Export Event for Transfer", picks an SSD / USB / network folder as the destination, and the app writes a metadata-only bundle (event JSON + scenarios used + gear list + checklist + settings overrides — **no photos yet**, no journals). On the field notebook the user runs "Import Event from Transfer" pointed at the same SSD, and the event is now ready for daily import. Same install on both machines; the transfer feature is opt-in and invisible to single-PC users.

**Non-negotiables:**
- No event-type picker. Single type, optional fields.
- No participants. Photos from non-camera sources get an "other sources" tag at import (see J5).
- Skipping is allowed at every step. Power user can create an event in 30 seconds with only a name + start date.
- Generated outputs (gear list, checklist, scenario reference cards) are exportable as PDFs.

**Nice-to-haves:**
- Save the event plan as a template ("Costa Rica wildlife trip 2026" → reuse for 2027).
- Generate the printable PWA with the chosen scenarios.
- Pre-populate scenarios from a destination database ("Pantanal — wildlife/birds + drone + low light").

### Editing the trip plan after creation (during the trip)

Real trips drift — days get added, places change, weather forces re-routing. v1 supports editing the trip plan throughout the trip's life, with rules tied to whether a day has photos yet.

**Editing rules:**

| Day state | What's editable | Filesystem impact |
|---|---|---|
| Future / unstarted (no `<day>/` folder yet) | Name, date, location, description, timezone, scenarios | None |
| Active (folder exists, no photos imported) | Same as above | Folder rename if name changes |
| With photos imported | Description, location, scenarios. Day name editable; folder rename is a filesystem operation with confirmation. | Day name change → atomic OS rename; journal + event JSON updated |

**Operations on the trip:**
- **Add a day at the end** (extending the trip) — always allowed. End date updates.
- **Fill a gap** (e.g., create Day 5 when Days 1, 2, 4 exist) — allowed. Day numbers stable; gaps fine.
- **Insert a day chronologically before existing days** — NOT in v1. Avoids renumbering existing folders. If genuinely needed, the user renames folders manually and the app picks up the change at next event scan.
- **Remove a day** — only if no photos imported. If photos exist, user moves them elsewhere first.
- **Change trip start date** — allowed pre-trip; disallowed once any day has photos.
- **Change trip end date** — always allowed; extends or contracts (only contracts to remove trailing empty days).

**Where the user does this:**
- **Trip Dashboard page** (`ui/pages/trip_dashboard.py`) shows the trip plan inline. Each day is a row with editable fields + status badges (per `docs/12` Principle 3). Edits happen directly in the row.
- **Context menu** on a day row: "Add day after," "Edit timezone," "Move photos to another day," "Remove (if empty)."
- **Add Day button** at the bottom of the day list.
- All edits write the event JSON atomically. Folder renames are journaled.

**Multi-PC implication:** edits made on the notebook during the trip propagate to the home PC at post-trip Import (the field copy is authoritative; `replace` is the default merge — see `docs/14` data transfer spec).

---

## J5 — Daily field work (on a multi-day trip)

**Goal:** End of day on the field. Ingest the day's SD card, organize, cull, back up. **Under 90 minutes for 500 photos.**

**Triggers:** User opens the app on the notebook (same app as on the desktop — no separate field mode in v1) and selects "Import day N".

**Personas:** P1 mostly. P2 might do this for a weekend trip. P3 less likely on multi-day trips.

**Key steps:**
1. Insert SD card (camera, GoPro, or any registered body) **or** point the app at a folder (phone, friend's camera, shared event photos). The latter gets tagged "other sources" at import.
2. Copy files to the field SSD into a daily folder. Verify checksums.
3. **Timezone-mismatch detection.** App reads EXIF `DateTimeOriginal` from imported files, compares to the expected day's date in the trip plan, computes a per-camera offset if one is detectable. If a consistent N-hour mismatch is found, app prompts:
   > *"Camera **Lumix G9 MkI** appears to be set to **UTC-3** instead of **UTC-6** (3 hours behind). **47 photos** affected by this offset. Apply correction now?"*
   On confirm: `core/exif_rewriter.py` writes corrected `DateTimeOriginal` to each affected photo and writes a `<photo>.timestamp_correction.json` sidecar storing the original value + applied offset (fully reversible). Subsequent photos from the same camera in the same event auto-apply the offset silently. The event JSON gains a `camera_timezone_offsets` entry for audit. **Why write to EXIF, not just the database:** Lightroom Classic reads `DateTimeOriginal` directly. Database-only correction would leave LRC seeing wrong times, breaking downstream develop.
4. Auto-classify each photo into one of the event's scenarios using EXIF heuristics.
5. Detect brackets (focus + exposure) and burst sequences. Group them.
6. User opens the culler. App scans, builds bucket overview (Focus brackets, Exposure brackets, Bursts, Individual photos, Videos; "other sources" buckets when present).
7. User culls bucket by bucket using the **two-pass workflow** described below. Crash-safe journal saves every decision atomically.
8. End of session: app summarizes (X kept of Y total, per bucket and overall).
9. **Backup Event to SSD** — at end of session, the app offers (or auto-triggers) an incremental backup to the configured backup SSD. Full mirror of the event: originals + journals + event JSON + scenario library + settings. Checksum-verified. First run copies everything; subsequent runs are incremental. Restorable if the notebook fails.

**Two-pass culling per bucket** (revised 2026-05-12 to match the author's actual high-volume workflow):

The state model is **three states, not two:**
- `discarded` — default. At save time, photos in this state are dropped. Never explicitly marked.
- `candidate` — explicit first-pass mark. The photo passed the "worth a second look" bar.
- `kept` — explicit second-pass mark. The photo passed the careful-review bar. Implies `candidate`.

Per-bucket flow:
1. **First pass (speed pass):** Browse the bucket fast with arrow keys, focus peaking off (or whatever default), full-screen photo canvas. `Space` marks the current photo as **Candidate**. `X` removes a wrong Candidate. The goal is rapid triage — discard the obviously-out-of-focus or duplicate frames; mark anything that might be a keeper.
2. **Filter toggle:** `C` (or a visible button) switches the view to **Candidates Only**. Photos not marked Candidate are filtered out of the bucket view.
3. **Second pass (quality pass):** With focus peaking on, careful review of the 4–6 candidates only. `Space` promotes Candidate → **Kept**. `K` marks Kept directly (skipping Candidate, for the obvious-winner case). `X` demotes Kept → Candidate; second `X` demotes Candidate → discarded.
4. **Save:** only `kept` photos survive. Candidates that didn't make it to Kept are discarded.

**Keep is sticky; Candidate is sticky.** Clicking the same mark twice is a no-op. Removing requires the explicit `X` / Remove button. The asymmetric-cost reasoning from v2_design.md §24.1 carries forward.

**Why two passes matter (per the author's daily field workflow):** 1000+ photos per field day. Speed is the name of the game. Browsing all 1000 with focus peaking on is too slow. The first pass narrows 1000 → ~100 candidates in fast keyboard-only motion. The second pass with peaking on only revisits the 100, not all 1000. This roughly **halves the time spent on culling** at high volumes.

**Non-negotiables:**
- A crash mid-cull does not lose any decisions. On restart, the app prompts Resume / Discard / Cancel.
- Quit dialogs always confirm before discarding work.
- Auto-classification flags low-confidence guesses for review rather than committing them silently.
- Works offline. Field laptops on trips have unreliable Wi-Fi.
- **Progress visible in every list view.** Returning to a moments / buckets / scenarios list shows per-row "X kept of Y candidates of Z total" status from the journal. (Per `docs/12` Principle 3 — Progress visible everywhere lists exist.)
- **Every keyboard shortcut has a visible button counterpart**, every visible button has a keyboard shortcut. (Per `docs/12` Principle 4.)
- **Predefined widget zones** around the photo canvas (top context, bottom 3-line actions/sliders/hints, left/right collapsible strips). No ad-hoc widget placement. (Per `docs/12` Principle 2.)
- **Speed is king in J5.** Frame rate of the canvas, time between arrow-key press and next-photo render, time between mode toggle and view filter applied — all visible to the user. (Per `docs/12` speed-vs-quality distinction.)

**Non-negotiables:**
- A crash mid-cull does not lose any decisions. On restart, the app prompts Resume / Discard / Cancel.
- Quit dialogs always confirm before discarding work.
- Auto-classification flags low-confidence guesses for review rather than committing them silently.
- Works offline. Field laptops on trips have unreliable Wi-Fi.
- **Progress visible in every list view.** Returning to a moments / buckets / scenarios list shows per-row "X kept of Y" status from the journal. (Per `docs/12` Principle 3 — Progress visible everywhere lists exist.)
- **Every keyboard shortcut has a visible button counterpart**, every visible button has a keyboard shortcut. (Per `docs/12` Principle 4.)
- **Predefined widget zones** around the photo canvas (top context, bottom 3-line actions/sliders/hints, left/right collapsible strips). No ad-hoc widget placement. (Per `docs/12` Principle 2.)

**Nice-to-haves:**
- Multi-source ingest: SD from camera + phone folder in one operation.
- Focus-peaking and sharpness overlays in the culler for stack frames.
- "Pin" or "favorite" tagging during cull for the later curate pass.

---

## J6 — Coming home from a trip

**Goal:** Move the event data from notebook to desktop, finalize culling, hand off to Lightroom for develop, and shape the curated set.

**Triggers:** User opens the app on the desktop after a trip.

**Personas:** P1 mostly. P2 if she took the notebook. P3 typically does this directly on the desktop without field work in between.

**Key steps:**
1. **Export Event for Transfer — results bundle** (on the notebook, post-trip). User clicks "Export Event for Transfer", picks SSD/USB destination, app writes a kept-only bundle: all photos that survived culling organized by `<event>/<day>/<scenario>/<photo>`, plus final event JSON, plus all journals (cull, etc.), plus any new or edited scenarios from the trip. Checksum-verified. *Alternatively:* if a current SSD backup mirror exists from J5's nightly Backup-to-SSD operation, the SSD itself can serve as the transfer medium — no separate Export step needed.
2. **Import Event from Transfer** (on the home desktop). User runs Import pointed at the SSD/USB. Bundle checksums verified before any local state changes. Default conflict behavior: **replace local event with imported** (the field copy is authoritative — it has everything the home copy has plus the trip results). Per `docs/14`'s data-transfer spec, the home copy from before the trip is overwritten cleanly; the user gets one event entry with the full trip data.
3. **Resume any unfinished culling on the desktop.** App detects the imported event has open journals and prompts Resume / Discard / Cancel.
4. **Late-stage timezone-mismatch detection** (catches cases where daily cull was skipped during the trip). On Import and again on entering the curate session, the app re-runs the same per-camera offset detection as in J5 step 3. Any camera offset that wasn't already addressed during the trip gets surfaced here with the same confirm-and-correct flow. Late corrections still write to EXIF via `core/exif_rewriter.py` with reversible sidecars; the event JSON's `camera_timezone_offsets` field reflects when correction was applied.
4. Process stacks: hand brackets and focus stacks off to Helicon Focus (external tool). Track which stacks are processed.
5. Trim videos (optional).
6. Hand off the keepers to Lightroom Classic: app generates a Lightroom-importable folder structure (or a smart-collection definition). User runs the import inside LRC. App tracks "sent to LRC" state.
7. After the user finishes LRC develop, point the app at the developed-export folder so the curate pass has the finished images.
8. Curate: narrative pass. Pick the slideshow-worthy subset. Tag by curate level (silver, gold). Map to output folders by genre.
9. Archive the event: mark as ARCHIVED, lock for read-only, finalize NAS backup.

**Non-negotiables:**
- LRC integration is loose-coupling: the app produces folder structures LRC understands and consumes folder structures LRC has produced. No in-app LRC catalog manipulation in v1.
- The user's existing folder layout is preserved.
- ARCHIVED status is reversible (with a warning) for re-curate or re-develop.

**Nice-to-haves:**
- Track "this stack has been processed in Helicon" via a sidecar marker.
- Smart-collection export for Lightroom (LRC-compatible smart-collection XMP).
- Distribution stage: export curated set in form PTE wants, in form Google Photos wants, in form WhatsApp wants.

---

## J7 — Building a slideshow

**Goal:** Take a curated set of images (and optionally videos), produce a PTE-importable bundle, and remember it as a distribution action on the event.

**Triggers:** User selects "Build slideshow" on an event at status CURATED or later.

**Personas:** All three; complexity scales with persona.

**Key steps:**
1. Pick the curated set (full event, by curate level, by genre subset).
2. Optionally pull in audio from the user's audio library.
3. Generate the photo folder PTE expects, in the right resolution, with the right file types.
4. Generate a PTE project skeleton (.pte) the user opens in PTE AV Studio.
5. After PTE rendering, point the app at the output MP4(s); record as DistributionAction on the event.

**Non-negotiables:**
- In-app slideshow rendering is **out of scope for v1**. The app produces the inputs PTE needs.
- The user's render naming conventions (`{name} PC.mp4`, `{name} TV.mp4`, `{name} Mobile.mp4`) are preserved.

**Nice-to-haves:**
- Audio library: tagged, organized music files the user can drag into a slideshow plan.
- Soundtrack builder: a sequencer for matching photo groups to musical sections.
- Template PTE projects per slideshow style (trip recap, portfolio, family-friendly).

---

## J8 — Generating a per-scenario reference card

**Goal:** Produce a per-scenario printable + installable mobile reference, derived from the user's scenario configuration.

**Triggers:** From the Scenario page; from Event preparation; standalone export from the Gear page.

**Personas:** P1 (prints cards, tapes them inside the camera bag), P3 (definitely prints — wants the physical), P2 (uses the PWA on her phone).

**Key steps:**
1. Pick scenario(s) and a target body.
2. Choose output: PDF (one card per scenario, double-sided), PWA bundle (installable on iOS/Android home screen), or both.
3. Generate. Output filename and location are configurable.

**Non-negotiables:**
- The reference-card UX (front: physical setup; back: software settings; hints; "why this scenario") carries forward from the LumixCameraSettingsProject design.
- PDFs work without any external font dependency that isn't bundled. (The Lumix prototype's `C:\Windows\Fonts\segoeui.ttf` hardcoding is the failure mode we avoid here.)

**Nice-to-haves:**
- One PWA per event (the event's selected scenarios) rather than one per scenario.
- QR code printed on the PDF that opens the PWA.

---

## J9 — Recovery from disaster (sketch only for v1)

**Goal:** A crash happened. A drive died. A file got corrupted. Get back to a known-good state.

**Triggers:** App restart after crash; user-initiated "verify event" check.

**Key elements:**
- Crash-safe journals already covered in J5/J6 (per stage).
- Settings file recovery (XdTd pattern) — corrupt settings.json does not crash the app.
- Event-store atomic writes — no half-written event JSON.
- Verify event: walk the event's data, flag missing files, broken references.

**v1 status:** Mechanism mandatory. UI may be minimal (a "verify event" button, a "settings recovered from backup" notice on startup).

---

## J10 — Migrating from PhotosWorkflow (sketch only)

**Goal:** The author of this project has events created in PhotosWorkflow v1.x. Bring them into the new app without re-doing work.

**Triggers:** First-run, or "Import legacy event" menu item.

**v1 status:** Useful for the author and for any other PhotosWorkflow user. Probably optional for v1 — depends on how much PhotosWorkflow data the author wants to bring forward.

---

## J11 — Sharing via channels other than slideshow (sketch only)

**Goal:** Export a curated set sized and formatted for Google Photos, Instagram, WhatsApp, prints, the user's website.

**Triggers:** After CURATED status, from the event's distribution panel.

**v1 status:** Recorded as DistributionAction metadata on the event (timestamp, channel, item count, share URL, notes). Per-channel export presets are a v2 expansion — v1 ships with at most 2 presets (TV slideshow + Google Photos).

---

## What these journeys tell us

- **J1, J3, J5, J6, J8 are the load-bearing journeys.** They must work flawlessly. Everything else flows from them.
- **The wizard (J1) is the highest-stakes UI in the app.** Time invested in getting it right pays off in every other journey. It is also the hardest UI to design well, because the questions must be EXIF-grounded (technically correct) AND non-intimidating (P3-friendly even though P3 is not v1-QA'd).
- **The event lifecycle is real.** Status milestones drive UI affordances. The PhotosWorkflow 8-status model (`PLANNED → PREPARED → LAUNCHED → WRAPPED → PROCESSED → ENHANCED → CURATED → ARCHIVED`) survives this requirements pass at first read but is up for review during Phase 3.
- **No separate field mode in v1.** The same app runs on desktop and notebook. The "Export event for transfer / Import event from transfer" pair handles the data-movement use case without splitting the UI.
- **Reference cards (J8) are an underrated differentiator.** No other tool produces "tape this inside your camera bag" output that's actually derived from the user's own scenario definitions. Carry this concept forward strongly.
- **Crash-safety must be mechanism-level, not policy-level.** Every long-running session writes a journal. No exceptions.
- **The app integrates with LRC at the folder level, not the catalog level.** Less powerful, far more robust, no Adobe API churn.
- **Slideshow rendering stays external.** Reduces v1 scope by months.
- **Classification accuracy depends on user discipline.** This is a feature, not a bug. The UI should be honest about it: "if you shoot everything in auto, classification will be coarse. The more deliberately you configure the camera per genre, the more detailed your library becomes."
