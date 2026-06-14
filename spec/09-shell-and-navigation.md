# spec/09 — The application shell (navigation rail first)

**Build-sequence step 7 (charter §4) begins here.** The shell is the persistent chrome
around the page surfaces — the navigation rail, and (landing as the first page surfaces
are reassembled) the page stack + main window. This spec records the shell as-built; it
grows as each piece lands. The first piece is the **navigation rail**.

Lives in the new namespace `mira/ui/` (charter §3): binds **only** to the gateway,
never to `core/`/`data/`, and never imports from the legacy `ui/` package. Reused legacy
widgets are copied in and rewired, not imported across the boundary.

---

## 1. The list-presentation decision (Nelson 2026-05-30)

The app has three deliberately-distinct "list" idioms, and **they stay distinct**:

| Surface | Widget | Idiom |
|---|---|---|
| Events dashboard | `EventCard` in a scroll area | big rich cards (heatmap / recap) |
| Cull pickers / Day / Bucket | `ListButton` | medium 4-line data-cards (badge + tally) |
| Curate collections / preview | native `QListWidget` | plain text rows + counters |

These serve different jobs and are **not** to be converged. The **only** list that read
as *ugly* was the **navigation sidebar** — the one piece of permanent, always-on-screen
chrome, yet rendered as the most primitive surface in the app. Nelson's call: *"the lists
you pick up are very different and will stay like that; it is just the sidebar list that
is ugly and needs to be more alike the other lists."* So the rail rises to the
card-quality of the content lists; the content lists are untouched.

## 2. The navigation rail — `mira/ui/shell/sidebar.py` (BUILT)

### 2.1 What was wrong with the legacy rail (`ui/shell/sidebar.py`)
- A bare native `QListWidget`: flat 36-px text rows with native chrome that clashed with
  the rounded card content area beside it.
- **Section headers faked as `NoItemFlags` "dead" list items**, styled per-item via
  `setForeground`/`setFont` — the legacy code itself documents this as *"a documented
  exception to docs/16 QSS-as-the-single-source."*
- No visual hierarchy or containment; destinations and actions read identically.

### 2.2 The rebuild — same contract, better presentation
**Navigation contract is unchanged** (a true drop-in for the host):
- Same entry **keys** (`ENTRY_DASHBOARD`, `ENTRY_WIZARD`, … — the host's key→page/action
  map is untouched).
- Same `entry_activated(key: str)` signal; same `select(key)` helper.
- Same layout order (Wizard floats above the headers; sections Events / Plan / Cull /
  Process / Curate / Help — the "no orphan TOOLS bin" convention).

**Presentation changed:**
- A composed `QWidget` rail (`#SidebarRail`) inside a frameless `QScrollArea`
  (`#SidebarScroll`) — not a `QListWidget`.
- **Real header widgets** (`_SectionHeaderLabel`, `QLabel#SidebarSectionHeader`) — the
  `NoItemFlags` hack is gone, the docs/16 exception with it. Visual treatment is QSS,
  role via `objectName` (spec/05).
- **Rounded pill entry rows** (`_EntryButton`, a checkable flat `QPushButton#SidebarEntry`):
  40-px min height, generous padding, `border-radius`, and **card-language states** —
  hover fills the rounded rect; the selected row fills `primary_subtle` with Gulf-blue
  text and heavier weight (the "you are here" affordance, formerly the native
  `::item:selected`). Selection is exclusive via a `QButtonGroup` so `:checked` tracks
  the active surface.
- spec/05 admission test met: pointing-hand cursor (clickable-cursor filter + explicit
  per-row), a tooltip hint on every entry, visual states in **both** QSS themes, all
  labels through `tr()`.

The QSS roles (`#SidebarRail`, `#SidebarScroll`, `#SidebarSectionHeader`, `#SidebarEntry`
+ `:hover`/`:pressed`/`:checked`) were added to **both** `assets/themes/light.qss` and
`dark.qss` in the same change (the docs/16 "add a role to both themes together" rule).
They use only existing resolved-palette placeholders, so theming flows from the one
source; the legacy `QListWidget#Sidebar` block stays for the legacy app.

### 2.3 No data tendril here
The rail only emits navigation keys — it has **no** data dependency. So the
"sever the tendrils / bind to the gateway" labor (charter §5.2) is a no-op for this part,
which is why it is the cheapest first reassembly. The host maps a key to a page and feeds
*that page* from the gateway.

### 2.4 Preview + gate
- **Eyeball preview** (the visual quality is Nelson's call): `python -m
  mira.ui.shell.sidebar_demo` (`--dark` for the dark theme) — the rail beside a
  placeholder pane; clicking an entry shows the activated key. A throwaway harness;
  borrows `ui.theme.apply_theme` for the preview only (theme is ported into
  `mira/ui/` during the main-window reassembly).
- **Tests** (`tests/test_sidebar.py`, 7, logic/structure only): the entry-key set matches
  the legacy contract; section headers are real `QLabel#SidebarSectionHeader` widgets
  (not dead rows); every entry is a checkable `#SidebarEntry` pill with a hint;
  click emits the key; default selection is Dashboard and is silent; `select` is
  exclusive and optionally emits; unknown key is a no-op.

## 3. What this drives next
- **Theme** ported into `mira/ui/theme.py` (reads the shared `assets/themes/*.qss`)
  so the shell stops borrowing the legacy module.
- **Page stack + main window** (`mira/ui/shell/`) hosting the rail + a `QStackedWidget`
  of page surfaces, with the key→page map.
- **First content surface: the events list** bound to `Gateway.list_events()` (spec/08
  §3.2), with the first oracle parity test (charter §6). Then ingest / "create event from
  files" (the rebuild-fresh production path).
