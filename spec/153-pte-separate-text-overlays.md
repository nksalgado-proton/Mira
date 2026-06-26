# 153 — PTE separate-text overlays (photo info + separators)

**Status: IN PROGRESS (Nelson 2026-06-26). The generated `.pte` carries
the photo-info and day-separator text as *separate PTE `:Text` objects*
layered over the slide image — not baked into pixels — so the user can
swap the image underneath in PTE AV Studio (e.g. replace a flat separator
background with a map or a photo) and keep the text. Mira owns a single
consistent default style; the user hand-tweaks in PTE afterward. Touches
`mira/shared/pte_project.py` (the generator), `mira/ui/pages/share_cuts_page.py`
+ `mira/ui/pages/library_page.py` (compose the text per member),
`mira/ui/shared/separator_card.py` (a text-less background render at
export), `assets/pte/skeleton.pte` (drop the single nested-Text proto),
and removes the `burn_in` overlay mode. Supersedes the single-combined-Text
approach of spec/107 §3.4 + spec/120.**

## Why

The old generator put **one** nested `:Text` per slide and dumped all the
selected overlay fields into it as a multi-line string (spec/107 §3.4),
*inheriting* its style from the skeleton's single Text prototype. Day
separators were a different beast entirely: a flat-colour JPEG with the
**Day N / date · location / description baked into the pixels**
(`render_separator_image`). Neither was editable downstream — you couldn't
restyle a field, and you certainly couldn't swap a separator's flat
background for a map without losing the words.

PTE's own idiom (the example Nelson hand-authored) is **separate text
objects** sitting over the image: each is its own `object TextN:Text` with
its **size driven by the KeyPoint `ScaleX/ScaleY` (the box), not a font
size**, its own font, position and alignment. Swapping the image beneath
leaves the text in place. This spec adopts that idiom.

## The model

| Slide kind | Background | Text objects (separate `:Text`) |
|---|---|---|
| **Photo** | the photo (Cover blur + PlaceInto sharp, as today) | **one** caption: the selected fields on a single centred line at the bottom — `•` between groups, `·` within — exactly the in-app cut-play pill |
| **Separator** | **flat-colour image, NO baked text** (swappable in PTE) | **two**: `Day N` (large, upper-centre) and `date · location · description` (smaller, below) — preserving the current card's hierarchy |
| **Video / opener** | as today | none |

The per-Cut control is **just the four content flags** — When / Where /
Camera / Exposure (the existing `cut_overlay_fields`). Overlay is on when
≥1 flag is set, off when none. There is **no overlay-mode picker** any
more.

## Mira owns the style

Mira generates each `:Text` object programmatically with a defined,
consistent default (position, box-scale, font, colour, shadow, align) —
the skeleton no longer supplies a Text prototype. The defaults are tuned
to look good out of the box; the user restyles in PTE if they wish (no
on-the-fly control in Mira). Default style roles:

* **`photo_caption`** — white, centred, shadow on, bottom; modest box scale.
* **`sep_title`** — white, centred, shadow on, upper-centre; large box scale.
* **`sep_sub`** — white, centred, shadow on, below centre; medium box scale.

(Concrete numbers live in `pte_project.py`'s style table, derived from the
hand-authored example; they are the one place to tune the look.)

## burn_in is removed

`overlay_mode` (`embedded` / `burn_in` / `off`) and the burn-in pixel
renderer (`cut_export.OverlayRenderer`) are deleted. Separate-text is the
only overlay behaviour; "off" is simply "no fields selected". Existing
Cuts persisted as `burn_in` are treated as normal (separate-text). The
`cut.overlay_mode` column is retired (ignored on read; new writes stop
setting it). In-app Play is untouched — it draws overlays live and renders
separator cards live (`render_separator_image`), so it never reads the
flat export image.

## Generation contract (pure logic, `pte_project.py`)

* `PteText(text, role)` — one styled text object request.
* `PteMember(..., texts: Sequence[PteText] = ())` — replaces the old
  `overlay_text: Optional[str]`. The generator emits one `:Text` per entry,
  styled by `role`, nested in the slide's foreground image, each with a
  fresh GUID. Empty `texts` → a clean slide.
* `generate(...)` drops the `overlay_mode` parameter.
