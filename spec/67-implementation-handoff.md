# spec/67 — Implementation handoff: phase-model revision, slices 4–6

**Authored 2026-06-14 (Nelson). Build brief for the local coding agent.**

This is the persistent handoff for finishing the spec/66 phase-model revision
(Collect / Pick / Edit / Export). The design is locked in
**spec/66-collect-pick-edit-export.md** — read it fully before any code.
Slices 1–3 are already done; this brief covers slices 4–6.

> **Read [spec/68](68-phase-redesign-coordination.md) before slice 5.** This
> brief was written without reference to the redesign (spec/65) or the
> `mira/ui/design/` component catalog. spec/68 amends slice 5: the **new Export
> surface must be built from the design catalog, fidelity-first** (it has no
> mockup), not as a port + recolor. Where spec/68 and this brief differ on
> slice 5, spec/68 governs.
>
> **If you are already running** when you reach this banner: commit or stash your
> current work first (so spec/68 lands cleanly and you keep a checkpoint), then
> read spec/68. The current tree is already post-redesign — confirm your branch
> descends from `XMC-redesign` (`f5766b7`) before committing slices 4–6; if it
> doesn't, stop and surface it. If you already built any of slice 5 before
> reading spec/68, re-check that surface against spec/68 §3 rather than assuming
> it's correct.

---

## Required reading (load-bearing)

- **spec/66** — the design being implemented (§4 has the full cascade, §6 the
  two open metric questions).
- **spec/00-charter.md** — Supreme Rule.
- **spec/57** — folders / round-trip.
- **spec/59** — Edit surface.
- **spec/60** — batch export engine.
- **spec/61** — Cuts.
- **spec/63** — LOCKED keyboard map.

## Hard rules

- `core/` never imports from `mira/ui/`. UI talks to gateway + core only.
- The keyboard map in spec/63 is **LOCKED** — do not change
  P / X / Space / C / Tab / Enter / F10 / etc.
- **View-over-engine:** don't rewrite working engines (batch export, PhotoCache,
  adjustment pipeline) to restyle a surface — wire surfaces to them.
- Run `verify.bat` after **each** slice; fix failures before moving on. Commit per slice.
- Slices 1–3 are already done (gateway `phase_day_progress`, the event-card
  pipeline, and PhasesPage now use Collect/Pick/Edit/Export with
  phase-identity colors).

---

## Slices (in order, each its own commit)

### SLICE 4 — De-clutter the Edit surface (`mira/ui/edited/*`)

Remove the export buttons + batch-queue UI from Edit. Edit keeps **only**
classification, tone, crop (develop). Every keeper still gets a
standard-correction baseline on entry. Edit must no longer trigger export.
The export status/queue UI moves to the new Export surface (slice 5).

### SLICE 5 — Build the new Export surface

A green/red decision grid over **all** picked keepers (each carrying its Edit
baseline, touched or not), default **GREEN** (opt-out), reusing the §5a state
colors + the locked P/X keymap (green = export, red = drop). Reuse the Thumb
widget + FlowLayout. Host the batch-export trigger + status/queue moved from
Edit; materialize the green set via the spec/60 engine. Add a route, wire the
PhasesPage Export tile click to it, and add the menu entry (slice 6).
Externally-edited (LRC/Helicon) returns count as edited and arrive green by
default (adopt/link, don't re-render).

### SLICE 6 — Menus + `Exported Media/` plumbing

- `main_window.py`: add an **Export** top-level menu ("Open Export phase")
  beside Collect/Pick/Edit; keep the **Share** menu but enable it **only** on
  closed events.
- `core/path_builder.py` + the spec/60 engine: add the `Exported Media/` tier;
  render the green set there. Repoint `lineage.export_relpath` from
  `Edited Media/` to `Exported Media/`; hardlink green third-party returns from
  `Edited Media/`.
- `exported_item_ids()` / `exported_files()` must distinguish the exported
  (shipped) set from mere edit candidates (e.g. a `phase='export'` marker or an
  `Exported Media/` relpath test). Update tests accordingly.

---

## Per-slice loop

After each slice: run `verify.bat`, launch the app to eyeball, then commit.

## Open questions

The §6 items in spec/66 (Collect metric; the exact "cleared in Edit" signal)
are left open. Follow spec/66's stated defaults, or — if a genuinely new design
question arises — bring it back to Nelson so the **spec** is updated rather than
letting code drift from it.

## First-time setup

```
cd D:\Projetos_Nelson\Mira
pip install -e .[dev]   # so verify.bat works
```
