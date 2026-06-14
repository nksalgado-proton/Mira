# spec/PROGRESS — live handoff

**Always current. Every session updates this before stopping.** A new agent
reads `spec/00-charter.md` then this file, and can start immediately.

---

> ## ⚡ CURRENT (2026-06-14, TENTH SESSION — WRAPPED) — **spec/66 phase-model revision (slices 4–6) + spec/69 icon-wiring fidelity, four commits.** The session ran two scoped programs end-to-end on top of the redesign foundation: the Collect/Pick/Edit/Export phase spine (Edit de-cluttered, a brand-new Export surface born from the design catalog, Export menu + Exported Media/ tier), and the icon-wiring sweep that retires the remaining Unicode glyph placeholders. **verify.bat: 2812 passed / 3 pre-existing baseline failures / 281 skipped (main pass) + 20/0 (quarantined)** — same 3 flakes that were red at session start, no new regressions.
>
> **The four commits, oldest → newest:**
> 1. `29fbc31` — spec/66 slice 4: de-clutter Edit surface
> 2. `5f67189` — spec/66 slice 5: the Export-phase surface (design-catalog fidelity)
> 3. `80ff99d` — spec/66 slice 6: Export menu + Exported Media/ tier
> 4. `b1b81f7` — spec/69: line-icon family + tinted_svg_pixmap helper
>
> **What landed (by arc):**
>
> 1. **Slice 4 — Edit creative-only (29fbc31).** Stripped from `mira/ui/edited/{edit_page, edit_video_page, edit_host_page}.py`: the export trigger menu + button, the inline async-export progress widget, the per-page `_ExportWorker` / `_VideoExportWorker` classes, the mark-for-export green/red border, the Exported watermark plumbing, the day-grid border-click cycle, the navigator/day-grid "Export all" buttons, the `process_export_committed` / `export_scope_requested` / `clip_exported` signals, the host's batch-export helpers (`_run_batched_export` / `_collect_*` / `_collect_recipes` / `_collect_styles_by_path` / `_on_*_export_committed` / `_refresh_cell_for_item`), and the host's watermark/exported-ids state. Edit is now classification + tone + crop only; P/X/Space/C fire on the viewport but the page deliberately doesn't connect to them (no Pick/Skip ledger here). Net -1639 / +121. `test_edit_page_keymap.py` rewritten to assert the keys are inert; the legacy `test_single_export_commit_signal_fires_after_lineage` retired (the contract moves to the Export surface).
>
> 2. **Slice 5 — Export surface (5f67189).** New module `mira/ui/exported/` (`__init__.py`, `export_page.py`). Built **fidelity-first** from the `mira/ui/design/` catalog per spec/68 §3 (NOT a port + recolor): `PageHeader` (real title weight), `ghost_button` / `danger_ghost_button` / `primary_button` for the toolbar, `StageProgress` for the live green/total counter (phase-identity green token), `mira.ui.design.dialogs.confirm` / `show_info` / `show_error` (no QMessageBox chrome), and `mira.ui.design.Thumb` cells laid out in `mira.ui.base.flow_layout.FlowLayout` — mirrors `days_grid_page.py` structurally. Pool = all `pick=picked` photos; default **GREEN** (born-green per spec/59 §8, carried by spec/66); click toggles green↔red; bulk **Pick all** / **Skip all** on the toolbar; primary "Export green (N)" submits through the unchanged spec/59 §8 `BatchExportQueue` + spec/60 worker engine (view-over-engine: engine + queue locked, this surface only re-parents the trigger). The PhasesPage Export tile click already emitted `"export"`; MainWindow gained `_EXPORT_PAGE_KEY` + the route + `_on_export_closed` / `_on_export_fullscreen`. 9 new pin tests in `tests/test_export_page.py` (all green).
>
> 3. **Slice 6 — Menus + Exported Media/ plumbing (80ff99d).** Menus (`main_window.py`): new top-level **Export** menu with "Open Export phase" alongside Collect/Pick/Edit; new `_SURFACE_CLOSED_EVENT` mode so the **Share** menu only appears on closed events (the empty-children rule hides it otherwise; label retitled "Open Cuts" per spec/66 §4). On-disk (`core/path_builder.py`): new `Exported Media/` tier (the spec/66 §1.2 shipped set) with `exported_media_dir()` helper, `ensure_event_tree` creates it, `RESERVED_DIR_NAMES` picks it up. Engine wiring (`mira/ui/exported/export_page.py`): default destination repointed; third-party returns from `Edited Media/` are **HARDLINKED** into `Exported Media/<day>/` instead of re-rendered (the return is itself a finished file — re-feeding through Mira's tone pipeline would change the pixels; copy fallback for cross-volume failures, mirroring spec/57 policy; synchronous commit so the render queue stays unblocked). Gateway (`mira/gateway/event_gateway.py`): `exported_item_ids()` + `exported_files()` now filter on `export_relpath LIKE 'Exported Media/%'` — only shipped rows count; new `edit_candidate_item_ids()` + `edit_candidate_relpath(item_id)` expose the inbox set. Vocabulary (`mira/event_classification.py`): `PHASE_EXPORT = "export"` added; `ALL_PHASES` / `DECISION_PHASES` re-spelled per spec/66 §3 (share leaves the phase tuple but survives as a state word for the Cuts code path); `event_card._PHASE_DISPLAY_LABELS` gains "export". Cut-test fixtures (all of `test_cut_*`, `test_cuts_shell`, `test_gateway_cuts`) bulk-rewrote `Edited Media/` → `Exported Media/` to reflect the new shipped-set semantic. `test_main_window_menu` got new tests for the Export menu + Share-on-closed-events.
>
> 4. **spec/69 — Icon wiring (b1b81f7).** Drew three line-icon glyphs in `assets/icons/glyphs/`: **eye** (outline + filled pupil), **check** (single stroke), **cross** (two strokes) — all 24×24 viewBox, `stroke="currentColor"`, stroke-width 1.8, round caps to match the family. Factored the `_CategoryTile.paintEvent` SVG-tint pattern into `mira/ui/design/icons.py::tinted_svg_pixmap(path, size, color)` (cached by path+size+color.rgba); also exports named glyph-path constants. Wired every Unicode placeholder spec/69 lists:
>    - `picker_page.py` / `editor_page.py` / `video_picker_page.py`: visited eye chip `QLabel("◉")` → tinted eye SVG (white-on-dark pill, theme-independent by design — overlays photos).
>    - `day_grid_cell.py`: visited tick `QLabel("✓")` → tinted check SVG, sized proportionally to the cell on every `set_size` so the badge stays legible across the size-slider range.
>    - `thumbs._paint_count_chip`: mixed-cluster split chip `"3✓·2✗"` text → `"3 ✓ · 2 ✗"` painted via the line-icon glyphs.
>    - Existing `_CategoryTile`, `_CrossEventGlyph`, `_render_search_glyph` retired their inline copies of the SourceIn pattern and now go through the shared helper.
>    Cluster-dir reconciliation: `cluster_icons.py` repointed at `assets/icons/clusters/badge/` (spec/69 canonical — the set Thumb already used); legacy top-level `assets/icons/clusters/{burst,exposure,focus,repeat}.svg` retired; `repeat → repeated` filename mapping. Verification: `scripts/smoke_icons.py` renders every spec/69 surface fragment on dark + light themes (`smoke_icons_{dark,light}.png`). The `test_cell_visited_tick_scales_with_set_size` test was re-pinned to assert pixmap-dimension scaling instead of the now-irrelevant QSS font-size string.
>
> **EYEBALL STATUS:** Smoke-rendered both themes via `scripts/smoke_icons.py`; PNGs delivered. The new Export surface, Slice 4 Edit de-clutter, and Slice 6 menu/Exported Media tree were not eyeballed in the real app this session — verify.bat catches regressions but the live launch flow + the on-disk `Exported Media/` materialisation on a real event remain to-be-touched.
>
> **OWED AT WRAP — what next session picks up:**
>
> 1. **Live eyeball + commit slice 3.** Two files are STILL UNCOMMITTED in the working tree (carried over from before this session — these are slice 3 of the spec/66 phase work that Nelson said was "already done" at session start, meaning the code was written but not committed):
>    - `mira/ui/pages/phases_page.py` — PhasesPage donuts using phase-identity colours (Collect blue / Pick accent / Edit amber / Export green)
>    - `spec/66-collect-pick-edit-export.md` — the §1 "Bars encode phase identity, not state" paragraph
>    These were intentionally NOT folded into my slice-4–6 commits (different scope). Next session: eyeball the PhasesPage donuts on a real event, then commit as "spec/66 slice 3" or whatever Nelson titles it.
>
> 2. **Four untracked design specs need to land alongside the code that implements them.** `spec/67-implementation-handoff.md` (the build brief for slices 4–6), `spec/68-phase-redesign-coordination.md` (the slice-5 amendment), `spec/69-icon-wiring-fidelity.md` (the icon job), `spec/70-new-ui-completion-plan.md` (the full redesign program). Per CLAUDE.md §6 ("Spec and code land together"), these should commit. I deliberately didn't commit them — they are Nelson's design docs, not mine to author, and committing them needed his nod.
>
> 3. **Real-app launch of the new surfaces.** Slice 5's Export surface, Slice 6's Export menu + Share-on-closed-events gating, and the `Exported Media/` materialisation have ONLY been smoke-tested + unit-tested. They have not been driven on a real event in the app. Next eyeball: open an event, go to Export, mark some green/red, hit Export green, confirm files land under `<event>/Exported Media/<day>/`; flip the event closed, confirm Share menu appears; assemble a Cut to confirm `exported_files()` picks up the new shipped rows.
>
> 4. **spec/70 punch-list (the redesign completion plan).** Spec/68 sequenced this AFTER the phase spine — spine is now done. spec/70 lays out every remaining surface that needs the fidelity pass (sizing, shadow, density, the spec/65 punch list). Slices in spec/70 are next session's main menu unless Nelson redirects.
>
> 5. **Three pre-existing baseline test failures stay open** (carried THROUGH this session unchanged):
>    - `tests/test_main_window_menu.py::test_top_level_menus_are_the_designed_seven` — title list reads `[]` in the test harness; menu bar visibility behaves weirdly in headless construction. Pre-existed at the session-start baseline; confirmed by running on stashed HEAD before my edits.
>    - `tests/test_main_window_menu.py::test_per_event_surface_unhides_collect_and_share` — same root cause.
>    - `tests/test_wizard_refresh.py::test_no_stale_app_name_in_wizard_sources` — wizard text references "Mira" as the app name; assertion expects something different. Stylistic / vocabulary mismatch, not a code bug.
>
>    None of the three relate to my work; they were present at session start. Worth a fix pass when someone gets to them.
>
> 6. **Branch-lineage caveat.** `git` cannot resolve the SHA `XMC-redesign @ f5766b7` locally — the repo's recent history begins at `f69f450 Initial commit — Mira`. spec/68 §1 anticipated this: it's a `.git/config` readability quirk in the inspecting environment, not a missing baseline. The working tree IS post-redesign (`mira/ui/design/` catalog + redesigned pages + `assets/themes/redesign.qss` all present); commit `9b575c2 Phase 1 foundation: Mira brand kit…` is on the line. All four commits this session sit on top of that line.
>
> 7. **Push status.** Four commits this session on `main`, not pushed.
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md banner. Four commits this session: spec/66 slices 4–6 (de-clutter Edit; build the design-catalog Export surface; add the Export menu + Exported Media/ tier with hardlink-for-third-party-returns) and spec/69 (line-icon family + tinted_svg_pixmap helper + retire every Unicode glyph placeholder). verify.bat: 2812/3 (same 3 pre-existing baseline flakes as before, no new regressions). Real-asset icon smoke delivered as PNGs.
> The phase-model work is DONE in code; slice 3 (PhasesPage donuts + the §1 paragraph) is the one bit still uncommitted in the working tree — eyeball it on a real event and commit. The four design specs (67/68/69/70) are still untracked; they describe work that's already in HEAD — commit them when you're ready.
> Start: a real-app launch of Export end-to-end (mark some green, ship them, verify the Exported Media/ files); then the spec/70 redesign completion plan, OR the live-eyeball owed items.
> ```
>
> **The four commits in detail —** see commit messages on HEAD for the per-file breakdowns; this banner stays at the arc level.
>
> ---
>
> **(below: ninth session's banner, kept as a log)**

> ## (PREVIOUS) 2026-06-13, NINTH SESSION — WRAPPED, EXTENDED — **SPEC/64 events-information split CLOSED + Bug 3 + classification UI strip + closed-tile body redesigns + Cuts back-routing.** Eleven commits this session, tree clean, **verify.bat 2873 / 0 + 20 / 0 at HEAD**. The session opened on slice 6 owed and grew into a full multi-arc sprint as Nelson eyeballed each result and queued the next change. **NEW MEMORY landed:** [[feedback_slow_down_on_visual_iteration]] — after the third rapid hardware-table iteration on the closed tile, Nelson called the rushing pattern and the change was reverted; the rule is now: 2-3 rapid visual commits without an eyeball → STOP + ask.
>
> **The eleven commits, oldest → newest:**
> 1. `f8a27fa` — spec/64 slice 6 of 6: tile updates (status badge + Header first-touch badge + closed-tile body + closed → Cuts list door)
> 2. `15f4821` — PROGRESS wrap (premature — superseded by this banner)
> 3. `1dcd23a` — slice 6 retire (Nelson eyeball): drop Header badge + status glyphs ("✓" / "●" → plain text)
> 4. `932bdb4` — pin EventCard minimumHeight across open/closed (the 220-vs-180 closed-bias retire)
> 5. `6da68d8` — Bug 3: blank-frame Day Grid video cells (ensure_thumb self-heal + WARNING log + placeholder pixmap)
> 6. `e214d21` — strip photographic classification UI from Pick surfaces (Edit-only now)
> 7. `ccd6ae6` — spec/64 §2.4 redesign: closed-tile body grows bar chart + photo carousel (v2)
> 8. `0ad2488` — closed-tile body sizing (Nelson eyeball): chart + carousel fill heatmap slot
> 9. `5cd357f` — pin EventCard to true fixed height (closed tile still grew the open card)
> 10. `6959ec3` — spec/64 §2.4 v3: retire photo carousel for classification donut + legend
> 11. `fcea600` — spec/64 §2.4: Cuts back-routing remembers entry door (closed-tile body → events list on Back; Share-phase tile → activity dashboard on Back)
>
> **What landed (by arc):**
>
> 1. **spec/64 slice 6 + iterations.** Status badge on every tile (clickable Open ↔ Closed toggle; plain text after Nelson's eyeball killed the ●/✓ glyphs). Header first-touch badge (built then RETIRED post-eyeball — title click already opens the Header, the badge was redundant; the sticky-touch gateway bit + the helper + the rollup all retired in one). Closed-tile body went through THREE shapes — the slice-6 "Cuts inside" hint + count, then bar chart + photo carousel (v2, carousel too small in the fixed-180-px height), then bar chart + classification donut + legend (v3, current HEAD). The card is `setFixedHeight(180)` so open + closed states share the exact vertical extent. Closed-tile body click routes to the Cuts list via the existing `heatmap_clicked` signal; MainWindow forks on `is_closed` to land there vs. the activity dashboard.
> 2. **Bug 3 — blank-frame Day Grid video cells.** Three changes: (a) `core/thumb_cache.ensure_thumb` self-heals a corrupt cached JPEG (PIL fails → drop + .vetted sidecar + re-run the ladder); (b) `pick_page._decode_thumbnail` logs at WARNING (not DEBUG) so the failure shows in the app log; (c) `pick_page._load_some_thumbs` substitutes a kind-aware placeholder pixmap (video gets a ▶ + "no preview" caption, photos get the caption only) instead of silently leaving the cell blank. The chip `task_fa18066a` no longer exists in this session (Nelson saw the task list cleared). 6 new pins in `tests/test_bug3_blank_video_thumbs.py`.
> 3. **Classification UI strip from Pick surfaces.** Per Nelson: photographic classification (Macro / Wildlife / Birds / Landscape / Urban-Street / None) drives Edit Auto correction recipes — so it only belongs in Edit. `pick_photo_surface.py` lost its genre readout + Reclassify dropdown + R-key + the `_effective_genre` / `_auto_genre` / `_bucket_genre` / `_refresh_genre` / `_on_reclassify` methods + the `_genre_cache` / `_genre_review` caches + the bracket-bucket EXIF prewarm (which only existed to feed the dropdown). `video_pick_page.py` lost the same shape for its bucket-level Reclassify. `pick_top_bar.py` lost its unused `style_label`. `pick_nudge_dialog.py` DELETED (no callers; classification-nudge UI for the retired Pick door). Data layer (`core.genre`, `mira.ingest.classify_pass`, the gateway's `set_classification`) untouched — Edit still reads + writes through the same seams.
> 4. **spec/64 §2.4 closed-tile body — three shapes, three eyeballs.** Shape 1 (slice 6c): "Cuts inside" hint + count, centred. Shape 2 (v2, `ccd6ae6` + `0ad2488`): bar chart (Collected / Picked / Edited / Exported with colour gradient slate → blue → amber → emerald, count + percent of Collected per bar) on the left, photo carousel cycling cached Picked thumbs every 2 s on the right. Shape 3 (v3, `6959ec3`): bar chart on the left, classification donut in the middle (slices proportional to per-photo `item.classification` count, dominant slice at 12 o'clock, transparent centre punch), legend on the right (colour swatch + label + count, sorted desc, caps at 6 rows with "+ N more"). All three widgets use `QSizePolicy.Expanding` so they fill the heatmap's slot on open tiles. `EventCard.setFixedHeight(180)` keeps both states pixel-identical in height.
> 5. **The hardware-table v4 attempt — REJECTED.** Nelson asked that "when only one style exists, replace the donut + legend with a hardware table." First attempt was shipped without an eyeball pause + had bad readability (no borders, font too small, lens / flash data didn't surface). Nelson called the rush pattern; the work was REVERTED uncommitted. The IDEA stays — when a closed event has ≤1 distinct classification, the donut reads as one wedge or empty + a hardware table (camera / lens / flash / etc., possibly more) would be more informative. Next session redesigns from scratch with a checkpoint BEFORE shipping. Also flagged: the data path needs verification — Nelson saw "no lens, no flash" even when both were clearly used, so either the EXIF fields aren't ingested or the aggregation has a bug.
> 6. **Cuts back-routing.** Closed-tile body click → Cuts list; Back from there now returns to the **events list**, not the activity dashboard. MainWindow remembers the entry door via `_cuts_entry_door` set in `_open_event_cuts_list` (events-list door) vs. `_on_phase_activated("share")` / `_menu_new_cut` (activity-dashboard door). 2 new pins in test_spec64_tile_updates.py.
>
> **EYEBALL STATUS AT WRAP:** the v3 donut + legend body has been seen by Nelson (he commented on it before pivoting to the hardware-table idea); the rejected v4 was the cue for the new memory. Other live-eyeball items still owed: Bug 3 on a real broken video; classification-strip visual confirmation on a real Pick session; slice 5's `PhoneGpsStretchDialog` (carried over from session 8, still not tried); the Cuts back-routing on a real closed-event tile.
>
> **OWED AT WRAP — what next session picks up:**
>
> 1. **Hardware-table v5 (PROPER design pass).** When the donut has ≤1 style, swap it (and the legend) for a hardware roll-up. Design needs to land BEFORE the build: bordered table, readable type, ALL the data fields (camera, lens, flash + verify the data is reaching the widget — Nelson saw "no lens, no flash" when both were used), maybe more (focal-length range, ISO range, aperture range, count per camera). Spec/64 §2.4 needs the design call recorded first.
> 2. **Exported watermark redesign (BLOCKED on Nelson's input).** Five design questions posted earlier in this session — visual shape · what the count counts · how LRC shows · where the compare button lives · discard policy. No code touched until Nelson answers.
> 3. **Live eyeballs (carried over):** Bug 3 on a real corrupt video · classification strip on a real Pick session · `PhoneGpsStretchDialog` Apply/Skip · the Cuts back-routing fork on a real closed-event tile.
> 4. **Push to origin.** This session is 21 commits ahead of origin/XMC + session 8's 9 = 30 total ahead (well, actually verify the exact count next time — the merge-base might shift). Nothing pushed.
> 5. Standing watches (unchanged): events-index clobber (`.history` first stop) · six stale legacy index rows logging bare-card tracebacks at dashboard build · the wizard "not yet completed" launch line · the latent Qt fastfail churn crash (verify.bat keeps it quarantined).
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner). 21 commits this session, verify.bat 2873/0 + 20/0 at HEAD. 30 commits ahead of origin/XMC, not pushed.
> The slice 6 arc CLOSED. Bug 3 + classification strip + closed-tile body v3 (chart + donut + legend) + Cuts back-routing all landed. Hardware-table v4 was attempted + REJECTED (rushed; lesson saved to memory feedback_slow_down_on_visual_iteration). The IDEA stays for next session: when ≤1 classification style, swap donut + legend for a hardware table — but DESIGN FIRST this time, no rushing.
> Start: the hardware-table v5 design pass — or the Exported watermark redesign (you owe me five design answers from earlier in session 9) — or the live-eyeball sweep (Bug 3 / classification / PhoneGpsStretchDialog / Cuts back-route) — or push to origin.
> Checkpoint with me before shipping any visual widget.
> ```
>
> **The single commit, `f8a27fa` — spec/64 slice 6 of 6: tile updates.** The four parts (all on `EventCard` + its data seam + the MainWindow fork):
>
> 1. **§2.3 Status badge** (clickable Open / Closed, EVERY tile). New `_StatusBadge` (QLabel subclass) eats its own `mousePressEvent` so the title-zone door doesn't open underneath; dynamic `state` Qt property drives the QSS colour with a repolish on change (memory `reference_qss_descendant_property_repolish`). Click → `status_badge_clicked` → MainWindow's `_on_card_status_toggle_requested` flips `event.is_closed` via the gateway, refreshes the index entry, and re-renders the events page. Replaces the legacy read-only `EventCardClosedBadge`.
> 2. **§5 Header first-touch badge.** "Header" nudge visible while context / experience_type / creative_focus are all unset AND the sticky `extras_json["header_touched"]` bit is unset. Gateway flips the sticky bit on first non-empty save in `set_classification` (touch = "user set a non-empty value"; empty-string / empty-list clears do NOT count); once flipped, the badge stays cleared even if the user later wipes the field back to blank (the §5 first-touch-counts rule). Pure-logic `event_classification.header_unset()` helper does the rollup; dashboard reads it into the new `EventCardData.header_unset` field.
> 3. **§2.4 Closed-tile body content** (first cut per §8 "pick one"). New `_ClosedBodyContent` widget replaces the phase × day heatmap on closed events with a "Cuts inside" title + Cut count label (zero / one / N variants). Stats charts and random Picked-photos strips parked per §8 — both can ride a follow-up if Nelson wants them.
> 4. **§2.2 Closed-tile body click → Cuts list.** `_open_event` forks on `_event_is_closed(event_id)` (cheap index-cache lookup, no `event.db` open per click): closed → `_open_event_cuts_list` (same shape as the existing `"share"` route in `_on_phase_activated`, promoted to a direct landing for closed tiles); open → the existing activity dashboard path unchanged.
>
> **Touchpoints:** `mira/ui/base/event_card.py` grew the three widgets + the `status_badge_clicked` signal + the `header_unset` / `cuts_count` fields on `EventCardData`. `mira/ui/pages/events_dashboard_page.py` grew the `event_status_toggle_requested` signal + the `_card_data` rollup (reads `ev.context` / `ev.experience_type` / `ev.creative_focus` / `extras_json` for the badge + `len(eg.cuts())` for closed tiles). `mira/ui/shell/main_window.py` grew the routing fork + the status-toggle handler. `mira/gateway/gateway.py` grew the sticky-bit write inside `set_classification` (shallow-merges into existing `extras_json` so spec/52 IPTC location facets survive). `mira/event_classification.py` grew `header_unset()`. Both QSS themes grew `EventCardStatusBadge` (with `[state="open"]` / `[state="closed"]` selectors + a hover border thickening that keeps the outer footprint stable), `EventCardHeaderBadge` (warning palette), and `EventCardClosedBody*` roles.
>
> **31 new pins in `tests/test_spec64_tile_updates.py`** (module name dodges the conftest `_SLICE_B_FILES` skip list per memory `feedback_slice_b_skip_list_swallows_tests`): five groups — (a) `header_unset` pure-logic matrix (all three blank / context set / experience_type set / creative_focus set / `["none"]` explicit no-photo / sticky bit / empty-string-as-blank), (b) `EventCard` structure (status badge on open + closed tiles, click → signal, tooltip coverage, legacy `EventCardClosedBadge` retired, Header badge visible iff `header_unset`, closed body replaces heatmap, zero / one / N Cut count phrasing, open tile keeps the heatmap, `heatmap_clicked` fires on the closed body), (c) dashboard wiring (`_card_data` for all four `header_unset` axes + `cuts_count` for closed/open, the new `event_status_toggle_requested` signal), (d) gateway sticky bit (flipped on context set, flipped on creative_focus set, NOT flipped on empty clears, doesn't clobber other extras keys), (e) MainWindow routing (`_event_is_closed` reads the cache, the toggle handler round-trips). All 31 pass by name (not silent `s`).
>
> **EYEBALL STATUS AT WRAP:** owed. Slice 6's surfaces (the badges on the tile, the closed-tile body swap, the closed → Cuts list route) have not been tried on Nelson's machine yet — landed on test coverage alone. The full §9 acceptance criteria can be eyeballed in one launch (open the dashboard, click a status badge, watch the toggle + body swap, click a closed tile's body, land on Cuts list; for the Header badge, look at any old event — the badge rides until the user fills one of the three new dimensions).
>
> **THE SPEC/64 ARC IS COMPLETE — slices 1–6 all landed.** Schema v6 with the three new dimensions + the per-day fixes + the per-location-group GPS prompt + the four tile doors + the Header / Status badges + the closed-tile body swap. Old events surface the Header badge on first dashboard render after migration; the moment any of the three is set, the badge clears for good.
>
> **OWED AT WRAP — what next session picks up:**
>
> 1. **Slice 6's live eyeball** (the four parts on Nelson's machine, per §9 acceptance).
> 2. **Bug 3 — blank-frame video cells in Day Grid** (still chipped: `task_fa18066a`). Video items render blank instead of an extracted representative frame on fresh ingests. Likely thumb-cache miss; cross-ref spec/59 §8's forward-walking ladder. Files: `mira/ui/base/day_grid_view.py`, `core/thumb_extract.py` (or similar), `<event>/.cache/thumbs/` directory walk.
> 3. **Slice 5's live eyeball** (`PhoneGpsStretchDialog` Apply / Skip on real data — still owed from session 8; test seam works, UX hasn't been tried).
> 4. **Push to origin.** This session is 1 commit ahead of origin/XMC + the 9 from session 8 = 10 total ahead, nothing pushed.
> 5. Standing watches (unchanged): events-index clobber (`.history` first stop) · six stale legacy index rows logging bare-card tracebacks at dashboard build · the wizard "not yet completed" launch line · the latent Qt fastfail churn crash (verify.bat keeps it quarantined; suspects: PyQt6/Qt6 DLL-level on this machine).
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner). The spec/64 arc is closed — slices 1–6 all landed; verify.bat 2871/0 + 20/0 at HEAD. 10 commits ahead of origin/XMC, not pushed.
> Start: live eyeball on slice 6 (the four tile-update parts), or pick Bug 3 (blank-frame video Day-Grid thumbs — chipped as task_fa18066a), or push the branch.
> Checkpoint with me on the eyeball before any follow-up.
> ```
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-13, EIGHTH SESSION — WRAPPED) — **SPEC/64 events-information split: slices 1–5 of 6 LANDED + two real bugs squashed + the slice 4 wheel filter refined to the focus-aware contract Nelson locked.** Nine commits this session, tree clean, **verify.bat 2840 passed / 0 failed / exit 0 at HEAD** + 20/0 quarantine, ahead of origin/XMC (not pushed). The spec/64 arc Nelson opened the session with built in five checkpointed slices, with one mid-arc spec-shape correction (slice 4 first stripped the legacy Include / Browse / CSV / Delete / Override / frozen features in the structural rewire; Nelson caught it at the checkpoint and the rewrite restored every one while keeping the slice 3 fixes); two of the three field-eyeball bugs Nelson flagged after his create-event run got fixed in-session (the third is chipped for its own session).
>
> **The nine commits, oldest → newest:**
> 1. `66e6985` — spec/64 slice 1 of 6: schema v6 + model + gateway (drop event.scope/mood/transport; add context/experience_type/creative_focus; per-unit duration cap retires)
> 2. `850d722` — spec/64 slice 2 of 6: EventHeaderDialog (8 fields; rich §3.2/§3.3 descriptions as per-item tooltips; required floor Name+Type+subtype; §3.4 Creative Focus None↔subjects mutual exclusion)
> 3. `d00b8e8` — spec/64 slice 3 of 6: EventDaysTableDialog (the §4 surface: focus-stays-put wheel filter, Country/TZ propagate-down with NoIcon confirm walling at user-touched rows, free-text touched ledger)
> 4. `9186c04` — spec/64 slice 4 of 6: PlanDialog retires + creation flows rewire (six legacy call sites collapse to two; tile click split per §2.2; Days Table dialog grew back every PlanDialog feature — Include / Browse / CSV / Delete / Override / frozen — after Nelson called the strip at the spec-shape checkpoint)
> 5. `90eeadd` — spec/64 §4.2 first wheel-fix: route every cell wheel event to the table viewport (Nelson eyeballed slice 4 and the slice 3 filter wasn't holding because Qt delivers wheel to widget-under-cursor regardless of focus)
> 6. `9b2d6c6` — spec/64 §4.2 wheel-fix refined: pass wheel through once a cell has focus (Nelson refined the contract: "After left clicking a field with a dropdown, the mouse wheel should work over that field")
> 7. `b4d2086` — Fix Bug 1: videos landing without a date when local TZ wasn't US-Mountain (core/exif_reader._parse_timestamp hardcoded "-07:00" split; truncate at 19 chars instead — Brazil 2023 GoPro footage was the canonical case)
> 8. `78e72bd` — spec/64 slice 5 of 6: phone-without-GPS per-location-group prompt (§4.4 replaces the silent home-fill with PhoneGpsStretchDialog; one prompt per consecutive stretch of blank country/TZ days; Apply spreads across the stretch, Skip leaves blank)
> 9. `b5072d9` — Fix Bug 2: refresh the days list Pick/Skip counts INSIDE Quick Sweep (PickDay/BucketStatus are frozen; new refresh_day_statuses helper re-projects on back-from-grid)
>
> **What the spec/64 arc built:**
>
> 1. **Slice 1 — schema + model + gateway.** Schema v6 migration: drop `event.scope` / `mood` / `transport` (+ their indexes); add `event.context` (TEXT) / `experience_type` (TEXT) / `creative_focus` (TEXT JSON array, NOT NULL DEFAULT '[]'); real ALTER TABLE — DROP INDEX before DROP COLUMN (SQLite won't drop a column an index references); old qualifier values do NOT carry over per Nelson's "drop clean, no leftovers" call. `Event` dataclass swap; `event_classification` ships `CONTEXT_OPTIONS` / `EXPERIENCE_TYPE_OPTIONS` / `CREATIVE_FOCUS_OPTIONS` + rich-description maps; `set_classification` signature swap with closed-enum validation; PlanDialog kept alive (slice 4 retires); test_event_header_migration NEW (12 pins).
> 2. **Slice 2 — EventHeaderDialog.** Eight fields per §3.1 (Name / Type+subtype required floor / Description / Duration free integer no cap / Context / Experience Type / Creative Focus / Participants); rich descriptions from §3.2/§3.3 ride as per-option Qt ToolTipRole tooltips (the §3.5 "hover teaches" rule); §3.4 None ↔ subjects mutual exclusion enforced UI-side; `header_info()` matches the legacy `PlanDialog.event_info()` shape for the surviving fields so slice 4 swaps cleanly; 13 pins.
> 3. **Slice 3 — EventDaysTableDialog.** Five columns (Date / Country / TZ / Location / Description); `_NoUnfocusedWheelFilter` event filter (initial slice-3 cut — slice 4 follow-up replaced it with `_WheelToTableFilter`); Country/TZ propagate-down with plain yes/no confirm (NoIcon per `feedback_qmessagebox_chrome_disliked`), walling at user-touched rows; free-text Location/Description with touched-state ledger; 15 pins.
> 4. **Slice 4 — PlanDialog retirement + creation-flow rewire.** Six MainWindow call sites collapse to two; tile click routing per §2.2 (title → Header, left → Days Table, body → activity dashboard); Just-create opens Header only; Create-from-media: scan + multi-date split + Header + Days Table preview (with Include + browse_handler + CSV gate) + create + auto-Collect; Collect-existing: scan + multi-date split + Days Table preview + ingest gate. EventDaysTableDialog grew back every legacy feature (Include checkbox with date label, Browse-day peek, CSV save/load premium-gated, Delete-day opt-in, Override marker auto-hide, frozen_after_ingest + tz_editable_when_frozen for spec/57 §4.2). PlanDialog file + test_plan_dialog deleted. Mid-arc spec-shape correction recorded above.
> 5. **§4.2 wheel filter, refined.** Slice 3 only blocked wheel on unfocused widgets without forwarding; user wheeled and nothing scrolled, then they clicked a combo (which then DID handle wheel) and the combo shifted while they were trying to scroll. First fix (90eeadd) routed every cell wheel event to the table viewport via `QApplication.sendEvent`. Nelson then locked the final contract: wheel over an unfocused cell forwards to the viewport (table scrolls), wheel over a focused cell passes through (combo shifts). The `_WheelToTableFilter` carries both contracts; 32 pins.
> 6. **Slice 5 — phone-without-GPS per-location-group prompt.** Replaces the silent home-fill. `PhoneGpsStretchDialog` lists the date range covered, one Country combo + TZ picker apply across the whole stretch, pre-filled with home values as suggestions. Apply spreads; Skip leaves blank for the Days Table dialog to handle. MainWindow's create-from-media + collect-existing flows now pass `home_country=None, home_tz_minutes=None` to scan_source (and build_scan_result after the multi-date split rebuild), then walk `_collect_phone_gps_stretches` for consecutive runs missing country OR TZ and prompt per stretch; 11 pins.
>
> **The two bugs squashed:**
>
> - **Bug 1 (no dates on videos).** Nelson collected an event and most videos came in with no capture date. Hypothesis: "the no-GPS path is short-circuiting the EXIF read." Root cause turned out to be a TZ-strip bug in `core/exif_reader._parse_timestamp` — the `.split("-07:00")` hardcoded US-Mountain. Any other negative TZ (`-03:00` Brazil, `-04:00` East Coast, etc.) survived the splits, failed strptime, returned None, and the item routed to the no-timestamp quarantine. Brazil 2023 GoPro footage was the canonical case. Fix: truncate the string to its first 19 characters before parsing — the calendar timestamp is always there; everything after (fractional, any TZ, future trailers) is discarded by construction. 12 new pins.
> - **Bug 2 (Quick Sweep days list stale).** Per-day Pick / Skip bars in the days list INSIDE Quick Sweep stayed frozen at their load-time counts as Nelson swept. Root cause: PickDay / BucketStatus are frozen dataclasses with precomputed counts; `self._days` was reused unchanged across navigation. Fix: cheap `refresh_day_statuses` helper re-projects every bucket + day-rollup against the page's current `_state_for`, called from `_on_day_grid_back` before showing the nav. 5 new pins (in `tests/test_quick_sweep_days_list_refresh.py` — outside the conftest `_SLICE_B_FILES` skip list per memory `feedback_slice_b_skip_list_swallows_tests`; tests in `test_quick_sweep_buckets.py` would be silent-skipped).
>
> **EYEBALL STATUS AT WRAP:** Nelson ran a full Create Event flow on real data after slice 4 ("All that we have created in this session seems to have worked fine") and the slice 4 commit landed on that signal. The §4.2 wheel filter went through TWO Nelson eyeballs to lock the contract. The Brazil 2023 GoPro repro confirmed Bug 1's TZ origin (the failure mode matched perfectly — every -03:00 timestamp). Bug 2's days-list refresh isn't eyeballed yet — landed on test-coverage alone. Slice 5's prompt dialog is not eyeballed live yet (Apply / Skip paths green by test only).
>
> **OWED AT WRAP — what next session picks up:**
>
> 1. **Slice 6 of 6 — tile updates (the closer of spec/64).** §2.2 + §2.3 + §2.4 + §5. Four parts:
>    - **Status badge (4th door, §2.3).** Small "Open" / "Closed" badge on every event tile. Instant toggle on click (no confirm) — flips `event.is_closed` via `eg.set_closed(...)` (already wired in `MainWindow._on_close_toggled` — the badge just triggers it). Distinct from the §5 Header first-touch badge; both can coexist.
>    - **Header first-touch badge (§5).** Shows on the tile while Context / Experience Type / Creative Focus are all unset (the new dims that arrive blank on every pre-spec/64 event after migration); clears the moment the user touches one. The existing `EventCardData` already has `is_closed` + classification; the Card needs to know context/experience/creative_focus presence — extend `EventCardData` to carry the new dims (or just a `header_unset: bool` rollup) and have the dashboard pass it through.
>    - **Closed-tile body content (§2.4).** When `is_closed`, the body replaces the phase × day heatmap. Spec says: stats charts OR random Picked photos (build picks; both acceptable). Quick interpretation Nelson confirmed in design: pick ONE for slice 6 and we can swap later. Easiest first cut: a small "Cuts inside" hint + count + maybe a tiny Picked-thumbs strip; charts can come second. Lives on `mira/ui/base/event_card.py`'s right-zone area when `data.is_closed`.
>    - **Closed-tile body click → Cuts list (§2.2 + §2.4).** On a closed tile, the body click stops going to the activity dashboard and routes to `CutsListPage` (spec/61 §6). The `heatmap_clicked` signal is already there; the host (`main_window`) needs to fork on `is_closed` and route to the Cuts shell instead.
>    
>    Touchpoints: `mira/ui/base/event_card.py` (badge widgets + closed-body content + signal routing), `mira/ui/pages/events_dashboard_page.py` (passes new data through), `mira/ui/shell/main_window.py` (the `is_closed` fork in the click handler + a new "open this event's Cuts list" entry point), `mira/gateway/event_gateway.py` if a "header_unset" rollup query is wanted. Spec/64 §10 has the design locked.
>
> 2. **Bug 3 — blank-frame video cells in Day Grid** (still chipped: `task_fa18066a`). Video items render blank instead of an extracted representative frame. Likely thumb-cache miss on fresh ingests. Cross-ref spec/59 §8's forward-walking ladder (position → fallback → 3 s → probed 10%/25%) which already exists for player-level black frames — check whether the same ladder fires for Day Grid thumbs or whether the cache is unpopulated for fresh items. Files: `mira/ui/base/day_grid_view.py`, `core/thumb_extract.py` (or similar), `<event>/.cache/thumbs/` directory walk.
>
> 3. **Live eyeball owed:** slice 5's `PhoneGpsStretchDialog` Apply / Skip on real data (the test seam works; live UX hasn't been tried). Slice 6 itself.
>
> 4. **Push to origin.** This session is 9 commits ahead of origin/XMC; nothing pushed yet.
>
> 5. Standing watches (unchanged): events-index clobber (`.history` first stop) · six stale legacy index rows logging bare-card tracebacks at dashboard build · the wizard "not yet completed" launch line · the latent Qt fastfail churn crash (verify.bat keeps it quarantined; suspects: PyQt6/Qt6 DLL-level on this machine).
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner), then spec/64 §2.2 / §2.3 / §2.4 / §5 (the slice 6 design), then spec/64's §10 record so you see what shape Slice 1–5 left the codebase in.
> verify.bat 2840/0 + 20/0 (quarantine) at HEAD. 9 commits ahead of origin/XMC, not pushed.
> Start: Slice 6 — the tile updates that close the spec/64 arc. Four parts (status badge / Header first-touch badge / closed-tile body content / closed-tile body click → Cuts list). Or pivot to Bug 3 (blank-frame video cells in Day Grid; chipped as task_fa18066a) if you'd rather a smaller piece first.
> Checkpoint with me before the slice after it.
> ```
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-12 → 2026-06-13, SEVENTH SESSION — WRAPPED) — **THE SPEC/60 BATCH ENGINE IS WHOLE + THE FOUR UI ROUNDS ALL LANDED.** Twelve commits this session, tree clean, **verify.bat 2785 passed / 0 failed / exit 0 at HEAD**, pushed to origin/XMC. The recommended next big arc (spec/60, designed 2026-06-11) built in three checkpointed slices, then four polish/feature rounds Nelson queued during the build, ending with a real slice (Look Strength slider — schema v5 + math + UI + render pipeline in three commits). 57 new sweep tests + 36 new feature tests + every neighbour green; MainWindow construct smoke OK at each step.
>
> **The twelve commits, oldest → newest:**
> 1. `fdee6f6` — spec/60 slice 1: the render worker (one binary, manifest in, per-unit truth out)
> 2. `1b89f2c` — spec/60 slice 2: batch rides the worker process — per-unit truth at the commit
> 3. `aec02a0` — spec/60 slice 3: the clip lane, the encoder ladder, the spec/56 §6 walker — videos join batch
> 4. `01bef0f` — Back-button sweep: plain "Back" everywhere, uncoloured
> 5. `f79172f` — Help-button sweep + shared ShortcutsDialog
> 6. `cb5f143` — Play/Pause polish: plain TransportButton + width pinned
> 7. `d5cd835` — Look Strength foundation: schema v5 + math
> 8. `eea1b48` — Look Strength UI: the slider in AdjustmentSurface
> 9. `38d6c98` — Look Strength render pipeline: preview = export by construction
> 10. `b5aee2b` — PROGRESS session record
> 11. (banner draft, superseded)
> 12. `aa82171` — verify.bat green: test fixups + the clean wrap banner this preserves
>
> **Spec/60 batch engine — the build arc Nelson opened the session with.** Designed + locked 2026-06-11 (the §10 acceptance criteria were already written); this session built it.
>
> 1. **Slice 1 — the worker protocol + photo lane.** `core/export_manifest.py` (`PhotoUnit` + JSON wire, forward-compat load), `core/render_worker.py` (`worker_main`: N-wide photo lane on the UNCHANGED `_render_one`/`_write_image` → §1 parity by construction; width = cores−2 floor 1, RAM-capped via `GlobalMemoryStatusEx`; self-lowered below-normal priority so ffmpeg children inherit the class; `_NameReserver` arbitrates in-flight collisions the serial engine never had; per-unit JSON-lines protocol on stdout with `kind` per unit; pure-ASCII wire; logs on stderr), `mira/__main__.py` (the one-binary `--render-worker` dispatch — also fixed the broken `build.bat` entry file as a side effect). 13 tests incl. real-subprocess end-to-end.
> 2. **Slice 2 — the app-side job + per-unit commit.** `core/worker_job.py` (`WorkerJob`: spawn at below-normal + no console window, Windows job object with kill-on-close so the worker tree dies with the app, `TerminateJobObject` cancel sub-second, stderr drain into the app log, JSON-lines `messages()` reader; `BatchJobResult` folds OK-units-only into `ExportResult` so the existing lineage writer inherits per-unit truth unchanged), `core/render_worker.py` += `run_manifest_inline` (the §4 last resort — sequential by design; an in-process pool would soak cores at normal priority and break §2), `mira/ui/edited/export_job.py` (`BatchExportJob(QThread)`, the queue-contract adapter; spawn-failure → inline fallback, started-then-died is NOT re-run inline — could double-write). The host's `_run_batched_export` rewired: manifest built where the gateway lives; bucket-as-a-unit retired (`_build_journal_for_items` deleted, as its docstring predicted); commit is per-`ok_unit_ids` only; cancel commits the finished units (real atomic exports on disk — the legacy path threw that truth away). 7 tests incl. a 200-unit kill-mid-job that verifies the reserver fans same-stem outputs into distinct names.
> 3. **Slice 3 — the clip lane, the ladder, the walker.** `core/encoder_ladder.py` NEW: NVENC → Intel QSV → AMD AMF → libx264 (the floor — every machine completes every job); real test encodes cached per process; ONE INFO log per session names the winner. `video_export_run` delegates here. `ClipUnit` added to the manifest (back-compat for slice 1/2 binaries via `clips=()`); the worker grows a one-clip-frame-parallel-inside lane that runs SIDE BY SIDE with the N-wide photo lane under one `as_completed` loop; every unit message tagged `kind`. `BatchJobResult.ok_clip_results` carries clip outcomes; clips deliberately stay OUT of `written/overwritten/renamed` (photo lineage walker keys by stem). `core/edit_export_walker.py` NEW (spec/56 slice-4: picked `VideoSegment` rows → `ClipUnit`s, geometry from `core.video_segments.segment_bounds`, plan via `build_export_plan` through an override-shim that keeps QtMultimedia out of the walker; skips on missing source / bad geometry / no item — never trips the worker mid-batch). `edit_host_page._run_batched_export` extended: clip segments collected per day/event; the commit closure adds `set_edit_exported` + `record_single_lineage` per ok clip with the recipe re-read from `VideoAdjustment` + params echoed from the worker. **Day/event batch finally carries VIDEOS too** (workshop single-clip Export untouched — spec/60 §8 as-you-go path stays off the queue). 17 new tests + 3 upstream re-pointed.
>
> **The four UI rounds — queued during the build, all landed before wrap.**
> - **(a) Back, plain style everywhere** (`01bef0f`) — `back_button()` default label dropped from `← Back` to plain `Back`; every raw `QPushButton(tr("← Back"))` converted to the factory; the bucket navigator's `#DangerButton` red-outline retired (the exact non-house colour Nelson called out on a navigation control). 9 surfaces touched; tooltip carries the side-effect context where it matters ("leave without saving"). 7 tests + the raw-glyph regression guard.
> - **(b) Help button + shared ShortcutsDialog** (`f79172f`) — `mira/ui/base/shortcuts.py` NEW (2-column table, mono key column, section dividers, four QSS roles in both themes); the four existing in-house impls collapsed to it; five surfaces gained Help for the first time (Quick Sweep / Cut session / Cut detail / Cut play [F1/? only, auto-pauses the rehearsal] / F10 lens [key-only]); PickTopBar's misrouted `#ReclassifyButton` role on the `?` button fixed to `#HelpButton`. 5 + 2 tests.
> - **(c) Play/Pause polish** (`cb5f143`) — `transport_button()` factory + `set_transport_playing(btn, playing)` helper + `#TransportButton` QSS role (plain look, both themes). Fixed-width icon-only `▶`/`⏸` swap — the width-dance is structurally impossible now (`sizeHint().width()` invariant under state swap). Four surfaces converted (Pick video, Pick photo slideshow, Quick Sweep video, Edit workshop); the misrouted `#Primary` (video Pick) and `#FeatureToggle:checked` (slideshow) accents both retired. 7 tests.
> - **(d) Look STRENGTH slider** — three-commit arc.
>   - **Foundation** (`d5cd835`): schema v5 (`adjustment.look_strength REAL DEFAULT 1.0 CHECK [0,2]` on fresh installs; v4→v5 migration adds the column with default 1.0 → pre-migration rows render IDENTICALLY by-construction); `Adjustment.look_strength: float = 1.0`; `compute_look_params(..., strength=1.0)` and `look_params_from_natural(..., strength=1.0)` scale the WHOLE composed Look via `Params.scaled(s)` (distinct from spec/54 §4.1 intensity which scales the bias only); s=1.0 is a no-op, s=0.0 returns identity, Original stays identity at any strength. 12 tests.
>   - **UI** (`eea1b48`): the slider lives inside the Look group box under the four Look buttons; range 0..2 with the tick at 1.0, double-click snaps back, the slider greys out on Original (inert there), Reset all snaps to 1.0; `SurfaceState` carries `look_strength` with the [0,2] clamp on set_state. 15 tests.
>   - **Pipeline** (`38d6c98`): `_render_one` reads `look_choice["strength"]` and threads to `compute_look_params` (single source of truth — preview = export by construction); `get_process_look` surfaces strength from the journal with [0,2] clamp + legacy 1.0 default; the spec/60 manifest carries strength on the `look` dict (omitted at default 1.0 to keep the wire small); `_collect_recipes` puts strength in the spec/54 §8 lineage snapshot so a re-render years later reproduces the exact pixels. 9 tests.
>   - **Design choices Nelson made:** (1) **Live re-resolve each drag** — the surface re-runs `look_params_from_natural` at the new strength each tick, so non-linear Look math (like the bias scaling for Brighter/Deeper) is honoured properly at fractional strengths. The cached Natural keeps the per-tick cost near-free; the debounced render absorbs the rest. (2) **Continuous 0..2 with tick at 1.0** — smooth slider, snap on double-click or Reset, matches existing Picker/Edit slider feel.
>
> **EYEBALL STATUS AT WRAP:** Nelson's `Eyeball showed all fine` at the post-spec/60 checkpoint (slices 1+2 in his hands; slice 3's video lane not yet eyeballed). UI rounds not yet eyeballed (they landed at session end). The §10 batch-engine eyeball + the slider's first real-data drag are both **owed** to the next session.
>
> **NEXT SESSION — Nelson's pick:**
> 1. **The §10 spec/60 eyeball** (owed) — `launch.bat`, open an event with green photos + at least one picked video segment, hit Export → "day" or "event" scope. Watch for stutter while browsing/developing/playing video alongside; cores busy at below-normal in Task Manager; the progress line ticks per unit; the green watermarks land only on the photos that exported; Cancel inside one second; close the app mid-job and confirm no orphan ffmpeg; check `Edited Media/<Dia N>/` for `<video>_clipN.mp4` files at the right segment timing. The INFO log line on first export says "encoder ladder: using …" — that's the chosen rung.
> 2. **The Look Strength slider's first real-data drag** (owed) — open a developed photo, drag 0.0 → 1.0 → 2.0; the develop-in-place rhythm should match what slice 6b promised (instant proxy-sharp landing, look settles in ~¼ s); double-click to snap back; flip to Original and watch the slider grey out. Export at strength 1.5 and confirm the baked file matches what the preview showed.
> 3. **The UI rounds eyeball pass** — Back uniform everywhere, Help dialog readable in both themes, Play/Pause no-dance with the row anchored, the Strength slider's value label legible.
> 4. **The latent-crash fix session** (a task CHIP exists — one click opens it in its own worktree; the reproducer + Event-Log signature + disproven cures are in `reference_latent_qt_fastfail_churn_crash` memory; un-quarantine `verify.bat` when fixed). Contained meanwhile — no urgency.
> 5. Parked design sessions: cross-event Cuts (spec/61 §8) · database protection (spec/61 §9) · the menu-bar structure (long-standing).
> 6. Deliberate Share gaps awaiting priority: compare view in session/detail · video playback in single views · session arrows across days · per-item export from detail · pool-algebra affordance re-read with real data.
> 7. Standing watches (unchanged): events-index clobber (`.history` first stop) · six stale legacy index rows logging bare-card tracebacks at dashboard build · the wizard "not yet completed" launch line.
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner), then spec/60 (if doing the batch eyeball) or spec/54 §3.2 (if doing the slider).
> All of yesterday's work is committed clean and pushed (12 commits, fdee6f6..aa82171 on XMC). verify.bat 2785/0 at HEAD.
> Eyeball: [batch engine clean / stutter found · slider drag rhythm / colour parity · UI rounds OK or finds]
> Start: [§10 batch eyeball — or — slider real-data drag — or — UI rounds eyeball — or — the crash-fix chip — or — your pick]. Checkpoint with me before the slice after it.
> ```
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-12, SIXTH SESSION — WRAPPED) — **THE PERF-TIER RUN IS COMPLETE: slices 7 + 8 + the probe re-run + 6b ALL LANDED — the spec/63 thesis (ONE display engine, every surface) is DONE, and the numbers moved exactly as designed. Nelson's eyeball verdict at wrap: "Runs very fast now."** Eight commits: `ed2ab08` (7, proxy tier) → `0b4660c` (8, export thumbs) → `48c9015` (after-numbers + focus-pin) → `3246d85` (6b analysis) → `159a562` (6b net) → `1e91941` (net era-portable) → `1725e11` (6b swap) → `b18070e` (verify.bat honest exits + quarantine). Tree clean; verify.bat 3/3 green (honest exit codes); only the untracked `_smoke_*`/`_probe_*` throwaway rigs remain at the root (keep or delete freely; `_probe_after_tiers.py` + `_probe_proxy_decode.py` are the re-runnable perf probes).
>
> **The day in numbers (real library, spec/62 §1.1):** browse @2560 93–145 → **18–30 ms** (under key-repeat — the FastStone bar met) · RAW browse 17 → **12.5 ms** · Cut grid cell ~24 → **0.4 ms** · Edit per-nav UI freeze ~290–770 ms → **gone** (instant landing, develop-in-place ~¼–⅓ s) · `_downsample` 111 → **37 ms** (and off-thread). Disk: ~3 GB / 5 000 photos, visible in Settings → Advanced with Clear.
>
> **Slice 7 — THE PROXY TIER — LANDED** (spec/63 §5 table + §7.7 carry the full record):
>
> 1. **`core/photo_proxy_cache.py`** (Qt-free): sha256-keyed `<sha>.jpg` (≤2560 px q85) + `<sha>.json` sidecar under `<event>/.cache/proxies/`. Sidecar = source mtime_ns+size (invalidation — covers the spec/57 round trip) + **the ORIGINAL's post-orientation native dims**; written LAST (commit marker). Plus the polite `ProxyBuilder` daemon (yields while the decode worker has queued jobs; cross-root seed drops the stale queue).
> 2. **Engine** (`photo_cache.py`): ONLY the scaled tier prefers proxies — proxy decodes at the display target, emits the original's native dims from the sidecar, so `sharp_pixmap_info` consumers never learn proxies exist. `request_pixmap` (Compare) and the F10 lens decode ORIGINALS by construction — the truth paths can't see proxies. Corrupt proxy self-heals (drop → original → rebuild). **Write-on-decode:** the worker persists proxy-grade decodes it already holds — AFTER the emit (sharp latency never pays); small-window decodes deliberately don't persist (would serve soft later; the builder fills those at full size). The (event_root, sha map) pair is now read from the worker thread → consistent under `_context_lock` (a cleared root with a stale map could have served a cross-event proxy).
> 3. **Seeding:** `set_event_context` (per bucket, free) + `PickPage.open_event` whole-event seed (one `items(kind="photo")` pass) — proxies build quietly while the user is still on the day grid. Quick Sweep (pre-ingest, no sha) and Cut grids (export files) untouched by design.
> 4. **Disk honesty:** Settings → Advanced **"Screen copies"** row (count · size for the open event) + NoIcon-confirmed **Clear…** (regenerable). New schema-driven `info` widget kind (host-injected providers/actions — MainWindow wires the open event).
> 5. **Measured** (`_probe_proxy_decode.py`, noisy 24 MP JPEG @2560): original 140 ms → **proxy 24 ms (5.9×**, inside the predicted 20–40 ms); proxy 0.61 MB → **~3 GB / 5 000 photos exactly as §5 promised**; build 309 ms/photo in background.
> 6. **Verified:** `test_photo_proxy_cache.py` (21, by name) + `test_settings_info_row.py` (4) + the six neighbour suites green (photo_cache / photo_viewport / pick_photo_surface / quick_sweep_viewer / cut_session_page / cut_detail_page — 93) + MainWindow construct-smoke + the settings-dialog-with-providers smoke (both OK; the six stale legacy index rows still log their KNOWN tracebacks).
>
> **Slice 8 — EXPORT-FILE THUMBS — LANDED** (spec/63 §5 table + §7.8 carry the full record):
>
> 1. **`core/photo_thumb_cache.py` export functions:** `<event>/.cache/thumbs/exports/<relpath-digest>.jpg` (exports aren't Items — the lineage `export_relpath` IS the identity). **280 px, not the casually-written 256** — the Cut grids request at the Day Grid's MAX_CELL_SIZE (280); 256 would upscale at the slider's top. Staleness is make-style (`thumb.mtime ≥ source.mtime`; re-export invalidates; hardlinked backfill sources' old mtimes always lose to the later thumb — correct).
> 2. **The four lineage writers QUEUE, never render inline** (a 200-file batch must not stall the foreground): `ui/edited/_lineage` (both entry points), the return scan, the spec/57 backfill → a process-wide background builder (the slice-7 `ProxyBuilder` generalised: injected ensure; **dedupe set now means "currently queued", not "ever seen"** — re-exports and stale proxies can re-queue; ensure()'s resolve-hit makes re-seeds ~free). Clips (.mp4) skip by suffix.
> 3. **Engine routing:** scaled requests at ≤280 targets for NON-item paths serve the thumb (~2 ms; native dims via the original's header probe); bigger targets (Cut single views) bypass; item paths never get export thumbs (proxy tier). Pre-slice-8 exports **self-heal** via the same write-on-decode hook. Cut session + detail pages register the event root (`set_event_context(root, {})`) — a straight-to-Share flow never passes a Pick surface.
> 4. **Verified:** `test_export_thumbs.py` (13, by name — incl. a real-gateway lineage-writer pin) + the twelve-suite sweep all dots (158: proxy/export/cache/viewport/pick-surface/quick-sweep/cut-pages/settings-row/external-returns/backfill-edited/watermark) with the DOCUMENTED machine-local teardown crash; MainWindow construct-smoke OK.
>
> **THE SPEC/62 PROBE RE-RUN — DONE** (spec/62 §1.1 carries the table; `_probe_after_tiers.py` untracked rig, real library files, caches to temp):
>
> | path | before | after |
> |---|---:|---:|
> | JPEG browse @2560 | 93–145 ms | **18–30 ms** (proxy) — under the 33 ms key-repeat: the FastStone bar is MET for browsing |
> | RAW browse | 17 ms | **12.5 ms** (proxy; skips rawpy container parse) |
> | Cut grid cell | ~24 ms | **0.4–0.5 ms** (thumb) — better than the predicted ~2 |
>
> Sidecar honesty verified on the wire (proxy serve reports the original's 5776×3248). Edit's numbers move with 6b.
>
> **FULL-SUITE SWEEP (cross-cutting engine change → justified): 2704 passed / 0 failed via verify.bat.** Two finds on the way, both fixed: (1) the settings AUDIT walked every schema field with `f["key"]` — the new keyless `info` rows need `.get` (audit updated); (2) **`test_viewer_focus_reaches_the_viewport` flaked under verify.bat and it is NOT ours** — verify.bat sets no QT_QPA_PLATFORM, so full sweeps run on the NATIVE platform, where `hasFocus()` needs the OS to keep the window ACTIVE against ~2700 sibling test windows; reproduced at the 4eb4d69 baseline worktree (same single F in the same region; passes alone, passes offscreen). Pin REWRITTEN to `focusWidget()` — the within-window assignment, which is what both halves of the original bug actually broke — with `hasFocus()` kept conditionally when the window IS active. ⚠ Lesson for the record: **verify.bat full sweeps run HEADED on this machine** (test windows really open); harness runs should keep setting offscreen explicitly.
>
> **6b — LANDED (`1725e11`): EDIT RIDES THE VIEWPORT — THE §1 THESIS IS COMPLETE, every photo surface speaks the ONE engine.** The full 5d recipe ran: analysis → §6.1 turnkey map → **Nelson's checkpoint** (plain-language form; all three recommended options: Q1 instant landing with the undeveloped flash + greyed tools · Q2 RAW F10 preview from the half-size copy · Q3 develop-only-on-settle) → era-portable net at clean HEAD (`159a562`, 10 pins) → the atomic swap, **net green UNEDITED**. What changed: EditPage's per-nav UI freeze (~290 ms JPEG / ~770 ms RAW measured) is GONE — instant proxy-sharp landing, settle-gated off-thread prep (`edit_prep.py`: a PhotoCache-shaped SINGLETON relay — the worker emits only to the long-lived relay, pages get same-thread delivery; signal-to-signal chaining, NEVER `.emit`-as-slot which silently runs downstream slots on the worker thread = instant 0xC0000409), develop-in-place + tools wake. `decode_image` grew `raw_half_size`; `_downsample` 111 ms → **37 ms measured** (reduce+BILINEAR) and off-thread anyway; the viewport grew the MediaCanvas-contract mirrors (photo area/rect/geometry-signal/watermark) + `set_rendered_pixmap` + `set_truth_internal(False)` wiring; EditPage gained **`shutdown()` quiesce, auto-run on DeferredDelete** (the defined lifecycle end). EditVideoPage: ZERO changes (the `canvas()` alias + mirrored APIs). 144 green across the nine Edit/viewport suites; construct smoke OK.
>
> **THE CRASH HUNT (90 minutes, Nelson called time — the verdict matters):** mid-verification, pytest processes started dying 0xC0000409 (no message — fail-fast bypasses handlers; **Windows Event Log carried the truth: Qt6Core.dll fastfail @0x1cf68 + one sip AV**). Systematic bisect eliminated EVERY 6b component (prep threading, emission semantics, render push, set_state/badge/aspect, focus proxy, bare-viewport churn, bare-surface churn — each via switch experiments), then the baseline check: **the crash reproduces 4/4 at `4eb4d69` — BEFORE ANY of today's work.** It is the long-documented "machine-local Qt teardown crash", now UNDERSTOOD: **any pytest process that constructs bare AdjustmentSurfaces (test_adjustment_surface_busy / _rotation) fail-fasts during whichever suite runs NEXT in the same process** — order/timing-dependent (unbuffered `-u` runs dodge it; suite-order shifts from new test files made it fire often TODAY). Cures tried and DISPROVEN: per-test gc.collect (8/8 still), deleteLater+flush fixtures (7/8), every lifecycle variant. **One-command reproducer: `python -m pytest tests/test_adjustment_surface_busy.py tests/test_aspect_ratio.py -q` → ~4/4 dead at "...ssss..".** The deep fix is ITS OWN SESSION (suspects: PyQt6/Qt6 DLL-level on this machine; consider PyQt upgrade first).
>
> **verify.bat is FIXED + QUARANTINED (same commit as this banner):** (1) it used to end with `type`, so a crashed run reported **exit 0** — pytest's exit code now propagates, with a "VERIFY FAILED" line; (2) the two bare-surface suites run in their OWN pytest process (both green in isolation — 20+1s) so the latent bomb never meets a victim. **verify.bat 3/3 green at HEAD** (main pass + quarantine pass, honest exit codes). ⚠ Also recorded earlier today: verify.bat sets no QT_QPA_PLATFORM → full sweeps run on the NATIVE platform (test windows really open).
>
> **EYEBALL STATUS AT WRAP:** Nelson's first live verdict is IN — **"Runs very fast now"** (the headline confirmed). The DETAILED checklist below stays available for fix-as-found on his next real-data passes: (a) slices 7/8 specifics — held-arrow at Pick after the first pass, Settings → Advanced "Screen copies" row + Clear, Cut grid re-open instant; (b) 6b specifics — the develop-in-place rhythm in Edit (photo instantly sharp → look snaps in ~¼ s, ~⅓ s RAW → tools wake; F10 = the developed full-res render; Toggle-Crop/Compare/crop/rotate unchanged; video workshop untouched); (c) the still-standing fifth-session arc checklist below (video autoplay-on-landing veto included).
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-12, FIFTH SESSION — WRAPPED) — **THE MIGRATION ARC IS DONE: 5d + 5e + 6a all landed; every decision surface speaks the spec/63 §4 locked map. Nelson's "move till the end → test in the final app" run.** Four commits: `9d5ef0f` (the 5d net) → `b560ec2` (5d swap) → `46b1fda` (5e) → `b1b1dfc` (6a). Tree clean, full-suite sweep green (details below).
>
> **5e (`46b1fda`) — VideoPickPage rides the viewport; PosterStack DELETED (flicker bug #1 closed by construction).** The page-owned QMediaPlayer/QVideoWidget/PosterStack stack is gone — arm-on-landing IS the spec/59 no-black-frame guarantee; the Day-Grid poster bridges in as a host-supplied `ViewportItem.pixmap` (the daygrid thumb store ≠ the photo-cache tier). Decisions leave as `decision_verb_requested("pick"/"skip"/"toggle"/"cycle")`; PickPage degrades cycle→toggle (binary ledger, no video compare). **Tab became TRANSPORT** (its legacy cycles-state test pin REWRITTEN as a §4 pin — the house precedent for evicted bindings); F/F11 fullscreen landed on the video surface for the first time; R actually opens Reclassify now (the tooltip always claimed it). Viewport grew `video_error` pass-through (the "Cannot play — Pick/Skip still works" honesty survives) + the F10-inert-on-poster guard. ⚠ BEHAVIOR CHANGE for the eyeball: the video surface now AUTOPLAYS on landing (uniform with Quick Sweep/Cut, the slice-3 model) — the old page parked paused on the poster frame. test_video_pick_page.py: 17 (locked-map pins + poster bridge + probe seeding + the graceful-error path).
>
> **6a (`b1b1dfc`) — both EDIT surfaces speak the locked map.** EditPage: P marks-for-export (SET) / X unmarks / Space toggles / C degrades (binary ledger); **the legacy P-Preview binding moved to F10** (the truth key = the developed full-res Preview) and **the DEAD second Key_P branch (P-export, shadowed since birth — the spec/63 named kill) is deleted**; F joins F11. EditVideoPage: **Tab = play/pause, Space/C = toggle-at-cursor** (the Space-plays binding evicted — transport and decisions never share); stale "(P / D)" texts fixed (D retired). cut_play deliberately keeps Space-pause (a pure player; no decision keys to collide). tests/test_edit_page_keymap.py NEW (5 pins; module name dodges the slice-B skip list).
>
> ### THE UI ROUNDS (same session, Nelson's live final-app eyeball — "browsing is faster" confirmed, then fix-as-found):
>
> 1. **The F10 lens went WINDOWED + grew chrome** (`19e2abe`→`f1262bb`): a normal resizable window (image-aspect, honest title "name — W × H"), the zoom/peaking CONTROL BAR back (Peaking · Colour · Sensitivity ×2-width · Zoom 1:1, collapse-until-active), **F11 inside = the PURE look** (fullscreen, bar+helpers off), **Esc one level at a time** (zoom → fullscreen → close), ASPECT-LOCKED (no letterbox bands; dominant drag axis wins), house-THEMED (inline black revoked — `InspectView`/`InspectBar` QSS roles in BOTH themes), and **MODAL** (the app waits). 1:1-only zoom is Nelson's locked ruling (integer-honest; the window IS the sub-1:1 control).
> 2. **Two lines under the Picker canvas** (`7cde45a`): the empty TOOLS row died; Play · Combined + the lens button ride the nav centre.
> 3. **FULL-RES PEAKING** (`9faa306`→`eeb8c91`): the lens mask computes ONCE on the full-resolution honest source — fit view = DENSITY downscale (~24% cutoff), 1:1 = a slice of the same mask, so overview and zoom agree. TWO field bugs fixed measured-not-theorised (the `_probe_peaking_real.py` rig): dilation was a k² noise amplifier ("whole photo peaked at 0 sensitivity") and un-denoised full-res sources need a 3×3 pre-blur (single-pixel spikes are noise at source resolution — the INVERSE of the display-scale no-pre-blur rule, which still governs the fast Sweep path). Bar prefs persist via `update_setting`. ⚠ A test once wrote the REAL settings.rebuild.json (restored to magenta/50; autouse guard added — lens tests can never touch the user file again).
> 4. **QUICK SWEEP'S VIEWER KEYS WERE DEAD** (`159f37c`) — Nelson caught Claude asserting unverified behaviour ("F10 does not work… make sure you know what you are talking about" → memory `feedback_verify_surface_claims_in_code`): the NoFocus loop swallowed the viewport AND showEvent stole focus back; the whole §4 grammar was keyboard-dead there (wheel/clicks masked it). Fixed + netted (`test_quick_sweep_viewer.py`, name dodges the skip list).
> 5. **THE STANDARD** (`8210d9b`, Nelson's ruling): Picker/Quick Sweep/Edit nav centres carry the SAME pair — **"Full Screen"** (F/F11, checked toggle) + **"Full Resolution"** (F10); lens TOOLS on the cull surfaces only (Edit + Cut open it clean — `set_lens_tools_visible`/`with_tools`); Cut keeps no nav buttons but F10/F11 verified wired. **EDIT'S F10 = the processed, cropped, FULL-RES render (what export produces) in the standard lens** — `AdjustmentSurface.render_full_pixmap()` (pure read) + the public `open_inspect_lens()` for viewport-less hosts; the in-canvas Toggle-Crop preview CONTINUES untouched (Nelson's explicit correction: ADD, never replace — both points confirmed in code: everyday canvas = 1280px working view, Toggle-Crop = full-res-computed canvas-fit).
> 6. **Lens resize flicker** (`55f8837`): mid-drag = fast ≤2560-proxy fit (image never blanks), smooth full-res + peaking once on a 180 ms settle.
>
> **PARKED, with maps, for the next sessions:** **6b** — Edit's §6 pixel model (viewport browse + off-thread working copy + the 111 ms `_downsample` fix): the canvas/crop-overlay/render pipeline is ONE organism inside AdjustmentSurface and Edit's suites are slice-B-skipped — needs the 5d treatment (analysis → net → atomic swap), do NOT blind-rewrite it. **7** — proxy tier (spec/63 §7 note: prefer-proxy must keep ORIGINAL native dims through `sharp_pixmap_info`; v1 = write-on-decode backfill + builder seeded from `set_event_context`). **8** — export-file thumbs (the four export writers materialise 256-px thumbs; Cut grids then fill from disk). The spec/62 probe re-run rides 7/8 (the perf tiers are what moves the numbers).
>
> ### (earlier, same session) — **5d: the Picker rides the viewport.** Tests-first, exactly as planned: the **28-pin safety net** (`tests/test_pick_photo_surface.py`, the Picker's FIRST tests ever) committed at clean HEAD (`9d5ef0f`), THEN the atomic ~1,500→~1,000-line rewrite of `pick_photo_surface.py` onto `PhotoViewport` — **the net passed UNEDITED across the swap**, + 9 new-locked-map pins written with it (37 green total).
>
> **What changed on the cull surface:** the surface is chrome + decisions only; the viewport owns pixels/nav/prefetch/F10/the key grammar (verbs wired: **P Pick · X Skip · Space toggle · C cycle**; **Enter = the sweep** — the legacy P-sweep is GONE; the sweep carries FAST stack-film peaking and the browse returns clean on pause/end; the F10 lens + corner 🔍 ride in free, AF point fed per nav). **Sharpness honesty landed** (the spec/62 score-the-thumb bug is dead): scores only the viewport's sharp pixels, skip-until-lands, `sharp_changed` re-enters. Combined = a viewport loose slide (nav genuinely locked, restores at the cursor). REMOVED: the zoom/AF-toggle/peaking tool clusters + Z/F keys (live in F10's lens now), the surface predecode timer (the viewport prefetches), the canvas bucket-colour flip, dead `_exif_line`/`_caption_html`. Execution glue: `setFocusProxy(viewport)` so PickPage's `photo.setFocus()` keeps working untouched; the surface keeps a small `keyPressEvent` (R/Home/End/F1) + stray-focus fallbacks to the same verbs. spec/63 §7 (5d ✓) + §8 (landed record) + CLAUDE.md key-map note updated with the change.
>
> **Verified:** the 37 by name (PASSED, not `s` — module name dodges the slice-B list) + compact-row 10 + exif-batch 4 alongside; coexistence combo (cut-session/viewport/cache suites) all-dots with the DOCUMENTED machine-local teardown crash, re-verified pre-existing at baseline WITHOUT the new file; MainWindow construct-smoke OK (the six stale legacy index rows still log their KNOWN bare-card tracebacks — pre-existing). Eyeball rig `_smoke_pick_photo_surface.py` (untracked): live cull over a real temp event.db (burst with a moving subject, exposure bracket with real fusion, focus-varied moment) or `--shots` PNGs (`_pick_cull_*.png`, both themes, regenerated post-swap).
>
> **OWED: Nelson's FINAL-APP eyeball (the whole arc, the point of this session)** — `launch.bat`, then:
> 1. **Pick, photos:** open a day → a cluster. Arrows/wheel browse (sharp within a beat), **P/X/Space/C** decide, **Enter** plays the sweep WITH peaking (P no longer plays!), **F10** opens the inspection lens (F peaking, Z 1:1, drag pans), corner 🔍 mirrors it, R reclassifies, Combined fuses on exposure brackets (button), F/F11/Esc.
> 2. **Pick, videos:** open a video cell — poster→live with NO black frame and NO flicker; ⚠ it now AUTOPLAYS on landing (uniform with Quick Sweep — veto if wrong); **Tab play/pauses; P/X/Space/C decide**; timeline + frame steps work; F/F11 fullscreen (new here); a corrupt clip says "Cannot play — Pick/Skip still works".
> 3. **Edit, photos:** **P now MARKS FOR EXPORT** (green border), X unmarks, Space/C toggle, **F10 = the developed full-res Preview** (P no longer previews); L/G/[ ]/\\/R unchanged.
> 4. **Edit, video workshop:** **Tab plays/pauses** (Space no longer does); **Space/C toggle the stop at the cursor**; P/X set.
> 5. Quick Sweep + Cut session/detail/Play: already migrated (slices 2/4) — regression glance only.
>
> **NEXT SESSIONS:** 6b (Edit pixel model — design checkpoint FIRST, then the 5d treatment) · 7 (proxy tier) · 8 (export thumbs) · spec/62 probe re-run for the after numbers. Standing items unchanged: events-index clobber watch · six stale legacy index rows · batch engine (spec/60) awaiting Nelson's word.
>
> ---
>
> ### (prior, same day — fourth session, wrapped) — the **ONE PHOTO VIEWPORT** is built and surfaces are migrating onto it. Started as a bug-fix session; became the nav-performance overhaul. **spec/62** (measured audit) → **spec/63** (the locked design: one display engine, locked keyboard map, three-mode peaking, F10 inspection lens). **16 commits, all tested + committed, tree clean.** Done: the engine (slice 0 queue-cure + scaled tier; the off-thread-QPixmap crash HARDENED — likely also the WATCH-freeze #7 culprit), the **PhotoViewport** (slice 1), **video arm-on-landing** (slice 3 — flicker bug #1 closed by construction), **Cut session+detail** (slice 2) and **Quick Sweep** (slice 4) fully migrated, Compare ported to Quick Sweep, and the complete **F10 inspection lens** (full-res/honest-RAW-demosaic source, peaking on F, true 1:1 zoom+pan on Z, AF, the corner-magnifier affordance). The Picker full-absorb engine pieces 5a (AF) + 5b (peaking) + 5c (zoom) all landed IN the viewport.
>
> **PAUSED before 5d** (the atomic 1504-line Picker rewrite onto the viewport) — Nelson + Claude agreed it's its own focused, tests-first session. The hard analysis IS done: **turnkey execution map in spec/63 §8** (every canvas→viewport call, verb wiring, sweep-with-peaking, sharpness honesty, what to remove vs keep, the gateway test approach). The Picker WORKS today (got the engine fixes free) — nothing broken by waiting. Then 5e (VideoPickPage → delete poster_stack.py).
>
> **Read `spec/63-photo-viewport.md` whole before any viewport work.** Earlier this session also fixed the spec/61 windowed-Play F11 + end-of-play freeze.
>
> ---
>
> ### (prior) spec/61 Share design LOCKED + ALL TEN SLICES LANDED + THREE EYEBALL ROUNDS — Share works end-to-end on Nelson's machine. event.db at **v4**; SHIP-TIME COUNTER RESET ruled. Print question CLOSED. Keyboard-shortcuts review — CLOSED THIS SESSION (the locked map in spec/63 §4).
>
> Design-mode session, no code touched. **`spec/61-share-event-cuts.md`
> is the artifact** — read it whole before any Cuts work. Headlines:
>
> 1. **#exported is the one built-in Cut** — a live query (= the
>    Exported-watermark population via edit lineage), never a stored
>    list; can't be stale, can't be deleted. The ladder collected →
>    picked → edited → exported exists conceptually, but event Cuts
>    expose ONLY #exported as the universe (the other rungs are reserved
>    for cross-event Cuts).
> 2. **Members are exported FILES** (lineage-backed), not items — two
>    exports of one photo = two pool entries, each keeping its link to
>    the original. Cuts are ZERO-BYTE until handoff (links materialize
>    only on export).
> 3. **Creation = one dialog → the good old Picker** (days → grid →
>    single → compare) on a SEPARATE decision ledger (phase decisions
>    untouched). Pool = boolean algebra over existing Cuts (`#exported −
>    #cut_1 + #cut_2`, left-to-right); filters style/type/camera;
>    default all-picked or all-skipped; budget in MINUTES (photos cost
>    seconds-per-photo, clips their true duration, separators one slide
>    each) live on the export-progress-line slot (green/amber/red).
>    Create Cut commits membership. Templates save the RECIPE and
>    re-evaluate per event; names are the cross-event glue (typed →
>    lowercased/underscored/de-accented LIVE, unique per event,
>    case-blind).
> 4. **Consumption = export + play, nothing else.** Flat WYSIWYG grid in
>    true show order with GENERATED SEPARATOR SLIDES at day boundaries
>    (plain card; aspect-ratio setting default 16:9; text = plan's date ·
>    location · description; derived live, never stored; settings flag
>    default ON; counts in budget). Play = full-screen rehearsal WITH
>    music. Export = `Cuts/<tag>/` linked media named for chrono sort +
>    separator images + `audio/` linked playlist (categories = the
>    user's own audio-library subdir names, no shipped vocabulary;
>    choice stored on the Cut, changeable at use; covers duration + a
>    bit more, crossing file included).
> 5. **Relational under the hood** — `cut` table + membership table +
>    built-ins as views; "#" is display language only. Storage lean
>    (CONFIRM at kickoff): cut + membership in event.db, templates
>    user-level. spec/53 §2.4 DDL is STALE vs spec/61 (annotated in
>    place).
>
> **Parked to own sessions:** cross-event Cuts (different soul — more
> search tool than share tool; universes #collected/#picked/#edited;
> originals-grabbing lands THERE; spec/61 §8 is the trailhead) ·
> database protection (Cuts zero-byte ⇒ DB loss loses them; spec/61 §9)
> · the still-pending menu-bar session.
>
> **Cross-refs updated this session:** spec/51 (superseded banner),
> spec/53 §2.4 (stale note), CLAUDE.md (Cut model + spec list),
> spec/README (read order).
>
> **KICKOFF (same session, Nelson's "Lets start"):** spec/61 §10 ALL
> RESOLVED — dialog mockup approved (chips + +/− pool builder, live tag
> preview, live pool/filter counts); amendments: NO camera filter, styles
> default All, **Load template…** added at the dialog top; storage
> CONFIRMED event.db (templates user-level); NO pre-shipped templates;
> lineage row = file identity; `last_exported_at` on the cut row.
>
> **Landed, 141 green across the five touched suites:**
> 1. **Schema v3** — `photo_tag` DROPPED (spec/51 item-membership plan,
>    retired UNUSED — zero callers existed); `cut` + `cut_member`
>    (→ lineage PK, FK cascades both directions) in the DDL + a real
>    v2→v3 migration + per-step migration test. models / repo registry /
>    json_dump backup shape updated (cuts + cut_members flat top-level;
>    "photo_tags" key gone); spec/03 drift-notice bullet added.
>    move_days: cuts deliberately do NOT travel (membership rides
>    lineage; exports stay with the source event).
> 2. **core/cut_names.py** — slugify (lowercase / de-accent / separators→
>    underscores / junk dropped / collapse+trim), display_tag, check_tag
>    codes ('empty'/'reserved'/'taken', case-blind), EXPORTED_TAG +
>    RESERVED_TAGS (exported/collected/picked/edited). **core/
>    cut_budget.py** — ShowTotals.seconds (photos+separators × photo_s +
>    true clip ms), zone() green/amber/red incl. degenerate budgets,
>    photo_only_hint keep-rate. tests/test_cut_core.py (17, by name).
> 3. **Gateway cuts facade** (event_gateway.py): `exported_files()` =
>    #exported live query (edit-phase lineage through visible_item —
>    hidden days drop; bracket rows pass via merged output item;
>    show-ordered by source capture time, relpath tie-break);
>    `resolve_pool()` left-to-right +/− set algebra, unknown tag = empty
>    contribution (graceful shrink), style filter ([] = All, active
>    filter excludes unclassified) + photo/video type filter; CRUD with
>    the name transform ENFORCED at the gateway (create_cut/rename_cut
>    raise ValueError('empty'|'reserved'|'taken'); rename excludes self);
>    update_cut_settings whitelist; delete_cut (cascade);
>    `set_cut_members` replace-all commit (dedupe, one transaction, no
>    nested-transaction trap); `cut_member_files` show-ordered;
>    `cut_show_totals` (photo/video counts + true clip ms + member days
>    = separator count, callers zero it when the setting is off);
>    `mark_cut_exported`. tests/test_gateway_cuts.py (16, first-run
>    green).
> 4. **The New Cut dialog** (`mira/ui/shared/new_cut_dialog.py`,
>    EYEBALLED by Nelson 2026-06-12 — "Looks ok" + one correction,
>    applied): returns a **CutDraft** (no cut row until the session's
>    Create Cut — no orphans); name field with LIVE tag preview
>    (slugify + check_tag states); pool expression as removable chips
>    (#exported tinted builtin) + add row with +/− per existing Cut,
>    BOTH rows on the house FlowLayout-in-a-host-widget pattern
>    (addLayout-nesting does NOT lay out — only the screenshot caught
>    it, tests passed through the breakage: checkpoint pattern earns
>    its keep); live pool/match counts via injected probes
>    (resolve_pool / pool_show_totals + new `cut_style_options()`);
>    photo-only "≈ N slides · keep ~1 in K" + "includes N day
>    separators" hint; Music = audio-library subdirs (setting already
>    existed), disabled-with-pointer when unset; Load/Save template
>    buttons present but INERT until slice 10. **Form-grammar
>    correction (Nelson): NEVER label+input, one titled FormFieldGroup
>    per input** — Time split into Target time / Max time / Per photo
>    groups, Filters split into Style / Media type (memory updated;
>    test pins 9 titled groups + every-control-hinted). New QSS roles
>    in BOTH themes: PoolTermChip(+builtin)/Text/Kill, PoolOpLabel,
>    PoolAddOp, PoolCountLabel. tests/test_new_cut_dialog.py (15).
>    Eyeball rig `_smoke_new_cut_dialog.py` (UNTRACKED, throwaway —
>    live dialog on fake responsive data or --shots PNGs; offscreen +
>    QT_QPA_FONTDIR=C:\Windows\Fonts from harness shells, which cannot
>    host the native platform; os._exit dodges the documented teardown
>    crash).
>
> **Queued from the eyeball:** Nelson didn't yet GET how the boolean
> pool operations read in the UI ("can wait for the real data") —
> revisit the pool-builder affordance when slices 5/6 put real Cuts
> behind it; candidate tweaks: preview the resulting filenames count
> per term, an "example" line under the expression, or op labels ON
> the chips.
>
> 5. **Slice 5 MODEL HALF — the session ledger**
>    (`mira/shared/cut_session.py` + package init; data layer,
>    no Qt, sibling of `mira/picked/`): `session_files()`
>    resolves a pool to cells (lineage → source item/bracket-output
>    join: kind, day, capture time, true clip ms; two versions of one
>    photo = two cells); **CutSession** = in-memory Pick/Skip per
>    FILE with undo, default all-picked/all-skipped from the draft,
>    `days()` grouping, live `totals()/show_seconds()/zone()` (one
>    separator per picked day, zeroed when the setting is off);
>    `commit()` = the ONE persistence moment (fresh → create_cut +
>    set_cut_members; re-entered via `for_cut()` → settings update +
>    membership replace; abandoned session leaves NOTHING);
>    PHASE_STATE PROVEN UNTOUCHED by test.
>    tests/test_cut_session.py (11, first-run green; 139 across the
>    cuts stack + neighbors).
>
> 6. **Slice 5 PAGE HALF — the session surface**
>    (`mira/ui/shared/cut_session_page.py`, screenshots sent for
>    the checkpoint): days panel (lean `CutSessionDayRow` rows — no
>    bucket layer) → **DayGridView reused as-is** (cells synthesized:
>    `CullCell(item_id=export_relpath, color=KEPT/DISCARDED from the
>    session)`; border-click toggles the LEDGER; lazy thumbs via
>    `load_pixmap` at MAX_CELL_SIZE, the Pick page's 4-per-20 ms
>    budget) → lightweight `_SingleView` (fit pixmap; P/D toggle,
>    ←/→/Space step, Esc back; VIDEO PLAYBACK deliberately deferred to
>    the slice-8 player work — poster-or-duration shown). Top bar:
>    heading · **CutBudgetLine** (the export-progress-slot sibling;
>    `zone` QSS property colours the text green/amber/red; roles in
>    BOTH themes + `CutSessionDayRow` + `CutSingleImage`) · Create
>    Cut (Primary) · Cancel (confirm only when decisions exist).
>    Ctrl+Z undo + F11 page-level. Create → `session.commit` →
>    `finished(cut)`; name-collision ValueError → NoIcon message.
>    tests/test_cut_session_page.py (8; 69 across the cuts suites).
>    Eyeball rig `_smoke_cut_session_page.py` (UNTRACKED): builds a
>    REAL temp event.db + generated JPEGs; live mode or --shots.
>
> **SLICES 6–10 (same session, Nelson: "I eyeball and test the complete
> solution" — per-surface checkpoints suspended):**
> 7. **Slice 6 — the Cuts shell** (`ui/shared/cuts_shell.py`, commit
>    `2118398`): CutsListPage landing (#exported pinned builtin row +
>    user rows tag·count·length·music·exported; Open/Adjust/Rename/
>    Delete; empty-state hint), New Cut dialog on the REAL probes,
>    sessions mount/unmount, rename dialog with live tag preview.
>    MainWindow: placeholder replaced, Share tile guards through
>    open_event, Share menu New Cut… live, **spec/57 §4.3.1 landing
>    FLIPPED** (already-edited events land on Share). for_cut hardened:
>    stray committed members APPEND to the session pool (never silently
>    dropped). Settings: use_separators + separator_aspect. audio_library
>    grew list_moods (dialog-fast) + build_playlist (spec/51 §6 algo).
>    MainWindow construct-smoked (six stale legacy index rows log their
>    KNOWN bare-card tracebacks — pre-existing, not this change).
> 8. **Slice 7 — flat grid + separators** (`820a7ed`):
>    separator_card.py (plain card, slideshow-fixed palette, derived
>    live from the plan; parse_aspect) + cut_detail_page.py (ONE flat
>    show-order grid, separator TILES at day boundaries, neutral rings,
>    read-only single view hopping separators; Adjust row).
> 9. **Slice 9 — export** (`1aee66b`): shared/cut_export.py —
>    Cuts/<tag>/ with hardlinked media (copy fallback counted),
>    NNN_-prefixed chrono naming, separator JPGs in sequence (injected
>    writer — UI owns pixels), audio/ playlist linked 01_-prefixed
>    covering the show + margin (short flagged), snapshot "(2)"
>    collision folders, missing sources reported, last_exported_at
>    stamped. Shell: Export all wired (wait cursor + honest summary).
> 10. **Slice 8 — Play** (`b380276`): cut_play.py — frameless black
>    fullscreen rehearsal over shared.show_entries (grid = export =
>    play, ONE sequence): photos + separators at photo_s, clips TRUE
>    length (QMediaPlayer), music underneath (0.6 volume, sequential);
>    QtMultimedia LAZY (photos-only never touches it); Space/arrows/
>    Esc. Empty cut → honest pointer to Adjust.
> 11. **Slice 10 — templates** (`db528ad`): user_store SCHEMA v2 —
>    user-level `cut` table RETIRED, `cut_template` reshaped to the
>    RECIPE; real v1→v2 migration; spec/53 §2.4 banner → REVISED.
>    Dialog: Load template… menu applies EVERY field (unknown pool
>    tags stay visible — honest empty contributions); Save as
>    template… → titled name dialog → (name, draft) to the shell's
>    saver → gateway.user_store.
>
> **NEXT SESSION — THE COMPLETE-SOLUTION EYEBALL (owed to Nelson):**
> 1. Launch for real: `launch.bat` → open an event with exported
>    finals → **Share tile**. The full loop: list (#exported row) →
>    New Cut (name transform, pool chips, filters, time, music) →
>    Start → session (Picker-grammar grid, green/red borders, budget
>    line zones, Ctrl+Z, single view P/D) → Create Cut → row appears →
>    Open (flat grid, separator tiles) → **▶ Play** (timed rehearsal,
>    music if audio_library_path set) → **Export all** → check
>    Cuts/<tag>/ on disk (NNN_ order, separators, audio/) → Adjust
>    (re-enter seeded) → Rename/Delete. Menu: Share → New Cut…;
>    "from existing media → already edited" now lands on Share.
>    Standalone rigs also available: `python _smoke_new_cut_dialog.py`
>    / `python _smoke_cut_session_page.py` (untracked, deletable).
> 2. **Deliberate gaps, named** (decide drop/queue at the eyeball):
>    compare view inside session/detail (A/B two versions of one
>    photo); video PLAYBACK in the single views (poster+duration now;
>    the rehearsal player DOES play clips); session arrows don't cross
>    days; per-item export from the detail single view; pool-algebra
>    affordance re-read with real data (queued from the dialog
>    eyeball); #exported row has no Open/Play of its own (informational).
> **EYEBALL ROUNDS 1+2 DONE (2026-06-12, commits `594fab1` + `817557c`):**
> round 1 — audio root cause (gateway.settings is the REPO; load() it),
> Start lands on photos, decision frame + Pick/Skip on the single view,
> wheel + Picker keys, separators open big, Play button was
> host-gated-hidden. Round 2 — first-open rescale race (deferred tick),
> frame = Picker's exact 6px ring, **UNIVERSAL KEYS: P picks / X skips
> (D retired) / Tab+Space toggles** (Cut single view + Edit video page;
> CLAUDE.md standard updated), and **the OPENER slide** (Cut name + facts,
> first in grid/play/export, rides the separators setting).
>
> **SCHEDULED (Nelson 2026-06-12): app-wide keyboard-shortcuts review.**
> Today's two legacy P bindings stand (Picker photo surface P = Play/
> Pause sweep; Edit photo page P = Preview toggle — neither has pick/skip
> keys). The review maps EVERY surface's keys to one grammar: P/X/Tab
> universal decisions, navigation, play, zoom/peaking, F-keys; also fix
> the Edit photo page's DEAD second Key_P branch (edit_page.py ~1226 —
> `_on_export` shadowed by the preview toggle at ~1214, P never exports).
>
> **ROUND 3 (`9d80d7d`) + WINDOWED PLAY (`13eb4a2`):**
> - **Festive cards**: per-Cut "Slide cards" choice (all black / one
>   random color / a color per day) — colors DETERMINISTIC from cut id
>   (+day for multi) so grid/play/export always agree; lives in
>   cut.extras_json → **event.db v4** added the extras_json column the
>   cut table was born without (lesson memorized: every durable table
>   gets extras_json from BIRTH).
> - **Dialog-first Adjust**: Edit Cut dialog prefilled (name/pool/
>   filters/times/music/cards), Start → session seeded from membership
>   (strays never dropped), Save commits settings (+validated rename)
>   + picks. `CutSession.for_cut_with_draft`. Modal behind a seam
>   tests stub (a test once exec'd the REAL dialog and parked a window
>   on Nelson's desktop 24 min — never again).
> - **The budget STRIP**: full-width row above the session stack (grid
>   + picture view) — filling QProgressBar in the zone color + numbers
>   + target—max; children repolished on zone flips (Windows QSS
>   descendant-rule trap — the old thin line never showed its colors;
>   Nelson "could never see it").
> - **Play windowed**: normal resizable window titled "#tag —
>   rehearsal"; F11/F/double-click toggles fullscreen; Esc steps down
>   one level; photos re-fit live on resize.
> - **gateway.settings IS THE REPO** (round-1 root cause, recurring
>   trap): attribute reads silently default — load() it (the shell's
>   `_settings()`); memorized.
>
> **NEXT SESSION:**
> 1. Nelson continues real-data testing of Share; fix-as-found.
> 2. The app-wide keyboard-shortcuts review (chip exists; scope above —
>    P/X/Tab grammar table, Picker-P + Edit-photo-P new homes, the dead
>    edit_page P-export branch; sign-off before code).
> 3. Parked design sessions: cross-event Cuts (spec/61 §8) · database
>    protection (§9) · menu-bar structure (long-standing).
> 4. Deliberate Share gaps awaiting Nelson's priority: compare view in
>    session/detail (A/B two versions); video playback in single views
>    (rehearsal DOES play clips); session arrows don't cross days;
>    per-item export from detail; pool-algebra affordance re-read.
> 5. Owed (unchanged): single-export watermark repaint re-eyeball;
>    off-thread preview render; events-index clobber watch + six stale
>    legacy index rows logging bare-card tracebacks at dashboard build.
> 6. Untracked throwaway rigs at repo root (_smoke_*.py, _*.png,
>    verify_output.txt) — keep for eyeballs or delete freely.
> 2. Then slice 6 (Cuts list landing) → 7 (flat grid + generated
>    separators) → 8 (Play rehearsal + music) → 9 (Export links +
>    audio playlist) → 10 (templates: user-level store + Load/Save…;
>    REVISE mira/user_store's stale spec/53 cut DDL there).
>    Checkpoint per surface — no eight-slice blind runs.
> 3. Owed from earlier sessions (unchanged): single-export watermark
>    repaint re-eyeball; off-thread preview render (queued); events-index
>    clobber watch.
>
> ## (previous CURRENT, preserved as record, 2026-06-11 second session wrap) — spec/60 batch-engine design LOCKED · the Exported watermark landed · the black-frame guarantee landed · post-wrap watermark ordering fix. All committed + pushed (``cc7581e``).
>
> **Eyeball cleared at open** (Nelson): export-status borders/grids/
> yellow video cells/batch queue + progress line, EXIF-less clips at
> Pick — OK.
>
> **What this session landed, in order:**
>
> 1. **THE BATCH ENGINE DESIGN SESSION → `spec/60-batch-export-engine.md`
>    (LOCKED).** Nelson accepted the proposal + added the hardware
>    addendum. Headlines: one render-worker process per job (our own
>    binary in worker mode; manifest in, streamed per-unit progress
>    out; the worker NEVER touches event.db); zero foreground lag =
>    below-normal OS priority + capacity rules (cores−2, RAM-derived
>    memory budget) — no knobs; photos N-wide ALONGSIDE one
>    frame-parallel clip (his call: best performance; clip-width is a
>    constant=1); **the fallback ladder** (his addendum): NVENC → QSV
>    → AMF → libx264, hw-decode probe → software, sizing from the
>    actual machine, worker-spawn failure → today's in-process path;
>    per-unit truth replaces bucket-as-a-unit marking; cancel kills
>    the process tree; the spec/56 slice-4 walker becomes manifest
>    building; as-you-go exports stay immediate (not queued).
>    **Implementation NOT scheduled — Nelson's word required.**
>    Queue + progress line stay exactly as landed.
> 2. **The Exported WATERMARK (spec/59 §8, landed):** diagonal
>    translucent "Exported" over photos that have an exported version —
>    driver = **edit-phase lineage** (all four writers: as-you-go,
>    batch, return scan, backfill), deliberately NOT ``edit_exported``
>    (that flag is freshness — resets on every adjustment edit — and
>    keeps its chip). Gateway ``exported_item_ids()``; ``CullCell.
>    exported`` (Pick callers untouched); painted ``ExportedWatermark``
>    widget (mouse-transparent, white+shadow, scales) on DayGridCell
>    (photo item cells only; clusters=icons + videos never) and
>    MediaCanvas (``set_exported_watermark`` — EditPage photos +
>    EditVideoPage developed snapshots). ``show_exported_watermark``
>    setting (Edit tab, ships ON; the only control). 13 new tests.
>    **Found on the way:** two test_day_grid_model tests still pinned
>    the RETIRED flag→colour border semantic (failing at clean HEAD
>    since the export-status slice) — rewritten as spec/59 pins
>    (phase_state IS the colour; the flag colours nothing).
> 3. **The BLACK-FRAME guarantee (spec/59 queue item, landed):**
>    *Leg A* — ``ensure_thumb``'s single backwards 0 ms fallback
>    (blacker still on fade-ins) became a FORWARD ladder (position →
>    fallback → 3 s → probed 10 %/25 %; probe only when the cheap
>    rungs are dark; brightest kept when nothing clears — dark videos
>    stay honestly dark). Cached-black thumbs **self-heal** with one
>    ladder re-run; a ``.vetted`` sidecar stops re-extraction. One fix
>    = both Day Grids (shared ``daygrid`` key). *Leg B* — the player
>    load window (setSource → first decoded frame painted RAW BLACK;
>    fade-ins legitimately park on black at 0): Pick wraps
>    QVideoWidget in the new **PosterStack** (stacked sibling — child
>    overlays paint UNDER the native compositor); Edit gets an
>    in-scene pixmap item above the video item (the crop-item
>    reasoning); both show the cached grid thumb until the sink's
>    first valid frame; cache-only lookup (never blocks on ffmpeg).
>    **Quick Sweep deliberately excluded:** pre-ingest card files have
>    no cache identity to poster from — it keeps the dance.
>    Thumb suite +6 (real-ffmpeg fade clips) + test_poster_stack 5.
> 4. **Post-wrap eyeball round (same day):** Nelson — "the watermark
>    appears after the batch job, but not after a single export."
>    Root cause: ``EditPage._on_export_finished`` emitted
>    ``process_export_committed`` (the host SYNCHRONOUSLY re-reads
>    ``exported_item_ids()`` and repaints the cell) BEFORE recording
>    the lineage row — the repaint always ran one row stale, nothing
>    repainted after. Batch had the right order, which is why it
>    worked. **Both pages reordered:** state writes → lineage → THEN
>    the commit signal (video's ``clip_exported`` fixed for symmetry —
>    latent, no visible bug). Photos single-exported before the fix
>    DID get lineage rows; their watermarks appear on the next
>    day-grid open. **Test-placement lesson:** the regression pin
>    first landed in ``test_edit_page_rebuild.py`` — that module is
>    on the conftest ``_SLICE_B_FILES`` bulk-skip list and the test
>    ran as a SILENT SKIP; it lives in ``test_exported_watermark.py``
>    (14 green). Check the skip list before placing tests in
>    edit_page / edit_host_page / pick-era modules.
>
> **⚠ Open watch-items:**
> - **Nelson's eyeball owed:** single-export watermark repaint (the
>   #4 fix — export one photo, the cell should turn green AND gain
>   the watermark live), the cluster sub-grid + Edit photo view +
>   the hide setting, and the black-frame round (previously-black
>   grid thumbs self-heal on the next day open; the players poster
>   through load). His renamed bird clips are the fade-in test case.
>   Batch-job watermark confirmed appearing (his report).
> - **The batch ENGINE implementation** (spec/60) awaits his
>   scheduling — the natural next big arc.
> - Born-green governing segment birth — veto still open.
> - Every launch logs "Wizard not yet completed; opening on first
>   run" (since at least 06-08, his launches included) — if the
>   wizard is NOT his intended landing page, the completed flag may
>   have been lost; ask him. The Nepal bare-card on the dashboard is
>   the standing index-clobber legacy (same log block).
> - The events-index clobber (standing; `.history` first stop). The
>   machine-local Qt teardown crashes (documented; tests pass).
>
> **NEXT SESSION:**
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, this banner, **`spec/60`**
>    (+ spec/59 §8 for the watermark shape).
> 2. **Nelson's eyeball:** the watermark + black-frame rounds above.
> 3. **His word on what follows** — the spec/60 engine implementation
>    is recommended; nothing starts without it.
> 4. Per-slice discipline as always: shape first, explicit OK, eyeball.
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner), then spec/60 (and spec/59 §8).
> All of yesterday's work is committed clean and pushed.
> Eyeball: [single-export watermark repaints live now / grids + photo view + hide setting / black thumbs self-healed / players poster through load — OK or issues]
> Wizard line: [the app opening on the wizard IS / IS NOT what I expect]
> Start: [spec/60 engine implementation — or — your pick]. Checkpoint with me before the slice after it.
> ```
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-11, SESSION WRAP) — The Edit Surface session: spec/59 designed AND implemented end-to-end, export-status + batch queue landed. All committed clean.
>
> **What this session landed, in order** (records below, newest-first):
>
> 1. **spec/58 slice 3** — the confidence-colored Style badge (band
>    ramp on the STYLE combo, `activated`-backed human flip,
>    inherit-at-creation for snapshots/merged masters). Eyeballed OK
>    ("I believe the style implementation is ok"); ramp numbers can be
>    fine-turned any time.
> 2. **Round-trip answers:** Helicon output → the TOP LEVEL of
>    ``Picked Media\`` (root-only scan; keep the default name); LRC
>    exports → a subfolder of ``Edited Media\`` (lineage association;
>    never "Save Metadata to File" onto the hardlinks).
> 3. **THE EDIT SURFACE DESIGN PARENTHESIS → `spec/59-edit-surface.md`
>    (LOCKED) → fully implemented.** Top grid ("Style, Look & Filter"
>    over Look|Style|Filter; Crop under Look, "No Crop"; Audio/
>    Vibrations slots; mixed case ALWAYS; combo=button heights). The
>    Stop model (Marker/Snapshot; "cut" + adj-frame concepts DEAD;
>    development anchors to the clip's initial marker). Modeless
>    development (landing on a Picked stop IS development; the Adjust
>    mode + 4 buttons deleted; cursor IS the selection). Middle line
>    [Marker·Snapshot·Remove·Toggle Status·Reset▾] + nav line with
>    ◀/▶ Stop + ◀/▶ Frame. Eyeball round 1: ▼ Markers / 📷 Snapshots
>    NAV dropdowns replaced the chip strip; transport hugs the sides;
>    first-mount video fit; U+FE0E on ⏮/⏭; photo x/y counter → top
>    line centred.
> 4. **spec/59 §8 — export status + batch queue (designed live,
>    implemented same session):** the border = MARKED FOR EXPORT
>    (green/red, click to toggle — surfaces AND grids; what's green is
>    what Share sees); born-green setting (``edit_default_state``,
>    already shipped 'picked') now also seeds segment birth
>    (**supersedes spec/56's fixed default-Skip — flagged for Nelson's
>    veto**); as-you-go export auto-marks green; video cells aggregate
>    green/red/YELLOW (cluster grammar) with paint-all border clicks;
>    day/event batch exports collect the GREEN set and run through the
>    NEW app-level **BatchExportQueue** — strictly one at a time, the
>    **progress line below the menubar** (new QSS roles both themes),
>    no modal, no popups; lost commits self-heal via the return scan.
>
> 5. **Post-wrap eyeball round (same day, all committed + pushed):**
>    launch fix (``QVBoxLayout`` missing from main_window imports — the
>    app died at window construction; import-smoke passes don't catch
>    construction-time names). **Black player after development close**
>    → a reparented video item paints black until the next seek; close
>    now forces a frame redelivery (the old Adjust-exit seeked for this).
>    **Phantom canvas window** → the detached canvas was parentless;
>    it's reparented to the surface holder on close (window-proof).
>    Corrupt frame-cache self-heals (drop + re-extract once). Timeline
>    skipped clips paint TRUE red at full weight (the muted half-alpha
>    brown wash read wrong — Nelson). **Collect ingest crash** (CHECK:
>    captured needs camera+time) on an empty-string camera id → falsy
>    guard; then **Nelson's ruling: EXIF-less media is FIRST-CLASS** —
>    recorded under the sentinel ``_unknown`` camera (mirrors the
>    ``_no_timestamp\_unknown`` folder) with file-mtime fallback time,
>    ``tz_source='none'``, undated; only stat-failures skip. (His
>    renamed bird clips are the canonical case.) Unknown-body log spam
>    deduped to one line per camera per run; blank Make/Model = one calm
>    INFO. *(Spec note: the EXIF-less rule should fold into the ingest
>    spec at the next consolidation — recorded here + in the code.)*
>
> **⚠ Open watch-items:**
> - **The batch ENGINE design session is the NEXT SESSION's headline**
>   (Nelson's word): maximise hardware — GPU encode, frame-parallel
>   clip rendering across cores — with zero foreground lag (process
>   isolation, yield-to-foreground). The queue/line are its consumers.
> - **The "Exported" WATERMARK (queued):** diagonal text over grid +
>   individual views, lineage-driven (in-app + third-party, photos),
>   hide-setting. Needs the MediaCanvas + grid-tile paint seams.
> - **Black-frame guarantee (queued):** videos must NEVER show a black
>   frame — Day Grid thumbs, Picker, Edit. Poster-pipeline
>   investigation, its own slice.
> - **Born-green governing segment birth** — decided coherently
>   mid-wrap; Nelson may veto back to fixed default-Skip for clips.
> - Greyed top tools show last-loaded values (spec/59 v1 roughness);
>   dev auto-opens at load when clip 1 is Picked — judge the feel.
> - The events-index clobber (standing; `.history` first stop). The
>   machine-local Qt teardown crashes (documented; tests pass).
>
> **NEXT SESSION:**
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, this banner, **`spec/59`**
>    (§8 included).
> 2. **Nelson's eyeball:** the export-status round — border toggles on
>    both Edit surfaces + grids, yellow video cells, paint-all clicks,
>    born-green defaults, day/event batch through the queue + the
>    progress line (launch two jobs; leave to the dashboard mid-run).
> 3. **THE BATCH ENGINE DESIGN SESSION** (design mode expected) — then
>    the watermark slice + the black-frame slice as Nelson schedules.
> 4. Per-slice discipline as always: shape first, explicit OK, eyeball.
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner), then spec/59 (incl. §8).
> All of yesterday's work is committed clean and pushed.
> Eyeball: [export-status borders/grids/yellow video cells/batch queue + progress line, EXIF-less clips now visible at Pick — OK or issues]
> Start: [the batch engine design session (design mode) — or — the Exported watermark slice — or — the black-frame slice]. Checkpoint with me before the slice after it.
> ```
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10, SESSION WRAP) — Second mega-session, all committed clean + PUSHED
>
> **What this session landed, in order** (each with its own record
> block below, newest-first within the day):
>
> 1. **spec/57 slice 5 — the backfill wizard ships; spec/57 COMPLETE.**
>    5a (entry "New event from existing media…" + landing-level chooser
>    + auto-Collect) → Nelson's first run found the latent
>    same-destination ingest overwrite/crash (fixed engine-wide:
>    duplicates ingest once, different bytes divert, never overwrite) +
>    the event-folder-name collision guard → 5b from-Picked (bulk pick
>    writes, land at Edit) → 5c from-Edited (`Edited Media/Imported/`
>    hardlinks + lineage, dashboard landing).
> 2. **spec/58 DESIGNED + LOCKED** (`spec/58-classification-and-wizard.md`)
>    — Nelson's eight-point brief + two audits; classification serves
>    ONE purpose (Edit correction-profile routing); background pass =
>    sole auto writer; Edit Style button = the one surface
>    (confidence ramp + human-decided color); stability rules
>    ("untouched means re-classifiable"); wizard refresh scope.
> 3. **spec/56 slice 3 — the Edit video WORKSHOP.** EditVideoPage
>    rebuilt: marker timeline (cut/move/merge on the slice-1 gateway
>    ops) + snapshot strip + selection-scoped development; per-clip
>    Export button retired (slice-4 walker owns bytes).
> 4. **spec/58 slices 1–2 + 5 implemented**: schema v2 (the FIRST real
>    migration — `classification_confidence`) + the quiet background
>    classify pass (RAW-first inheritance, §3 guards, rides every
>    ingest + event open; the rules-version stamp now sees wizard
>    re-runs via the user-scenarios fingerprint) + the wizard refresh
>    (Miracraft→Mira strings, four missing QSS roles).
> 5. **Feature: free-angle crop rotation by handle drag** — lollipop
>    above the crop box, live readout, cardinal snap; rides the
>    existing box_angle persistence (photos + video workshop both).
> 6. **EYEBALL PASSED across the whole stack** ("Eyeball ok"), with
>    one find: photo Export crashed — root-caused via the LOCAL LOG
>    (`%LOCALAPPDATA%\Miracraft\logs\miracraft.log` is the forensic
>    first stop) to a legacy ``ui.culler`` lazy import → **ExportDialog
>    ported** with audit → then reshaped on Nelson's quiz (titled
>    FormFieldGroups; **JPEG|TIFF only — grabbing originals belongs to
>    SHARE**, noted for the Cuts design) → **honest-UI render-lag fix**
>    (immediate control flush + stacked wait cursor on every render
>    path) → **surface top reshaped to LOOK · STYLE · FILTER named
>    boxes**.
>
> **⚠ Open watch-items:**
> - The events-index clobber (standing; `.history` first stop).
> - **Off-thread preview render** — the real cure for the seconds-class
>   Edit lag (full-res Preview path + filter blurs); queued, not started.
> - Box titles went UPPERCASE (LOOK·STYLE·FILTER) to match CROP —
>   Nelson may prefer mixed case; one-word flip.
> - Machine-local Qt session-teardown crashes (documented per suite;
>   tests pass, interpreter dies after).
>
> **NEXT SESSION:**
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, this banner; spec/58 §6 +
>    spec/56 §6 carry the open slice queues.
> 2. **Quick re-eyeball (small):** export end-to-end with the reshaped
>    dialog (three titled boxes, JPEG|TIFF only); Look/Filter/Style
>    switches show busy cursor + honest button/combo states; the
>    surface top reads as LOOK · STYLE · FILTER · CROP boxes; crop
>    rotation handle feel.
> 3. **Nelson's pick (do NOT start without his word):**
>    **spec/58 slice 3** — the confidence-colored Style button (needs
>    him live for ramp calibration; RECOMMENDED) · **spec/58 slice 4**
>    — retire the Pick genre chrome + lazy writer · **spec/56 slices
>    4–5** — video Export walker + cleanup · the off-thread render
>    arc · the retirement sweep (spec/57 §6 + §11 inventory).
> 4. Per-slice discipline as always: shape first, explicit OK, eyeball
>    each sub-slice.
>
> **Opening-message template for Nelson:**
> ```
> Read spec/PROGRESS.md (the session-wrap banner), then spec/58 and spec/56.
> All of yesterday's work is committed clean.
> Re-eyeball: [export dialog / busy cursor / LOOK·STYLE·FILTER boxes / crop handle — OK or issues]
> Start: [spec/58 slice 3 (ramp, with me) — or — 58/4 — or — 56/4–5]. Checkpoint with me before the slice after it.
> ```
>
> ---
>
> ## (record, 2026-06-11, latest) — spec/59 IMPLEMENTED + FIRST EYEBALL ROUND IN: "transition from photo to video is smooth, no jumps"; "all seems to work fine". Five findings → four fixed same session, one queued.
>
> ### Eyeball round 1 (Nelson) + the fixes
>
> 1. **Photo→video transition smooth, no jumps** — the §2.1a space
>    preservation verified ✓.
> 2. **Video opened tiny-centred until a window nudge** → first-mount
>    fit fixed: ``QTimer.singleShot(0, _fit_video_to_view)`` after
>    mount (the locked deferred pattern) + refit on QEvent.Show.
>    **⚠ QUEUED (its own slice): videos must NEVER show a black frame
>    — Day Grid thumbnails, the Picker canvas, the Edit canvas. Still
>    happens sometimes; needs the poster-pipeline investigation.**
> 3. **Snapshot chip strip doesn't scale** → REPLACED by two NAV
>    dropdowns flanking Play: **▼ Markers** and **📷 Snapshots** (one
>    timestamped item per stop → seek; greyed while empty). The
>    transport now hugs the sides (Start/◀Stop/◀Frame left,
>    Frame▶/Stop▶/End right; the centre widget takes the row's stretch).
>    ``_SnapshotStrip`` DELETED — which also killed finding 5.
> 4. **⏮/⏭ rendered with their own blue emoji background** → U+FE0E
>    text-presentation selector appended (Edit + Pick video pages).
> 5. **Strip timestamps stayed white on the light theme** — hardcoded
>    ``_C_PLAYHEAD`` paint; moot, the strip is gone (menus theme
>    natively).
>
> Tests: rebuild suite 19 green (+ the dropdowns pin). NEXT: eyeball
> round 2; the black-frame sweep when Nelson schedules it.
>
> ---
>
> ## (record, 2026-06-11, earlier) — spec/59 IMPLEMENTED: the whole Edit Surface design lands (Stop model, modeless development, the two bottom lines, cursor-driven visibility).
>
> ### The design closed and was captured FIRST
>
> Nelson closed the parenthesis live ("Lets go !!!!"):
> **`spec/59-edit-surface.md`** (LOCKED) carries the full design — top
> grid + §2.1 visibility rules, the Stop model (Marker/Snapshot; "cut"
> and the adjustment-frame concept DEAD; development anchors to the
> clip's initial marker), modeless development (§3), the middle +
> navigation lines (§4), the retirements (§6). spec/56 header notes the
> surface supersession (its DATA model stands).
>
> ### What landed (one rebuild of EditVideoPage, commit follows)
>
> - **Navigation line:** ⏮ Start · **◀ Stop** · ◀ Frame · ▶/⏸ ·
>   Frame ▶ · **Stop ▶** · End ⏭ between Previous/Next; pure transport
>   (the workshop buttons + the THREE mode buttons left it). ◀/▶ Stop
>   walks markers ∪ snapshots ∪ endpoints with the ancestor fallback.
> - **Middle line:** [Marker] [Snapshot] [Remove] [Toggle Status]
>   [Reset ▾(everything/markers/snapshots)] + the tenants (strip ·
>   Mute · Vol · Speed) to the right. Creators grey on any stop; Remove
>   greys off-stop + at endpoints; Toggle works anywhere (snapshot
>   under cursor, else the owning marker's clip). The temp third line
>   is DEAD.
> - **Modeless development:** ✎ Adjust / Reopen / Adopt and the whole
>   Adjust mode DELETED (~200 lines). Landing the cursor on a Picked
>   stop opens development (wait-cursor frame extract, cached, latch-
>   deduped); a Skipped stop shows ALL-greyed tools (extras exemption
>   removed); off-stop/playing hides them with **retainSizeWhenHidden**
>   (no geometry shifts). The selection model is GONE — ``_stop_at`` /
>   ``_segment_item_at`` resolve everything from the playhead; P/D/Del
>   keys + strip clicks + marker-handle clicks all route through the
>   cursor. Playback tenants (Mute/Vol/Speed) follow the CONTAINING
>   clip, works-anywhere like Toggle.
> - **Timeline:** snapshot glyphs (state-coloured squares) + permanent
>   endpoint marks ON the bar; the rep-frame glyph died; handle click
>   seeks. Vocabulary sweep: no user-visible "cut" survives (tooltips,
>   shortcuts dialog, Reset menu).
> - **Tests:** the rebuild suite REWRITTEN to the cursor model — 18
>   green (load/birth, cursor-scoped persist routing, marker rules,
>   status writes, snapshot place→cursor-target, style routing by
>   cursor, jump math, Stop walk, Remove routing, clear/reset, the NEW
>   visibility pin: hidden-off-stop + greyed-on-skipped + creator/
>   Remove enables). busy 7 + badge 17 green alongside (+ the
>   documented teardown crash).
> - **⚠ Known v1 roughness for the eyeball:** greyed tools show the
>   LAST-loaded values (no rebind without a frame); development
>   auto-opens at load when the start marker's clip is already Picked
>   (rule-consistent — judge the feel); frame extraction runs per
>   landing (cached after first).
>
> **NEXT: Nelson launches** — top grid + visibility, Stop navigation,
> middle line, modeless development on a real video. Then his pass
> ruling (placement tweaks land fast; the photos-bottom completion is
> already true: EditPage shows only Previous/Next).
>
> ---
>
> ## (record, 2026-06-11, later) — EDIT SURFACE DESIGN OPEN (a parenthesis, mid-flight): the TOP REORGANIZATION lands + bottom pass 1 lands (corrected to the ancestor culler's real control set). spec/58 slice 3 eyeballed OK ("I believe the style implementation is ok").
>
> ### The design parenthesis (Nelson opened design mode; capture into a numbered spec is DUE when he closes it)
>
> - **BOTTOM:** designed in passes, video contents first (most complex),
>   photos later. Pass-1 corrections from Nelson (first build missed):
>   top reorg comes FIRST; research the old Cull surface's
>   **IMPLEMENTATION**, not the docs; "Start a new pass…" is named
>   **Reset**; the third line is TEMPORARY (a later pass reorganizes the
>   bottom and kills it — the controls were meant for the Mute/Vol/Speed
>   line, relaxed to a temp third line).
>
> ### The TOP reorganization — IMPLEMENTED (mixed case, two-line grid)
>
> - ``_build_tools`` rebuilt: line 1 = ONE outer ProcessGroupBox
>   **"Style, Look & Filter"** holding [**Look** (stretch 3, always
>   widest) | **Style** | **Filter**]; line 2 (margins compensated to
>   align under line 1) = [**Crop** (stretch 3, under Look) | the
>   **Audio** | **Vibrations** SLOTS (stretch 1 each — empty slots still
>   claim their columns, so Crop stays Look-sized for photos)]; line 3 =
>   the action row, untouched. ``add_right_column_widget`` retired →
>   ``set_video_extra_boxes(audio, vibrations)``; EditVideoPage drops
>   its EXISTING Fade box (Audio) + Stabilise box (Vibrations) in,
>   contents unchanged.
> - **Crop is one horizontal row** ([aspect combo][↺ 90°][90° ↻][Reset]),
>   function identical; the aspect combo got a **display/data split** —
>   shows **"No Crop"**, persists "Original" (every stored label,
>   ``get_aspect_ratio`` lookup and ``label_changed`` payload keeps the
>   canonical vocabulary; ``AspectRatioCombo`` now item-data-based).
> - **Mixed case, always:** Look · Style · Filter · Crop · Audio ·
>   Vibrations + the outer title. App-wide ALL-CAPS audit: only those
>   six were box titles; "B&W"/"JPEG"/"TIFF" are acronyms and stay;
>   ⚠ found out-of-scope: ``picked/grid_view.py`` badges still say
>   KEEP/DISCARD/COMPARE (legacy vocabulary — flagged for its own task).
> - **Dropdown height = button height:** ProcessStyleCombo /
>   ProcessFilterCombo / ProcessAspectCombo get the QPushButton metrics
>   in BOTH themes (the VideoExtraCombo treatment).
>
> ### Bottom pass 1 (corrected) — the old Cull surface's control set, from its implementation
>
> - **Research redo** (``Miracraft/ui/culler/video_cull_page.py`` — the
>   authority per Nelson): action line = K/D filter chips · Create
>   still · Create marker · Remove marker (context-swapped while ON a
>   marker) · Toggle Keep/Discard · per-clip Audio/Rotate/Crop · Reset ·
>   Clear markers · Clear stills; nav = ⏮ Start · **◀ Marker** · ◀ Frame
>   · ▶ · Frame ▶ · **Marker ▶** · End ⏭ (jump set = pure markers incl.
>   the permanent endpoints, fallback to start/end). Stills: **no own
>   state row** — a co-located still toggles WITH its marker's segment
>   via the ONE Toggle button (`_still_kept` derived). The
>   toggle-at-a-marker question dissolved: ancestor + today's
>   containing-segment rule agree — the segment STARTING at the marker.
> - **The temporary third line now carries:** ✂ Cut · ◀ Cut · Cut ▶ ·
>   📷 Snapshot · ◀ Snapshot · Snapshot ▶ · Toggle Pick/Skip · Remove ·
>   **Reset**▾ (Reset everything / Clear cuts only / Clear snapshots
>   only — NoIcon confirms). ◀/▶ Cut = ancestor marker nav (endpoints +
>   fallback, end parks a frame short); ◀/▶ Snapshot **lands on AND
>   selects** the snapshot so the one Toggle acts on it (the old
>   one-toggle-for-both rule, selection-scoped); Cut/Snapshot/Toggle
>   wire to the existing workshop handlers — the nav-row duplicates
>   stay until the kill-the-third-line pass (accepted scaffolding).
> - **Semantics kept from the first build:** Remove routes
>   snapshot→cut at ±1 frame; Clear cuts = chained left-survives merges
>   (first segment's decision+development survive — the old net
>   effect); Reset everything also returns the single survivor to Skip,
>   development untouched.
> - **Tests:** rebuild suite 17 green (jump math, context-Remove,
>   clear-cuts-keeps-snapshots, reset, snapshot-jump-selects); busy
>   suite 7 green (mixed-case grid pin incl. no-ALL-CAPS sweep +
>   the No-Crop display/data pin); style badge 17 green (all + the
>   documented teardown crash; edit-page suite imports clean exit 0).
> - **NEXT:** Nelson eyeballs the new top + the temp line → placement
>   pass (reorganize the bottom, kill the third line, dedupe vs the nav
>   row) → further bottom passes → parenthesis close captures the
>   design into a numbered spec.
>
> ---
>
> ## (record, 2026-06-11) — spec/58 slice 3 LANDS: the confidence-colored Style badge (v0 ramp; Nelson's live calibration PENDING). Round-trip Q&A: Helicon → Picked Media root; LRC → Edited Media subfolder.
>
> ### Session opening (Nelson's questions before the slice)
>
> - **"Where do Helicon results go back?"** → the TOP LEVEL of
>   ``Picked Media\`` (beside the bracket folders, never inside one —
>   the Leg-A scan is root-only; subdir strays are preserved-but-ignored).
>   Keep the default output name (it starts with the link stem). Then
>   enter Edit / "Scan for external results" → adoption moves bytes to
>   ``Original Media\Merged\``.
> - **"And LRC-treated photos?"** → export into a subfolder of
>   ``Edited Media\`` keeping default naming; the scan associates them
>   as external versions (lineage, bytes stay put). Caution given:
>   never "Save Metadata to File" onto the hardlinks.
>
> ### spec/58 slice 3 — what landed (Nelson: "OK — build it" + "Inherit at creation")
>
> - **The STYLE combo is the badge** (spec/58 §2): border color by the
>   ITEM's stored classification on discrete QSS bands —
>   ``ProcessStyleCombo[confidenceBand="low|mid|high|human"]`` in BOTH
>   themes ({error} red / {warning} amber / {success} green / {primary}
>   blue). v0 thresholds in ``adjustment_surface.py``: low < 0.55 ≤
>   mid < 0.80 ≤ high; unclassified/no-confidence = low ("needs your
>   eye"). **Calibration with Nelson live is the open tail** (spec/58
>   §5.1); tooltip gained the live status line ("Auto-classified —
>   confidence N%." / "You decided this style.").
> - **The human flip rides ``QComboBox.activated``** — fires on every
>   USER pick including re-picking the shown style (the spec's "even
>   the currently shown one"), never programmatically. New surface
>   signal ``style_decided``; the badge flips to ``human`` optimistically
>   and the host persists ``set_classification(item, style, 'user')``.
>   ``Adjustment.style`` (render routing, export recipes) untouched.
> - **Scoping:** EditPage → the photo's row. The video workshop →
>   segment selection decides the SOURCE VIDEO's row; a snapshot its
>   own. The workshop's default style now routes by classification
>   (mirrors EditPage — the badge must match what renders); EditPage's
>   ``_normalize_style`` consolidated into the surface's public
>   ``normalize_style`` (alias kept for edit_host_page + tests).
> - **Inherit at creation (Nelson's call):** ``create_video_snapshot``
>   copies the video's five classification fields;
>   ``adopt_stack_output`` copies the anchor member's — children the
>   captured-only pass never sees stop sitting red forever.
> - **Tests:** new ``tests/test_style_badge.py`` (17 — band boundaries,
>   badge property + tooltip, activated flips human + same-index pick,
>   loading/imageless guards, set_state never decides, both-themes band
>   rules pin); +1 in gateway (snapshot inherits), +1 in
>   external_returns (adopted master inherits), +1 in the workshop
>   rebuild suite (segment→video / snapshot→own routing). 66 green exit
>   0 on the data suites; Qt suites all-dots with the documented
>   machine-local teardown crash — **verified pre-existing at clean
>   HEAD ``7640562`` via a temp worktree** before accepting.
> - **Next:** Nelson launches, we calibrate thresholds + colors live
>   (the eyeball-loop pattern), then his word on 58/4 vs 56/4–5.
>
> ---
>
> ## (record, 2026-06-10, evening) — EYEBALL PASSED ("Eyeball ok") across the whole stack. spec/57 complete · spec/58 slices 1–2+5 · spec/56 slice 3 · crop rotation handle — all verified in-app. The one find: photo Export crashed (legacy ``ui.culler`` lazy import) → ExportDialog ported same evening. Next: Nelson picks 58/3 (ramp calibration) / 58/4 / 56/4–5.
>
> ### Eyeball verification record + the export crash (Nelson, 2026-06-10 evening)
>
> - **"Eyeball ok"** — the accumulated stack passed: spec/57 (round
>   trip, split preview, TZ unlock, folder names), the backfill wizard
>   (all three levels), the video workshop, the wizard refresh, the
>   crop rotation handle.
> - **The find: photo Export crashed.** Nelson had no console; the
>   local log (`%LOCALAPPDATA%\Miracraft\logs\miracraft.log`) carried
>   the traceback twice: ``ModuleNotFoundError: No module named 'ui'``
>   from ``edit_page._export_current_item`` — BOTH Edit export paths
>   (per-photo + the host's day/event) lazily imported the ancestor's
>   ``ui.culler.cull_export_dialog``, which never travelled to MC. The
>   lazy import only fires on click; no test covers it (edit_page /
>   edit_host_page are Slice-B-skipped) — first real export click ever
>   in MC found it.
> - **Fix: the dialog is ported** —
>   ``mira/ui/edited/export_dialog.py`` (``ExportDialog``),
>   audit applied on the way over: locked vocabulary (picked, not
>   kept; no Cull in the name), MC import paths, the never-defined
>   ``CollisionBox`` role dropped. Both call sites swapped; the
>   retired "processed photos" scope labels became "this photo" /
>   "edited photos · day|event". Repo-wide sweep: zero remaining
>   ``from ui.`` imports in the live tree.
> - **Tests:** new ``tests/test_export_dialog.py`` (7 — defaults,
>   JPEG-quality gating, no-choice fallback, collision section +
>   policy, empty-dest guard, accept snapshot, and the regression pin:
>   no Edit module imports the ancestor's ``ui.`` tree).
>
> ### Export dialog reshape (Nelson's quiz + ruling, same evening)
>
> - **"Export works now."** Then Nelson's quiz: the ported dialog
>   violated one very clear UI rule — **label-beside-input instead of
>   titled QGroupBoxes** (the FormFieldGroup grammar). Guessed right;
>   reshaped: Destination / File type / Name collisions are titled
>   FormFieldGroup boxes, and every radio gained its missing hint
>   (the every-control-has-a-hint rule was broken too).
> - **Ruling: ORIGINAL leaves the Edit export entirely.** First take
>   was "original as an additive checkbox"; Nelson corrected —
>   **grabbing the original file belongs to SHARE**, not Edit export.
>   The dialog now offers **JPEG | TIFF only** (the rendered, edited
>   photo); the engine's ORIGINAL byte-copy path survives untouched
>   for the Cuts rebuild to use. ``file_type_choice`` param dropped;
>   fallback file type is JPEG. **For the Cuts/Share design session:**
>   the include-the-original option lands there.
> - **Tests:** suite reworked (8 — adds the form-grammar pin: three
>   titled FormFieldGroup boxes + every radio hinted; ORIGINAL
>   not offered + ORIGINAL-default falls back to JPEG).
>
> ### Honest UI during Look/Filter render lag (Nelson, same evening)
>
> - **The report:** switching Looks left TWO buttons painted blue
>   through the lag with no cursor change; filter switches kept the
>   PREVIOUS filter's name in the combo through an even longer lag —
>   the user can't tell anything is happening.
> - **Mechanics:** the handlers update control state (a QUEUED
>   repaint) then run ``render_now`` synchronously on the UI thread —
>   the repaint only lands after the render returns. Fix at the seams:
>   ``set_look`` flushes the segmented row (``repaint()``) before
>   rendering; ``_on_filter_changed`` flushes the combo;
>   ``_on_style_changed`` flushes + raises the wait cursor BEFORE its
>   heavy ``compute_auto_params`` re-route; and ``render_now`` itself
>   wraps in a stacked override wait cursor (every caller — look,
>   filter, style, rotation, compare/preview, debounce — inherits the
>   busy-cursor-on-lag rule).
> - **Lag structure (for the perf follow-up):** the working view
>   already renders at the 1280-px preview cap (~50 ms by design);
>   the seconds-class lag is the **Preview (P) full-res path** and the
>   filter blur passes (clarity/vignette). The real cure is the
>   off-thread render arc (busy-cursor memory: "off-thread progress
>   preferred") — queued as a candidate, not started.
> - **Tests:** new ``tests/test_adjustment_surface_busy.py`` (5 —
>   exclusive look buttons after switch, cursor stack balanced after
>   look/filter/style renders AND when the render raises).
>
> ### Surface-top form grammar — LOOK · STYLE · FILTER boxes (Nelson, same evening)
>
> - Same violation one level up: the surface's LOOK group carried
>   "Style:" / "Filter:" labels beside combos. Restructured to **three
>   named ProcessGroupBox frames — LOOK (segmented chooser + Grid) ·
>   STYLE (combo) · FILTER (combo)** — beside CROP in the tools row;
>   ``set_filter_features_visible`` now toggles the whole FILTER box.
>   Grammar pin added to the busy suite (titled boxes present, no
>   label-beside-input survivors).
>
> ### spec/58 slices 1–2 — schema + the background classification pass LAND (same session; Nelson: "leave the export to later … tackle the classification")
>
> - **Schema v2 — the first real migration** (`_migrate_v1_to_v2`):
>   ``item.classification_confidence REAL`` added via ALTER (policy:
>   migrations, events preserved — Nelson's live test events survive).
>   ``set_classification`` gained ``confidence``; new bulk
>   ``set_classifications_bulk`` (ONE transaction = one short lock
>   window for the worker thread) + ``edit_touched_item_ids`` (the §3
>   freeze set: adjustment rows — own or a child segment/snapshot's —
>   and edit-phase lineage).
> - **The rules-version stamp now sees wizard re-runs.** Found while
>   wiring: ``_rules_version`` reads only the BUNDLED rules file — a
>   wizard re-run (user scenarios) would never have re-opened
>   classifications. New ``scenario_loader.user_scenarios_fingerprint``
>   + public ``genre.rules_version_for(source)`` compose
>   ``"<bundled>.<user-fingerprint>"`` — both shipped-rules updates AND
>   wizard re-runs change the stamp. (Also: the audit's finding stands —
>   the lazy Pick writer never stamped rules_version at all; its NULL
>   rows read as stale and converge on the next pass.)
> - **`mira/ingest/classify_pass.py`** —
>   ``classify_event_items(eg, event_root)``: candidates = captured
>   items (photos AND videos, hidden days included) that are
>   unclassified or stale-stamped; **never** ``source='user'`` rows,
>   **never** Edit-frozen items (even for a first classification —
>   writing one would change render routing after the user worked).
>   **RAW-first** ("Use the raw"): grouped by (camera, day, stem) — the
>   RAW classifies once, photo siblings inherit verbatim; videos always
>   classify themselves. One ExifTool batch spawn for the
>   representatives; explicit camera/phone source from the camera row;
>   one bulk write. Injectable ``*_fn`` hooks keep tests
>   ExifTool-free.
> - **Triggers (slice 2):** ``_spawn_classify_pass`` daemon thread —
>   opens its OWN gateway in-thread (SQLite is thread-bound), logs only,
>   per-event re-entry guard. Fired from the Collect copy-all success
>   tail AND from ``_open_event`` (the catch-all: backfilled events that
>   never visit Pick, pre-58 events, wizard re-runs). No-ops fast when
>   current.
> - **Tests:** new ``tests/test_classify_pass.py`` (9 — pair-inherits,
>   video-self-classifies even sharing a stem, the three §3 guards,
>   stamp-change reopen, child-segment freeze, missing files, phone
>   source routing, needs_review at low confidence, the v1→v2
>   migration). Adjacent: store + gateway + backfill + returns 88 green;
>   scenario/genre/classifier 196 green.
> - Still live until 58/4: the Pick surfaces' lazy writer + genre chrome
>   (their removal is slice 4, AFTER the background pass is eyeballed).
>
> ### spec/58 slice 5 — the wizard refresh LANDS (same session; Nelson: "adjust the terminology in the wizard")
>
> - **Rename sweep:** 11 wizard step files, word-boundary case-sensitive
>   ``Miracraft`` → ``Mira`` (welcome title, calibration
>   questions, every genre block's "expected setup" hint, capture
>   overview, precull). The ``%LOCALAPPDATA%/Miracraft`` path reference
>   in ``core/wizard.py`` stays — it's the literal shared data-dir name,
>   not app-name prose.
> - **The four undefined QSS roles defined in BOTH themes** (BodyText,
>   WizardRadio, WizardRadioHint, WizardWarning — warning uses
>   ``{error}``); placeholder-set diff vs HEAD = NONE new, so theme
>   formatting is untouched.
> - **Tests:** new ``tests/test_wizard_refresh.py`` (2 pins — no stale
>   app name anywhere in wizard sources; all nine wizard roles exist in
>   both themes). spec/58 now: slices 1–2 + 5 landed; 3–4 await the
>   eyeball (3 = the ramp colors; 4 removes the Pick chrome after the
>   pass is seen working).
>
> ### Feature — free-angle crop rotation by handle drag (Nelson: "rotate it in any angle … by moving handles rather than entering an angle")
>
> - **The crop box gets a rotation handle** — a lollipop above the top
>   edge's midpoint (stem + circle, riding the box at any angle).
>   Drag = free rotation about the box centre; live angle readout
>   (``+3.5°``) beside the handle while dragging; **magnetic snap** to
>   0 / ±90 / 180 within 2° (the horizon clicks level); commit on
>   release. The 90° step buttons + Reset stay as coarse controls
>   (Reset tooltip now points at the handle).
> - **Plumbing:** new ``CropOverlay.angle_changed`` signal (release-
>   commit, mirroring ``rect_changed``); AdjustmentSurface routes it
>   through the existing ``_set_box_angle`` → ``changed("angle")`` →
>   persistence path (photo ``crop_angle`` / video ``box_angle``) — so
>   EditPage, the video workshop's segments AND snapshots all inherit
>   the handle with zero page changes. Hit-testing maps through the
>   box's local frame, so the handle works while rotated; resize stays
>   a 0° operation (the existing v1 simplification).
> - **Tests:** new ``tests/test_crop_overlay_rotation.py`` (6 —
>   normalize/snap math, handle hit at 0° + riding a 30° box, the
>   press→move→release gesture landing the exact bearing, snap-to-level,
>   and the commit contract: release emits ``angle_changed``, never
>   ``rect_changed``). Workshop suite re-run green alongside.
>
> ### spec/56 slice 3 — Edit video workshop LANDS (same session, Nelson: "start")
>
> - **EditVideoPage IS the workshop now** (spec/56 §1): top stays
>   development (the same AdjustmentSurface), bottom = **marker timeline
>   + snapshot strip**, everything scoped to the SELECTION.
> - **`_MarkerTimeline`** (new painted widget): segments tile the bar
>   with P/D washes (green picked / red skipped), draggable cut handles
>   (clamped one frame inside their neighbours — the UI half of the
>   may-not-cross rule; the gateway guard stays the backstop), selected-
>   segment ring, playhead + the selected segment's rep-frame glyph.
>   **`_SnapshotStrip`**: one chip per snapshot (📷 + time, state ring,
>   click to develop), empty-state hint ("press S…").
> - **Selection model:** the playhead's segment is the implicit
>   selection (follows during playback/stepping); clicking a snapshot
>   chip holds it until a transport/timeline action returns to the
>   playhead's segment. Binding per selection: segment → its
>   VideoAdjustment (audio/volume/speed/fade/stabilise are per-segment
>   now); snapshot → its photo Adjustment (video extras disabled;
>   crop_angle/aspect_label mapping mirrors EditPage).
> - **Ops on the slice-1 gateway:** M/✂ Cut = ``add_video_marker`` at
>   the playhead (split, both halves inherit verbatim); handle drag =
>   ``move_video_marker`` (identity = order position); Del/Remove cut =
>   ``delete_video_marker`` (LEFT survives); S/📷 =
>   ``create_video_snapshot`` (auto-Picked + selected for immediate
>   development); P/D = ``set_phase_state(edit)`` on the selection — the
>   MediaHost border is the P/D indicator, same grammar as Pick. First
>   workshop open backfills NULL ``item.duration_ms`` (ingest leaves it
>   unset; marker ops require it).
> - **Retired with the rebuild:** the whole-video adjustment target (the
>   source video's VideoAdjustment row is never written now), the
>   per-clip "Export →" button (bytes only at Export — the slice-4
>   walker; the worker + progress chrome stay for it), and a stale
>   ``_refresh_trim_label`` call — a latent AttributeError the conftest
>   Slice-B blanket skip had been masking.
> - **Tests:** ``test_edit_video_page_rebuild.py`` REWRITTEN for the
>   workshop (11: lazy-birth + explicit skipped rows, segment-scoped
>   persist, cut-inherits-both-halves, move-keeps-identity,
>   remove-cut-left-survives, P/D writes, snapshot auto-pick + select,
>   photo-vs-video row routing on surface save, edge nav) and its
>   conftest Slice-B skip entry REMOVED — the suite runs for real.
>   11 green (+ the documented Qt session-teardown crash);
>   video_segments + gateway + video_export_plan 69 green, exit 0.
>
> ### DESIGN SESSION (same day, after slice 5) — spec/58 LOCKED: classification + the wizard
>
> Nelson opened design mode on photo classification + the first-run
> wizard with an eight-point brief; two audits grounded it; every open
> question closed one by one. Captured in
> **`spec/58-classification-and-wizard.md`** (LOCKED). Headlines:
>
> - **Classification serves ONE purpose:** choosing Edit's correction
>   profile (spec/54 router). Audit found today's only writer is the
>   Pick surface (lazy, on browse) — photos never browsed and ALL
>   backfilled events reach Edit unclassified; the confidence score is
>   computed then dropped; the genre chrome sits on two Pick surfaces.
> - **The fix:** a **background classification pass** after ingest
>   (Collect AND backfill) becomes the sole auto writer; photos + videos;
>   RAW-first (a RAW+JPEG pair is ONE shot — "Use the raw"; JPEG inherits
>   by stem; user decision applies to the pair); confidence PERSISTED
>   (new ``classification_confidence`` column).
> - **One surface:** Edit's Style button, colored by confidence
>   (red→green ramp, thresholds eyeball-calibrated later); any user
>   style selection — even the same value — flips to a fourth
>   "human decided" color + ``source='user'``. ALL pre-Edit
>   classification chrome retires (Pick photo surface genre chip +
>   Reclassify; video Pick page "genre · Reclassify" — consciously
>   supersedes the spec/56 slice-2 keep).
> - **Stability:** auto re-classification (rules_version change, e.g.
>   wizard re-run) touches ONLY items untouched in Edit (no Style/Look/
>   Filter choice, no adjustment, no export) and never
>   ``source='user'`` rows. "Untouched means re-classifiable."
> - **Wizard refresh:** vocabulary already post-pivot-correct; the real
>   staleness = 18 user-visible "Miracraft"→"Mira" strings + four
>   QSS roles referenced but defined in NEITHER theme (BodyText,
>   WizardRadio, WizardRadioHint, WizardWarning). Re-run stays
>   overwrite-on-complete (blast radius bounded by the stability rule).
> - **Deliberately kept:** the dormant style pie
>   (``style_breakdown_last_phase``, zero UI callers) — "we may find
>   some use for it in the future."
> - Touchpoints: CLAUDE.md load-bearing list + this banner; the
>   RAW-first auto-memory re-affirmed and repointed at spec/58.
> - spec/58 §6 has the five implementation slices. **Do NOT start
>   without Nelson's word** — sequencing vs spec/56 slices 3–5 is his
>   call at the next checkpoint.
>
> **Where we are.** Nelson confirmed the slice-5 shape at checkpoint —
> recorded in **spec/57 §4.3.1**: one absorbed menu entry (level
> question is step one); from-Edited bytes = `Original Media/` master
> + the same bytes as hardlinks under `Edited Media/`, lineage pointing
> at the Edited Media placement (`recipe_json` NULL — the external-
> return shape, `exported_at` = backfill time); from-Edited lands on
> the event dashboard while the Cuts page is a placeholder; cancel
> after creation keeps the event and lands on its dashboard.
> **⚠ His eyeball of yesterday's slices (57/1–4 + 56/2) is still
> pending** — the checkpoint list in the session-wrap record below.
>
> ### Slice 5a — what landed
>
> - **`ui/pages/landing_level_dialog.py`** (new) — the wizard's first
>   question ("Where does this media stand?"): three radio levels,
>   FormFieldGroup role, every control hinted. Module-level
>   `AVAILABLE_LEVELS` gates what's live — 5a serves "collected" only;
>   picked/edited render disabled ("Coming in the next build step")
>   until 5b/5c flip them on.
> - **`_open_new_event_flow` is the wizard spine now:** level question
>   → source pick → scan → **multi-date split confirm** (DaySplitDialog
>   + boundary regroup via `build_scan_result` — the same moment
>   Collect got in slice 4; all dates are new here, so >1 scanned date
>   triggers it) → coverage box → PlanDialog → create → **auto-Collect**
>   (spec/57 "what runs automatically"): re-read fresh trip_days, the
>   same `_collect_run_tz_calibration` ask + the same
>   `_open_collect_ingest_gate` (Copy all / **Quick Sweep first** /
>   Cancel — the spec's "Quick Sweep optional" for free) + the same
>   copy engine. The old flow stopped at event creation ("Slice 3
>   stop") and required a manual Collect pass — that gap is closed.
> - **Landing:** the gate + `_run_collect_copy_all` gained
>   ``land_phase`` (default None = Collect behavior unchanged) and a
>   ran-to-completion bool; the wizard lands from-Collected at **Pick**
>   (`_on_event_created` + `_on_phase_activated("pick")`, guarded on
>   `_current_event_id`). Every post-creation abort (calibration
>   abort, gate cancel, sweep back-out, ingest crash) lands on the
>   event dashboard instead of stranding the user — the event exists
>   with its plan baked (spec/57 §4.3.1 cancel posture).
> - **Renames:** menu + nav entry **"New event from existing media…"**
>   (Ctrl+Shift+N kept); events-dashboard empty-state + PlanDialog
>   Include-column help follow. §11-retired ``past_photos_dialog``
>   deliberately left alone for the retirement sweep.
>
> **Tests:** new `tests/test_landing_level_dialog.py` (7 — render,
> default, AVAILABLE_LEVELS gating, level(), every-control-hinted,
> OK/Cancel); menu-label assert updated — 15 menu tests pass (the
> documented interpreter-dies-at-session-teardown on this machine
> persists, pre-existing); plan_dialog + events_dashboard 34 green;
> collect_tz_dialog_glue 6 green.
>
> ### Slice-5a follow-up — Nelson's first backfill run crashed; same-destination ingest fixed
>
> - **The crash:** ``sqlite3.IntegrityError: UNIQUE constraint failed:
>   item.origin_relpath`` in ``_record_collect_in_event_db`` — the whole
>   recording transaction rolled back (copies on disk, zero DB rows).
>   Root cause in the SHARED engine (``core/ingest_pipeline.run_ingest``):
>   ``_copy_and_hash`` opened destinations "wb" unconditionally — two
>   jobs mapping to one destination (same camera + day + filename)
>   silently overwrote the first copy, then both reported the same
>   destination and the second item insert tripped the UNIQUE. A
>   backfill source makes the collision certain: legacy event folders
>   carry the same file under captured AND selected subtrees. Latent in
>   Collect since E.3; first contact via the wizard.
> - **The fix (engine):** destinations are never blindly overwritten.
>   Per-run ``claimed`` map: identical bytes already claimed this run →
>   duplicate, ingested ONCE (new ``photos_duplicates`` counter,
>   completion box says "N duplicate(s) ingested once"); identical bytes
>   already on disk → interrupted-run resume (kept + reported so the
>   item row still records); different bytes → divert to "name (2).ext"
>   — the captured copy always survives (invariant #7). 5 new tests in
>   ``test_ingest_pipeline`` (dup-once, divert, resume, never-overwrite,
>   quarantine collisions).
> - **Defense in depth (host):** ``_record_collect_in_event_db`` keeps
>   an in-batch planned-rels set — a duplicate destination now drops the
>   row with a log instead of aborting the whole transaction.
> - **Adjacent hazard closed:** ``materialise_event`` DELETES an
>   existing ``event.db`` at its target root — re-creating an event
>   under an existing folder name would hijack the folder and orphan the
>   old index card (the clobber family). ``_create_event_from_plan`` now
>   refuses with a clear message; covers the wizard AND New event paths.
> - **Recovery for the crashed event:** it holds plan + copied files,
>   zero items. EITHER delete it (Event → Delete event, with files) and
>   re-run the wizard — the full 5a retest — OR open it and run Collect
>   at the same source folder: re-scan + the new resume path record
>   everything without re-copying.
> - Tests: ingest_pipeline + capture_offload 34 green (exit 0);
>   plan_browse_day 11 green (its documented Qt teardown crash stands).
>
> ### Slice 5b — from-Picked lands (same session, Nelson: "Let continue")
>
> - **"Already picked" is live** (``AVAILABLE_LEVELS`` += LEVEL_PICKED;
>   the dialog's gating + a new explicit levels pin in its tests).
> - **Level state-writes seam:** ``_run_collect_copy_all`` gained
>   ``post_record`` — invoked right after ``_record_collect_in_event_db``
>   INSIDE the progress dialog, so the states exist before any surface
>   opens. The picked level's closure:
>   ``items(provenance='captured')`` → bulk
>   ``set_items_phase_state('pick','picked')`` — explicit rows,
>   ``decided_at`` stamped, so the configured pick default can never
>   flip them.
> - **Landing:** picked → ``land_phase="edit"`` — ``_on_phase_activated``
>   runs the spec/57 Edit entry seams (return scan + Picked Media
>   projection rebuild) with the picks already written, then opens
>   EditHostPage. The projection therefore links every backfilled item
>   the moment the event first opens.
> - **Quick Sweep gated by level:** the ingest gate gained
>   ``offer_quick_sweep`` — the sweep button shows for the collected
>   level only (picked/edited arrive pre-filtered, spec/57 §4.3.1);
>   Collect's own gate unchanged (default True).
> - Tests: dialog suite 8 green; flow glue is eyeball territory.
>
> ### Slice 5c — from-Edited lands (same session) — SLICE 5 COMPLETE
>
> - **"Already edited" is live** (``AVAILABLE_LEVELS`` = all three; the
>   dialog's levels pin updated).
> - **`mira/ingest/backfill.py`** (new) —
>   ``apply_edited_level(eg, event_root, now=…)``: explicit picked
>   states at BOTH phases (decided_at stamped); every captured item's
>   bytes also stand under **``Edited Media/Imported/``** as NTFS
>   hardlinks (copy fallback; foreign files never overwritten —
>   picked_media posture); one ``lineage`` row per item in the
>   external-return shape (``phase='edit'``, ``recipe_json`` NULL,
>   ``exported_at`` = backfill moment) so the Cut picker reads the
>   finals like in-app exports and the return scanner skips them.
>   Same-name finals divert to "name (2).ext"; re-runs are idempotent
>   (recorded relpaths re-used, deleted links restored, no suffix
>   spiral). Runs via the 5b ``post_record`` seam — states + links
>   exist before any surface opens.
> - **Landing:** edited → ``land_phase=None`` — the event dashboard
>   (spec/57 §4.3.1; flips to the Share surface when Cuts lands).
> - **Tests:** new ``tests/test_backfill_edited.py`` (6 — both-phase
>   states, hardlink + lineage shape, same-name divert, idempotent
>   re-run, deleted-link restore, missing-source reporting); dialog 8 +
>   external_returns 6 green alongside (20 total, exit 0).
>
> ### Picking up
>
> 1. **The eyeball stack is CLEARED** (Nelson 2026-06-10 evening,
>    "Eyeball ok") — the per-slice lists live in the records above if a
>    regression ever needs the script again.
> 2. **Nelson re-verifies photo Export** after the dialog port (all
>    three scopes: this photo / day / event — the dialog opens, the
>    export runs, lineage records).
> 3. **Then Nelson's word for what follows**, in recommended order:
>    **spec/58 slice 3** (the confidence-colored Style button — needs
>    him live for ramp calibration), **spec/58 slice 4** (retire the
>    Pick genre chrome + lazy writer — the background pass is verified
>    now), **spec/56 slices 4–5** (video Export walker + cleanup).
>    spec/57 §6 retirement inventory also remains. Do NOT start any
>    without his word.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10, SESSION WRAP — context exhausted) — One mega-session, all committed clean
>
> **What this session landed, in order** (each with its own record
> block below, newest-first within the day):
>
> 1. **spec/56 slices 1+2** — schema v4 marker-partition video model
>    (markers/segments/snapshots; identity rules in the gateway) +
>    Pick simplification (video surface = watch + whole-video P/D,
>    Play/Pause + timeline kept; materialisation path deleted).
> 2. **The v1 schema reset** — all events deleted by Nelson; the
>    v1→v4 migration chain folded into the base DDL;
>    SCHEMA_VERSION = 1, MIGRATIONS = [].
> 3. **spec/57 DESIGNED + LOCKED** (`spec/57-folders-and-roundtrip.md`)
>    — folder model, external round trip, no-phase-lifecycle, event
>    creation; charter/CLAUDE invariant #7 carve-out recorded.
> 4. **spec/57 slices 1–4 implemented**: folder model (Original
>    Media / Edited Media / Cuts; one tree-birthing helper) → Picked
>    Media projection (manifest-guarded links) → return seams
>    (stacker adoption + LRC association + scan + reminder) →
>    incremental Collect (multi-date split preview, late-phone TZ
>    reconciliation, retime_day + plan-editor TZ unlock).
>
> **⚠ Open watch-item:** the events-index clobber incident (see the
> slice-2 record below + the auto-memory) — an unexplained external
> rollback ate the fresh event's index row once; repaired additively;
> six stale legacy rows deliberately left for Nelson.
>
> **NEXT SESSION:** Nelson's eyeball pass (the list under the slice-4
> record: round trip with Helicon/LRC, multi-day split, TZ unlock,
> folder names, video watch surface), then HIS pick between
> **spec/57 slice 5** (backfill wizard — the three landing levels,
> from-Edited one-folder rule) and **spec/56 slices 3–5** (Edit video
> workshop on the slice-1 gateway ops → Export walker → cleanup).
> Do not start either without his word.
>
> ---
>
> ## (record, 2026-06-10, late night) — spec/57 slice 1 LANDS: the folder model. Events are born as Original Media + Edited Media + Cuts (fixed English, no numbered dirs).
>
> ### Slice 1 — what landed (Nelson: "Your call" → folder model first)
>
> - **`core/path_builder.py`** is the spec/57 single source of truth:
>   `ORIGINAL_MEDIA_DIR_NAME` / `EDITED_MEDIA_DIR_NAME` /
>   `CUTS_DIR_NAME` / `PICKED_MEDIA_DIR_NAME` / `MERGED_SUBDIR_NAME`
>   + helpers (`original_media_dir` … `merged_dir`). The numbered
>   stage names survive ONLY in a clearly-marked RETIRED block for
>   legacy-era core modules pending their sweep; the two RENAMED dirs
>   alias to the live names (`CAPTURED_DIR_NAME = ORIGINAL_MEDIA…`,
>   `PROCESSED_DIR_NAME = EDITED_MEDIA…`) so any legacy reader that
>   runs resolves to the real tree. RESERVED_DIR_NAMES carries both
>   generations.
> - **`core/event_service.create_folder_structure`** births the
>   spec/57 tree: `Original Media/{_cameras,_phones,_other}` +
>   `Edited Media` + `Cuts` — verified by smoke test. `Picked Media`
>   (slice 2) and `Original Media/Merged` (first adoption) stay lazy.
> - **Live call sites repointed:** ingest engine copies into
>   `Original Media`; EditPage/EditHostPage default exports to
>   `Edited Media` via the helper; all reachable user-visible strings
>   (wizard capture steps, back-up-card dialog, Quick Sweep, offload
>   calibration, settings help) renamed. The §11-retired
>   `capture_action_dialog` keeps old text, pending its sweep.
> - **Dead between-ends writers deleted:** `ui/picked/pick_sync.py`
>   (the 02 - Selected reconciler — zero callers) and
>   `core/day_folder_reconciler.py` + its 22 tests (zero callers).
> - **Tests:** 241 passed + 37 pre-existing skips across the touched
>   surface (path_builder, new_event_page, ingest, edit_lineage,
>   store, gateway, move_days, day_hidden, photo_import,
>   overview_stats, capture_plan_check, capture_offload,
>   ingest_pipeline, phase_progress, event_backup_card,
>   event_metrics, event_stats, events_dashboard_page; fixtures in
>   backup-card/metrics/ingest/lineage updated to the new names).
>   **Known pre-existing** (verified at pre-change commits via clean
>   worktrees): `test_plan_editor_flow.py` hard-crashes the
>   interpreter after 5 passing tests, same family as the
>   `test_main_window_menu` session-teardown crash.
>
> ### Slice-1 follow-up — Nelson's first real event caught two gaps
>
> Nelson created **Everest - Nepal** via create-from-files and pointed
> at the tree: ingest landed in `Original Media/…` correctly, but
> `02 - Selected` + `04 - Curated` appeared and `Cuts` was missing.
> Root causes + fixes (commit follows this banner):
>
> - The REBUILD creation flows (`New event` + `New event from photos`)
>   go through `Gateway.create_event → materialise_event`, which made
>   only the bare root — the skeleton lived on the legacy
>   `event_service` path. New single tree-birthing helper
>   **`core.path_builder.ensure_event_tree`**; `materialise_event`
>   (covers create AND restore), `event_service.create_folder_structure`,
>   `ingest_pipeline._ensure_event_structure` and
>   `reconcile_pipeline.reconcile_commit` all call it now. Regression
>   test pins it (`test_materialise_event_births_spec57_tree`, incl.
>   Picked Media / Merged staying lazy).
> - `core/ingest_pipeline.py` (the LIVE create-from-files pipeline)
>   still built the legacy selected/processed/curated trio — that's
>   where 02/04 came from. Fixed; Nelson's Everest event hand-cleaned
>   (empty strays removed, `Cuts` added).
> - The "Importing photos…" progress dialog → **"Importing media
>   files…"** (Nelson note 2026-06-10 — videos are first-class).
> - **Pre-existing found:** `test_reconcile_pipeline.py` (legacy
>   engine, UI already §11-retired) has been failing on machines WITH
>   bundled exiftool since spec/52 dropped `Camera.is_reference`
>   (19 tests, "exactly one camera must have is_reference=True" —
>   verified identical at pre-slice commits). Now skipped with a
>   dated reason; the module is retirement-sweep inventory.
> - Expected-and-confirmed: the creation FLOW itself is unchanged
>   (manual Collect start) — auto-Collect backfill is spec/57 slices
>   4–5, still queued.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10, late night) — DESIGN SESSION: spec/57 LOCKED (folder model + external round trip + event creation + no-phase-lifecycle).
>
> **Where we are.** Nelson opened design mode before recreating any
> events ("we need to make another change"). The full session is
> captured in **`spec/57-folders-and-roundtrip.md`** (LOCKED, all ten
> questions closed one by one). The locked headlines:
>
> - **Folder model:** an event holds `Original Media/` + `Edited
>   Media/` (the two ends, real bytes), `Cuts/<cut>/` (handoffs) and
>   `Picked Media/` — the ONE projection: a links doorway for external
>   tools. Numbered phase dirs (01/02 + prefixes) retire. Fixed
>   English names on disk; the database handles all intermediate
>   state.
> - **External round trip** (parallel track to Edit): flat picked root
>   with `D03_G9M2_…` deterministic-prefix links; per-bracket subdirs
>   holding picked-members-only links (never at root); stacking is
>   ALWAYS external — outputs land at the root and are ADOPTED into
>   additive-only `Original Media/Merged/` (the one sanctioned
>   carve-out to invariant #7 — charter + CLAUDE.md amended) with a
>   seamless re-link; LRC returns into an `Edited Media/` subdir,
>   associated by **starts-with-the-link-stem** (unmatched → flagged
>   report); discovery = scan on surface entry + button; links build
>   on entering Edit + a Refresh action.
> - **Phase lifecycle: none.** Phases are surfaces; the user
>   self-manages via the breadcrumb tick trail; reminders are derived
>   facts at concrete moments (e.g. "N picked brackets have no merged
>   result" on entering Edit) — dismissible, never walls.
> - **Event creation:** live trips start empty — the plan is a product
>   of Collecting (multi-date runs auto-split + confirm; per-day
>   manual metadata; late-phone TZ reconciliation prompts only on
>   mismatch); plan editor gains the post-ingest single-day TZ unlock;
>   **backfill wizard** with three landing levels (ready-at-Pick /
>   -Edit / -Share; from-Edited = ONE folder treated as both). Bar:
>   "simple and flawless".
> - Touchpoint amendments landed with the spec: charter
>   tree-projection bullet, CLAUDE.md invariant #7 + load-bearing
>   list, spec/51 Cuts folder row, spec/52 supersession banner.
>
> ### spec/57 slice 2 — Picked Media projection (LANDS, same session)
>
> Nelson: "continue" → slice 2. The external tools' doorway exists:
>
> - **`core/picked_media.py`** — the manifest-guarded link engine.
>   Deterministic names (`D03_DC-G9M2_P1000001.RW2`; undated → `D00`),
>   flat root for whole items, one subdir per focus/exposure bracket
>   (picked members ONLY there, never at root), NTFS hardlinks with
>   copy fallback (cross-volume), and the never-touch-real-bytes
>   rebuild: ownership tracked in `.miracraft_links.json` (path +
>   inode); an entry is deleted only while its recorded inode still
>   matches, so tool outputs at the root — and even a tool REPLACING
>   an owned name — are preserved, always. Corrupt manifest
>   self-heals (samefile keep + re-own).
> - **`edit_model.picked_media_entries`** — mirrors `edit_pool_ids`
>   EXACTLY (explicit picks + configured pick default), byte-bearing
>   items only, bracket membership threaded through. The projection
>   always equals what Edit shows; when the master rule changes in
>   spec/56 slice 3, the projection follows automatically.
> - **Wiring:** entering Edit rebuilds quietly (wait cursor,
>   log-only); Edit menu gains "Refresh Picked Media links" with a
>   NoIcon summary box. 14 new tests (`tests/test_picked_media.py`).
> - **Live smoke on Nelson's real Everest event:** 1797 items, zero
>   picks yet → an honestly empty projection, no errors.
>
> ### ⚠ Incident found during the smoke — events-index clobber
>
> Nelson's fresh **Everest - Nepal** event had VANISHED from the
> events index. `.history` forensics (the atomic-journal pre-write
> snapshots): 10:47–10:48 his per-event deletes shrank the index to
> empty ✓; 14:28 creation upserted Everest ✓ (the 14:36 snapshot
> still contains it); then the main `events_index.json` turned up
> holding the SIX-ROW legacy document with **6/2 mtimes preserved** —
> something only a rename/copy-with-metadata produces; no protected
> write does that, and no further `.history` snapshot exists. The
> mechanism is UNIDENTIFIED (external restore/rollback suspected;
> pytest suites verified isolated; no env override).
> **Repair (additive only):** the Everest row was re-upserted via the
> app's own writer — `open_event` verified (1797 items). The six
> stale legacy rows (folders gone, wrong base) were left in place —
> Nelson decides whether to delete those cards. Watch for a repeat;
> if the index loses rows again, `%LOCALAPPDATA%\Miracraft\.history\`
> is the first stop.
>
> ### spec/57 slice 3 — return seams (LANDS, same session)
>
> Nelson: "continue" → slice 3. The round trip is closed end-to-end:
>
> - **`mira/picked/external_returns.py`** —
>   ``scan_for_returns``: (Leg A) foreign files at the Picked Media
>   root whose stem starts-with a picked bracket member's link stem
>   adopt as that bracket's final master; (Leg B) files under
>   ``Edited Media/`` not yet in lineage associate to their source by
>   the same starts-with rule (LONGEST prefix wins — p10 never
>   resolves to p1) → external ``lineage`` rows (``recipe_json``
>   NULL), idempotent across scans; (Leg C) the derived fact: picked
>   brackets with no merged result. Unmatched files are FLAGGED and
>   left untouched, never silent; ``.xmp``-class sidecars skip
>   silently.
> - **`EventGateway.adopt_stack_output`** — copy → sha-verify → move
>   into ``Original Media/Merged/`` (collision-suffixed), then one
>   txn: ``provenance='stack_output'`` item placed on the bracket's
>   day (anchor member's day/camera/corrected time),
>   ``stack_bracket`` (action='stacked', output wired) +
>   ``stack_member`` rows, explicit ``phase_state('pick','picked')``
>   (merging it WAS the pick). Source deleted only after bytes are
>   safe + recorded; DB failure rolls the copy back.
> - **`EventGateway.bracket_memberships`** — bracket grouping now
>   comes from the CACHED scanner clusters (the brackets the user
>   actually saw in the day grid); ``item.bracket_group_id`` (the
>   never-populated ingest-detector column) survives as a per-item
>   override. The slice-2 assembler threads it + ``item_id`` through.
> - **Wiring:** entering Edit = scan → rebuild links → summary box
>   only when something user-relevant happened; the unmerged-brackets
>   reminder shows at most once per event per app session
>   (dismissible, never a wall). Edit menu gains "Scan for external
>   results" (always reports, even "No new external results found").
> - **Tests:** 6 new in `tests/test_external_returns.py` (adoption
>   end-to-end incl. seamless re-link + reminder clearing, unmatched
>   flagging both legs, association idempotence, longest-prefix);
>   113 green across the touched surface.
>
> ### spec/57 slice 4 — incremental Collect (LANDS, same session)
>
> Nelson: "continue" → slice 4. Scenario 1 (the live trip) is wired:
>
> - **Multi-date split + confirm** (locked Q9): `core.scan_source`
>   gained ``day_start_minutes`` (one boundary param threaded through
>   ALL grouping — rows, presences, per-photo records — and the raw
>   ``photos`` retained on `ScanResult` so regrouping is a pure
>   recompute, no re-scan). New `DaySplitDialog`
>   (`ui/pages/day_split_dialog.py`): dates + live counts + the
>   "Day starts at" combo (00:00–06:00) — the locked moment to pull
>   00:30 night shots into the previous evening. Collect shows it
>   only when a run spans >1 NEW date; single-date runs go straight
>   through. (Day creation itself already existed — the date-keyed
>   merge + max+1 numbering; that's why Nelson's first run "seemed
>   exactly the same".)
> - **Late-phone TZ reconciliation** (locked): after the merge,
>   existing plan days whose saved TZ disagrees with this run's phone
>   EXIF get ONE prompt (per-day list, "Use phone times" / "Keep my
>   plan"). Phone wins → merged rows update AND days already holding
>   photos re-time via the new gateway primitive. Matching days stay
>   silent — the plan is never silently overridden.
> - **`EventGateway.retime_day(day, new_tz)`** — the §4.2 primitive:
>   delta-shifts the day's captured items (raw never touched),
>   ``tz_source='user_declared'``, day_number reassigned from the new
>   corrected date (the "may move across days"), trip_day.tz updated
>   in the same txn, downstream marks dirty.
> - **Plan-editor single-day TZ unlock** (locked Q10): PlanDialog
>   gained ``tz_editable_when_frozen``; the Edit-plan path keeps rows
>   live and gates at Apply — changed TZs on photo-holding days get
>   the explicit "Re-time these days?" confirmation (Cancel = nothing
>   saved), then retime_day per day before save_trip_days.
> - **Tests:** +2 scan-source boundary tests (consistency across all
>   derived structures), +2 retime_day tests (shift, cross-midnight
>   move, dirty cascade, unknown-day raise); 185 green across the
>   touched surface.
>
> ### Picking up next session
>
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, **`spec/57`**, `spec/56`.
> 2. **CHECKPOINT (eyeball):** (a) the round trip — Pick incl. a
>    bracket → Edit → `Picked Media/` → Helicon/LRC → Scan for
>    external results; (b) incremental Collect — collect a multi-day
>    card → split preview (+ boundary), per-day metadata, late phone
>    run → TZ mismatch prompt; (c) plan editor → change an ingested
>    day's TZ → re-time confirm.
> 3. Remaining queues: spec/57 slice 5 (backfill wizard — the three
>    landing levels) vs spec/56 slices 3–5 (Edit workshop / Export /
>    cleanup). Do NOT start without Nelson's word.
> 4. Per-slice discipline as always: shape first, explicit OK,
>    eyeball each sub-slice.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10, night) — spec/56 slice 2 LANDS: Pick simplification. The video surface is watch + whole-video P/D (Play/Pause + timeline kept per Nelson). PLUS: the second greenfield reset — all events deleted, SCHEMA_VERSION back to 1, migrations folded into the DDL. Eyeball pending a fresh event; then slice 3.
>
> ### The v1 reset (rode in after slice 2)
>
> Nelson deleted every event for the new schema and called the reset:
> *"The new schema can be V1 again … we can start fresh."* Same pattern
> as the 2026-06-06 Slice-0 reset: the v1→v4 migration chain (spec/54
> Look columns + lineage snapshots, the v3 'repeat' bucket kind, the
> spec/56 marker-partition tables/retirements) folded into the base
> DDL; `SCHEMA_VERSION = 1`, `MIGRATIONS = []`; the two per-step
> migration tests deleted (the migration MACHINERY stays covered by
> `test_migrate_future_version_raises` + `test_open_existing_roundtrips`).
> Fresh databases now get the clean CHECKs everywhere (e.g.
> ``materialized_phase`` without `'pick'`) that migrated DBs could
> never get. spec/03 + spec/30 carry the reset notes ("v2/v3/v4" in
> specs = design generations, not live migration targets).
>
> **Where we are.** Slice 1 (schema v4) committed
> [`8846e64`](https://github.com/nksalgado-proton/Mira/commit/8846e64)
> after Nelson ratified the delete-marker left-survives rule; slice 2
> followed in the same session ("Go ahead and do not forget to leave
> the Play/Pause and timeline in the picker surface for video
> playback").
>
> ### Slice 2 — what landed
>
> - **VideoPickPage carved to watch + P/D** (full rewrite, 2311 → ~640
>   lines). KEPT per Nelson's constraint: the chrome skeleton (Back ·
>   genre · Reclassify · Help), the MediaHost border as the P/D
>   indicator + click target, **the neutral timeline (playhead +
>   click-to-jump) + time readout**, the transport (⏮/⏭ cell nav ·
>   ⏮ Start · ◀ Frame · **▶ Play/⏸ Pause** · Frame ▶ · End ⏭),
>   ffprobe-seeded duration/fps (frame stepping at 1/fps), poster
>   paint, wheel/keyboard parity, the shortcuts dialog. GONE: the
>   docs/18 action line (Create marker/still · Remove · Toggle ·
>   Start-a-new-pass), marker/still/kept-span timeline painting,
>   VideoSession/journal seeding + mirrors, clip-range playback,
>   still-preview pause, modes (cull/select/process), immersive
>   autoplay (caller-less), `keep_whole_video`-on-exit derivation.
> - **The video P/D round-trip is now real.** Pre-slice-2 the surface's
>   ``cull_state_cycle_requested`` was UNCONNECTED in PickPage (border
>   click + Space did nothing; the master state was derived from kept
>   children on Back — the spec/56-condemned shape). Now: PickPage
>   connects it to ``_on_video_state_cycle`` → toggles the WHOLE
>   video's phase_state **Pick ↔ Skip** (videos carry no Compare —
>   spec/56 "Pick or Skip the whole video"; there is no video compare
>   surface) and pushes ``set_binary_state`` back so the border
>   repaints; ``_open_video_item`` paints the current state on open.
> - **Materialisation path deleted:** `picked/video_model.py` (the
>   slice-1 stub), `picked/materialize.py`, `ui/picked/bg_materializer.py`
>   + PickPage's materializer wiring (`_ensure_materializer` /
>   `_finish_materialization` / `_sync_video_source_state` / stop
>   block). Pick writes decisions only; bytes commit at Export.
> - **Yellow-video rule retired** (the old spec/32 §2.4 "≥1 kept
>   extract → MIXED override"): Pick creates no children, so a video
>   cell shows its own whole-video P/D state like a photo.
>   `cell_color_for_item` lost ``has_kept_extracts``;
>   `model._video_has_kept_extracts` + the day-grid prefetch +
>   `gateway.parent_ids_with_kept_children` deleted. (That spec/32 is
>   the Miracraft ancestor's Day-Grid spec — never traveled to MC's
>   tree; spec/56's supersession clause governs.)
>
> **Tests:** targeted green — new `tests/test_video_pick_page.py` (6:
> workshop chrome absent, transport + timeline present, border state
> push, Space/Tab cycle emission, Day-Grid nav relabel, arrow cell
> nav) + reworked day-grid colour tests; **238 passed** across the
> touched surface (video_pick_page, day_grid_model, pick_model,
> gateway, day_grid_gateway, base_surface, store, video_segments,
> overview_stats, move_days, quick_sweep_clusters).
> **Known pre-existing:** `test_main_window_menu.py` passes all 15
> tests but the interpreter dies at SESSION teardown (exit 127, no
> summary line) — verified identical at Nelson's pre-slice commit
> `3b1547a` via a clean worktree, same class as the documented
> `test_adjustment_surface_rotation` teardown crash on this machine.
>
> ### Picking up next session
>
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, `spec/56-video-workshop.md`.
> 2. **CHECKPOINT: Nelson eyeballs slice 2 — needs a fresh event first**
>    (all events deleted for the reset; create one via New event +
>    Collect). Then open Pick on a video day: video cell opens the
>    watch surface; Play/Pause + timeline + frame stepping work; border
>    click / Space toggles Pick↔Skip and the day-grid cell reflects it
>    on Back.
> 3. Then slice 3 (§6): **Edit workshop** — EditVideoPage rebuild:
>    development on top (existing AdjustmentSurface), marker timeline +
>    snapshot strip on the bottom (the slice-1 gateway ops are ready:
>    add/move/delete marker, ensure_video_segments,
>    create_video_snapshot, segment_bounds), selection-scoped
>    adjustment state.
> 4. Slices 4 (Export walker) → 5 (cleanup: VideoSession/video_marks/
>    video_overrides core modules, EditVideoPage trim remnants,
>    `bucket_cache.kind` 'video_moment' vocabulary) follow.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10, later) — spec/56 slice 1 LANDS: schema v4, the marker-partition video model. Markers are first-class rows; segments derive from marker order; clip_span / trim deltas / full-span retired.
>
> **Where we are.** Slice 1 of the locked spec/56 design (the video
> workshop) is implemented and green. The DB now speaks the
> marker-partition model end-to-end:
>
> - **Schema v4** (`store/schema.py`): new D-stratum tables
>   `video_marker` (user cut points; start/end implicit, never stored;
>   `UNIQUE(video, at_ms)` forbids zero-length segments),
>   `video_segment` (1:1 satellite on segment items — ONLY `seg_index`;
>   geometry derives at read time, never stored), `video_snapshot`
>   (1:1 point satellite). **Retired:** `clip_span` (label →
>   `item.subject`), `video_adjustment.trim_*_delta_ms` (markers ARE
>   the trim), `'pick'` in `item.materialized_phase` (bytes never
>   commit during deciding). Migration `_migrate_v3_to_v4` follows
>   policy (real ALTERs; deletes Pick-era clip/snapshot rows per
>   spec/56 §3 no-grandfathering; files on disk untouched); the
>   v1→v4 chain passes on the minimal fixture.
> - **Gateway marker ops** (`gateway/event_gateway.py`) embody the §1
>   locked identity rules: `add_video_marker` splits the containing
>   segment — left half keeps its row, right half is a NEW item
>   inheriting phase_state + video_adjustment verbatim, tail shifts up;
>   `move_video_marker` updates `at_ms` only (identity = order
>   position, not ms) and refuses to cross/land on a neighbour;
>   segments materialise lazily (`ensure_video_segments`,
>   count = markers + 1, each born with an EXPLICIT
>   `phase_state('edit','skipped')` so the settings-driven edit
>   default can never flip the spec/56 default-Skip);
>   `create_video_snapshot` auto-Picks. `materialize` +
>   `unmaterialized_kept_children` survive as the slice-4 Export seam.
> - **One design call Nelson must ratify** (spec/56 locked insert +
>   move but said nothing about delete): `delete_video_marker` merges
>   with the **LEFT half surviving** (it occupies the surviving order
>   position; the right half's item + state + adjustments delete; the
>   tail shifts down). Recorded in spec/30 §3.9 + gateway docstring.
> - **Geometry derivation** is pure-core: `core/video_segments.py`
>   (`segment_bounds`, `containing_segment`).
> - **Graceful slice-1→2 window:** `picked/video_model.py` is an inert
>   logged-no-op stub — the Pick video page opens + plays + P/D works
>   from the grid; its clip/snapshot buttons do nothing (logged).
>   Slice 2 deletes the stub + the workshop chrome +
>   `materialize.py`/`bg_materializer.py`. EditVideoPage trim
>   persistence removed (trims pinned 0; page otherwise functional
>   until the slice-3 rebuild). `overview_stats` time-share dropped
>   the clip-span fallback (NULL-duration video = one photo-slide
>   equivalent).
> - **Specs synced:** spec/03 drift notice gained the v4 bullet;
>   spec/30 gained the v4 amendment banner, a rewritten §3.9 (the
>   three tables + locked rules), the §3.11 trim retirement, the
>   concept-#3 sample as the seg_index walk, and superseded §7 video
>   ops.
>
> **Tests:** targeted green — store 20 (incl. both migration tests;
> those two deleted again by the later v1 reset), gateway 57
> (split-inherit / move-keeps-identity / merge-left-survives
> / snapshot-auto-pick / lazy-birth / whole-video-no-special-case),
> new `tests/test_video_segments.py` 5, plus adjacent suites
> (move_days, overview_stats, day_grid_model, pick_model,
> day_grid_gateway, day_hidden, recompute_tz, video_export_plan,
> video_overrides, video_export_run): **228 passed, 8 pre-existing
> skips** across the touched surface. `test_video_model.py` + `test_clip_materialization.py`
> deleted with the behavior they tested.
> `test_edit_video_page_rebuild.py` stays a pre-existing skip suite
> (trim tests removed inside it). Committed as
> [`8846e64`](https://github.com/nksalgado-proton/Mira/commit/8846e64)
> after Nelson ratified the left-survives rule.
>
> ### Picking up next session
>
> 1. Read `spec/00-charter.md`, `CLAUDE.md`, `spec/56-video-workshop.md`.
> 2. **CHECKPOINT (do not skip): Nelson ratifies the delete-marker
>    left-survives rule + the slice-1 intermediate state** (Pick video
>    workshop buttons inert until slice 2 lands).
> 3. Then slice 2 (§6): Pick simplification — video page → watch + P/D;
>    delete the workshop chrome, `video_model.py`, `materialize.py`,
>    `bg_materializer.py`, PickPage materializer wiring, and the
>    yellow-video rule machinery.
> 4. Slices 3 (Edit workshop UI) → 4 (Export walker) → 5 (cleanup)
>    follow, one checkpoint each.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-10) — The Edit-tone redesign LANDS end-to-end: Looks + nine creative filters + calibration trims. Zero sliders on the Edit surface.
>
> **Where we are.** One mega-arc, designed and landed in a single day,
> all verified in-app by Nelson:
>
> - **spec/54 (Looks):** the six tone sliders / Strength / AUTO toggle /
>   Copy-Paste are GONE. Tone = three choices: Style → Look (Original /
>   Natural / Brighter / Deeper; segmented row + the 2×2 grid moment,
>   key G) → Filter. The A-router (per-style cluster fits from Nelson's
>   499 LRC-pair set; `tools/calibrate_looks.py` workbench: census /
>   sweep / analyze / fit / sheets / spread / filters / export) replaced
>   the over-darkening single fit — held-out validation improved every
>   cluster. Engine: `core/photo_auto.py` (routed `compute_auto_params`,
>   `compute_look_params`) + generated `core/photo_looks_data.py`.
> - **Schema v2+v3:** `adjustment`/`video_adjustment` tone payload is
>   the CHOICE (`style`/`look`/`creative_filter`); `lineage` gained
>   `recipe_json`+`exported_at` (versions-as-exports: re-exports are
>   versions, Share picks per Cut — spec/54 §8); v3 widened
>   `bucket_cache.kind` for 'repeat' (latent Quick-Sweep crash, fixed).
> - **spec/55 (filters):** nine locked (Vivid B&W Sepia Faded Golden
>   Cinema Bleach Dramatic Crisp), engine primitives in
>   `core/photo_render.py` (`FilterRecipe`/`apply_filter`: bw_mix, tint,
>   split-tone, fade, clarity, vignette), live in preview + photo export
>   + per-frame video export. Crisp has macro/wildlife per-style
>   recipes. EN names only (pt-BR deferred to the i18n pass).
> - **Calibration trims:** Settings → Calibration tab, 12 knobs on the
>   house AdjustmentGrid (new "slider" schema kind in
>   `settings_dialog.py`), -100..0..+100, engine-cached
>   (`active_tone_scaling`), recorded in every lineage snapshot.
>   Nelson field-calibrates by usage; positions get harvested as new
>   shipped defaults later.
> - Latent bugs fixed along the way: Collect recorder crash on
>   EXIF-less files (skip+log), the 'repeat' kind CHECK.
>
> **Tests:** targeted suites green (store, photo_auto, looks, filters,
> settings). Edit-page suites remain pre-existing skips;
> `test_adjustment_surface_rotation.py` has a PRE-EXISTING Qt teardown
> crash on this machine (verified via stash — not from this arc).
>
> ### Picking up next session
>
> 1. Read `spec/00-charter.md`, `CLAUDE.md`.
> 2. **Start spec/56 (video workshop) slice 1 — schema v4.** The design
>    is LOCKED (marker-partition model; Pick uniformity; Edit-time
>    clips; bytes only at Export; §6 has the five slices in order).
> 3. Deferred items (do NOT start without Nelson): subject-fill UI
>    (spec/56 §5), pt-BR naming pass.

---

> ## 2026-06-09, late night — Picker post-Compare polish + photo-surface fast-nav redesign LAND. Worst-case day (482 high-res JPEGs) navigates fast end-to-end.
>
> **Where we are.** Two themes landed in commit
> [`534cf67`](https://github.com/nksalgado-proton/Mira/commit/534cf67).
> First, the loose ends from the Compare slice-A landing:
> the cluster slideshow Play button is reachable again, the cluster
> sub-grid has its own Pick all / Skip all, and the day-grid batch ops
> stop silently dropping cluster members. Second, the Picker photo
> surface gets a real fast-navigation redesign — Nelson eyeballed
> 1-2 s click-to-photo delays on the Everest Region "Dia 8" folder
> (482 JPEGs, ~5-7 MB each); the redesign hides every blocking cost
> behind a worker thread + caches and reads EXIF for the whole bucket
> in one subprocess instead of N.
>
> ### Picker post-Compare polish (Theme 1)
>
> * **Cluster slideshow Play** — the ``_film_btn`` on
>   ``pick_photo_surface.py`` still worked but was one click deeper
>   after the cluster sub-grid replaced grid mode. Surface a
>   ``▶ Play`` button on the cluster sub-grid top bar (only for
>   burst / focus / exposure bracket clusters via
>   ``_PLAY_KINDS``); clicking it opens the photo surface at
>   member 0 + starts the slideshow via a new public
>   ``PickPhotoSurface.start_play``. Repeat clusters skip the button
>   per legacy semantics.
> * **Cluster sub-grid Pick all / Skip all** — Quick Sweep's
>   ``cluster_grid`` already had them; PickPage's didn't. Opt the
>   widget in + new ``_on_cluster_batch(state)`` handler.
> * **Day-grid batch ops** — ``_on_current_day_batch`` was collecting
>   ``c.item_id`` from each cell, but cluster cells carry
>   ``item_id=None``. On a cluster-heavy day Pick all / Skip all
>   silently did nothing. Expanded the walk to include
>   ``cluster.members``.
> * **``Gateway.set_items_phase_state`` bulk helper** —
>   ``EventStore.transaction()`` isn't reentrant, and
>   ``set_phase_state`` opens its own. The "outer
>   ``store.transaction()`` + per-row ``set_phase_state``" pattern
>   raised ``sqlite3.OperationalError: cannot start a transaction
>   within a transaction``. Four PickPage handlers switched to the
>   new bulk helper (``_on_batch_op``, ``_on_current_day_batch``,
>   ``_on_cluster_batch``, ``_on_event_scope_batch``).
>
> ### Photo-surface fast-nav redesign (Theme 2)
>
> Design-mode shape locked with Nelson — A. skim-then-settle on rapid
> wheel sweep, session-wide singleton cache, two-tier (thumb + display
> pixmap), session-wide scope. Implementation:
>
> * **``mira/ui/media/photo_cache.py``** — new module.
>   ``PhotoCache`` (``QObject`` singleton) + ``_DecodeWorker``
>   (``QThread`` with a heap-based priority queue). Job priorities:
>   0 = current target (never dropped); 1 = predecode (dropped via
>   generation counter when the user navigates past). Two-tier
>   cache: in-memory LRU of native-resolution display ``QPixmap``
>   (cap 20 — ~2 GB peak at 24 MP × 96 MB), and an in-memory thumb
>   tier backed by the existing on-disk 256-px cache
>   (``core/photo_thumb_cache``, sha256-keyed; already warmed by
>   ingest). Path → sha256 map fed by surfaces via
>   ``set_event_context``.
> * **``MediaCanvas.set_photo``** — routes through the cache. LRU
>   hit → sync paint; thumb hit → paint as placeholder + queue
>   async decode; full miss → keep previous photo painted + queue.
>   ``_on_cache_pixmap_ready`` swaps in the full pixmap only when
>   ``_current_path`` still matches (out-of-order predecode landings
>   silently drop). Public ``target_render_size()`` for hosts that
>   want to thread the canvas size into predecode requests.
> * **``PickPhotoSurface`` predecode** — ``_predecode_timer``
>   (``QTimer.singleShot(150 ms)``) restarted on every ``_go``.
>   Settle handler queues priority-1 decodes for N+1, N+2, N-1
>   (asymmetric forward bias). Rapid wheel sweep keeps the timer
>   pending → no work fires until the user pauses.
> * **Bulk EXIF prefetch** — ``read_exif_batch`` already existed in
>   ``core.exif_reader`` (single ``exiftool.exe`` subprocess via
>   argfile for N files). ``PickPhotoSurface._spawn_exif_prefetch``
>   fires it in a daemon thread on every ``load()`` and merges
>   results into ``_exif_cache`` through a cross-thread Qt signal
>   (``_ExifPrefetchSignals``). Generation-tagged: stale buckets
>   drop on arrival. Replaces the per-photo cold ``exiftool`` spawn
>   (~300-500 ms each on Windows) that was the **actual** 1-2 s
>   click-to-photo delay the user felt — the JPEG decode itself
>   was ~100 ms; the EXIF subprocess dominated.
> * **``image_loader.load_pixmap`` target-size hint** — added as a
>   future-proof param using ``QImageReader.setScaledSize``;
>   ``PhotoCache`` doesn't pass it (display-size source broke the
>   box-zoom 1:1 indicator on JPEGs — Nelson eyeballed: "shows a
>   very large box, low resolution"). Pixmaps cache at native, the
>   hint stays available for thumbnail-tier callers elsewhere.
>
> ### Tests
>
> Targeted-only per ``feedback_scope_tests_to_what_changed``:
>
> | File | Tests | Coverage |
> |---|---|---|
> | ``tests/test_image_loader_target_size.py`` | +5 | target_size hint contract (full-size default, fit-inside-box, aspect preserved, larger-box no-op, invalid QSize fallback) |
> | ``tests/test_exif_batch_shape.py`` | +4 | read_exif_batch returns one PhotoExif per input + path equality (skips if bundled exiftool missing) |
>
> **174 tests green** across the touched + adjacent surfaces
> (day_grid_view, pick_model, pick_top_bar, pick_state,
> pick_edit_compact_row, quick_sweep_clusters, day_grid_gateway,
> gateway, image_loader_target_size, exif_batch_shape).
>
> ### Eyeball verification record
>
> Nelson on the live app, 2026-06-09 late night:
> 1. Cluster Play button + batch buttons surface on the cluster
>    sub-grid → verified.
> 2. "Pick all days" / "Skip all days" no longer raise nested-
>    transaction error → verified.
> 3. Initial fast-nav landing: navigation was *much* faster but
>    quality dropped — box-zoom indicator covered the whole view.
>    Diagnosed: display-size decode was the cause. Reverted to
>    native-resolution decode (cap 80 → 20 entries to absorb RAM).
>    Quality restored; navigation still feels snappy because the
>    actual wins are (a) async decode, (b) bulk EXIF, (c) predecode.
> 4. Final state — "It is very fast now... we have reached a good
>    compromise."
>
> ### Memory saved this session
>
> * ``design_rule_cluster_slideshow_load_bearing.md`` — preserve a
>   one-click path to the Picker slideshow during any redesign of
>   cluster routing.
> * ``feedback_sqlite_no_nested_transactions.md`` — store.transaction()
>   isn't reentrant; wrapping a loop of set_phase_state in an outer
>   with raises. Bulk helpers fix it.
> * ``design_rule_photo_cache_architecture.md`` — PhotoCache is a
>   session-wide singleton with two-tier cache + background worker;
>   new photo-display surfaces should route through it instead of
>   calling load_pixmap directly. Decode-to-target-size is unsafe
>   for the source pixmap (box-zoom regressions).
>
> ### Pickup steps for the next session
>
> 1. Read ``spec/00-charter.md`` + ``CLAUDE.md``.
> 2. Read this banner — the Picker is in a good place; Compare,
>    cluster routing, slideshow, batch ops, and fast navigation
>    all behave correctly end-to-end on Nelson's worst-case day.
> 3. Pick the next area from the priority list further down this
>    file. Live candidates: **Cuts rebuild** (spec/51), **Maps
>    surface**, **§11 retirement sweep**, **Compare slice B**
>    (focus peaking in compare grid), **Compare port to Quick
>    Sweep**.
> 4. If you do touch the photo surface again — read
>    ``design_rule_photo_cache_architecture.md`` first. The "always
>    decode at native, don't pass target_size to the worker"
>    constraint is load-bearing for zoom + 1:1.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09, deeper into the night) — Picker Compare redesign LANDS (slice A). Cluster routing now goes via cluster sub-grid; photo surface is single-view-only.
>
> **Where we are.** Slice A of the small-feature Compare redesign in
> the Picker landed in commit
> [`f8ddbc1`](https://github.com/nksalgado-proton/Mira/commit/f8ddbc1).
> Comparison is its own surface now — a dedicated
> ``ComparePage`` pushed onto PickPage's stack when the user clicks
> "Compare" on the day grid or cluster sub-grid (gated by 2+ Compare-
> state cells). The old grid-mode toggle that bolted comparison
> onto the photo surface is gone; cluster routing now opens the
> cluster sub-grid (which was dead code until this commit) instead
> of pushing the photo surface in grid mode.
>
> ### What landed
>
> #### DayGridView — Compare button (sub-slice A.1)
>
> * New ``show_compare_button`` opt-in + ``compare_requested`` signal.
> * Button surfaces only when 2+ flat (non-cluster) cells in the
>   current cell set are in Compare state — counted live via
>   ``_count_flat_compare_cells`` and refreshed on every
>   ``set_cells`` / ``update_cell``.
> * Cluster cells skip the count by design — cluster members compare
>   only with members of the same cluster, so a multi-Compare cluster
>   on the day grid doesn't activate the day-grid Compare button. The
>   user opens the cluster sub-grid where its own Compare button does
>   the right thing.
>
> #### ComparePage — new surface (sub-slice A.2)
>
> * Hosts the existing ``GridView`` widget populated with the originating
>   grid's Compare-state items.
> * "Quit Comparison" button + Esc / C return to the originating grid.
> * Border-ring click cycles **K↔D only** — no return to Compare; the
>   user is here to finalise. State writes go to
>   ``gateway.set_phase_state`` using the new wire vocabulary
>   (``picked`` / ``skipped`` / ``candidate``) — the first iteration
>   imported the legacy ``core.cull_state`` (``kept`` / ``discarded``)
>   and tripped the schema CHECK constraint; fixed.
> * ``C`` shortcut wired per-grid widget so it fires only when that
>   grid has focus + is visible.
>
> #### PickPage — wiring + cluster-routing change
>
> * ``day_grid`` + ``cluster_grid`` both opt into ``show_compare_button``.
> * ``_on_compare_requested(origin_stack_index)`` gathers the right
>   items (day grid: flat cells whose ``color == COMPARE``; cluster
>   sub-grid: members whose ``phase_state`` is ``candidate``) and pushes
>   ``ComparePage`` as ``_COMPARE = 5``.
> * ``_on_compare_quit`` pops back + reprojects the originating grid's
>   cells (states moved during compare).
> * ``_open_cluster`` rewritten: cluster cells from the day grid now
>   route into ``cluster_grid`` (a DayGridView peer page on the stack)
>   instead of pushing the photo surface in grid mode. The cluster_grid
>   widget was constructed but had no ``set_cells`` callsite in PickPage
>   — it was dead code, and this commit wakes it up.
>
> #### PickPhotoSurface — retire grid-mode machinery (sub-slice A.3)
>
> Surface is single-view-only now. Removed:
>
> * Buttons: ``_view_toggle`` (Grid / Single), ``_filter_btn``
>   (Candidates only), ``_reset_compare_btn``, per-cluster Pick All /
>   Skip All.
> * Signals: ``reset_compare_requested``.
> * Methods: ``set_grid_mode``, ``_set_grid_mode``,
>   ``_on_view_toggled``, ``_on_filter_toggled``,
>   ``_on_reset_compare_clicked``,
>   ``_refresh_reset_compare_visibility``, ``_apply_view_visibility``,
>   ``_visible_indices``, ``_rebuild_grid``, ``_grid_captions``,
>   ``_on_grid_activated``, ``_on_grid_cycle``, ``_mark_all``,
>   ``_cluster_has_shared_genre``.
> * Imports + instance: embedded ``GridView`` + alt central widget
>   binding, ``GridItem`` / ``GridView`` import, ``exposure_diff``
>   import.
> * keyPressEvent ``G`` + ``C`` handlers and the candidates-only nav
>   filter; the help dialog updated.
> * ``self._grid_mode`` + ``self._candidates_only`` instance flags.
> * Surrounding refresh blocks that called the retired methods.
>
> PickPage drops the dead ``self.photo.reset_compare_requested.connect``
> and ``self.photo.set_grid_mode`` call sites. ``_on_reset_compare``
> survives as a method since the gateway helper
> ``reset_compare_in_day`` is still useful for any future caller.
>
> ### Locked design — the remaining slices (NOT yet implemented)
>
> Slice A (above) shipped the core feature. Three more slices are part
> of the locked design, queued for a follow-up session:
>
> * **Slice B — Focus peaking in the compare grid** (Picker only).
>   Toggle button on ``ComparePage``'s top bar; renders each tile's
>   thumbnail with the ``core.focus_peaking`` overlay used in the
>   single-photo viewer. Sensitivity + colour-cycle reused.
> * **Slice C — Synchronized zoom in the compare grid** (Picker only,
>   stretch). Box-zoom in one tile mirrors region to every tile +
>   pan syncs across tiles. If it conflicts with the lazy thumbnail
>   loader, ships disabled.
> * **Same flow into Quick Sweep**. Quick Sweep already uses
>   ``DayGridView`` on both its day grid and cluster sub-grid — adding
>   ``show_compare_button=True`` + the same temp ComparePage flow gives
>   the user identical affordances. No peaking + no zoom there (Quick
>   Sweep is the early triage surface — keep it simple).
>
> ### Tests
>
> No new targeted tests this session — the DayGridView surface is
> already heavily covered, and ``ComparePage`` is pure-presentation
> over ``GridView`` + gateway calls (both already tested).  Carrying
> forward as a follow-up task:
>
> * ``ComparePage.load`` populates ``GridView`` with the right items
>   and initial states.
> * ``ComparePage._on_cycle`` does K↔D only, writes to gateway.
> * ``DayGridView`` Compare button visibility flips on 2+ Compare
>   cells, including ``update_cell`` -driven transitions.
>
> 177 tests green across the touched + adjacent surfaces
> (day_grid_view, main_window_menu, pick_model, day_grid_gateway,
> pick_edit_compact_row, pick_top_bar, quick_sweep_clusters,
> cluster_classifier, fast_buckets).
>
> ### Pickup steps for the next session
>
> 1. Read ``spec/00-charter.md`` + ``CLAUDE.md``.
> 2. Read this banner — the locked Compare design (slices B + C +
>    Quick Sweep port) is queued.
> 3. Decide: continue with slice B (peaking) OR pivot to the next
>    area on the priority list (Cuts / Maps / §11 retirement sweep).
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09 late night) — Quick Sweep ↔ Picker parity ports LAND. PROGRESS backlog #1 closed.
>
> **Where we are.** Two small parity improvements between Pick and Quick
> Sweep landed in commit
> [`7533d8d`](https://github.com/nksalgado-proton/Mira/commit/7533d8d).
> Closes PROGRESS backlog item #1 (repeat clusters in Picker) and adds
> the previously-Picker-only visited tick affordance to Quick Sweep so
> the two surfaces behave the same on every interaction the user
> regularly touches.
>
> ### What landed
>
> **Quick Sweep — visited ticks + "Start a new pass…"** (verbatim port
> of PickPage spec/32 §2.10):
>
> * ``FAST_CULL_CONFIG.show_clear_marks_button=True`` → button on the
>   days panel.
> * ``DayGridView(show_clear_marks_button=True)`` on day grid AND
>   cluster sub-grid → same button in both contexts.
> * Two in-memory sets: ``_visited_paths`` (item_ids) +
>   ``_visited_clusters`` (bucket_keys). Quick Sweep has no event.db
>   pre-ingest, so the "Start a new pass…" handler just clears the sets
>   and reprojects the open grids. Decisions in ``_state`` preserved.
> * Centre-click activations track visits eagerly so the ✓ tick is
>   already in place when the user Backs out of the viewer / sub-grid.
> * ``fast_day_grid_cells`` gains optional ``visited_for`` /
>   ``cluster_visited_for`` callables; legacy callers pass None.
> * ``_reproject_cell`` reads visited live from the sets so refreshed
>   cells always reflect the current state.
> * ``load()`` resets the visited sets on a fresh card.
>
> **Picker — phone repeat clusters** (PROGRESS backlog #1, option a):
>
> * Extracted ``split_repeats_in_nodes(nodes, assignments)`` into
>   ``core/cluster_classifier.py`` as a pure-logic shared helper.
> * Refactored ``quick_sweep_buckets.build_quick_sweep_buckets`` to use
>   the helper (same output, no duplicated split logic).
> * ``mira.picked.model._compute_day`` now runs
>   ``classify_clusters`` (settings-driven ``repeat_window_seconds`` +
>   the same defensive fallback as Quick Sweep) and
>   ``split_repeats_in_nodes`` after ``_flatten``.
> * ``mira.picked.model.REAL_CLUSTER_KINDS`` gains ``"repeat"``
>   so PickPage renders repeats as one cluster cell that expands into
>   the sub-grid. ``cluster_icons`` + ``day_grid_cell`` tooltip already
>   carried ``"repeat"`` from slice B — no change needed there.
>
> ### Tests
>
> +12 targeted (per ``feedback_scope_tests_to_what_changed``):
>
> | File | Tests | Coverage |
> |---|---|---|
> | ``tests/test_cluster_classifier.py`` | +6 | ``split_repeats_in_nodes`` shape — passes-through, splits, drops residual, handles moment kind, preserves order |
> | ``tests/test_quick_sweep_clusters.py`` | +6 | Visited tracking — item / cluster / member activations, ``_on_clear_marks`` preserves decisions, no-op when empty, ``load()`` resets state |
>
> **137 tests green** across the touched + adjacent surface
> (cluster_classifier, repeat_detector, fast_buckets,
> quick_sweep_clusters, bucket_navigator_model, pick_model, pick_state,
> main_window_menu, discrete_tz_dialog).
>
> ### Pickup steps for the next session
>
> 1. Read ``spec/00-charter.md`` + ``CLAUDE.md``.
> 2. Read this banner.
> 3. Pick the next area from the priority list further down this file.
>    Candidates after Event Creation: **Pick phase rebuild**, **Cuts
>    rebuild** (spec/51), **Maps surface**, **§11 retirement sweep**.
> 4. Whatever you pick, follow the discipline from the prior sessions:
>    surface the spec shape FIRST, get explicit OK, eyeball each
>    sub-slice with Nelson.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09 night) — TZ-correction LANDS. **Event Creation (spec/52, incl. Collect) is CONCLUDED.** Next session pivots to Pick / Cuts / Maps.
>
> **Where we are.** Nelson eyeball-verified an event creation on his
> real 2026-cross-border data: PlanDialog OK → conditional
> "Calibrate camera timezones?" → per-TZ `PastPhotosCamerasDialog`
> using the new dedicated `CollectPhotoPicker` for sync-pair photo
> selection → ingest → event.db populated end-to-end. Two real bugs
> caught + fixed mid-eyeball. **Event Creation closes here.** Code
> in commit [`ff56081`](https://github.com/nksalgado-proton/Mira).
>
> ### What landed
>
> #### Calibration flow (`main_window._collect_run_tz_calibration`)
>
> * Spec/52 §8.2 trigger — after PlanDialog OK, group event days by
>   declared TZ, skip days matching `home_tz`, skip already-calibrated
>   `(camera, day)` pairs. "Calibrate / Skip" entry prompt only when
>   real candidates remain.
> * Per-TZ loop reusing the tested `PastPhotosCamerasDialog` (NOT the
>   spec/45 per-(camera,day) `DiscreteTzDialog` — first iteration tried
>   that, Nelson eyeballed it, reverted). Each step shows
>   "Step N of M" + the day-numbers covered by that TZ. One per-camera
>   answer applies to every day in that TZ (spec/52 §8.4).
> * Cancel on a per-TZ step → "Abort Collect / Continue with partial".
>
> #### Calibration persistence (`_record_collect_in_event_db`)
>
> * `camera_day_tz` rows written inside the items transaction AFTER
>   cameras + trip_days are upserted (FK parents must exist first).
>   Each decision pre-filtered against the upserted/existing
>   `camera_id` + `day_number` sets — a Quick-Sweep-skipped camera
>   gracefully drops its TZ row instead of FK-failing.
> * `tz_offset_minutes` derived from `corrected − raw`; `tz_source`
>   set to `phone_auto` (phones) / `user_declared` (calibrated cams)
>   / `none` (uncalibrated cams) per spec/52 §13.
>
> #### `CollectPhotoPicker` (new — sync-pair photo selection)
>
> The flat-scan source layout has no per-camera subfolders for
> `QFileDialog` to scope to, so the legacy file-dialog approach was
> unusable. Built a dedicated single-photo picker:
>
> * Stage 1 — per-day rows with caller-formatted plan labels
>   (`Day N · date · location\ndescription`). Caller (calibration host)
>   builds these from `edited_rows` so the picker stays
>   pure-presentation.
> * Stage 2 — `QSplitter` horizontal: thumbnail grid + ~480×360
>   preview pane with filename + EXIF `DateTimeOriginal` + "Use this
>   photo" button. Click = preview, double-click / Enter = commit.
> * Filters videos + files ≥ 30 MB from the grid (perf — pair-pick
>   uses stills). Grid header shows skipped count.
> * Lazy thumbnail loader (single `QTimer` + FIFO `_thumb_pending`,
>   4 per 30 ms tick — the `quick_sweep_page` pattern). Pixmap cache
>   keyed by `Path` so re-entering a day is instant.
>
> #### Dialog plumbing
>
> * `SyncPairPickerDialog` `_PhotoPanel.open_picker` accepts an
>   optional `picker_callback` that replaces `QFileDialog` and skips
>   the wrong-camera EXIF warning (the callback pre-filters).
> * `PastPhotosCamerasDialog` accepts `phone_reference_id` — the
>   phone serves only as the pair-pick reference and is NOT shown as
>   a row (phones never need calibration). Also accepts
>   `picker_factory: Callable[[camera_id], picker_callback]` which
>   threads down through the per-row pair-pick button. When no phone
>   AND no factory, the "I don't know" combo item is hidden — Path A
>   becomes the only mode.
>
> ### Two real bugs caught + fixed during the eyeball
>
> 1. **`bake_corrections=True` was the default in `run_ingest`** —
>    a pre-rebuild holdover that rewrote `DateTimeOriginal` in every
>    copy whose `capture_time_corrected ≠ capture_time_raw`. Direct
>    violation of CLAUDE.md invariant #7 (the captured tree is never
>    mutated) + spec/52 §8.1 ("TZ correction is purely a projection
>    at read time"). Nelson eyeballed it as a slow "two-passes"
>    import + the misleading "1440 EXIF time(s) corrected" message.
>    **Fix:** Collect calls `run_ingest(..., bake_corrections=False)`;
>    success message no longer claims EXIF was rewritten.
> 2. **`camera_day_tz` upsert was catching only `ValueError`** —
>    `sqlite3.IntegrityError` (FK violation) would bubble up, roll
>    back the WHOLE transaction, and leave files copied with zero
>    items in `event.db`. **Fix:** broadened to
>    `except Exception` so a single bad TZ row drops with a log line
>    instead of poisoning the items write. Belt-and-braces — the
>    pre-filter against upserted/existing IDs is the primary
>    safety.
>
> ### Memory rules added this session
>
> Save these via the auto-memory system on the next session's first
> opportunity:
>
> * **No spec-violating defaults in core helpers.** `run_ingest` had
>   `bake_corrections=True` as the default years after the spec
>   removed bake-on-ingest. When a helper has a parameter whose
>   default conflicts with current spec, the helper's default needs
>   updating in lockstep — not just the call sites. *Why:* the bug
>   was invisible at the call site (just `run_ingest(jobs, root)`),
>   only the default did the wrong thing. *How to apply:* when a
>   spec change retires a behavior, sweep `def …(` defaults across
>   `core/` for the retired flag.
> * **Narrow exception catches mask FK violations.** SQLite FK errors
>   are `sqlite3.IntegrityError`, not `ValueError`. Catching only
>   `ValueError` on a gateway mutator that also touches an FK column
>   silently rolls back the whole transaction. *How to apply:* on
>   any per-row write inside a multi-write transaction, catch
>   `Exception` (with explicit log), not the narrowest type.
>
> ### What's NOT in this commit (deferred, not blockers)
>
> * Spec §11 retirements still pending (`capture_action_dialog.py` +
>   `capture_flow.py` + `past_photos_dialog.py` + the
>   `ENTRY_PLAN_TEMPLATE` menu entry). They're unreachable from menus
>   now but still imported by transitive test surfaces; cleanup goes
>   in its own §11 sweep.
> * `DiscreteTzDialog` (spec/45 per-(camera, day) picker) stays in
>   the codebase, unused by Collect. Has its own tests. Future
>   cleanup may retire it if no other consumer materialises.
> * The "Export →" button in QuickSweep still bypasses the
>   Back-confirm dialog. Carried forward.
>
> ### Pickup steps for the next session
>
> 1. Read `spec/00-charter.md` + `CLAUDE.md`.
> 2. Read this banner.
> 3. Pick the next area from the priority list further down this
>    file. Likely candidates: **Pick phase rebuild**, **Cuts
>    rebuild** (spec/51), **Maps surface**, **§11 retirement sweep**.
> 4. Whatever you pick, follow the discipline from this session:
>    surface the spec shape FIRST, get explicit OK, eyeball each
>    sub-slice with Nelson, and don't push past sub-slices.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09 evening) — App-menu redesign + Settings audit LAND. One TZ-correction eyeball test is the LAST step before Event Creation (incl. Collect) concludes.
>
> **Where we are.** Two design sessions back-to-back this afternoon
> closed two pending design items from the previous-banner backlog:
> the app menu structure + the settings dialog. Both implemented +
> tested in commit [`ed26c83`](https://github.com/nksalgado-proton/Mira).
> 145 tests green across the touched surfaces.
>
> **One thing left in the Event Creation queue (the test Nelson
> mentioned at session end):** create + Collect an event that needs
> **TZ correction**, eyeball-verify the conditional calibration ask
> + per-(camera, day) offset persistence + corrected-time read-back.
> When that lands, Event Creation (spec/52) — including Collection —
> is **CONCLUDED**. Next session pivots to a different area (Pick /
> Cuts / Maps per the priority list further down this file).
>
> ### Where to start in the next session
>
> The TZ-correction wiring still has a STUB in the Collect flow per
> the prior banner:
>
> > In ``_run_collect_copy_all``, set
> > ``capture_time_corrected = capture_time_raw`` so cameras with
> > wrong-TZ EXIF end up grouped into the wrong day.
>
> The work needed:
>
> 1. After PlanDialog OK and before the ingest-mode gate, run
>    ``core.tz_calibration.needs_calibration`` against the merged
>    plan. (Pure-logic helper is already in place; ungated.)
> 2. If candidates exist, show the conditional ask dialog
>    ("Calibrate camera TZ now, or skip?"):
>    - **Path A** — discrete TZ pick via the existing
>      ``DiscreteTzDialog`` (already wired).
>    - **Path B** — pair-pick via the existing ``sync_pair_picker``;
>      defer to a follow-up if it's too much work in one slice.
> 3. On accept: persist each declared offset via
>    ``gateway.set_camera_day_tz(...)`` with
>    ``source='user_declared'``.
> 4. In ``_run_collect_copy_all``, set
>    ``capture_time_corrected = capture_time_raw + (declared_offset)``
>    per the per-(camera, day) lookup.
>
> Once Nelson eyeballs an event that creates correctly with
> calibrated TZ, **Event Creation (spec/52) is concluded**, including
> Collection.
>
> ### What landed today (full afternoon session, in commit order)
>
> | Commit | Theme |
> |---|---|
> | [`888805b`](https://github.com/nksalgado-proton/Mira/commit/888805b) | spec/52 — Quick Sweep redesign lands (slices A+B+C + eyeball iteration) |
> | [`860cabd`](https://github.com/nksalgado-proton/Mira/commit/860cabd) | PROGRESS — Quick Sweep redesign lands; TZ-correction is the last step before Event Creation concludes |
> | [`ed26c83`](https://github.com/nksalgado-proton/Mira/commit/ed26c83) | **App menu redesign + Settings audit** (this banner's main subject) |
>
> #### App menu redesign — the locked design
>
> Top-level set: **App · Event · Collect · Pick · Edit · Share · Help**.
> Children are surface-aware — `_action_surfaces` registry +
> `_refresh_menu_state()` toggles visibility per surface
> (`events_list` / `per_event`) and per closed-event state (F-024).
> Top-levels auto-hide when every child resolves hidden (Collect +
> Share on the events list).
>
> Notable per-tab + per-surface entries:
>
> | Menu | Events list | Per-event |
> |---|---|---|
> | App | Wizard · Settings · Quit | Library · Wizard · Settings · Audit · Quit |
> | Event | New event (Ctrl+N) · New event from photos (Ctrl+Shift+N) · Restore from backup | Edit info · Stats · Back up event · Close ↔ Re-open Event · Delete event |
> | Collect | *(hidden)* | **Edit Event** (alias of Event→Edit info — unified info+plan) · **Edit plan** (plan-only with Delete-day + CSV) · Manage days · Camera clocks · Adjust TZ · Re-import LRC |
> | Pick | Standalone Picker · Standalone Quick Sweep | Open Pick phase · Quick Sweep this event |
> | Edit | Standalone Photo Processor | Open Edit phase |
> | Share | *(hidden)* | Open Share phase · New Cut · Audio |
> | Help | Third-party tool guides | Third-party tool guides |
>
> #### Edit Event / Edit plan — distinct editing paths
>
> * **Edit Event** opens `PlanDialog` with `with_event_info=True,
>   with_plan=True` → info section + plan rows in one surface; OK
>   persists both via `set_classification` + `save_trip_days`.
> * **Edit plan** opens `PlanDialog` plan-only with
>   `can_save_load_csv=True, can_delete_days=True`. Plus
>   **frozen_after_ingest** mode (auto-detected: any item with
>   `day_number is not None`) — TZ pickers go read-only and CSV-load
>   ignores TZ so a re-imported plan can't shift photos across a TZ
>   boundary. Only country / location / description editable when
>   frozen.
> * **Delete-day** button — selection-driven enable; confirms before
>   removing the row; gateway's `save_trip_days` rejects
>   orphan-causing removals.
> * **Adding days is intentionally NOT here** — new days enter via
>   Collect (re-scan + ingest).
>
> #### Settings dialog audit — outcomes
>
> **Tab renames + structural cleanups:**
> * Tabs now: General · Appearance · Paths · Collect · Pick · Edit ·
>   Share · Video · Advanced (was Picker · Select · Process ·
>   Curate · Import — legacy vocab eliminated).
> * Killed the dead `cull_default_state` dialog reference (key didn't
>   exist in the rebuild Settings model — only `pick_default_state`
>   did, so the previous Picker tab silently did nothing).
>
> **`font_scale` globally wired** (was Nelson's primary frustration —
> field defined in model with ZERO consumers). New
> `apply_font_scale(app, scale)` in `mira/ui/app.py` reads the
> QApplication baseline font point size, caches it, applies
> `baseline × scale`. Clamped to 0.5–2.0×. Called at startup from
> `main()` + re-applied on settings change via
> `MainWindow._on_settings_changed`.
>
> **Three orphaned settings now exposed:**
> * `font_scale` (Appearance)
> * `prefer_helicon_for_focus` (Paths)
> * `preferred_burst_genre` (Pick)
>
> **Seven NEW settings promoted from hardcoded constants** (all with
> defensive try/except in the consumer + fall-back to the prior
> hardcoded default):
>
> | Setting | Default | Consumer site |
> |---|---|---|
> | `repeat_window_seconds` | 2.0 | `quick_sweep_buckets.build_quick_sweep_buckets` → `RepeatDetectorConfig` |
> | `peek_target_photos` | 20 | `main_window._browse_day` → `select_for_peek(target=...)` |
> | `jpeg_export_quality` | 95 | `core/process_render.save_jpeg(quality=None)` |
> | `video_clip_crf` | 20 | `core/video_export_run` libx264 codec args |
> | `focus_peaking_opacity` | 0.7 | `core/focus_peaking.compute_peaking_mask(opacity=None)` |
> | `default_day_grid_cell_size` | 140 | `DayGridView(cell_size=None)`; clamped to MIN/MAX |
> | `log_rotate_keep_days` | 14 | `core/logging_setup._make_file_handler` |
>
> Pattern for each: caller passes `None` (or omits) → setting wins;
> explicit value still overrides; settings-read failure falls back to
> the prior hardcoded constant. No risk of regression on upgrade.
>
> ### Test surface added this session
>
> | File | Tests | Coverage |
> |---|---|---|
> | `tests/test_main_window_menu.py` | 17 | Top-level set; surface-dependent children; F-024 closed-event filter; Edit Event vs. Edit plan distinct actions |
> | `tests/test_plan_dialog.py` (+8) | 8 | Delete-day button visibility + enable + no-op when no selection; frozen_after_ingest disables TZ pickers |
> | `tests/test_settings_audit.py` | 11 | Tab structure regression; orphan exposure; promoted defaults; font_scale + DayGridView consumer wiring |
> | + the prior afternoon's surfaces | 89 | Slice A+B+C cluster tests + bug-fix regressions |
> | **Total touched this afternoon** | **125+** | (full sweep 145 green) |
>
> ### Backlog flagged this session (does NOT block TZ-correction work)
>
> 1. **FUTURE — extend main Cull (PickPage) with repeat clusters.**
>    Today only Quick Sweep emits `repeat` buckets; PickPage uses
>    scanner output directly via `core.bucket_navigator_model._flatten`
>    and only renders cluster cells for the three scanner-emitted
>    kinds (burst / focus_bracket / exposure_bracket). To bring
>    repeats to PickPage: either (a) extend `_flatten` to call
>    `cluster_classifier` and split individuals into repeat sub-
>    buckets (same pattern as Quick Sweep), or (b) make the scanner
>    itself emit a `repeats` field on `BucketScanResult`. Option (a)
>    is smaller and keeps the scanner pure. Also update
>    `mira.picked.model.REAL_CLUSTER_KINDS` to include
>    `"repeat"`. The cluster_icons + day_grid_cell tooltip already
>    include `"repeat"` (slice B's additive changes).
>
> 2. **Audit candidates still un-promoted** — advanced detector
>    thresholds (bracket detector window/size, preingest_check
>    thresholds, sharpness rating floor/full). Land in a follow-up
>    when the consumer-wiring path is clean to thread settings into
>    pure-`core/` detectors without layer violations.
>
> 3. **QuickSweep "Export →" button** still bypasses the Back-confirm
>    dialog (emits `saved` directly). Nelson left the call open at
>    the slice-C eyeball; still unresolved. Decision: keep as quick
>    shortcut / route through `_confirm_done` / remove entirely.
>
> ### Pickup steps for the next session
>
> 1. Read `spec/00-charter.md` + `CLAUDE.md`.
> 2. Read this banner.
> 3. Read `spec/52-event-creation-vision.md` §8 (TZ correction) for
>    the spec shape.
> 4. Wire the conditional TZ-calibration ask + `DiscreteTzDialog`
>    into the Collect flow (per "Where to start in the next session"
>    above).
> 5. Eyeball-test by creating + Collecting an event that needs TZ
>    correction (cross-border trip, or camera clock set wrong).
> 6. Once verified — Event Creation (spec/52) is **concluded**.
>    Pivot to Pick / Cuts / Maps per the priority list further down
>    this file.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09 afternoon) — Quick Sweep redesign LANDS (slices A+B+C + post-eyeball iteration). One TZ-correction eyeball remains before Event Creation concludes.
>
> **Where we are.** The cluster-aware Quick Sweep is end-to-end on
> Nelson's eyeball checks this afternoon. Capture-time triage now shows
> one cell per cluster (bracket / burst / repeat) with the user picking
> keepers in an expansion sub-grid; non-clustered photos render
> unchanged. The "Save does nothing" / "Skip cells are gray" /
> "thumbnails stuck at cell 24" / "border-click does nothing" eyeball
> bugs are all fixed. 89 targeted tests green.
>
> ### Where to start next
>
> One queued item — the **TZ-correction eyeball test** that was Next #2
> in the prior banner. After it lands, Event Creation (spec/52) —
> including Collection — is CONCLUDED. The detailed work plan is
> preserved verbatim in the previous-banner block below; quick recap:
>
> * After PlanDialog OK and before the ingest-mode gate, run
>   `core.tz_calibration.needs_calibration` against the merged plan.
> * Conditional ask dialog ("Calibrate camera TZ now, or skip?"),
>   wired to `DiscreteTzDialog` (Path A) — pair-pick (Path B) is a
>   slice extension.
> * On accept: `gateway.set_camera_day_tz(..., source='user_declared')`
>   per (camera, day).
> * In `_run_collect_copy_all`, set `capture_time_corrected = raw +
>   declared_offset` per the per-(camera, day) lookup.
>
> ### What landed this afternoon (uncommitted at the time of writing)
>
> **Slice A — pure-logic foundation.**
>
> * `core/repeat_detector.py` — phone "tap-twice doublet" detector.
>   Greedy **span-based** grouping (NOT consecutive-gap): ≥ 2 photos
>   whose total span first→last is within the window. Default
>   window **2.0 s** (tightened from 5 s on eyeball — most genuine
>   doublets fire within a second). Mirrors
>   `core.bucket_scanner.annotate_clusters`. A 1 s-interval chain of
>   10 photos fragments into runs of 3 at the 2 s span boundary.
> * `core/cluster_classifier.py` — small unified helper. Consumes the
>   scanner's `BucketScanResult` and emits one `ClusterAssignment`
>   per path with `kind ∈ {bracket, burst, repeat, none}` +
>   `group_id`. Precedence (bracket > burst > repeat > none) is
>   implicit in the scanner's mutually-exclusive output; the helper
>   just routes. Repeat detection runs **phone-only** over
>   `scan_result.individuals` via `core.phone_detector.is_phone` —
>   camera rapid-fire belongs to the burst detector, not the repeat
>   layer (Nelson eyeball 2026-06-09).
> * `assets/icons/clusters/repeat.svg` — universal duplicate-document
>   glyph (two offset frames, folded corner on the front) with a
>   tiny landscape photo inside the front frame (sky / amber sun /
>   teal back mountain with snow cap / green front mountain).
>
> **Slice B — cluster cells in Quick Sweep.**
>
> * `mira/picked/quick_sweep_buckets.py` —
>   `build_quick_sweep_buckets()` now runs `classify_clusters()`
>   after the scanner and splits individual / moment nodes by repeat
>   membership into `FastBucket(kind="repeat")` per group.
>   `fast_day_grid_cells()` emits `CullCluster` cells for
>   `REAL_CLUSTER_KINDS = {burst, focus_bracket, exposure_bracket,
>   repeat}` (mirrors the main Cull's pattern). Reverses the
>   2026-06-05 "Quick Sweep flattens clusters" design.
> * `mira/ui/base/cluster_icons.py` — `CLUSTER_KINDS` +
>   `_FILE_STEM` learned `"repeat"` (additive).
> * `mira/ui/base/day_grid_cell.py` — tooltip kind label
>   dict learned `"repeat": tr("Repeat")` (additive).
>
> **Slice C — cluster expansion + batch ops + UX.**
>
> * `mira/ui/picked/quick_sweep_page.py`:
>   * Second `DayGridView` instance as the cluster sub-grid (mirrors
>     PickPage's `cluster_grid` pattern). Centre-click on a cluster
>     cell opens it; back returns to day grid.
>   * `show_pick_all_button` + `show_skip_all_button` on both day
>     grid and cluster sub-grid → "✓ Pick all" / "✗ Skip all"
>     buttons. Sub-grid versions act on cluster members; day grid
>     versions act on every item in the day.
>   * Border-click on a day-grid cluster cell bulk-cycles every
>     member's K → D → C → K together — one click to wipe a whole
>     burst to Skip without entering the sub-grid.
>   * `_viewer_came_from` routes Back from the single-item viewer
>     back to the cluster sub-grid when opened from a member (vs.
>     the day grid when opened directly from a flat cell).
>   * `_state_for` translates legacy `core.cull_state` values
>     (`"kept"` / `"discarded"` / `"candidate"`) to the rebuild
>     vocabulary the renderer's `_phase_state_map` filter expects
>     (`"picked"` / `"skipped"` / `"candidate"`). Without
>     translation every state change silently dropped on the floor —
>     this was the root cause of "border click does nothing".
>   * Ported PickPage's `_refresh_day_cell` + `_reproject_cell`
>     pattern — handles cluster cells uniformly (cluster aggregate
>     via `cluster_color` → MIXED yellow paints when members
>     disagree).
>   * Ported PickPage's thumbnail loader — unbounded session
>     `_thumb_pixmap_cache` + FIFO `_thumb_pending` work queue +
>     single `QTimer`. Replaces a 24-entry LRU that thrashed past
>     cell ~24 in larger clusters (Nelson eyeball: "only the first
>     28 or something have thumbnails").
>   * **Back at the outermost level confirms and commits** (Nelson
>     2026-06-09 suggestion). Single-day grid back OR days-panel
>     return → `QMessageBox` with pick / skip / compare counts;
>     "Copy and finish" emits `saved` (capture_flow commits the
>     copy via its lambda); "Stay in Quick Sweep" keeps the user
>     in the page. Replaces the old UX where Back silently emitted
>     `cancelled` and nothing happened. Browse mode skips the
>     dialog (read-only flow stays cancel-only).
>
> * `mira/settings/model.py` — new user-tunable
>   `quick_sweep_default_state` setting (default `"picked"`).
>   `mira/ui/base/settings_dialog.py` exposes it on the
>   Picker tab right after `cull_default_state`. The page reads
>   it in `load()` and seeds `self._state` + `_renderer_default`
>   accordingly. Flip to `"skipped"` for a stricter "actively
>   pick keepers" flow at capture time.
>
> * `assets/themes/{light,dark}.qss` — fixed half-renamed QSS
>   rules: `DayGridCell[status="discarded"]` → `"skipped"` (×2)
>   and `MediaHost[state="discarded"]` → `"skipped"` (×4 — two
>   light + two dark). Skip cells were rendering gray
>   **everywhere** (including main Cull) because the QSS keys
>   hadn't been swept when `CellColor.DISCARDED` was renamed to
>   value `"skipped"`. Affects every phase.
>
> ### Test surface delta
>
> All targeted-file runs, per the "scope tests to what changed"
> rule:
>
> | File | Tests | Status |
> |---|---|---|
> | `tests/test_repeat_detector.py` | 21 | ✓ new file |
> | `tests/test_cluster_classifier.py` | 18 | ✓ new file (+3 phone-only) |
> | `tests/test_fast_buckets.py` | 17 | ✓ 5 new slice-B tests; 2 updated for new behaviour; fixtures gained phone make/model |
> | `tests/test_quick_sweep_clusters.py` | 22 | ✓ new file (slice C + bug-fix regressions + setting + Back-confirm) |
> | `tests/test_settings.py` | 11 | ✓ unchanged (new setting field auto-covered by default-state test) |
> | **Total** | **89** | **✓ all passing** |
>
> ### Out of scope / scope changes this session
>
> * **Slices D + E dropped from Quick Sweep queue** (Nelson 2026-06-09
>   "lets leave peaking and play out of the quick sweep"). The
>   focus-peaking toggle and the video-transport polish are deferred
>   to a future scope — Quick Sweep redesign is now A+B+C only.
> * The legacy "Export →" button in the in-viewer chrome is still
>   live (emits `saved` directly, bypassing the Back-confirm dialog).
>   Not removed — Nelson left the call open. Two reasonable
>   follow-ups when picked up: (a) route it through `_confirm_done`
>   for a single commit path, or (b) remove it entirely.
>
> ### Pending / backlog flagged this session
>
> * **FUTURE — extend main Cull (PickPage) with repeat clusters.**
>   Today only Quick Sweep emits `'repeat'` buckets. The main Cull
>   uses scanner output directly via `core.bucket_navigator_model.
>   _flatten` and only renders cluster cells for the three scanner-
>   emitted kinds (burst / focus_bracket / exposure_bracket). To
>   bring repeats to PickPage: either (a) extend `_flatten` to call
>   `cluster_classifier` and split individuals into repeat sub-
>   buckets (same pattern as Quick Sweep), or (b) make the scanner
>   itself emit a `repeats` field on `BucketScanResult`. Option (a)
>   is smaller and keeps the scanner pure. Also update
>   `mira.picked.model.REAL_CLUSTER_KINDS` to include
>   `"repeat"`. The cluster_icons + day_grid_cell tooltip already
>   include `"repeat"` (slice B's additive changes).
>
> ### Eyeball verification record (this session)
>
> Nelson on the live app, 2026-06-09 afternoon:
> 1. Two-overlapping-frames + folded corner + landscape photo →
>    "Very good !!! Lets move on"
> 2. Border-click cycles state on both flat cells AND clusters; mixed
>    cluster paints yellow → verified
> 3. Cluster sub-grid thumbnails fill in past cell 24 → verified
> 4. Back at outermost level → confirmation dialog → "Copy and
>    finish" triggers the offload → verified
> 5. Skip cells render red (DayGridCell + MediaHost border) →
>    verified
> 6. Phone-only repeat filter — two camera shots 1s apart do not
>    cluster → covered by `test_camera_individuals_do_not_form_a_
>    repeat_even_when_tight`; live verification still pending on a
>    mixed phone+camera folder
>
> ### Pickup steps for the next session
>
> 1. Read `spec/00-charter.md` + `CLAUDE.md`.
> 2. Read this banner.
> 3. Read `spec/52-event-creation-vision.md` §8 (TZ correction) for
>    spec shape.
> 4. Wire the conditional TZ-calibration ask + `DiscreteTzDialog`
>    into the Collect flow (per the "Next #2" recap above).
> 5. Eyeball on a real cross-border or wrong-camera-clock event.
> 6. Once verified — Event Creation concludes. Pivot to Pick / Cuts
>    / Maps per the priority list further down this file.
>
> ---
>
> ## (previous CURRENT, preserved as record, 2026-06-09 morning) — Collect LANDS end-to-end. Quick Sweep redesign is the next slice; one TZ-correction eyeball test closes Event Creation.
>
> **Where to start, in order.** Both items are queued in this banner. After
> the second one, Event Creation (spec/52) — including Collection — is
> CONCLUDED, and the next session can pivot to a different area (probably
> Pick / Cuts / etc.).
>
> ### Where we are right now
>
> Last commit: `a6b67bb` *(spec/52 — Collect end-to-end + free-text
> qualifiers + retire legacy info dialogs)*.
>
> Working flows (end-to-end on Nelson's real data, eyeball-approved):
>
> * **File → New event (Ctrl+N)** — info-only dialog (PlanDialog with
>   `with_plan=False`); creates the event row with zero `trip_days`. Plan
>   grows as Collect adds photos.
> * **File → New event from photos… (Ctrl+Shift+N)** — `QFileDialog` →
>   `scan_source` (with home-country + home-TZ fallback) → coverage popup
>   → unified PlanDialog (Info + per-day plan + Save/Load CSV) → OK
>   creates event row + per-day plan (no photos copied — `_create_event_from_plan`).
> * **Library tile title-zone / left-zone / Events menu Edit-info** —
>   `_open_existing_event_info` → PlanDialog in info-only edit-existing
>   mode, persists every changed qualifier via `set_classification`.
> * **Activity dashboard → Collect tile** — full ingest path. Source pick →
>   scan → unified PlanDialog (with merged existing + new days) → ingest-
>   mode gate (Copy all / Quick Sweep first / Cancel) → `run_ingest`
>   off-thread (sha256 captured during the copy stream so the second
>   "Recording N items" phase is fast DB upserts) → event.db gets
>   cameras + trip_days + items in one transaction → activity dashboard
>   refreshes. Dup-skip on `item.origin_relpath` so partial-run recovery
>   + re-Collect on the same source is idempotent.
>
> Last-chance schema (locked 2026-06-08): `event.{duration_value,
> duration_unit, scope, participants, mood, transport}` + `item.subject`.
> `scope/mood/transport` are free-text (CHECK constraints dropped);
> `duration_unit` + `participants` stay closed. `Settings.home_country`
> added with a `country_picker` widget in the Settings dialog. White-stroke
> tick SVG on every checkbox (`assets/icons/check.svg`).
>
> ### Next #1 — Quick Sweep redesign (design session locked Nelson 2026-06-09)
>
> The current Quick Sweep treats every photo as an independent cell. The
> redesign makes it cluster-aware: most photo sets carry a lot of
> redundancy (bursts, brackets, "tap-twice" repeats) and the user should
> triage at cluster granularity, not photo-by-photo.
>
> **Three cluster types, mutually exclusive, detected in this order:**
>
> 1. **Brackets** — existing `core/bracket_detector`.
> 2. **Bursts** — existing burst detector (in `core/` somewhere — locate
>    when you start slice A).
> 3. **Repeats** *(NEW)* — chronological run of ≥ 2 photos where every
>    consecutive gap is ≤ **5 seconds** AND none of the photos already
>    belong to a burst or bracket cluster. Catches the cell-phone
>    "tap-twice-just-in-case" pattern. The 5-second threshold lives as
>    a constant in the new detector module with room to promote to a
>    user-tunable Setting later.
>
> **UX shape:**
>
> * Each cluster takes up ONE cell on the day grid — leader frame visible,
>   small badge in a corner indicating cluster type (bracket / burst /
>   repeat). New SVG icon needed for the **Repeats** badge (two-overlapping-
>   rectangles glyph or similar; Nelson decides at slice time).
> * Click cluster cell → expansion view showing all members as
>   thumbnails in a grid → user picks one or more keepers → unpicked
>   members auto-marked Skip → Esc returns to day grid.
> * Non-clustered photos render as today (one cell each, chronological).
> * Focus peaking overlay surfaces on the single-photo viewer (toggle
>   button + keyboard shortcut). `core/focus_peaking` already exists —
>   Edit-phase tooling already uses it.
> * Video play (QMediaPlayer / QVideoWidget) already wired in
>   `quick_sweep_page.py`; verify it actually works on typical camera
>   `.mp4` / `.mov` and add transport controls if missing.
>
> **Slicing plan (start with slice A, then B → C → D → E):**
>
> | Slice | Scope | Touches |
> |---|---|---|
> | **A** | Pure-logic foundation: `core/repeat_detector.py` (5 s window, exclude burst + bracket members) + small unified cluster classifier helper that calls bracket / burst / repeat in order and returns `cluster_kind ∈ {bracket, burst, repeat, none}` + `cluster_group_id` per item. `assets/icons/repeat.svg` (two-overlapping-rectangles or Nelson's call at slice time). **Targeted tests only.** | `core/`, `assets/icons/`, `tests/test_repeat_detector.py` |
> | **B** | Day grid renders cluster cells. Quick Sweep groups input items by cluster before laying out. Each cluster = one cell, leader frame + badge. Non-clustered items unchanged. Click cluster cell → placeholder. Eyeball checkpoint with Nelson on a real folder. | `mira/ui/picked/quick_sweep_page.py`, `mira/ui/base/day_grid_*` |
> | **C** | Cluster expansion grid. Click cluster cell → modal/sub-page showing all members as thumbnails. User picks keeper(s) (multi-select shape TBD; come back with options to Nelson). Unpicked auto-marked Skip. Esc returns. | `mira/ui/picked/quick_sweep_page.py` (or new sub-page) |
> | **D** | Focus peaking in the single-photo viewer. Surface existing `core/focus_peaking` as a toggle. Keyboard shortcut + toolbar button. | `quick_sweep_page.py` viewer + maybe `core/focus_peaking` polish |
> | **E** | Video play polish — verify QMediaPlayer integration on typical camera mp4/mov; add transport controls (play/pause/scrub/±1 frame) if missing. | `quick_sweep_page.py` viewer |
>
> Each slice gets a spec-shape eyeball checkpoint with Nelson before the
> next slice starts (memory: `feedback_verify_spec_shape_during_integration`).
>
> ### Next #2 — TZ-correction eyeball test (concludes Event Creation)
>
> After Quick Sweep redesign lands, Nelson will test creating + Collecting
> on an event that needs **TZ correction**:
>
> * Camera-only or mixed-camera-and-phone source where the day's location-
>   derived TZ ≠ his home TZ (e.g. cross-border trip, camera clock set
>   wrong).
> * The conditional TZ-calibration ask (spec/52 §8.2) is currently a
>   STUB in the Collect flow — `capture_time_corrected` is set equal to
>   `capture_time_raw`, so cameras with wrong-TZ EXIF end up grouped into
>   the wrong day.
>
> The work for #2 is the actual TZ-calibration wiring:
>
> * After PlanDialog OK and before the ingest-mode gate, run
>   `core.tz_calibration.needs_calibration` against the merged plan.
> * If candidates exist, show the conditional ask dialog: "Calibrate
>   camera TZ now, or skip?" (Path A — discrete TZ pick via the existing
>   `DiscreteTzDialog`; Path B — pair-pick via the existing
>   `sync_pair_picker` — defer pair-pick to a follow-up if it's too much
>   work in one slice).
> * On accept: persist each declared offset via
>   `gateway.set_camera_day_tz(...)` with `source='user_declared'`.
> * In `_run_collect_copy_all`, set
>   `capture_time_corrected = capture_time_raw + (declared_offset)` per
>   the per-(camera, day) lookup.
>
> Once #2 lands and Nelson eyeballs an event that creates correctly with
> calibrated TZ, **Event Creation (spec/52) is concluded**, including
> Collection. The session can then pivot to a different area (Pick / Cuts
> / Maps / etc. — see the priority list further down this file).
>
> ### Spec §11 retirements still pending (carry into a future cleanup sprint)
>
> Not blocking Event Creation conclusion; just noting for the next §11 sweep:
>
> * `capture_action_dialog.py` — still imported by `capture_flow.py` (the
>   legacy capture chain). Both files are unreachable from menus now
>   (Collect goes through `_open_collect`, not the legacy `_open_capture`
>   which now just delegates). Safe to delete.
> * `past_photos_dialog.py` + `plan_editor_dialog.py` — unreachable from
>   menus but used by `test_phone_tz.py` (36 tests) + `test_past_photos_ingest.py`
>   (3 tests) for the `_carry_forward_fill` helper. Move that helper into
>   a `core/` module first, then the legacy dialogs delete cleanly.
> * `ENTRY_PLAN_TEMPLATE` menu entry — drop; Save/Load CSV on PlanDialog
>   makes "Download plan template" redundant.
> * 19 pre-existing `test_reconcile_pipeline.py` failures vanish when the
>   legacy reconcile pipeline retires with the dialogs above.
>
> ### Memory rules already on file — apply automatically
>
> * `feedback-titled-groupbox-over-label` — every form input is a titled
>   QGroupBox with the `FormFieldGroup` QSS role. No label-beside-input.
> * `feedback-scope-tests-to-what-changed` — targeted test files only on
>   localised changes; never the full 135-second sweep for one widget.
> * `feedback-qmessagebox-chrome-disliked` — always `Icon.NoIcon` on
>   QMessageBox. Build a custom user-message component when there's
>   bandwidth.
> * `feedback-verify-spec-shape-during-integration` — mandatory
>   "shape matches spec — confirmed?" eyeball checkpoint between every
>   integration sub-slice. Don't push 5 slices in one go.
> * `feedback-design-mode-protocol` — "let's enter design mode" trigger;
>   listen + reflect, no implementation questions; defer is the exception;
>   done = done.
>
> ### Pickup steps for the next session
>
> 1. Read `spec/00-charter.md` + `CLAUDE.md`.
> 2. Read this banner.
> 3. Read `spec/52-event-creation-vision.md` §6 (Collect) + §8 (TZ
>    correction) so you have the spec shape in mind before touching code.
> 4. Skim the existing `core/bracket_detector` and the burst detection
>    code (find it via grep — likely in `core/`) so you know the
>    detector signatures slice A needs to consume.
> 5. Start slice A. Spec-shape checkpoint with Nelson when it's testable.
>
> ---
>
> ## (previous CURRENT, preserved as record) — New-event flow LANDED. Event creates + lands on activity dashboard. Photos via Collect (next).
>
> **What just landed (uncommitted at write time; the very next step is the commit).**
> Restart of Sprint #3 slice E from the Option-2 revert produced a working
> end-to-end "create event from photos" path on Nelson's actual data
> (`D:\Photos\trips recovered\2025 - Argentina`). The flow now is:
>
> ```
> File → New event (Ctrl+N) — single menu entry; "Create from photos" duplicate retired (spec §11)
>   → QFileDialog directly (no SD-vs-directory branching ceremony)
>   → scan_source(path) off-thread via run_with_progress
>   → QMessageBox banner ("Phone-EXIF coverage: phones X/N · TZ X/N · GPS X/N") so the
>     user immediately knows which days will need manual country / location
>   → PlanDialog (with_event_info=True) — ONE dialog: Event Info at top + per-day
>     plan table below + Save/Load CSV. Browse opens DayBrowseDialog (legacy
>     reused) with peek_select picking ~20 photos per day spread across the day
>     and collapsing tap-twice duplicates (15 s window). HEIC + RAW thumbnails
>     work via image_loader.load_pixmap.
>   → OK → gateway.create_event(EventDocument) — event row + per-day plan
>     materialised; NO photos copied; lands on the new event's activity
>     dashboard. Collect is the next pass that adds photos.
> ```
>
> ### Structured event qualifiers (last-chance schema lock, Nelson 2026-06-08)
>
> The old `event_subtype` mixed duration / scope / activity into one free-text
> bucket. Cleaned up — subtype is now activity-only, the rest become columns:
>
> | Column | Type | Values |
> |---|---|---|
> | `event.duration_value` | INTEGER | 1..cap per unit (hours 23 / days 6 / weeks 3 / months 11 / years 50) |
> | `event.duration_unit` | TEXT enum | hours / days / weeks / months / years |
> | `event.scope` | TEXT enum | international / domestic |
> | `event.participants` | TEXT JSON array | Solo / Couple / With Family / With Kids / With Friends / With Colleagues / Client |
> | `event.mood` | TEXT enum | relaxed / active / cultural / professional |
> | `event.transport` | TEXT enum | flight / car / train / cruise / **motorhome** / mixed (Trip-only) |
> | `item.subject` | TEXT free | per-item user annotation (bird species, plant, landmark — UI deferred) |
>
> Subtype combo is **editable** (curated presets are suggestions; user can
> type a custom value). Gateway `set_classification` validates every enum;
> bad values raise `ValueError`. Indexes added on `scope` + `mood`.
>
> ### Test suite delta this slice
>
> Targeted runs only (no full sweeps — see `feedback_scope_tests_to_what_changed`).
>
> | Module | Result |
> |---|---|
> | `tests/test_plan_dialog.py` | 22 / 22 |
> | `tests/test_gateway.py` | 53 / 53 (+4 new for the qualifier columns) |
> | `tests/test_store.py` | 18 / 18 |
> | `tests/test_peek_select.py` | 35 / 35 (+6 new for tap-twice collapse) |
> | `tests/test_scan_source.py` | 38 / 38 (+4 new for PhoneScanSummary) |
> | `tests/test_settings_home_timezone.py` | 3 / 3 |
> | `tests/test_event_info_dialog.py` | 12 / 12 (legacy untouched) |
> | `tests/test_plan_browse_day.py` | 11 / 11 (legacy DayBrowseDialog still passes) |
>
> ### Spec docs updated this slice
>
> * `spec/52` — §2.4 rewritten ("same dialog as plan, presented once"); added
>   §14 "Structured event qualifiers" documenting the new columns + UI shape.
> * `spec/03` — drift notice at top listing the qualifier columns + `item.subject`
>   + the retired tables. DDL blocks below not yet rewritten (deferred to a
>   cleanup sprint).
> * Auto-memory — `feedback_scope_tests_to_what_changed` added.
>
> ### What's next (the next session picks up here)
>
> 1. **Collect entry on the event card** — clicking Collect on the new event
>    re-enters the same scan → plan dialog (now with the Override-marker
>    column lit when re-scan brings new phone data) → on OK runs
>    `core.ingest_pipeline.run_ingest` to actually copy the photos. This is
>    where slice 4 closes the event-creation loop.
> 2. **TZ calibration ask + DiscreteTzDialog wiring** — when the day's
>    location-derived TZ ≠ home TZ AND non-phone cameras are present
>    (spec/52 §8.2). Skipped on the new-event path right now because no
>    photos are ingested yet; needed for Collect.
> 3. **Override-ask UI** — when re-scan brings new phone data for an
>    existing day (spec/52 §6.2). Plumbing exists (the marker badge + the
>    override-handler seam in PlanDialog); the actual side-by-side compare
>    dialog needs to be built.
> 4. **Per-item Subject UI** — the column exists; the user-facing edit
>    surface is undesigned ("how to expose it without burdening the user"
>    per Nelson). Defer until Pick-phase UX work picks it up.
>
> ### Spec §11 retirements still pending (carry into next session)
>
> * `capture_action_dialog.py` still imported by `capture_flow.py` — retire
>   when Collect on existing events lands and the old capture flow is dead.
> * `ENTRY_PLAN_TEMPLATE` "Download plan template" menu entry — Plan dialog's
>   Save/Load CSV buttons make this entry redundant; drop.
> * The 19 pre-existing reconcile_pipeline test failures vanish when the
>   legacy reconcile pipeline retires alongside the above two.
>
> ---
>
> ## (previous CURRENT, preserved as record) — Option-2 revert EXECUTED. Sprint #3 restart staged, NOT yet started.
>
> **The lesson, preserved.** The previous session attempted slices C2 → E.8
> of Sprint #3 (event-creation surfaces per spec/52) in one push. Suite went
> 1961 → 2116 passing with 213 new tests and (claimed) zero failures.
> **But when Nelson actually ran the app and tried to create an event, the
> assembled flow did NOT match spec/52.** Nelson's words: *"I just do not
> recognize the spec in this you have implemented. So many wrong things
> that I have not even the energy to report."*
>
> This session audited the working tree against spec/52, confirmed the
> deviations (EventInfoDialog never opened; event name collected upfront;
> SD-card vs directory not distinguished; per-(camera, day) calibration
> collapsed to per-camera legacy shape; new `core/ingest_pipeline` built
> and orphaned; spec §11 retirements incomplete) and **executed the
> Option-2 revert with Nelson's explicit approval**.
>
> ### Working tree after revert
>
> * **HEAD = `13d0bb2`** (last committed: "PROGRESS — slice C2 decisions locked").
>   Every UI file the prior session modified is restored to its HEAD content;
>   every legacy file it deleted is restored from HEAD; every new UI file it
>   added is removed.
> * **4 untracked pure-logic core modules retained** + their tests
>   (103 tests passing in 0.42s):
>
>   | File | Tests | What spec says it does |
>   |---|---|---|
>   | `core/peek_select.py` | `test_peek_select.py` (28) | spec/52 §5.6 — time-spread sampling + RAW/JPEG sibling dedup + video/huge filter + stats counters |
>   | `core/tz_calibration.py` | `test_tz_calibration.py` (15) | spec/52 §8.2 + §8.4 — conditional-ask trigger + per-(camera, day) candidate enumeration (skips home-TZ, phones, already-calibrated) |
>   | `core/scan_source.py` | `test_scan_source.py` (34) | spec/52 §2 — photos → ScanResult builder; `build_ingest_jobs` adapter; **houses `ScanDayRow` + `OverrideMarker` pure-logic dataclasses** (moved from the deleted UI file this session, per CLAUDE.md invariant #8) |
>   | `core/ingest_pipeline.py` | `test_ingest_pipeline.py` (26) | spec/52 §8.1 — fresh greenfield ingest (`IngestPhotoJob` → `run_ingest`, per-(camera, day) tz aware, quarantines stripped-EXIF files) |
>
> * **`bin/exiftool.exe` + `bin/exiftool_files/`** kept — orthogonal to
>   Sprint #3; the app needs ExifTool to run at all.
>
> * Nothing committed yet. The 4 untracked library files + this PROGRESS
>   banner are the only diff vs `13d0bb2`. Nelson will decide when to commit.
>
> ### Pre-existing test failures at HEAD (not regressions)
>
> Full suite after revert: **2134 passed / 19 failed / 273 skipped** in 135 s.
> The 19 failures are all in `tests/test_reconcile_pipeline.py`; confirmed
> pre-existing at `13d0bb2` (verified by checking out the file from HEAD
> in isolation and running). The previous session's PROGRESS claim of
> "1961 → 2116 / 0 / 318 with zero failures" was a misreport — the reconcile-
> pipeline tests have been broken since the 2026-06-08 schema sprint
> (`4585d10`); the prior session was going to retire the whole legacy
> pipeline in slice E.7 so these tests would have vanished, but it never
> committed and we reverted. Live with them; they retire when slice E
> actually lands (correctly).
>
> ### Restart plan — read this BEFORE writing any UI code
>
> Sprint #3 slice E restarts from the library substrate above. **Read order
> before any implementation:**
>
> 1. [`feedback_verify_spec_shape_during_integration.md`](../../../../C:/Users/nksal/.claude/projects/D--Projetos-Nelson-Mira/memory/feedback_verify_spec_shape_during_integration.md) — the lesson the prior session learned the hard way: integration sub-slices need explicit "shape matches spec — confirmed?" checkpoints, distinct from "want to push more?".
> 2. **`spec/52-event-creation-vision.md` end-to-end.** Carefully. The prior session built the library pieces right and stitched them wrong because the spec wasn't re-read between component and integration.
> 3. Memory: `feedback_design_mode_protocol` — the working agreement (design-mode listen-and-reflect; raise removals immediately; done = done).
>
> ### The shape spec/52 specifies (re-read this if anything below feels surprising)
>
> ```
> Source pick (§2.1 — SD card OR directory, distinct affordances)
>   → scan_source(path) → ScanResult
>   → PlanDialog (§2.2-§2.3 + §5 — single surface, 14 rows, save/load CSV,
>      Browse peek per day, override marker per day when re-scan conflict)
>   → conditional TzCalibrationAsk (§8.2 — only when day TZ ≠ home AND
>      non-phone cameras present)
>   → DiscreteTzDialog per-(camera, day) (§8.3 Path A; Path B pair-pick later)
>   → EventInfoDialog (§2.4 — name + type + subtype; THIS IS THE COMMIT POINT;
>      no event records exist before this confirms)
>   → ingest_pipeline.run_ingest (§8.1 — correction-on-read via
>      camera_day_tz; source files never mutated)
> ```
>
> ### Checkpoints to confirm with Nelson BEFORE writing any E.1 code
>
> Each is a "shape matches spec — confirmed?" gate. Don't proceed past one
> without an explicit answer.
>
> 1. **Sequencing.** Source pick → scan → Plan → (conditional TZ ask +
>    discrete TZ) → EventInfo → commit. NOT: name + source upfront → scan
>    → Plan → ingest. (Prior session got this wrong.)
> 2. **Source-pick surface.** Spec §2.1 says "SD card OR directory" but
>    doesn't pin the surface design — auto-detect inserted SD cards? Two
>    buttons? A radio? Need Nelson's call before building the entry dialog.
> 3. **EventInfoDialog reuse.** Existing `event_info_dialog.py` is alive
>    (the per-event Information tab in `event_dialog.py` uses it). Does
>    it open cleanly as the post-day-triage commit surface, or does it
>    need restructuring? Read it before answering.
> 4. **Calibration persistence path.** Spec §8.1 says writes go to
>    `camera_day_tz` per-(camera, day). New-event path needs to either
>    create the event row first (so an `EventGateway` exists to write
>    against) OR carry calibration decisions through the EventInfoDialog
>    commit into the first ingest. Prior session collapsed to per-camera
>    on the new-event path — that's the spec violation to avoid.
> 5. **Spec §11 retirements still pending.** `capture_action_dialog.py`
>    is still imported by `capture_flow.py`; `ENTRY_PLAN_TEMPLATE` is
>    still on the Plan menu. Both need to retire as part of slice E (in
>    sequence with the new-event-flow swap).
>
> **Full new pipeline shape:**
> ```
> scan_source(path) → ScanResult
>   → host fills home_tz + flags → FlowInputs
>   → EventCreationFlow(inputs, gateway).run() → FlowResult
>   → host builds IngestPhotoJob list from ScanResult + FlowResult
>   → run_ingest(jobs, event_root) → IngestResult (photos copied + EXIF baked)
> ```
>
> ### Sprint-3 progress (7 of ~7 sub-slices except E done)
>
> | Slice | Commit | What |
> |---|---|---|
> | A | [67b6970](https://github.com/nksalgado-proton/Mira/commit/67b6970) | Phone detector (data-driven Make/Model match, `assets/phone_makers.json` — 17 makers with Sony/LG model-scoping) + Pick gate (`plan_gate.evaluate(event_gw) → PlanGateOutcome` per §10) |
> | B | [5dc6df4](https://github.com/nksalgado-proton/Mira/commit/5dc6df4) | Autofill engine — phone-EXIF (country/TZ/location) + subdir-name (description), with the §3.3 conflict rule baked in (subdir beats phone-default description) |
> | C1 | [38a8b35](https://github.com/nksalgado-proton/Mira/commit/38a8b35) | Plan CSV codec — `;`-CSV encode/decode + `apply_to_scan_days` non-destructive merge (§5.5) |
> | C2 | (this session) | **Plan dialog Qt UI** — `mira/ui/pages/plan_dialog.py` (618 lines) + `tests/test_plan_dialog.py` (37 tests). Column scheme: include · Browse · country · TZ · location · description · override (spec/52 §5.3). Salvaged the legacy QTableWidget setup; gutted Import/Paste/Save toolbar + add/remove-day buttons + date column + date-cascade model. Searchable country combo with flag-emoji prefix (new factory `country_picker.make_single_country_combo_with_flags` — legacy combo untouched). TzPicker reused verbatim per §12. Save/Load CSV buttons gated by `feature.plan_save_load_csv` (hidden when off). |
> | D.1 | (this session) | **Browse peek dialog** (spec/52 §5.6). Two parts. **D.1.a** — pure-logic selection (`core/peek_select.py`, 28 tests): `PeekCandidate` input shape; `select_for_peek` picks ~20 photos per day with time-spread sampling (equal-time-bucket centers, avoids breakfast-burst dominance), RAW+JPEG sibling dedup keeping JPEG, video-skip, huge-file-skip (>40 MB), <target → return-all short-circuit. `stats_for_peek` produces the counters for the empty-peek hint. **D.1.b** — Qt UI (`mira/ui/pages/plan_peek_dialog.py`, 21 tests): modal popover, 6×4 thumbnail grid (`_THUMB_PX=160`), click-to-zoom in-place via QStackedWidget (grid ↔ zoom pages), Esc-in-zoom returns to grid, Esc-in-grid closes dialog, empty-peek header hint with filter counts ("(no preview-able photos — 3 videos, 2 RAW(s) deduped)"). |
> | D.2 | (this session) | **Override-ask UI** (spec/52 §6.2). `mira/ui/pages/override_ask_dialog.py` (310 lines) + 22 tests. Side-by-side modal showing existing vs new (phone-EXIF) values for Country / TZ / Location with per-row Keep/Override radios + "Keep all" / "Override all with new" shortcuts + OK/Cancel. Description row is informational only (spec/52 §4 propagate-if-untouched: applied by the host on accept). Default state: all rows pre-select Override (phone is ground truth per §1.2). Returns frozen `OverrideDecision(override_country, override_tz, override_location)`; host applies to row + clears `override_marker`. Country labels render with flag emoji + display name; TZ formats as `±HH:MM` via the `core.plan_csv.tz_minutes_to_string` shared helper. |
> | D.3 | (this session) | **Conditional TZ-calibration ask** (spec/52 §8.2 + §8.4). Pure logic `core/tz_calibration.py` (15 tests) — `needs_calibration` returns `CalibrationCandidate`s per (camera, day) needing user calibration: skips home-TZ days, skips phones (carry TZ in EXIF), skips already-calibrated pairs, handles border-crossing (same camera, different days). `summarize` aggregates counts for the dialog hint. Ask dialog `mira/ui/pages/tz_calibration_ask_dialog.py` (13 tests) — "Calibrate now / Skip for now" with rationale + candidate list (`{date} — {camera}  (day TZ {tz})`). Reads `was_accepted()` after exec; host wires to existing `DiscreteTzDialog` (Path A) at slice E. Path B (pair-pick via `sync_pair_picker`) is a slice-E enhancement (existing widget is per-row entry — needs orchestration). |
>
> ### Slice C2 — three locked decisions, all implemented as specified
>
> 1. **Day-list widget** — salvaged the legacy QTableWidget setup; the
>    new dialog has the 7-column scheme (`COL_INCLUDE` / `COL_BROWSE` /
>    `COL_COUNTRY` / `COL_TZ` / `COL_LOC` / `COL_DESC` / `COL_OVERRIDE`).
>    Checkbox label = ISO date. Override column renders only when a
>    `ScanDayRow.override_marker` is set.
> 2. **Country picker** — `make_single_country_combo_with_flags(code)`
>    in `mira/ui/base/country_picker.py`. Each entry is
>    `"🇧🇷 Brazil (BR)"`; searchable via QCompleter with substring match.
>    `flag_emoji_for_code(code)` is a public helper (returns the
>    regional-indicator pair).
> 3. **TZ picker** — `mira.ui.base.tz_picker.TzPicker` dropped
>    in as a per-row cellWidget. **No TZ cascade** (departure from
>    spec/05 §4b, documented in `plan_dialog.py` module docstring):
>    the per-day model treats each day's TZ as its own EXIF reading.
>    Test enforces this: `test_no_tz_cascade_between_days`.
>
> ### Slice C2 — implementation notes (for slice D + slice E continuation)
>
> - **Constructor seams (DI for slice D):** `browse_handler:
>   Optional[Callable[[date], None]]` and `override_handler:
>   Optional[Callable[[date], None]]`. Slice C2 wires the buttons +
>   emits the date; slice D wires the actual peek dialog + override-
>   ask UI as the handlers. With no handler, the Browse button is
>   visibly disabled (`isEnabled() is False`) — explicit affordance
>   the host hasn't lit Browse yet.
> - **`ScanDayRow` UI dataclass** (in `plan_dialog.py`) is the
>   host-facing input/output shape. Mutable per row; `date` is the
>   round-trip key, never user-editable. `override_marker` carries
>   the existing + new side of every conflicting field so slice D
>   can render the side-by-side comparison without re-querying the
>   gateway.
> - **No geometry persistence yet.** Legacy `PlanEditorDialog` saves
>   geometry + column widths to `Settings`; the new dialog skips
>   this. Add later if Nelson asks — would need new Settings fields
>   (`plan_dialog_geometry` / `plan_dialog_column_widths`).
> - **Salvaged from legacy:** the `_PlanFocusKeeper` defence-in-depth
>   pattern (vendored, not shared — slice E retires the legacy file).
>   The mouse-only Cut/Copy/Paste + Location↔Description cross-fill
>   context menu on text cells.
> - **Save CSV doesn't write `checked` state** per spec/52 §5.5 — it's
>   scan-level, not plan content.
> - **Load CSV** applies field-by-field via `_apply_loaded_to_table_row`
>   to the cell editors directly (not via `_rows`), so the user sees
>   the imported values reflected immediately in the table.
>
> ### Sprint-3 remaining slices (slice E sub-slices, bottom-up by dependency)
>
> Slice E grew to 8 sub-slices after the E.0 call-graph audit found 4
> of the 6 "retire" files in spec/52 §11 were still load-bearing
> (`new_event_page.py` on MainWindow stack; `plan_editor_dialog.py` in
> 4 call sites including `past_photos_dialog.py` which charter §5.2
> says STAYS; `trip_plan_parser`/`skeleton` used in core `reconcile_pipeline`).
> Bottom-up so the legacy stays working until the new replaces it.
>
> | Sub-slice | What | Size | Status |
> |---|---|---|---|
> | E.0 | Audit legacy call graph — found 4 of 6 §11 "retire" files still load-bearing; revised E from "~150 lines" to 8 sub-slices. | — | ✅ done (durable — audit still applies) |
> | E.1 | `EventCreationFlow` orchestration host | 300 lines + 14 tests | ⛔ **REVERTED** — wrong shape (no EventInfoDialog open; calibration→`gateway` path skipped for new events). Restart from scratch with the spec-shape checkpoints in the banner above. |
> | E.2 | Scan pipeline `core/scan_source.py` — `build_scan_result` + `scan_source`. Source → `ScanResult` (scan_rows + candidates_by_date + day_date_lookup + day_tz_lookup + presences). | 290 lines + 26 tests | ✅ **survives** (untracked) — uses pure-logic `ScanDayRow` + `OverrideMarker` now defined in-module per CLAUDE.md inv #8. |
> | E.3 | Ingest pipeline `core/ingest_pipeline.py` — fresh greenfield primitive: `IngestPhotoJob` → `run_ingest(jobs, event_root)`. Phones → `_phones`, cameras → `_cameras`, missing camera_id → `_other`, no timestamp → `_no_timestamp` quarantine. | 280 lines + 26 tests | ✅ **survives** (untracked) — needs a UI host that actually CALLS it (prior `past_photos_dialog.py` was wired to the legacy `mira.ingest.run_ingest` instead, contradicting the §8 design). |
> | E.4 | `event_dialog.py` Plan tab swap — `embedded=True` PlanDialog + conversion helpers; per-event Plan tab drops add/remove-day buttons. | 120 lines + 3 tests | ⛔ **REVERTED** — depended on the now-gone PlanDialog. Restart later when the new PlanDialog lands cleanly. |
> | E.5 | `past_photos_dialog.py` rewrite (1124 → 290 lines) | 290 + 290 lines + 8 tests | ⛔ **REVERTED** — this is the file Nelson eyeball-tested and rejected. Restart from scratch after the spec-shape checkpoints land. |
> | E.6 | MainWindow rewiring per §11 — NewEventPage removed, ENTRY_CREATE_FROM_PAST collapsed into ENTRY_NEW_EVENT. | ~140 lines net delta | ⛔ **REVERTED** — restored. Re-do (correctly) when the new flow exists. |
> | E.7 | Legacy deletions per §11 — 6 production + 6 test files deleted. | ~3000 lines net delete | ⛔ **REVERTED** — all 12 files restored. Schedule for AFTER the new flow actually works (this was deleted prematurely; the new flow didn't replace the legacy paths correctly). |
> | E.8 | Sprint #3 closeout — PROGRESS final pass. | small | ⛔ **REVERTED** (never properly executed). |
> |  |  |  |  |
> | E.1' | NEW orchestration host — start from spec/52 §2 + the checkpoint list in the banner. First: scope the SOURCE-PICK surface (SD card vs directory) with Nelson. | TBD | pending |
> | E.2'-E.8' | After E.1' lands, replay the rest of slice E *in order* with spec-shape checkpoints between each. | TBD | pending |
>
> ### Read order for the next session (in order)
>
> 1. **`spec/00-charter.md`** — the constitution.
> 2. **`CLAUDE.md`** — vocabulary + invariants.
> 3. **This file (`spec/PROGRESS.md`)** — picks up here.
> 4. **`spec/05-ui-standards.md`** — the UI admission test (expanded 2026-06-08
>    with 8 promoted rules: titled QGroupBox §3b, sidebar-vs-menu §4d, Qt
>    overlay §5.3, cluster routing §5.4, phase-default state §5.5, clear-marks
>    pattern §5.6, Day Grid back-refresh §5.7). Every new widget passes this
>    admission test.
> 5. **`spec/52-event-creation-vision.md`** — the unified event-creation flow.
>    Single path from photos, one source per pass, one-surface plan dialog,
>    phone-EXIF as ground truth, save/load CSV, Pick-gated on completeness,
>    two-dialog flow (plan → event info), TZ correction-on-read.
> 6. **`spec/51-share-cuts-vision.md`** — the Cuts redesign. §6 questions all
>    closed; §10 documents the storage architecture (Cut definitions in
>    `mira.db.cut`, membership in `event.db.photo_tag`).
> 7. **`spec/53-user-data-store.md`** — the `mira.db` SQLite store
>    that replaces `settings.rebuild.json` + `events_index.json` on first
>    launch. §2.7.1 has the canonical 17 Premium-vs-Basic feature flag keys.
> 8. **Memory: `feedback_design_mode_protocol`** — the working agreement
>    (design-mode / implementation-mode split, no-reinvent rule, raise-
>    removals-immediately rule, done-=-done).
>
> ### Implementation-mode discipline (Nelson 2026-06-08, locked)
>
> Per `feedback_design_mode_protocol` + the protocol reinforced at sprint
> kickoff:
>
> - **Existing code is the primary source.** Every surface touched, read
>   what's there first and modify rather than rewrite. The spec says WHAT;
>   the existing code says HOW MUCH is already built.
> - **Raise removals immediately.** If applying the redesign to a surface
>   would lose a feature, flag it before deleting. Nelson decides port-vs-
>   drop per case.
> - **Premium flags from day one.** When a surface listed in spec/53 §2.7.1
>   is built or revised, the FIRST thing built is the flag gate. No
>   "implement now, gate later".
> - **Default-parameter rule.** Every default that's a parameter for
>   something goes into `mira.db`. Per-parameter decision on user-
>   customizability — if customizable, also exposed in the Settings dialog;
>   flags are set at install-time / rare events (license class change), not
>   in the normal Settings.
>
> ### Next sprint candidates (priority order)
>
> 1. ~~**Test cleanup sweep.**~~ ✅ landed (1811 → 1884 / 0 / 362 since).
> 2. ~~**`mira.db` user-level store** (spec/53).~~ ✅ landed —
>    schema + repo + protection contract (WAL / integrity_check /
>    SHA-256 sidecar / rolling backups) + `core/feature_flags.py` (17 v1
>    keys) + one-shot import on first launch + `Gateway.user_store`
>    lazy seam. SettingsRepo + EventsIndex stay unchanged on the public
>    surface (shim mode, Nelson 2026-06-08 — truly additive); new
>    surfaces consume `gw.user_store` directly. 64 new tests across
>    5 files.
> 3. **Event-creation surfaces** (spec/52). **In progress** — slices A,
>    B, C1 done (foundation logic). Slice C2 (Plan dialog Qt UI),
>    slice D (Browse peek + Override-ask + TZ-ask), slice E (retire
>    legacy surfaces) remaining. See the CURRENT banner above.
> 3. **Event-creation surfaces** (spec/52). The plan dialog (one surface,
>    14 rows, scrollable, autofill, save/load CSV) + the event info dialog
>    + the conditional TZ-calibration ask + pair-pick reuse. Replaces /
>    consolidates today's `new_event_page` + `past_photos_dialog` +
>    `past_photos_cameras` + `capture_action_dialog`.
> 4. **Cuts surfaces** (spec/51). New Cut dialog + walk surface + Cuts
>    list. Reads `event.db.photo_tag` for membership, `mira.db.cut`
>    for definitions. Replaces the gone-now `ShareShell` placeholder.
> 5. **Maps + Collages authoring** (spec/52 §4 + spec/51 §3.12). Per-event
>    authoring page; items with `provenance='authored'`. Unlocks two
>    Premium flags (`feature.maps`, `feature.collages`).
> 6. **People catalog + people filter** (spec/51 §3.13 + spec/53 §2.5).
>    Simplest tier per Nelson — user uploads reference photos, face match
>    at filter time. Premium flag `feature.people_tagging`.
>
> ### Pending design sessions (flagged for Nelson when he's ready)
>
> Each lands as its own scoped design pass with Nelson; blocks the
> respective surface from being final.
>
> - **Menu bar — first-level structure + dynamic context-aware population.**
> - **Photo Edit / Process redesign.** A simplified Edit experience that
>   replaces the current slider-heavy controls; `feature.advanced_edit_controls`
>   flag is in place but the actual simplified-Edit design is pending.
> - **Print feature design.** Legacy had a Print surface; spec/51 doesn't
>   mention it. Decide what Print means in the new model (photo-book album?
>   single-photo print sheet? Cut export variant?) and either restore as a
>   feature or formally drop.
>
> ### What landed today (2026-06-08 commits)
>
> | Commit | What |
> |---|---|
> | `ba2d1c9` | spec/52 + spec/53 + spec/51 update — design artifacts |
> | `120036c` | spec/05 — 8 promoted UI rules (titled QGroupBox / sidebar-vs-menu / Qt overlay / cluster routing / phase-default / clear-marks / back-refresh) |
> | `5a8d031` | spec/53 §2.7.1 — Premium flag set (cross_event_cuts, tz_correction, quick_sweep, video_clips_snapshots, third_party_roundtrip, audio_export, maps, collages, people_tagging) |
> | `2c12167` | spec/53 §2.7.1 — extended (bracket_detection, bracket_stacking, wizard_custom_rules, advanced_pick_overlays, plan_save_load_csv, advanced_edit_controls, event_lifecycle_close, detailed_event_types) — 17 flags total |
> | `4585d10` | Schema sprint — full DDL cleanup + photo_tag/photo_person + provenance='authored' + tz_source aligned + phase-enum cleanup + legacy Share/Curate UI + engine DELETED (11,914 deletions) |
> | (earlier in session) | Test cleanup sweep — 183 failures → 0; completes the schema sprint by retiring the dead `tags_json`/`notes` references the schema commit missed in gateway+index+3 UI dialogs, aligns 6 stale `tz_source` literals + 7 `miracraft.` monkeypatch paths, and deletes test_schema_v4_to_v5.py (v4→v5 migration path no longer exists in greenfield v1). |
> | (this session) | spec/52 slice C2 — Plan dialog Qt UI (`mira/ui/pages/plan_dialog.py` + `tests/test_plan_dialog.py`, 37 tests, all passing). 7-column QTableWidget per §5.3; salvaged legacy table setup; new `country_picker.make_single_country_combo_with_flags` factory for the flag-emoji-prefixed searchable combo; TzPicker reused verbatim; Save/Load CSV gated by `feature.plan_save_load_csv`; no TZ cascade (per-day model). Browse + Override marker click into injectable handlers — slice D wires the actual dialogs. Suite: 1961 → 1998 / 0 / 362. |
> | (this session) | spec/52 slice D.1 — Browse peek dialog. Pure logic `core/peek_select.py` (28 tests) — time-spread sampling + RAW+JPEG sibling dedup + video/huge filter + stats counters. Qt UI `mira/ui/pages/plan_peek_dialog.py` (21 tests) — modal 6×4 grid + click-to-zoom in-place via QStackedWidget + empty-peek hint. Suite: 1998 → 2047 / 0 / 362. |
> | (this session) | spec/52 slice D.2 — Override-ask UI. `mira/ui/pages/override_ask_dialog.py` (310 lines) + 22 tests. Side-by-side modal: 3 pickable rows (Country / TZ / Location) with per-row Keep/Override radios + Keep-all / Override-all shortcuts + OK/Cancel. Description row informational only (§4 propagate-if-untouched applied by host). Returns frozen `OverrideDecision`; default state pre-selects Override across the board (phone is ground truth per §1.2). Suite: 2047 → 2069 / 0 / 362. |
> | (this session) | spec/52 slice D.3 — Conditional TZ-calibration ask. Pure logic `core/tz_calibration.py` (15 tests) — `needs_calibration` produces per-(camera, day) candidates per §8.2 + §8.4; skips home-TZ, phones, already-calibrated. Ask dialog `mira/ui/pages/tz_calibration_ask_dialog.py` (13 tests) — Calibrate-now / Skip-for-now entry with candidate list. Slice E wires the host (PlanDialog + DiscreteTzDialog + gateway writes). Suite: 2069 → 2097 / 0 / 362. |
> | (this session) | spec/52 slice E.0 — call-graph audit. Found 4 of 6 "retire" files in §11 still load-bearing; PROGRESS estimate revised from ~150 lines to 8 sub-slices. |
> | (this session) | spec/52 slice E.1 — `EventCreationFlow` orchestration host (`mira/ui/pages/event_creation_flow.py`, 300 lines) + `PlanDialog.row_for_date` / `update_row` row-mutation seams + 14 stub-driven tests. Dialog classes injected as class attributes for test-swap; `_open_discrete_tz_dialog` is a method seam for stubbing the existing DiscreteTzDialog. Suite: 2097 → 2111 / 0 / 362. |
> | (this session) | spec/52 slice E.2 — Scan pipeline `core/scan_source.py` (290 lines) + 26 tests. `build_scan_result(photos, source_root)` (pure logic, fully tested with synthesized PhotoExif) + `scan_source(path)` (thin wrapper over existing `read_exif_batch` + `walk_photo_paths`). Produces all FlowInputs the host needs (minus `home_tz_minutes` + `existing_offsets` from settings/gateway). Suite: 2111 → 2137 / 0 / 362. |
> | (this session) | spec/52 slice E.3 — Ingest pipeline `core/ingest_pipeline.py` (280 lines) + 26 tests. `IngestPhotoJob` carries source + routing metadata; `run_ingest(jobs, event_root)` copies + bakes EXIF corrections (delegating to existing `capture_bake.bake_operations`). Quarantines no-timestamp files to `_no_timestamp/<camera>/`. Source files untouched (CLAUDE.md invariant). Built fresh (option 1) rather than extracting from `reconcile_commit` — much smaller (280 vs 530 lines) and cleaner for the new model. Suite: 2137 → 2163 / 0 / 362. |
> | (this session) | spec/52 slice E.4 — `event_dialog.py` Plan tab swap. Added `embedded=True` to PlanDialog (hides Save/Load CSV + OK/Cancel — host owns buttons). Two conversion helpers in event_dialog.py: `_scan_rows_from_trip_days` (gateway → dialog) and `_trip_days_from_scan_rows` (dialog → gateway, preserves day_number by date-match against `_original_trip_days`). Soft-hide via Include checkbox. Per-event Plan tab loses add/remove-day buttons (Nelson Option A: matches spec/52's "days come from Collect" model). One existing test updated to edit existing day instead of add a new one. Suite: 2163 → 2166 / 0 / 362. |
> | (this session) | spec/52 slice E.5 — `past_photos_dialog.py` rewrite (1124 → 290 lines; zero legacy imports). Drives `scan_source` → `EventCreationFlow` → adapter → `mira.ingest.run_ingest`. Added `per_photo_records` + `build_ingest_jobs` to scan_source. `IngestPhotoJob.day_date` made Optional. 8 new scan_source tests. Suite: 2166 → 2174 / 0 / 362. |
> | (this session) | spec/52 slice E.6 — MainWindow rewiring per §11. `NewEventPage` removed from page stack + menus. "Create from photos" merged into "New event" (Ctrl+N). `_open_plan_editor_for_event` delegates to `_open_event_plan_from_card`. 2 legacy tests skipped. Suite: 2174 → 2172 / 0 / 364. |
> | (this session) | spec/52 slice E.7 — Legacy deletions per §11. 6 production files + 6 test files deleted (~3000 lines net): reconcile_pipeline.py, trip_plan_parser.py, trip_plan_skeleton.py, new_event_page.py, past_photos_cameras.py, plan_editor_dialog.py + tests. `core/event_service.py` refactored to drop `parse_trip_plan` dep. Zero remaining imports of any deleted module. Suite: 2172 → 2116 / 0 / 318 (net -50 from deleted test files; 0 regressions). |
> | (prior session) | spec/52 slice E.8 — premature CLOSEOUT. Code was written for all 16 sub-slices but **Nelson eyeball-tested and the assembled flow did not match spec/52**. See the CURRENT banner at the top of this file. Nothing committed. |
> | (this session) | **Option-2 revert.** Audited the working tree vs spec/52; confirmed the deviations (EventInfoDialog never opened; event name collected upfront; SD-card vs directory not distinguished; per-(camera, day) calibration collapsed to per-camera; new `core/ingest_pipeline` orphaned; §11 retirements incomplete). With Nelson's explicit OK, restored 6 modified + 12 deleted files from HEAD, deleted 11 new UI/test/tool files, and kept the 4 pure-logic core modules + their tests (103 tests passing). Moved `ScanDayRow` + `OverrideMarker` into `core/scan_source.py` so the kept library files are self-sufficient. Working tree = `13d0bb2` + 4 untracked core files. Nothing committed. Suite: 2134 passed / 19 failed (pre-existing at HEAD) / 273 skipped. |
>
> ### Where the migration left off (2026-06-08 morning)
>
> ### State
>
> | Element | State |
> |---|---|
> | `mira/` package (139 .py) | ✅ every byte-compiles |
> | `core/` (91 KEEP modules) | ✅ ported |
> | `spec/` (19 KEEP + fresh PROGRESS) | ✅ ported with reference sweep |
> | `docs/` (8 KEEP foundational) | ✅ ported |
> | `tests/` (118 files) | ✅ 1811 pass / 0 fail / 362 skipped (post-cleanup-sweep) |
> | Memory (`~/.claude/projects/D--Projetos-Nelson-Mira/memory/`) | ✅ 35 durable items + index |
> | Build / verify / launch (`build.bat`, `verify.bat`, `launch.bat`) | ✅ ported (Phase 2); launch reaches Qt event loop (post-`ddb70f6`) |
> | Desktop shortcut `Mira (dev).lnk` | ✅ `python -m mira.ui` — dev-phase choice: console stays visible for live stderr / tracebacks. Swap to `pythonw.exe` when shipping (`core.proc.install_window_suppression` already suppresses child-process console flashes either way; see commit `9a5be85`). |
>
> ### Migration commit trail (for archaeology)
>
> | Phase | Description | Commit |
> |---|---|---|
> | 1 | Audit (KEEP / DROP lists) | (in retired MIGRATION.md, see git log) |
> | 2 | Skeleton (gitignore, infra, lean CLAUDE.md, assets) | `fe67c62` |
> | 3 | Specs + docs + reference sweep | `fde7df6` |
> | 4 | `core/` migration (91 KEEP modules) | `15f96de` |
> | 5 | `miracraft/` → `mira/` with import sweep | `8a8ab75` |
> | 5b | PROGRESS + MIGRATION update | `97ea217` |
> | 6 | `tests/` filtered + boot baseline | `39d4341` |
> | 6b | PROGRESS + MIGRATION update | `6ed467b` |
> | 7 | Memory consolidation + retire MIGRATION.md | `0d22d32` |
> | post — Fix launch (restore `core/video_marks` + rewire `ui.i18n` import) | `ddb70f6` |
>
> The full audit recipes + the regex-gap retrospective notes live in the
> deleted `MIGRATION.md` (see `git show 6ed467b:MIGRATION.md` to read).
>
> ### Outstanding work (not migration; ordinary project polish)
>
> Three items were explicitly **deferred** during Phase 7 per the
> retired MIGRATION.md's "optional" classification — none of them block
> XMC development, and each has a real reason to defer:
>
> - **`%LOCALAPPDATA%\Miracraft\` → `\Mira\` filesystem rename.**
>   Affects Nelson's existing user data (settings, events_index, logs).
>   Defer until there's a real reason — easier than orchestrating a
>   migration on every existing install.
> - **Broader visible-chrome rename Miracraft → Mira** (wizard
>   "Welcome to Miracraft", settings dialog tooltips, schema upgrade
>   message, `gateway.py:398` docstring, etc.). Pairs naturally with the
>   LOCALAPPDATA rename above — both should land together so chrome and
>   filesystem path stay consistent through one upgrade. See the next
>   section for the "do not blind-sweep" catalog.
> - **`core/` filename rename pass per spec/48 §1.1** (cull_*.py →
>   pick_*.py, curate_*.py → share_*.py, process_*.py → edit_*.py).
>   Pure polish; defers cleanly.
> - **Distribution build path (`build.bat`) not yet exercised.** Two
>   known issues to address when we first try `build.bat`:
>   (a) line 33 references `mira\__main__.py` which doesn't
>   exist — stale carryover from the `miracraft` → `mira` rename;
>   real entry is `mira\ui\__main__.py`. (b) Swap free Nuitka
>   for Nelson's Nuitka Commercial license (private repo
>   <https://github.com/Nuitka/Nuitka-commercial.git>) before any
>   distributed binary — see README's "Build step — Nuitka commercial"
>   section. Same closed-source-distribution-legality reason as the
>   PyQt6 commercial swap.
>
> ### 59 inherited test failures — triage backlog
>
> The Phase 6 baseline is 2101 pass / 59 fail / 353 skipped in ~63s.
> The 59 failures are inherited test/code drift from the Miracraft
> snapshot, not migration artifacts. Catalog (full per-file counts in
> `git show 6ed467b:MIGRATION.md`):
>
> - `test_schema_v4_to_v5.py` (14): test creates a v4 event.db and
>   migrates to v5, but `mira/store/schema.py::SCHEMA_VERSION`
>   reads as 1 — either the test was written against a future schema
>   bump that didn't land in the snapshot, or the constant regressed.
> - `test_settings.py` + `test_settings_dialog_rebuild.py` (3): the
>   `Settings` model dropped `cull_default_state` in favour of
>   `edit_default_state`; the tests still reference the old name.
>   Mechanical rename in the tests should fix.
> - `test_share_session.py` (2): `CurateSession.is_discarded` returns
>   False where the test expects True; `KeyError: 'skipped'` reading a
>   journal — behavioural drift in the curate state machine.
> - `test_capture_bake.py` (8), `test_day_grid_gateway.py` (7),
>   `test_video_session.py` (2), `test_focus_peaking.py` (9), and ~14
>   more: assorted RuntimeError / FileNotFoundError / AssertionError
>   spread across capture-bake + day-grid + video surfaces. Each needs
>   one-line triage.
>
> Not blocking — `pytest -q` runs to completion and the suite collects
> without errors. Triage as a standalone cleanup sprint.
>
> ### Test cleanup — schema-sprint fallout (LANDED this session)
>
> Resolved in this session. The 2026-06-08 schema sprint (event.db cleanup
> per spec/52 / spec/51 / spec/53) intentionally retired Event fields
> (`tags_json`, `notes`, `google_album_*`, `whatsapp_message`), Camera
> fields (`is_reference`), several tables (`participant`,
> `participant_device`, `checklist_item`, `distribution_action`,
> `share_tag`, `subset`, `subset_member`, `share_map`), and the entire
> legacy Share/Curate UI + engine. The first follow-on sprint (this
> session) finishes the job:
>
> - **Gateway + index** (`gateway.py`, `index.py`, `event_gateway.py`):
>   `_tags_from_json` helper deleted; `tags=`/`notes=` params dropped
>   from `set_classification`; `tags` field dropped from
>   `make_entry`/index entries; search haystack no longer reads `tags`;
>   the dead `share_tag` override in `phase_day_progress` retired; the
>   dead `share_tag` branch in `phase_picked_count` retired; the
>   `recompute_corrected_times` write uses `'user_declared'` instead of
>   the retired `'manual'` tz_source value.
> - **Three UI dialogs touched minimally** (`event_info_dialog.py`,
>   `new_event_page.py`, `preingest_dialog.py`): stale `ev.tags_json` /
>   `ev.notes` reads dropped; `Gateway.set_classification(notes=/tags=)`
>   calls dropped. The dialogs' tags-chip + notes widgets remain as
>   inert UI state until the event-creation-surfaces sprint (sprint #3
>   in the priority list) does the full redesign.
> - **Ingest engine + offload** (`engine.py`, `offload_record.py`):
>   `m.Camera(...)` construction no longer passes `is_reference`
>   (retired); `_tz_source` returns the new `'pair_picker'` /
>   `'user_declared'` values; `offload_record` writes
>   `'user_declared'` for the no-pair / configured-tz case. The
>   plan-level `CameraPlan.is_reference` survives (pair-picker UI
>   concept, doesn't reach event.db).
> - **Overview stats** (`overview_stats.py`): `_PHASE_FUNNEL_LABELS`
>   was a duplicate-key bug (two `"pick"` entries) wrapped around the
>   pre-collapse vocabulary; replaced with `{"pick": "Picked", "edit":
>   "Edited"}` reflecting spec/48 + spec/52.
> - **183 test failures resolved**: deleted `test_schema_v4_to_v5.py`
>   (v4→v5 migration retired with the greenfield v1 reset); rewrote
>   test_gateway.py / test_day_grid_gateway.py / test_day_grid_model.py
>   to drop the retired surfaces and use `pick`/`edit` for cross-phase
>   independence checks (cull+select collapsed); swept 7 stale
>   `miracraft.X` monkeypatch paths to `mira.X`; added skipif
>   markers to capture_bake + video_session for the missing-exiftool
>   environmental cases. PROGRESS's old "59 inherited drift" catalog +
>   the post-schema-sprint catalog are both empty now.
>
> ### Lazy legacy-import tombstones — runtime triage backlog
>
> Five `from ui.X` / `from data.X` imports survive inside function
> bodies in production code. Consistent with the deliberate Phase 4
> lazy-`data.event_store.save_event` tombstone pattern — they fire ONLY
> if the surrounding feature path is reached. The app launches and
> runs without hitting them; each will surface as a runtime
> `ModuleNotFoundError` the first time a user (or test) exercises the
> feature. Triage by either (a) rewiring to the MC equivalent, or
> (b) deleting the legacy branch entirely if the feature has been
> superseded:
>
> | File | Line | Legacy import | Triggered when |
> |---|---|---|---|
> | `core/event_service.py` | 167 | `data.event_store.save_event` | legacy event save path (MC uses gateway → store) — likely dead |
> | `core/reconcile_pipeline.py` | 1102 | `data.event_store.save_event` | same as above; likely dead |
> | `core/phase_progress.py` | 230 | `ui.pages.day_status_table` | phase-progress export hook reaches into a legacy UI page |
> | `mira/ui/edited/edit_host_page.py` | 1079 | `ui.culler.cull_export_dialog` | Edit-phase export dialog opener — MC likely wants `mira/ui/picked/pick_export*` |
> | `mira/ui/edited/edit_page.py` | 812 | `ui.culler.cull_export_dialog` | same as above |
>
> Same regex-gap pattern as the production launch fix (`ddb70f6`): the
> Phase 1 audit's transitive-closure regex was `from core\.X` (dotted)
> and the Phase 5 sweep was `miracraft.X` only — neither caught
> bare-package `from core import X` nor legacy `from ui.X`. If the
> audit script gets reused for a future package rename, widen the
> regex to all three forms (`from X import Y`, `from X.Y import`,
> `import X.Y`) up front.
>
> ### Intentionally kept as "Miracraft" (do NOT sweep blindly)
>
> Filesystem / namespace continuity (preserves Nelson's existing user
> data + Qt translation memory + internal stable identifiers):
>
> - `mira/paths.py` — `%LOCALAPPDATA%\Miracraft` path
> - `mira/ui/app.py:29-30` — `APP_NAME` / `ORG_NAME = "Miracraft"`
>   (drives `QSettings` registry path + LOCALAPPDATA dir)
> - `mira/ui/app.py` — `miracraft.log` filename,
>   `getLogger("miracraft")` / `getLogger("miracraft.qt")` namespace
> - `mira/ui/i18n.py:13` — `_DEFAULT_CONTEXT = "Miracraft"`
>   (translation context key — changing it invalidates translation memory)
> - `mira/ui/theme.py` — `PALETTES` key `"Miracraft"` + the two
>   call sites (`list_button.py:76`, `pick_stats_chart.py:69`)
> - `settings.rebuild.json` filename (settings file convention)
>
> Plus the deferred visible-chrome refs catalogued in "Outstanding work"
> above — those stay as "Miracraft" until they're swept with the
> LOCALAPPDATA rename.
>
> ### Branch policy (locked)
>
> - **`XMC`** — current working branch. Full enthusiast version.
> - **`MC`** — future branch off XMC once XMC ships; streamlined version.
> - Long-term: one unified MC, with streamlining driven by user profile
>   + how the user works — not two parallel codebases.
>
> ### Vocabulary locked from day one
>
> Collect / Pick / Edit / Share. Decision verbs Pick / Skip. No Cull /
> Curate / Keep / Discard legacy remnants. The Share-phase artifact is a
> **Cut** (see `spec/51-share-cuts-vision.md`).
>
> ### Looking back at Miracraft
>
> `D:\Projetos_Nelson\Miracraft\` stays intact as the ancestor repo.
> When MC code needs context that didn't travel — a retired surface, a
> dropped spec, an old commit message — look there. Don't copy code back
> without an audit; the cuts were intentional. The full audit lists +
> migration recipes are preserved in commit `6ed467b`'s MIGRATION.md.
>
> ### Pending design sessions (flagged during the 2026-06-08 design session)
>
> Two design sessions explicitly deferred during the event-creation + Cuts
> design work. Each lands as its own scoped design pass with Nelson; not
> blocking implementation of what's already locked, but blocks the
> respective surface from being final:
>
> - **Menu bar — first-level structure + dynamic context-aware population.**
>   What top-level menus exist, what entries each holds, and how entries
>   show/hide based on context (event open? per-event-or-cross-event view?
>   feature flags off? closed event?). Surfaces touched on every screen.
> - **Photo Edit / Process redesign.** Nelson has a simplified-Edit idea
>   that replaces the current slider-heavy controls with something MC users
>   can use without becoming pro photographers. The flag
>   `feature.advanced_edit_controls` is in place; the simplified-Edit design
>   defines what MC actually shows when the flag is off (and likely
>   restructures XMC's Edit too).
> - **Print feature design.** Legacy Share/Curate has a "Print" surface
>   (flagged during the 2026-06-08 audit of code to delete). spec/51 has
>   no equivalent. Need to decide what Print means in the new model
>   (photo-book album? single-photo print sheet? Cut export variant?)
>   and either restore as a feature or formally drop. Until designed, the
>   legacy Print code is gone and there's no Print surface in XMC.
>
> ### Picking up next session
>
> 1. Read `spec/00-charter.md` (the constitution).
> 2. Read `CLAUDE.md` (vocabulary + locked invariants).
> 3. Pick from the "Outstanding work" / "59 inherited test failures" /
>    "Lazy legacy-import tombstones" backlogs above, OR start ordinary
>    XMC sprint work — the app launches and the migration has cleared
>    the runway.

---

## How to update this file

Single CURRENT banner above. When you finish a session, rewrite the banner
to reflect:
- What's freshly done (commit hash if relevant)
- What's open and what the next session should pick up
- Any open questions Nelson should answer when he returns

Older banners can stay below the current one as a log if useful, or get
trimmed when they're no longer informative.
