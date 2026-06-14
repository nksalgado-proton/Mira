# 01 — Target User Personas

> **Status: Phase 1.** Three personas span the v1 audience. The architecture is brand-agnostic and the wizard is designed to work for any modern ILC, so all three personas are v1 users from day one. Author's QA reach (Lumix G9 family + Sony A6700) is a *testing* boundary, not an *audience* boundary.

The product targets the *serious amateur* middle. To make that concrete, we name three personas. Every requirement, every UI decision, every default should be checkable against these three. If a feature only helps one persona at the expense of another, that's a decision to make consciously, not by accident.

**Important framing post-Phase 0:**
- **All three personas are v1 users.** The wizard-driven, EXIF-pattern classification model works for any brand. The reference-card UX works for any brand.
- v1 ships shallowly QA'd on non-Lumix-G9 bodies — bugs may surface and earn fixes in patch releases.
- v1 ships En + Pt; **Spanish is the v1.1 priority** specifically because it unlocks the Latin American audience overlap with P2 and P3.
- Brand-neutral phrasing in the wizard ("your camera's color preset" or brand-detected terminology) is a v1 requirement, not a v2 feature.

---

## P1 — The Specialist (the project's own author archetype)

**Name:** Nelson — closely modeled on the project's own author, but generalized to other specialists in similar shoes.

**Demographics:** Late-40s to 60s. Engineer or technical-professional background. Disposable income to invest in gear. Time on weekends and during planned trips. Computer-literate; comfortable editing JSON if needed, but prefers good UI.

**Gear:** Multiple mirrorless bodies across one or two brands. 4–8 lenses, including at least one specialist optic (macro, super-telephoto, fast prime). Flash gear for macro or off-camera lighting. Tripods, filters, sometimes lightning triggers. A NAS at home, a portable SSD for field work, a laptop or tablet for daily ingest on trips.

**Photographic focus:** Deep in 1–2 genres (typically wildlife/birds + macro, or landscape + long-exposure, or wildlife + travel). Comfortable with manual exposure, knows what each scenario demands. Owns the gear that fits the focus.

**Software environment:** Lightroom Classic for develop. Helicon Focus or similar for focus stacks. PTE AV Studio or Premiere/Resolve for slideshow assembly. Adobe subscription tolerated (not loved). Synology DSM for backup. Comfortable with the Windows desktop.

**Workflow today:** Plans trips weeks in advance. Configures camera custom modes before leaving. Imports daily on the field laptop. Culls in a custom workflow that's part-automated, part-manual. Returns home with thousands of images and a backlog of develop work. Produces 2–4 family-facing slideshows per year and 1–2 portfolio pieces.

**Pains:**
- Configuring custom modes on a camera body is fiddly and easy to forget between trips.
- Pre-trip preparation is high-stakes: forgetting one scenario or one accessory means missing shots that can't be retaken.
- Day-of-trip ingest, sorting by scenario, and same-day culling is exhausting after a full day of shooting.
- Post-trip workflow (cull → process focus stacks → develop in LRC → curate → slideshow) takes weeks. Each step has its own context-switch cost.
- Multi-body workflows: when two bodies see different scenarios, sorting after import is manual.
- Sharing with non-photographer family requires another full round of trimming, downsizing, and curation.

**Non-negotiables:**
- Crash-safe culling. Losing a half-day of cull decisions because the app crashed is unacceptable.
- The user's existing folder layout (`D:\Photos\trips\{YEAR} - {Event}\originals\`, etc.) is sacred. The app conforms to the user, not the other way around.
- Multi-brand support that actually works on Panasonic and Sony files, not just Panasonic.
- ExifTool-driven metadata; no proprietary photo database that locks the user in.

**Nice-to-haves:**
- Auto-classification of photos into scenarios (macro vs. wildlife vs. landscape) by EXIF heuristics.
- Bracket and focus-stack detection without manual marking.
- Pre-trip checklist generation tied to the chosen scenarios.
- Per-scenario reference card (printable, installable on phone).

**Watch out for:**
- This persona is the most demanding and the most knowledgeable. Building only for this persona produces a tool too sharp for the other two.

