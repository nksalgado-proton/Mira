# CLAUDE.md

Guidance for Claude Code sessions working on Mira.

## What this project is

**Mira** — a Windows desktop photography workflow tool for serious
amateurs. Closed-source freeware, strict offline-first, multi-language
(English + Portuguese). The descendant of [Miracraft](../Miracraft/), forked
fresh 2026-06-08 to leave behind the pivot history and start on the locked
vocabulary + relational data model.

## One product, two branches (today)

- **`XMC`** — the full enthusiast version. Current working branch. Includes
  every helper, every adjustment, every advanced surface.
- **`MC`** — the streamlined version. Branched from XMC once XMC ships,
  by carving down. Same codebase, slimmer user-facing surface.

Long-term: one MC, with streamlining driven by user profile + the way the
user uses the system — not by two parallel codebases. Don't optimise for
permanent divergence.

## The four phases

| Phase | What happens |
|---|---|
| **Collect** | SD-card / past-photos ingest, day plan, Quick Sweep |
| **Pick** | One unified decision pass across all captured content (default-Skip) |
| **Edit** | Non-destructive tone + crop (develop) |
| **Export** | Materialise developed/keeper frames to processed JPEGs. The surface speaks **versions** (one item ⇒ 0..N lineage rows) + **provenance** (`mira_render` / `third_party`). Third-party returns from external editors hardlink into `Exported Media/` at scan time (spec/89 Model B); the `↑ Export now` batch trigger does the deletes-then-renders run. See [`spec/89-export-model-b.md`](spec/89-export-model-b.md). |

