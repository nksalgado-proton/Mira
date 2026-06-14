# Composing separator slides in PTE AV Studio

How to assemble a Curate separator slide — background photo +
map inset + title text + annotations (arrows, labels, icons,
paint-brush highlights, route lines) — using PicturesToExe AV
Studio. Companion to `docs/19-external-tools-map-images.md`
(which covers sourcing the base map image).

Side reference. Mira is offline-first and does not talk to
PTE; this guide documents the *external* workflow that converts
Mira's outputs into a finished slideshow.

## What Mira delivers + what PTE composes

| Element | Source |
|---|---|
| Background photo | Long bucket → `04 - Curated/<theme>/Long/` (Mira's Curate Export). |
| Map image (cropped + optionally captioned) | `04 - Curate Maps/dia{N}_{seq}_{slug}.png` (Mira's map-slide composer). |
| Title text | Composed in PTE as a text layer. |
| Arrows / labels / icons / paint-brush | Composed in PTE as object layers on top of the map. |
| Animation (arrow draws itself; car moves along the route) | PTE keyframes. |

PTE owns composition + animation; Mira owns the photo
processing + map source preparation. The split keeps each tool
doing what it does best.

## Per-separator-slide recipe

1. **New slide.**
2. **Background = the photo.** Drag the photo from Long into the
   slide. Set it as the bottom-most layer; resize to fill the
   slide canvas (PTE: right-click → Object properties → Position
   tab → "Fit to screen" or similar). Optional: a slight dark
   gradient overlay on top of the photo so the map + title read
   cleanly against busy photos.
3. **Map inset = the Mira PNG.** Drag the
   `dia{N}_{seq}_{slug}.png` from `04 - Curate Maps/` into the
   slide. Position + resize freely — typical inset is 25-40% of
   the slide width, anchored to a corner that doesn't compete
   with the photo's subject. PTE's snap-to-grid + alignment
   guides help here.
4. **Title = a text layer.** Insert text object: "San José" or
   "San José → Monteverde". Place at top centre or above the map
   inset. Use a clean sans-serif at 60-80pt; outline + drop-
   shadow so it reads against any photo.
5. **Annotations on the map** (the part that used to live inside
   Mira):
   - **Arrows along a route**: Insert → Shape → Arrow. Set
     thick stroke (4-6 px), high-contrast colour (white with
     black halo, or solid yellow). Position the head + tail on
     the map inset.
   - **Location labels**: Insert text object on top of the map
     inset, anchored at the city/landmark. Smaller font than
     the title (24-32pt), white text on a translucent black
     plate for legibility on busy maps.
   - **Icons** (car, walking, plane, etc.): Insert → Image.
     Drop in any PNG/SVG you have. Recommended sources:
     [Material Symbols](https://fonts.google.com/icons),
     [OpenMoji](https://openmoji.org/), or
     [Font Awesome Free](https://fontawesome.com/icons). Save
     a small reusable library of trip-aesthetic icons in a
     folder you can drag from for each slide.
   - **Paint-brush highlight** (a route hand-drawn over the
     map): Insert → Shape → Pencil / Polyline. Draw the path;
     set high-contrast stroke. PTE doesn't have a true
     freehand pen, but a polyline with enough vertices reads
     as a brushstroke.
6. **(Optional) Animate.** PTE's Animation timeline supports
   per-object keyframes:
   - Route arrow that "draws itself" as the slide enters:
     animate the arrow's `Width` or `Length` from 0% to 100%
     over 1-2 s.
   - Car/plane icon moving along the route: keyframe the icon's
     X/Y position over the slide duration. Use the same
     bezier/path the arrow follows.
   - Title fade-in: keyframe `Opacity` from 0 to 100 over 0.5 s.

## Tips

* **Build a per-trip annotation library** in PTE. Once you've
  styled one separator slide (matching title font, arrow stroke,
  icon set), copy the slide and swap only the photo + map +
  title text for each subsequent location/leg. Saves enormous
  time on multi-stop trips.
* **Use Mira's caption sparingly.** The Mira composer
  bakes a translucent banner at the bottom of the map image if
  you fill the caption field. That's fine when the map is the
  *whole* slide, but for separator slides where the map is an
  inset, the baked caption competes with the slide title.
  Leave the Mira caption blank for separator-slide maps;
  put the title in a PTE text layer instead.
* **The Locations & travels checklist** in Mira's Curate
  navigator tracks which separators you've built maps for, NOT
  which PTE slides you've assembled. You'll typically work
  through the checklist top-to-bottom: produce the map in
  Mira → switch to PTE → assemble the slide → check it off
  mentally. The checklist's status (`1 map(s) saved` vs `—`)
  reflects Mira state only.
* **Keep the map asset library tidy.** All composed maps land
  under `<event-root>/04 - Curate Maps/` with filenames like
  `dia02_005_la-fortuna-to-monteverde.png`. PTE's project file
  references them by path, so don't rename them after dropping
  into a project. If you re-compose a map in Mira (same
  day_number + sequence), the file gets overwritten with the
  new bytes — PTE picks up the change on next reload.

## Why the split exists

A single-tool approach (Mira composes the full separator
slide, PTE just plays the result) was considered + rejected
2026-05-24. Reasons:

1. **PTE already does this well.** Arrows, text, icon overlays,
   animation are core PTE features. An in-app annotation editor
   would have been a less-capable reimplementation.
2. **WYSIWYG matters for slide composition.** In PTE, you see
   the photo + map + arrows together as you build them. In a
   Mira annotation editor, you'd see the map + arrows;
   then switch to PTE and discover the arrows clash with the
   photo background.
3. **Animation is impossible in a static PNG.** A car icon that
   moves along the route, an arrow that draws itself — these
   require keyframes, which only PTE provides. Baking them into
   a single PNG defeats the purpose.
4. **Mira stays offline + focused.** No third-party
   integration (no SVG icon library to redistribute, no font
   licences to track, no animation engine to maintain).

Mira's value-add for the map workflow is what only Mira
can do: plan-derived workflow tracking (#134), source-image
free-form cropping (#135 / B'.1), and consistent file output to
`04 - Curate Maps/` for PTE to consume.
