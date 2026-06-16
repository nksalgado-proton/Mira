# spec/73 — Phase 5 "Left Behind" audit

**Status:** generated 2026-06-15 by a per-surface migration audit (parallel
read-only passes comparing spec/70 + each governing spec against the live
code). Seeds the Phase 5 closeout (spec/70 §"Phase 5"). Surfaces 12 (in
flight), 13 (folded into the 09 findings), Quick Sweep (not built) and the
visual-fidelity-vs-mockup layer are NOT yet covered here.

> **Update 2026-06-16 — read spec/74 first.** A follow-up audit
> ([`spec/74-ui-fidelity-handoff.md`](74-ui-fidelity-handoff.md)) and the
> intervening commits closed Tier-1 #2 (`2bdd50f`), Tier-1 #4 (`380e8c9`),
> spec/65 §3.8 "crop drag handles," and the "Look preset previews" item.
> The crossed-out entries below are the ones that have already landed —
> the next audit starts from there, not from the 2026-06-13 punch list.

Per-surface verdicts: **07 Picker** substantially done · **08 Editor** done on
function, test holes · **09 Share/Cuts** shell migrated, deep Cut program has
real gaps · **Export** shipped-quality, feature-complete · **05/06 Days
Lists/Grid** built + wired, but the legacy-retirement DoD fails and tests are
thin.

---

## Tier 1 — Functional gaps / bugs (fix)

1. **Cuts: clips & snapshots are not placed chronologically in their day.**
   Segment and snapshot items are created with no `day_number` /
   `capture_time_corrected` (`event_gateway.py:1466` segment, `:1533` split
   right-half, `:1639` snapshot), so in a Cut they resolve to `day_number=None`
   (`cut_session.py:62-65`) and all collapse under one undated separator
   instead of interleaving with photos by capture time. Fix is upstream in the
   gateway: inherit the parent video's `day_number` + offset `capture_time`
   (snapshot `at_ms`; segment start `in_ms`). *(Already briefed; still unbuilt.)*

2. ~~**New Cut dialog shows fabricated pool/match counts.** The redesigned dialog
   multiplies static declared `PoolOption.count` values
   (`new_cut_dialog.py:624-633`) and never binds the real `pool_probe` /
   `totals_probe` callbacks — the "N of M match" count is cosmetic and style/media
   filters don't change it (`new_cut_dialog_adapter.py:292-296`).~~
   **DONE (commit `2bdd50f`, 2026-06-15):** "New Cut dialog: live pool/totals
   probes, CutDraft moves to mira.shared." Adapter now binds the real probes;
   match count is live.

3. **New Cut dialog: Load template… / Save as template… are dead controls.**
   Both buttons render (`new_cut_dialog.py:291,542`) but aren't wired; the
   adapter stubs them even though the host fully implements the template store
   and passes it in (`share_cuts_page.py:717-772`).

4. ~~**Days Lists bulk actions are log-only stubs.** "+ Start a new pass…",
   "✓ Pick all days", "✗ Skip all days" and per-row `day_pick_all` /
   `day_skip_all` all route to `_on_days_lists_*_stub` handlers that only
   `log.info(...)` (`main_window.py:2217-2233`). Buttons render and emit but do
   nothing.~~ **DONE (commit `380e8c9`, 2026-06-15):** "Days Lists: wire the
   bulk Pick / Skip actions." The stubs are replaced; the buttons now apply
   the decision across the selected scope.

## Tier 2 — Definition-of-Done blockers (legacy not retired)

5. **Legacy grid modules still live-imported.** spec/70 §6 requires no live
   imports of `base/day_grid_view.py`, `base/day_grid_cell.py`,
   `picked/grid_view.py`. Still load-bearing under the Share/Cuts surfaces:
   `cut_detail_page.py:39-40`, `cut_session_page.py:46-47`,
   `pool_detail_page.py:59-60` (→ day_grid_cell/view), `compare_page.py:60`
   (→ grid_view). Surface 06 itself is migrated off them; the modules survive
   under 09.

