# Agent tasks — DC / Cut implementation (spec/81)

Implementation handoff for the **Dynamic Collection / Cut** model
([`spec/81`](../spec/81-dynamic-collection-and-cut.md)). Two nouns
(Dynamic Collection, Cut), two verbs (pin: DC → Cut; export: Cut → directory).

## Phase split (Nelson 2026-06-16)

**Phase 1 — event level (build now).** DC over the `#exported` base universe,
pinned into a Cut, played/exported for a single event. Tasks **A → B → C**.

**Phase 2 — cross-event (after Phase 1 lands).** The same engine, library-wide:
the full ladder (`#collected / #picked / #edited / #exported`), the full spec/32
filter catalogue, `app.db` `global_items`. Task **D** (queued — do not start
until A–C are green and shape-checked with Nelson).

## Build order & dependencies

```
A  data layer (event.db schema + store + models)
└─> B  resolution engine + pin + export  (needs A's tables)
    └─> C  UI surfaces (New Cut dialog, DC list, Cuts list, flat grid, play)
D  cross-event (Phase 2) — sits on A+B+C, separate session
```

A and B can overlap once A's DDL shape is agreed; C needs B's gateway seam.

## Read before coding (every task)

1. [`spec/81`](../spec/81-dynamic-collection-and-cut.md) — the governing model.
2. [`spec/61`](../spec/61-share-event-cuts.md) — Share surfaces (Cuts list,
   Picker session = pin, flat grid, separators, audio, export).
3. [`spec/80`](../spec/80-cut-construction-model.md) — construction session +
   New Cut dialog notes (reconciled to spec/81).
4. [`spec/32`](../spec/32-dynamic-collections.md) — DC filter dimensions + the
   cross-event index (Phase 2).
5. [`spec/00-charter.md`](../spec/00-charter.md) + this repo's `CLAUDE.md`.

## Invariants that bind ALL tasks (charter / CLAUDE.md)

- **One-way deps:** `mira/ui/` → `mira/gateway` + `core/`. `core/` imports
  neither Qt nor `mira/ui/`. Pure DC-resolution logic lives in `core/`.
- **No hardcoded user paths** (`core/settings.py` + `mira/paths.py` only). The
  Cut never stores an absolute export target (spec/81 §5).
- **No network, no telemetry.** Offline-first.
- **Every user-visible string through `tr()`.** EN is not firm.
- **Atomic write-then-rename** for persisted state; **WAL** + FK on (existing
  `mira/store/schema.py` pattern).
- **QSS only** for styling; roles via `setObjectName`, present in both themes;
  no inline `setStyleSheet`. Every clickable gets border/hover/pressed/disabled
  + pointing-hand cursor.
- **Spec lands with code.** If behaviour drifts from spec/81, fix the spec first.

## Vocabulary (locked)

DC and Cut are the only two nouns. **pin** and **export** the only two verbs.
A Cut's optional **attachments** are separators, audio, and **overlays**
(provenance text — none is a verb; spec/81 §3.1). Decision verbs **Pick /
Skip**, state `'picked'`. No `cull/curate/keep/discard/select/pool/Dynamic Cut/
Show profile` anywhere in new code, UI, schema, or strings. The legacy
`core/cull_*` modules are out of scope here but must not be imported by new
DC/Cut code.

**Overlays deliver two ways (spec/81 §3.1):** default **embedded EXIF/IPTC** (PTE
renders natively, export stays link-pure — Mira just writes the *where* IPTC),
opt-in **Mira-native burn-in** (rendered copies for non-PTE use). In-app Play
always draws them live.

## Shape checkpoints (spec/61 §10.6)

The DC list, the New Cut dialog, the Picker-session chrome, the Cuts list, and
the flat grid each get a "shape matches spec/81?" confirmation with Nelson as
they land. Don't batch them to the end.