---

## P2 — The Enthusiast Newcomer

**Name:** Marcia — a hypothetical hobbyist 3 years into her serious photography journey.

**Demographics:** 30s–50s. White-collar professional, photography is a real hobby but not the center of her life. Less technical than P1 — comfortable with apps, not with editing config files.

**Gear:** One mirrorless or DSLR body (typically Sony, Fuji, Canon, or Nikon). 2–4 lenses. A kit zoom, a fast prime, a telephoto. A tripod. No flash yet. No NAS — uses an external USB drive and maybe Backblaze or iCloud Photos for backup. A laptop, not a desktop.

**Photographic focus:** Travel + family + occasional nature. Hasn't yet specialized. Curious about macro, intimidated by night sky, vaguely aspires to wildlife but doesn't own the lens.

**Software environment:** Lightroom Classic *or* Lightroom CC (subscription). Sometimes Capture One trial. Apple Photos for family snapshots. Mac household, but might keep a Windows laptop for photo work.

**Workflow today:** Imports after each event (not daily during a trip — events are weekends, not 2-week expeditions). Culls in Lightroom directly, occasionally in PhotoMechanic if it's a big event. Shares to family Google Photos albums. Wants to make a slideshow but PTE looks intimidating; uses Apple Photos slideshow feature instead.

**Pains:**
- Custom camera modes are mysterious. Manuals are 400 pages. YouTube tutorials are scattered and inconsistent.
- Doesn't know what scenarios *should* be set up for her gear. Wants guidance, not just configurability.
- Culling 800 photos from a 3-day weekend is exhausting. Doesn't have a system.
- Post-trip slideshow takes forever and the result is mediocre.
- Feels imposter syndrome — "everyone else seems to have a workflow."

**Non-negotiables:**
- It needs to work without reading documentation. First-run wizard must be friendly.
- It must not feel like enterprise software. No 6-step setup, no jargon.
- Free or very cheap. She's already paying Adobe.
- Works on her actual brand of camera (likely not Panasonic).

**Nice-to-haves:**
- Suggested scenarios for her gear ("you have a 70-200 f/2.8 and a 50 f/1.8 — here are the 3 scenarios people with this kit typically configure").
- Templates for common trip types (city break, beach week, road trip).
- One-click "make me a slideshow my family will watch."

**Watch out for:**
- This persona is **why v1 can't be Panasonic-only**. If we don't cover the brand she shoots, the entire mid-market is excluded.
- This persona is **why we need an opinionated default workflow**, not just configurability.
- This persona is **why English-only might be wrong**. She might be in São Paulo, Mexico City, Berlin, Tokyo.

---

## P3 — The Returning Veteran

**Name:** José — a hypothetical retiree returning to photography after a 20-year gap.

**Demographics:** 60s–70s. Retired or near-retired. Time-rich, energy-fine, focus-strong. Used to shoot film seriously in the 80s and 90s; sold or shelved his gear when kids arrived; has just bought modern mirrorless and is rediscovering the craft.

**Gear:** Recently purchased premium mirrorless body (often Fuji or Sony, sometimes Panasonic). 2–3 lenses — a 35mm equivalent, a portrait/short tele, maybe a 70-200. Considering buying a macro or telephoto. Owns an iPad and a Windows desktop. Has a NAS (recommended by a son or grandson). Considers a Synology because his nephew has one.

**Photographic focus:** Family events, occasional travel, slowly drifting toward one specialty. Cares about prints — orders large prints from a local lab. Cares about archival — wants his grandchildren to have the photos.

**Software environment:** Lightroom Classic (perpetual, ideally — resistant to subscriptions). Sometimes Capture One. Likes Affinity Photo. Refuses to use the cloud unless he really has to.

**Workflow today:** Imports per event. Develops in Lightroom. Prints monthly. Shares via WhatsApp to family. Doesn't make slideshows yet but would love to.

**Pains:**
- Modern camera UI is overwhelming. Modes, custom modes, function buttons, Quick Menu — all foreign vs. his old Nikon F3.
- Wants to set up his camera "the way a pro would" but doesn't know what that means anymore.
- Doesn't want to learn a new file-management system; wants something that fits how he already thinks (chronological + thematic).
- Backup terror — has lost photos before.