6. **Legacy `shared/new_cut_dialog.py` not retired.** The redesigned dialog is
   wired only through `new_cut_dialog_adapter.py`, which still depends on the
   legacy module for the `CutDraft` dataclass (`:46`). Surface 13 is "live via
   adapter," not a clean swap.

7. **App-wide `mira/ui/picked/` imports still live.** `adjustment_surface.py:80`
   (`crop_overlay`), `compare_page.py:60` (`grid_view`), `list_button.py:45`
   (`pick_stats_chart`), `main_window.py:2317` (`camera_clock_dialog`). None are
   reached by the Picker (07 is clean) but they keep the package alive.

8. **Legacy `picked/pick_page.py` + `pick_photo_surface.py` still in tree.**
   Unwired from MainWindow (route swap is genuinely done), deliberately kept for
   the Quick Sweep build that reuses them (`picker_page.py:37-39`). OK to keep
   until Quick Sweep lands, then retire.

## Tier 3 — Test holes

9. **Editor write path untested.** No test that a tone/look/**crop**/**rotation**/
   aspect edit reaches `save_adjustment` via `_on_surface_changed`
   (`editor_page.py:1054-1152`) — the load-bearing persistence contract. Also
   untested: the F10 developed-preview render (`_open_processed_lens`) and the
   classification human-flip write (`_on_style_decided`). The test docstring
   overclaims F10 coverage.

10. **Days Grid Pick-mode suite missing.** `test_days_grid_export_mode.py` covers
    only `phase="export"`. No coverage of the Pick-mode grid, the locked keymap
    (`keyPressEvent` P/X/Space/C/Esc/Ctrl+Z), cluster expand/collapse, or the
    mixed-split chip. The old `test_quick_sweep_clusters.py` is `skip`-ed wholesale
    with a "port to a DaysGridPage suite" TODO never done.

11. **Days Lists: zero dedicated tests.** No `test_days_lists*.py`.

12. **Picker keymap smoke missing.** No PickerPage-level P/X/Space/C/Enter
    end-to-end pin (spec/70 §5.5 wants the keymap-verification smoke).

13. **Cuts: no test for chronological placement (Tier 1 #1) or live filter counts
    (Tier 1 #2).**

14. **Export: no integration test for the menu/phase-tile → export-mode entry**
    (`_export_phase_active` → `open_for_day(phase="export")`).

## Tier 4 — QSS / housekeeping

15. **Inline `setStyleSheet` violations (charter QSS invariant).** `_PoolCard`
    (`share_cuts_page.py:257`) and the redesigned New Cut dialog (pervasive —
    chips, dividers, formula tokens, match label, header). Move to theme roles.

16. **Dead import** `danger_ghost_button` (`share_cuts_page.py:55`).

17. **"cull" docstring relic** in `picker_page.py:1` (internal comment, not a user
    string — scrub for spec/48 cleanliness).

18. **Stale spec/70 row:** Days Lists is marked "❌ no entry point," but the Pick
    tile lands on it via `_open_days_lists_for` (`main_window.py:4575-4579`). Update
    the spec row.

## Tier 5 — Design / product confirmations (not bugs)

19. **Export trigger is per-day only** (`days_grid_page.py:1787-1800`) — no
    event-wide "export everything." Intentional per spec/68 §3; confirm the UX is
    acceptable.

20. **Editor video-workshop F10 live in-canvas preview** is a known deferred
    follow-up (`editor_page.py:1284-1285`), lens-only for now.

---

## Suggested closeout order

Tier 1 (real bugs the user will hit) → Tier 2 (retire legacy; needed for the DoD
and unblocks the final sweep) → Tier 3 (test holes; pin before declaring done) →
Tier 4 (QSS + housekeeping) → confirm Tier 5. Fold Surface 12, Surface 13's clean
swap, Quick Sweep, and the visual-fidelity-vs-mockup pass in as those land.
