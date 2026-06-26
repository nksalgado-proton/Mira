# 154 — Cross-event Cut overlays (provenance, not narration)

**Status: IN PROGRESS (Nelson 2026-06-26). Brings cross-event Cuts to
overlay parity with event Cuts across BOTH surfaces — the in-app Play AND
the PTE export — and does it on a SHARED text-composition backbone (no
duplication). A cross-event Cut is a *search result*, not a story: its
overlays explain **provenance** — where each frame came from and how the
set was assembled. Touches `mira/ui/shared/separator_card.py` (the shared
card renderer), a new pure slide-text composer, `cross_event_cut_play.py` +
`library_page.py` (Play), `cross_event_cut_export.py` + the cross-event PTE
generation (export), and the New Cross-event Cut dialog (the per-slide
origin flag).**

## Two surfaces, one composition (the reuse backbone)

The text CONTENT for a slide (what words go on it) is composed ONCE by a
pure composer; three consumers render it, exactly as event Cuts already do:

* **Card renderers** (`render_label_card`) — bake the opener/separator
  title + lines INTO the card image ("burned in", non-editable Play).
* **Live caption overlay** (`CutPlayOverlay`) — draws the photo caption
  (+ origin label) on each frame during Play.
* **PTE generator** — emits the same strings as separate, editable
  `:Text` objects (spec/153 mechanism, flat swappable backgrounds).

Event and cross-event each supply their own provenance source into the
composer; the composer output + all three renderers are shared. The
drawing primitive `render_label_card(title, lines, …)` is extracted from
today's `render_cut_opener_image` / `render_separator_image` so event and
cross-event cards draw through one path.

## Today's gap

Cross-event export produces **photos + audio only** — no opener, no
separators, no overlays (`cross_event_cut_export` wires no writers;
`library_page._generate_cross_event_pte_into_folder` builds bare members).
In-app cross-event Play *does* show per-(event, day) separator cards live,
but export/PTE never has. spec/154 brings export/PTE to a *cross-event*
parity (not a copy of the event story — a provenance read-out).

## The three text kinds

1. **Photo caption** — same vocabulary as event Cuts: When / Where /
   Camera / Exposure, controlled by the same field flags in cut creation,
   one centred line at the **bottom**. Sourced cross-event (each member
   resolves back to its source event + item → `FrameProvenance`).

2. **Per-slide origin label** (NEW) — a separate line at the **top** of
   each slide naming where the frame came from: **source event name +
   capture date** (e.g. `Salta, Argentina · 28 Sep 2025`). Gated by its
   own flag ("Source label per slide"), independent of the four content
   flags. This is the "search result" signal — know each frame's origin
   without opening the Cut.

3. **Opener summary** (up to **2** slides, adaptive) — the Cut's
   composition so you know its provenance without opening it:
   * **Slide 1 — "what":** the Cut name + the **sources** it draws from
     (the collections / recipes / events the expression references), by
     **name**.
   * **Slide 2 — "how":** the **filters** applied — style, type, date
     range, event filter — by name, with **expanded values** where the
     value is the point (a date range, a style list).
   Collapses to **1 slide** when there's little to show.

## Styling

Reuse spec/153's `_TEXT_STYLE` roles + add cross-event roles:
`origin_label` (top-centre, small), and the opener reuses
`opener_title` / `opener_sub` (the "what" slide is title + sources; the
"how" slide is a title + the filter read-out). Backgrounds are flat,
text-less, swappable (the spec/153 `render_flat_background`).

## Implementation (phased)

* **Phase 1 — Opener summary.** Wire a flat `opener_writer` into the
  cross-event export; compose the what/how opener slides in the
  cross-event PTE generation from the Cut's `expr_json` + `filters_json`
  (reusing the `_format_dc_expr` / `_format_dc_filters` helpers). No
  per-item provenance needed → lowest risk, most distinctive piece.
* **Phase 2 — Per-slide origin label. DONE (Play side, 2026-06-26).**
  The pure composer `cross_event_cut_play.compose_origin_label(event_name,
  capture_time)` builds the one string (`'Salta, Argentina · 28 Sep 2025'`);
  `cross_event_origin_resolver(lg, cut_id)` wraps it with the
  `list_events_for_scope` name map for Play. `CutPlayerDialog` grew a
  second, TOP-anchored `#CutPlayOrigin` label (mirror of the bottom
  `#CutPlayOverlay`), wired via a new `origin_resolver` param. The
  "Source label per slide" flag lives in the cross-event Cut's
  `extras_json` (sibling to `card_style`), threaded
  dialog → `CrossEventCutDraft.source_label` → session → gateway. The
  control renders only under `INVENTORY_LIBRARY`. **PTE side reuses
  `compose_origin_label` in Phase 1's generator (slice B).**
* **Phase 3 — Photo captions.** A per-source-event provenance index
  (`{relpath → FrameProvenance}`, mirroring `_origin_index_for_source_event`)
  so each member composes its When/Where/Camera/Exposure line.

In-app Play is unchanged throughout.