The four working phases are **Collect → Pick → Edit → Export** (spec/66,
revising spec/48's 4th phase). **Share is NOT a phase** — it is a permanent
**state of closed events**: assembling **Cuts** from the exported files that
survived the pipeline (see The Cut model below). It has no progress bar.
Per-day progress metrics: Collect = day has captures; Pick = decided ÷
captured (review completeness); Edit = developed ÷ picked; Export = exported
÷ picked.

Decision verbs: **Pick / Skip**. Internal state value `'picked'`. The
app-wide keyboard map is LOCKED (Nelson 2026-06-12, spec/63 §4 —
**cycle direction updated 2026-06-18**):
**P Pick / X Skip / Space toggles Pick⇄Skip / C cycles
Skip→Pick→Compare**; **Tab = play/pause** (clips; never focus traversal
on photo surfaces), **Enter = cluster sweep play/pause**, **F10 = the
truth key** (full-res real pixels; in Edit, the developed Preview),
F/F11 fullscreen, Esc one level back, Ctrl+Z undo. Transport and
decision keys never share. BOTH legacy P bindings are MIGRATED
(2026-06-12): Picker P-sweep → Enter (spec/63 5d) and Edit P-Preview →
F10 (6a, which also deleted the dead P-export branch and made the
video workshop's Tab transport). D stays retired. No legacy "Cull /
Curate / Keep / Discard" vocabulary anywhere in code, UI, schema,
settings, or specs.

## The Cut model (Share — the closed-event state)

Share is the permanent state of a **closed** event whose files survived
Collect→Pick→Edit→Export; it is where Cuts are assembled (spec/66).

A **Cut** = a time-budgeted, chronologically-ordered set of **exported
files** the user assembles to hand off to an external slideshow tool.
**Cuts are NOT final slideshows** — transitions, music sync, and rendering
belong in PTE. Every event has the built-in **#exported** Cut (a live query
over every `Exported Media/` lineage row, never stored — Model B / spec/89
expands what enters that set: Mira renders **AND** hardlinked third-party
returns); new Cuts are composed by pool algebra over existing Cuts
(`#exported − #cut_1 + #cut_2`), refined in a Picker session on a separate
decision ledger, and consumed via flat-grid Play/Export with generated
day-separator slides. Cuts are zero-byte until export materializes links.
See [`spec/61-share-event-cuts.md`](spec/61-share-event-cuts.md) (governs;
spec/51 stays as the superseded brainstorm record).

## Sources of truth — read first, then code

The `spec/` tree governs. Read in numerical order; `spec/00-charter.md` is
the constitution. Specs trump docs trump code. When code disagrees with a
spec, fix the spec first (capture the new understanding), then the code.

Load-bearing specs:

- **`spec/00-charter.md`** — the Supreme Rule, principles, locked decisions
- **`spec/03-schema.md`** — the relational schema (the SQL source of truth)
- **`spec/05-ui-standards.md`** — UI grammar (every-control-has-a-hint, QSS roles)
- **`spec/08-gateway.md`** — the data seam
- **`spec/41-mira-x-completion.md`** — XMC completion sprint scope *(rename pending)*
- **`spec/48-four-phase-pivot.md`** — locked vocabulary + 4-phase model (its 4th phase, Share, is revised by spec/66)
- **`spec/66-collect-pick-edit-export.md`** — phases are Collect/Pick/Edit/Export; Share is a closed-event state (revises spec/48 §Share)
- **`spec/61-share-event-cuts.md`** — the Cut model, now the Share *state* of closed events (supersedes spec/51's model)
- **`spec/56-video-workshop.md`** — marker-partition video model (Pick uniformity + Edit-time clips)
- **`spec/57-folders-and-roundtrip.md`** — folder model + external round trip + event creation
- **`spec/58-classification-and-wizard.md`** — background classification pass + Edit-only classification surface
- **`spec/59-edit-surface.md`** — Edit surface (Stop model, modeless development, export status + batch queue)
- **`spec/60-batch-export-engine.md`** — batch export engine (worker process, hardware ladder, zero foreground lag)
- **`spec/62-navigation-performance-audit.md`** — the 2026-06-12 nav-sluggishness audit (measured numbers; the record behind spec/63)
- **`spec/63-photo-viewport.md`** — ONE photo/video display engine + pixel tiers (thumb/proxy/original) + the LOCKED keyboard map
- **`spec/72-third-party-roundtrips.md`** — Model B contract (parent): provenance signal, scan-time hardlink into `Exported Media/`
- **`spec/89-export-model-b.md`** — Export-surface rebuild: versions cluster, provenance badges + scan chip, preview viewer, `↑ Export now` batch trigger, single-item `Export this` re-render-ask

## Critical invariants

1. **One-way dependency:** `mira/ui/` imports from `mira/gateway`
   + `core/`. `core/` never imports from `mira/ui/`.
2. **No hardcoded user paths.** `core/settings.py` + `mira/paths.py`
   are the only sources of truth for user-data locations.
3. **No network calls.** Strict offline-first. `urllib` / `requests` / `httpx`
   / `aiohttp` / `socket` must not appear outside specific allow-listed
   locations.
4. **No telemetry, no analytics, no crash reports.** Failures log locally.
5. **English is NOT firm.** Every user-visible string passes through
   `tr()` (Qt's translation system).
6. **Atomic write-then-rename** for any persisted state.
7. **The captured tree is never mutated** except via the sanctioned SD-card
   wipe gate (see Collect-phase docs). One sanctioned *addition* (spec/57):
   externally-merged stack masters are adopted into the additive-only
   `Original Media/Merged/` subfolder; card-derived subtrees stay untouchable.
8. **Pure-logic `core/` modules are reusable.** No Qt imports inside `core/`.

## Project commands

```powershell
pip install -e .[dev]
launch.bat                                          # run from source
verify.bat                                          # full test suite
verify.bat tests\test_gateway.py                    # single file
python -m pytest tests/test_<file>.py -x -q         # single test, fail-fast
build.bat                                           # Nuitka → dist/Mira.exe
ISCC.exe installer.iss                              # Inno Setup → installer
```

## Module structure

```
mira/
  gateway/         — the per-event facade over event.db
  ingest/          — Collect-phase backing logic
  picked/          — Pick-phase data layer
  settings/        — settings model + repo
  shared/          — Share-phase data layer (Cuts)
  store/           — SQLite schema + repo
  ui/
    base/          — base widgets, surface scaffolding
    edited/        — Edit-phase UI
    media/         — MediaCanvas + hosts
    pages/         — dashboards, dialogs
    picked/        — Pick-phase UI
    shared/        — Share-phase UI (Cuts)
    shell/         — MainWindow, sidebar, page stack
    wizard/        — first-run wizard

core/              — pure-logic modules, no Qt
assets/            — themes, brand profiles, scenarios, icons
bin/               — bundled binaries (ExifTool, FFmpeg — not committed)
spec/              — design docs, numerical order
docs/              — supporting reference docs
tests/             — pytest suite
```

## Looking back at Miracraft

`D:\Projetos_Nelson\Miracraft\` stays intact as the ancestor repo. When
something in MC needs context that didn't travel — a retired surface, a
dropped spec, an old commit message — look there. Don't copy code back
without an audit pass.

## When in doubt

1. Re-read the relevant `spec/XX-*.md`.
2. If it's not clear, ask before guessing. The cost of surfacing an
   ambiguity is much lower than the cost of building the wrong thing.
3. Update the spec before (or with) the code change.

## QSS + clickable affordances

Visual treatment lives in QSS, not in widget code. Never `setStyleSheet(...)`
inline in widget modules (the `tests/test_no_inline_qss.py` guard fails the
suite on any growth past `scripts/qss_guard_baseline.json`; a reviewed
exception is marked with a trailing `# pragma: no-qss`). The canonical
stylesheet is **`assets/themes/redesign.qss`** — one token-substituted
template covering both themes (substitution happens in
`mira/ui/palette.py::build_redesign_qss`). Legacy `assets/themes/dark.qss`
and `light.qss` are being retired per **spec/92**; what remains is the
residue of still-referenced legacy roles plus the global `QWidget` base
rule, all concatenated AFTER redesign.qss by `theme.py::apply_theme()`.

Widgets opt into roles via `setObjectName("<Role>")`. Variation rides on
**Qt dynamic properties + `polish/unpolish`**, never on sibling roles —
e.g. `#Chip[tone="open"]`, `#Card[level="2"]`, `#StateBorder[state="picked"]`,
`#SectionBox`, `#Tile[tone="stat\|cover"]`. The full canonical catalog lives
in **spec/92 §2** (per-widget standard) and **Appendix B** (pixel-precise
values); spec/92 Appendix A maps every retired legacy role to its canonical
replacement.

Every clickable widget gets visible border + hover + pressed + disabled
states + a pointing-hand cursor on hover. The cursor is applied by an
app-level event filter (PyQt6 QSS `cursor` is unreliable on Windows).
