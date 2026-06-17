# spec/83 — Collection filters: opt-in two-tier model + high-cardinality picker

**Status:** design agreed with Nelson 2026-06-17 (UI design session). The
cross-event "New collection" dialog
(`mira/ui/pages/new_cross_event_dc_dialog.py`) currently shows **all ~15 facets
at once**, each rendered as a full widget, and high-cardinality facets (camera,
lens, city, country) draw a single endless row of checkboxes — an unusable, very
wide dialog. This spec fixes both: an **opt-in two-tier filter model** (you start
with no filters and add only the ones you want), with a **high-cardinality
picker** as the second tier.

Read with: `spec/81 §2.1` (cross-event surface; event-scope stays deliberately
thin), `spec/32 §2` (the facet catalogue + its groupings), `spec/05` (UI grammar).

> **Built together with [spec/85](85-gear-profile-wizard.md)** (the gear-profile
> wizard) as **one** agent task (Nelson 2026-06-17). They share the camera/lens
> inventory + counts layer (§5 here), the wizard's "I use this" flag makes the
> §4 picker's main-vs-occasional split correct (gear flag beats the count
> heuristic), and the wizard launches from this dialog. One combined brief; the
> shared data layer is built once.

---

## 1. Audit — every facet, classified

Three kinds of facet. Only **multi-select** facets break; single-select and
numeric facets are bounded by construction.

| Facet | Widget | Inventory source | Cardinality | Verdict |
|---|---|---|---|---|
| **City** | multi-checkbox | `available_cities` | hundreds | **Broken** |
| **Camera** | multi-checkbox | `available_cameras` | dozens | **Broken** |
| **Lens** | multi-checkbox | `available_lenses` | dozens | **Broken** |
| **Country** | multi-checkbox | `available_country_codes` | tens | **Broken** |
| **Style** | multi-checkbox | `available_classifications` | ~10–20 | **Borderline** |
| Colour label | multi-checkbox | `available_color_labels` | 5 fixed | OK inline |
| Media / Flag / Flash / Rating / Origin | radio | static | 3–6 | OK |
| ISO / Aperture / Shutter / Focal | min-max spinbox | n/a | n/a | OK |
| Capture date | from/to | n/a | n/a | OK |

But the deeper problem the audit surfaced is not just the wide row — it is that
**showing every facet at once is itself the complexity.** Most collections use
two or three constraints; the other dozen are noise. The fix below addresses both
the wide row *and* the always-on clutter.

### 1.1 Future facets that will hit the same wall
spec/32 has **tags/keywords** (§2h) and **people/faces** (§2g) on the roadmap —
both high-cardinality. The model below is written so they slot in for free.

---

## 2. The governing model — opt-in filters, one growing dialog

**Default is no filter.** The dialog opens nearly bare: name, origin (which
ladder rung), a live count, and a **+ Add filter** button. If the user does not
care about city or camera, they do nothing — those facets are not on screen at
all. Complexity is opt-in.

**Tier 1 — choose the dimension.** **+ Add filter** opens a short menu of the
available dimensions, **grouped** the way spec/32 §2 groups them:
*Curatorial* (style · rating · colour · flag) · *Camera & lens* · *Settings*
(ISO · aperture · shutter · focal) · *When & where* (date · country · city).
Pick one and it becomes an active filter row.

**Tier 2 — configure it.** The active filter shows the right editor for its type
(§3): small facets inline in the row; high-cardinality facets a summary + a
**Choose…** button to the picker (§4). Each active filter has an ✕ to remove it.

**Active filters read like a sentence**, each collapsed to one line:
`Camera: GH6, A7 IV  ✕` · `ISO: ≥ 1600  ✕` · `Country: Nepal  ✕`. Clicking a row
reopens its editor. The live count updates as filters are added/removed.

**Empty state names the default:** *"No filters — matches everything in
#exported (12,480 items)."* The do-nothing path is never ambiguous.

**Decisions (Nelson 2026-06-17):**
- **One dialog that grows**, not a separate chooser window — same "default is
  nothing" goal, no window-to-window flow.
- **Apply the model across the dialog family** for consistency. Cross-event gets
  the full catalogue; the **event-scope dialog keeps its deliberately thin
  surface** (spec/81 §2.1: `#exported` + Style + media type only) but reuses the
  same Add-filter components so the two feel identical.

---

## 3. Tier-2 editors — adaptive multi-select
When a multi-select facet is opened, it picks its own editor by option count
(threshold suggested **12**, a module constant):

- **≤ threshold → inline, wrapping.** Checkboxes in a **wrapping `FlowLayout`**
  (the existing one), never a single overflowing row. Style, colour render here.
- **> threshold → summary + Choose… picker** (§4). Camera, lens, city, country.

Driven by count, so no facet is special-cased and future facets (tags, people)
inherit the behaviour automatically.

---

## 4. The high-cardinality picker (`FacetPickerDialog`)
A small dedicated dialog for one facet, opened by Choose…:
- **Search box** to filter the list.
- **Each row = value + photo count** (`Lumix GH6 — 4,210`), **sorted
  most-used-first**.
- **Main vs occasional split:** values below a small count (suggested **< 10
  photos**) drop into a **collapsed** "Occasional (N)" section — the
  borrowed/guest-photographer one-offs stay out of the way, one click away.
- **Select all / Clear.** Returns the set to the facet row; live count refreshes.
`tr()` all strings; clickable affordances per spec/05.

---

## 5. Data layer — counts, loaded lazily
- Convert the six `available_*` methods in `mira/gateway/library_gateway.py`
  from `DISTINCT … ORDER BY name` to **`<col>, COUNT(*) … GROUP BY <col> ORDER
  BY COUNT(*) DESC`**, returning `(value, count)` pairs.
- **Lazy load.** With opt-in filters, an inventory query runs **only when its
  filter is added**, not on dialog open — a real win over a 30-year archive
  (today the dialog loads every camera/lens/city/country up front).
- Update `CrossEventInventories` + the host wiring
  (`cross_event_dcs_dialog.py::_build_inventories`) to fetch on demand and carry
  counts.
- **Verify:** `available_cameras` returns `camera_id`. If that is an opaque key,
  the picker must show a display label (the camera model), not the raw id.

---

## 6. Slices
1. **Two-tier shell** — rebuild the dialog around name + origin + live count +
   **+ Add filter**; grouped dimension menu; active-filter rows with ✕; empty
   state. No facet shown until added. (Uses the existing facet widgets as the
   tier-2 editors for now.)
2. **Counts + lazy inventories** — the six queries → `(value, count)`, fetched
   on filter-add; `CrossEventInventories` + host wiring; tests. Resolve the
   camera display-label question (§5).
3. **Adaptive inline editor** — multi-select uses `FlowLayout` + the count
   threshold; small facets wrap instead of overflowing.
4. **`FacetPickerDialog`** — search + counts + most-used-first + collapsed
   occasional section + select-all/clear; wire >-threshold facets to it; tests.
5. **Family consistency** — apply the Add-filter shell to the event-scope and
   cross-event Cut dialogs (event-scope keeps its thin facet set per spec/81
   §2.1); shared components.

Slice 1 alone removes the clutter; slices 2–4 fix the wide-row problem; slice 5
unifies the family.

---

## 7. Open questions
- **Inline-vs-picker threshold** — 12 a good default? (Module constant either
  way.)
- **Occasional-items cutoff** — fixed < 10 photos for v1 (Nelson: build-time
  constant is fine), revisit if needed.
- **Country display** — full names vs ISO codes in the picker (store the code,
  show the name — leaning names).
