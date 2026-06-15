# spec/71 — Surface identity header (you-are-here across the decision surfaces)

**Authored 2026-06-14 (Nelson + Claude). A UI standard, child of
[spec/65](65-redesign-fidelity-pass.md) / applied by
[spec/70](70-new-ui-completion-plan.md).**

## The problem

The decision surfaces — Quick Sweep, Picker, Editor, Export — share the same
grid/viewport chrome, so the user can't tell at a glance **which phase they're
in or what they're meant to do there.** The same reusable Days List / Days Grid
component even appears inside three different phases (Quick Sweep, Pick, Export),
making them visually identical.

## The pattern

Every decision surface carries a consistent **identity header**:

1. **Phase-color accent** — a header rail / underline in the phase's identity
   colour.
2. **Phase name** — a short badge: `QUICK SWEEP` · `PICK` · `EDIT` · `EXPORT`.
3. **Purpose line** — one sentence saying what to do here.
4. **The surface's legend** — the §5a swatches + reminder, worded per surface.

The "where am I" cue is the **combination** (name + colour + purpose), never
colour alone — see the collision rule below.

## The one rule: two colour systems, two jobs (never mix)

- **Phase-identity colour → chrome only** (the header rail + name badge).
- **§5a state colour → cell borders only** (green=picked, red=skipped,
  orange=compare, yellow=mixed) — **unchanged, never repurposed.**

This separation is mandatory because the systems **overlap**: the Export phase
colour is `green` (#34d399) — *identical* to the "picked / will-export" border.
If phase identity painted the cell borders, Export would be all-green and
unreadable. Keeping phase colour in the header and state colour on the cells
resolves it; the explicit `EXPORT` name disambiguates the shared green.

## Colours (reuse existing tokens — do NOT invent)

From `_PHASE_COLOR_TOKEN` (`_event_card_redesign.py`) / `_PHASE_COLORS`
(`phases_page.py`) — the same palette the event-tile bars and 2×2 donuts use, so
the surface matches the donut the user clicked:

| Phase | Token |
|---|---|
| Collect | `blue` (#22d3ee) |
| Pick | `accent` (indigo) |
| Edit | `amber` (#fbbf24) |
| Export | `green` (#34d399) |

**The shared grid inherits its host phase's colour.** The same Days List /
Days Grid reads **blue** under Quick Sweep, **accent** under Pick, **green**
under Export — the identity header is what tells them apart.

## Per-surface spec

| Surface | Colour | Name | Purpose line | Legend |
|---|---|---|---|---|
| Quick Sweep | blue (Collect) | QUICK SWEEP | "Fast pass — skip the obvious rejects" | green **Keeping** · red **Skipped** · yellow **Mixed** — *"Everything starts kept — press X to skip the rejects."* |
| Picker | accent (Pick) | PICK | "Decide each shot — pick the keepers" | green **Picked** · red **Skipped** · orange **Compare** · yellow **Mixed cluster** — *"Border = your pick · P pick · X skip · C compare."* |
| Editor | amber (Edit) | EDIT | "Develop your picked keepers" | **no state legend** (P/X inert); optional hint *"\\ compare before/after · F10 full-res preview."* |
| Export | green (Export) | EXPORT | "Choose what ships" | green **Will export** · red **Won't export** · yellow **Mixed** — *"Everything ships by default — press X to drop what you don't want."* |

## Scope notes

- **Share / Cuts is NOT a phase** (spec/66 — it's the closed-event state). It is
  outside the four-phase identity palette; give it its own clear name/purpose
  header (e.g. the closed-card treatment), not a phase colour.
- Build it with the design catalog (`PageHeader` etc.), `tr()` all strings,
  both themes, no Unicode-glyph placeholders.
- **Future surfaces are built with this header from the start** — it's the
  standard, not a retrofit.

## Related

- [spec/63 §5a](63-photo-viewport.md) — the locked state colours (cell borders).
- [spec/66 §1](66-collect-pick-edit-export.md) — the phases + their identity colours.
- [spec/70](70-new-ui-completion-plan.md) — applies this during the surface passes.
