# External tools — sourcing map images for Curate slides

Side reference. Mira is strictly offline-first and does NOT
talk to map services or third-party tools (see
`CLAUDE.md` §"Phase 0 partner-tool stance"). This document
describes how to **produce** the map images Mira consumes —
the user runs these tools, picks a region, exports a PNG, then
drops the PNG into Mira via the Curate phase's "Map slides"
workflow (task #134 / #135).

Companion to `docs/20-pte-annotation-workflow.md` (the PTE side of
the slideshow handoff). Same intent: a workflow the user follows
outside Mira, with notes on the friction points and the choices
that matter when feeding the result back in.

## Recommended: Maperitive (offline OSM rendering)

**[Maperitive](http://maperitive.net/)** by Igor Brejc (Czech
developer; Apache 2.0 license). Renders OpenStreetMap data
locally — once map tiles are downloaded, the tool runs fully
offline, which matches Mira's discipline. Strong styling
controls — chunky strokes, big legible labels at the zoom you'll
use for slideshow inset, no online clutter.

### Setup

1. Download from <http://maperitive.net/>. Windows-native, zip
   distribution (no installer required). Extract anywhere; run
   `Maperitive.exe`.
2. First run downloads a tile-set for your region (a few hundred
   MB for a country-sized area). After that, fully offline.
3. Optional: download a custom rules file (`*.mrules`) to control
   line widths, label fonts, colour palette. The bundled
   `Default` rules are fine to start.

### Producing a slideshow-friendly map

1. **Navigate** to the region (mouse drag + scroll wheel; or
   `Map → Geocode` for a place-name jump).
2. **Set the zoom** so the route you want is comfortably visible
   with room around the edges. Slideshow targets are 1920×1080
   or smaller — pick a zoom that's readable at the inset size
   you'll use in PTE.
3. **Style for legibility at small sizes**: pick a thicker road
   style (`Tools → Set Web Map → OpenStreetMap.de` works well as
   a base; or load a custom `.mrules` with stroke widths × 2).
4. **Export PNG**: `File → Export to bitmap…` — pick a size
   matching your target inset at roughly 2× resolution (e.g.
   1600×1200 if your PTE inset will be ~800×600). PNG with no
   compression; Mira accepts whatever Maperitive writes.

### Tips

* **Bigger labels, fewer features**: at slideshow inset sizes
  (~25% of the screen), 8-pt body labels disappear. Crank the
  font size in `.mrules` or pick a stylesheet that does it for
  you.
* **Hide the GPS-trace overlay** if Maperitive has loaded one
  (`Map → Visible map`). Same for grid + scale bars unless they
  add value for the audience.
* **Plan your route in advance**: Maperitive doesn't compute
  routes, but you can draw lines + waymarks with `Map → New
  geometry`. Or skip — Mira's annotation overlay can paint
  arrows / icons over the map at compose time (#135 / B'.2).

## Alternatives

### QGIS

**[QGIS](https://qgis.org/)** — free GIS, GPL. Overkill for a
single-route slide but invaluable if you're building a map with
custom layers (terrain, hill-shading, named-feature overlays,
GPX traces). Export to PNG via `Project → Import/Export → Export
Map to Image`. Steep learning curve; recommended only if you
already know the tool.

### GIMP / Krita + screenshots

Lowest-friction path: open Google Maps / Apple Maps / OsmAnd in a
browser/desktop client → fit the route → screenshot the region →
open in GIMP or Krita → trim / desaturate / annotate. Works in a
pinch for one-off slides. Drawback: most map services' Terms of
Service forbid redistribution of their tiles, so this is fine for
personal slideshows but not for distributed material.

### Tracky / Trackrouter / GPX visualisers

If you carried a GPS tracker on the trip, tools like
**[Strava Routes](https://www.strava.com/routes)** (online),
**[Komoot](https://www.komoot.com/)** (online), or
**[GPSBabel](https://www.gpsbabel.org/)** (offline conversion)
can render your actual GPS trace onto a base map. Same Terms-of-
Service caveat as the previous point for personal use.

## Feeding the PNG back into Mira

1. Save the PNG anywhere on your machine (Downloads, Desktop, a
   dedicated trip folder, your taste).
2. Open the event → Curate phase → **Locations & travels**
   checklist on the left (task #134). Each entry that needs a
   map shows "—" status.
3. Click the entry → **MapSlideDialog** opens with the day +
   suggested caption pre-filled. Click **Browse…** → pick your
   PNG.
4. **Optional but common**: click **✂ Crop source** (task #135 /
   B'.1) and drag a rectangle inside the map. Useful for thin
   vertical strips (north-south travel) where the full Maperitive
   export wastes horizontal space.
5. **Optional**: add annotations (icons / arrows / labels) via
   the table editor (task #135 / B'.2). The bundled icon set
   covers car, walking, small-prop-plane, jet, helicopter, boat.
6. **Save**. The composed PNG lands at
   `<event-root>/04 - Curate Maps/dia{N}_{seq}_{slug}.png` and
   appears in the Curate slideshow at the sequence position you
   picked.

## What aspect / resolution to favour

Mira is permissive — any aspect, any reasonable size works.
But for cleanest output:

* **2× the inset size you'll use in PTE.** A 1600×1200 source
  cropped to 800×600 in PTE looks crisp. Going lower than 1×
  forces upscale + softening; going higher than 4× wastes disk
  with no visible benefit.
* **Free aspect.** Don't pre-crop in Maperitive — let Mira's
  source-crop overlay do it (the user gets to position the rect
  visually with the photo route in mind).
* **No watermark / attribution baked in.** OpenStreetMap's
  attribution requirement applies to the rendered output you
  distribute; if you're shipping the slideshow publicly, add the
  attribution as a separate slide rather than baking it onto
  every map.

## PTE composition recap

Mira's job ends at producing the annotated map PNG. The
PTE side of the workflow:

1. PTE timeline has one slide per separator point in the trip.
2. The slide's **background layer** = a nice photo from the day
   (picked from the event's Long bucket via PTE's file picker —
   the photos live under `04 - Curated/<theme>/Long/`).
3. The slide's **inset layer** = the Mira-composed map PNG
   from `04 - Curate Maps/`. Resize + position freely in PTE.
4. The slide's **title layer** = a text layer in PTE: location
   name or travel leg ("San José → La Fortuna").

Mira never produces the composed separator slide — that's
PTE's job. Splitting the concern keeps Mira offline-only and
PTE-driven for composition (the right tool for the layered
slideshow work).