**Non-negotiables:**
- The tool must not require a constant internet connection.
- It must produce *physical* outputs that survive — JPG/TIFF, not lock-in formats.
- Big text, big buttons. Older eyes. Light theme by default.
- Reliable. Crashes break trust permanently.

**Nice-to-haves:**
- Reference cards he can print and tape inside his camera bag.
- "Setup my camera like a [wildlife / portrait / street] photographer" templates.
- Local archival workflow that fits onto his NAS without cloud.

**Watch out for:**
- This persona is **why accessibility matters from day one**. Font scaling, contrast, button sizes.
- This persona is **why offline-first is mandatory**. No cloud lock-in.
- This persona overlaps with P1 in gear quality and photographic ambition, but with much lower technical comfort.

---

## What these personas tell us (requirements implications, post-Phase 0)

- **Brand-agnostic architecture is non-negotiable, even though v1 release is G9-only.** P1-on-G9 alone is too narrow to justify the engineering investment. The architecture has to serve P2 and P3 *eventually*, which is what makes the classification model EXIF-pattern-based rather than slot-based or brand-keyed. Phase 0 explicitly resolved this with the wizard-driven, EXIF-grounded approach.
- **Opinionated defaults plus customizability.** P2 needs the app to make decisions for her. P1 needs the app to get out of his way once configured. Both must coexist. Solution shape: the wizard does both — it asks "how do you shoot wildlife?" with short multiple-choice and "skip" options, then generates editable scenario JSON that the power user can refine forever.
- **First-run experience is critical, AND it doubles as the classification configuration.** Phase 0 made this the centerpiece of v1: the wizard is the mechanism by which user knowledge becomes machine rules. Time invested here pays off in every other journey.
- **The user's existing folder layout matters.** P1 has 30 years of archive. P3 has 40 years. The app conforms to their layout; it does not impose a proprietary catalog. v1 starts fresh on event creation (no migration), but it respects the user's photos-base-path.
- **Lightroom Classic is the lingua franca.** All three personas use it or are willing to. The app integrates at the folder level (not the catalog level), which is robust to LRC version churn and to Adobe API changes.
- **Slideshow is universally wanted but universally hard.** All three want one; P1 has a process; P2 fakes it; P3 has never made one. PTE assembly is external in v1, but the app produces the curated photo set in a form PTE accepts.
- **Crash-safety is universal.** P1 will lose trust if a half-day of culling vanishes. P3 will *quit* if the app crashes once during a backup workflow. Mechanism-level crash safety (journals on every long-running session) is mandatory.
- **English-only is overridden.** Phase 0 decided multi-language from day one (v1 ships En + Pt; Spanish in v1.1). The "English-only, firm" principle from v2_design.md does not carry forward. i18n infrastructure is in v1.
- **Classification quality scales with user discipline.** P1 (disciplined manual / aperture-priority shooter) will get rich classification. P2 who shoots in iAuto + portrait mode will get coarser classification. P3 who shoots in P-mode mostly will be in between. The wizard and UI should be honest about this without preaching.
- **The reference card (J8) is the persona-bridging feature.** P1 prints them and tapes them in the camera bag. P2 installs the PWA on her phone. P3 prints them in big text and tucks them inside the camera bag. Same artifact, three different uses, one design.

---

## Personas we are NOT serving in v1

Documented here so they don't sneak in:

- **Working professionals.** Lightroom + Capture One + PhotoMechanic + custom pipelines already serve them. The product would be a downgrade.
- **Casual phone snappers.** Apple Photos, Google Photos, and the iOS/Android camera apps serve them perfectly.
- **Content creators / influencers.** Workflow centers on social-first export and motion. Different tool category.
- **Wedding / event photographers.** Volume + client deliverables + watermarking + galleries. Different tool category.
- **Print-only fine-art photographers.** Need color-managed soft proofing the product won't have.
- **Cinematographers / hybrid stills+video pros.** Video is conditional in v1, not core.
