# 107 — PTE integration: launch + template-driven `.pte` generator (photos · video · overlays)

**Status: SHIPPED (Nelson 2026-06-23). Both tiers landed.
Tier 1: `mira/shared/pte_launch.py` (Qt-free `reveal_in_explorer` /
`open_in_pte` / `pte_launch_available`); `use_pte` master toggle (OFF by
default) + `pte_path` in `mira/settings/model.py`, both wired into the
Paths tab (`settings_dialog.py`) with hints; the export-complete dialog in
`share_cuts_page.py` + `library_page.py` grows "Open folder" (always) +
"Open in PTE" (toggle on + path valid + project generated).
Tier 2: `assets/pte/skeleton.pte` (hand-authored bundled default, sanitized
from `photo and video example.pte` — no personal paths, the three
prototype GUIDs preserved); `mira/shared/pte_project.py` (Qt-free) with
`parse_skeleton` / `capture_skeleton` / `load_skeleton` (captured > bundled
fallback) and `generate(...)` — clones photo/video/overlay prototypes,
regenerates per-object GUIDs (load-bearing for both photo & video), wires
`[Tracks]` `VideoClip` (shared `ClipGUID`, `MasterID` = Cover-Video GUID,
0-based `StartSlideIdx`), strips dangling clips, fills `[Times]` with
cumulative ms (clip length for videos), overrides `[Main]` AspectRatio /
opt_scr_* / DefDuration / ProjectFilePath / ImagesFolder from
`core.cut_aspect.aspect_spec`, swaps in the N-item music block; overlay
`embedded` populates the nested `:Text`, `burn_in`/`off` strip it;
`slideshow_target` disambiguates `slideshow (2).pte`; `write_pte` → UTF-8
BOM + CRLF, atomic `.tmp`-rename. Generator wired best-effort into both
per-event and cross-event `_on_export_cut` (failure logs, never blocks the
export summary). 41 new tests (`test_pte_project.py` +
`test_pte_skeleton_capture.py`: members, GUIDs, Times, Tracks, overlays,
BOM/CRLF, golden-file determinism, prototypes-kept/paths-stripped,
end-to-end consumability) + 91 passes across settings/export/cut-aspect.
The 9 `test_focus_keeper.py` failures are the same pre-existing headless-Qt
`focusWidget() is None` flake, untouched by this branch. Original proposal
follows.**

**Status: PROPOSED — finalized 2026-06-22 after design + end-to-end
empirical validation against real exports + PTE AV Studio 11.021.** After
exporting a Cut, the most common next step is to open it in PTE and build
the slideshow. This spec adds (Tier 1) opt-in launch actions, and (Tier 2)
a generator that writes a ready, openable `.pte` into the export folder
from a **content-void skeleton template** — handling **photos, video
clips, and overlays**. PTE is the **single sanctioned third-party output
integration** (the documented exception to spec/108). Generator is
**cut-type-agnostic** (works on the export *folder*, so per-event and
cross-event Cuts are identical to it). New: `mira/shared/pte_project.py`
(Qt-free), `pte_path` + the stored skeleton + an "I use PTE" toggle, the
export-complete actions. **Dependencies:** spec/106 (music picker — cuts
must carry audio), spec/111 (Cut aspect ratio + aspect-matched cards),
spec/112 (cross-event audio parity). Charter inv. #2: no path stored on
the Cut.

---

## 0. Validated PTE format facts (the cheat sheet — do not relitigate)

`.pte` is text, UTF-8 **with BOM**, **CRLF**. Sections: `[Main]` →
`object Music:Music` → `[Tracks]` (`VideoClip` objects) → `[Effects]` →
`[Slide N]`… → `[Times]` (one `opt_synchpos` per slide + `opt_slidescount`).

- **Photo image binds by GUID, not path.** Cloning a slide with the GUID
  left duplicated shows the *original* image; **regenerating the per-object
  `GUID={…}` makes the new path take effect.** (Leave the bracketed
  `StyleOptions=[{…}]` style GUID alone.)
- **Audio binds by path.** Repathing the music items (fresh GUIDs) plays
  the new tracks.
- **Video member = three coordinated pieces** (validated):
  - a `[Slide N]` block whose objects are `:Video` (not `:Image`),
    carrying `FileName=<mp4>`, `Duration=<ms>`, `AutoRotate=1`, and a
    shared `ClipGUID={…}` on both the Cover and PlaceInto objects;
  - a `VideoClip` object in `[Tracks]` with `FileName`, `Duration`,
    `ClipGUID` (= the slide's), `MasterID` (= the **Cover** Video object's
    `GUID`), and `StartSlideIdx` (**0-based** slide index);
  - the slide's `Picture=` line points at the **.mp4**.
- **`[Times]` is per-slide and authoritative.** Photo slide = the Cut's
  per-slide seconds; **video slide = the clip's own length** (+ transition);
  separator card = its card duration. `opt_slidescount` must match.
- **`FitMode=Cover`/`PlaceInto` reflow to the canvas** — changing the
  `[Main]` aspect re-fits every slide; no per-slide aspect edits.
- Structural edits (added slides, extended `[Times]`, `opt_slidescount`,
  added `VideoClip`s) open clean in 11.021.

## 1. Tier 1 — launch (opt-in, low risk)

- Setting `pte_path` (path to the PTE executable), mirroring the existing
  bundled-exe path pattern. A master **"I use PTE" toggle** gates all PTE
  UI (off by default; non-PTE users never see it).
- On the export-complete summary (+ the Cut detail row): **"Open folder"**
  (always — reveals the export dir) and **"Open in PTE"** (enabled when
  `pte_path` is set + exists). A small Qt-free launch/reveal helper.
- Even alone this helps: `001_…`-prefixed files import in order via PTE's
  *Add Files and Folders*, with `audio/` right there.

## 2. The skeleton template (content-void) + the generator

The example `.pte` resolves into **two artifacts**: (a) a **content-void
skeleton** carrying only **placeholder references**, and (b) the
**generator code** that turns the skeleton into a complete playable `.pte`
at runtime.

- **Skeleton contents:** `[Main]` style options + a **photo-slide
  prototype** (`:Image` objects), a **video-slide prototype** (`:Video`
  objects + a `VideoClip` stub), a **`Text`-overlay prototype** (placeholder
  text), and an **audio-item stub** — all paths are placeholders
  (`{photo}` / `{video}` / `{audio}` / `{overlay_text}`). The block
  *structure* (objects, animation, GUIDs) is real; only the references are
  void. **The skeleton is NOT a valid openable `.pte`, and need not be.**
- **Capture (user customizing their style):** Mira reads the user's saved
  1-photo-1-video project, keeps the style (`[Main]`, transition, the two
  slide-block structures + a Text prototype if present), **strips all real
  media to placeholders, drops baked `Text` content and dangling
  `VideoClip`s**, and stores the skeleton at `<library_root>/.mira/
  pte_skeleton.pte`.
- **Shipped default:** one hand-authored skeleton (built from the example,
  stripped) — **zero private content**, a Nuitka data asset, used when the
  user hasn't captured their own. (Version-baseline caveat: a captured
  skeleton matches the user's PTE exactly; the bundled one targets a
  conservative baseline — "if it won't open, capture your own.")

## 3. The generator (`mira/shared/pte_project.py`, Qt-free)

Read the skeleton (preserve BOM + CRLF). Then, per cut member (in
`001…NNN` order):

1. **`[Main]` overrides (Mira-owned):** `DefDuration` ← the Cut's per-slide
   seconds; `AspectRatio` + `opt_scr_width`/`opt_scr_height` ← the Cut's
   aspect (spec/111). Everything else in `[Main]` is the skeleton's, verbatim.
2. **Photo member:** clone the photo prototype → `[Slide i]`, regenerate
   the per-object `GUID`s, rewrite `ImageName=`/`Picture=` to the frame's
   **Windows absolute path** in the export folder.
3. **Video member:** clone the video prototype → `[Slide i]`; rewrite both
   `FileName=` + `Picture=` to the mp4; set `Duration`; mint a fresh shared
   `ClipGUID` + fresh object `GUID`s (record the Cover's); emit a matching
   `VideoClip` in `[Tracks]` with `ClipGUID` (= shared), `MasterID`
   (= Cover GUID), `StartSlideIdx = i-1` (0-based), `FileName`, `Duration`.
4. **Overlays (per the Cut's `overlay_mode`, spec/81):**
   - `burn_in` → nothing to do; the fields are already in the JPEG pixels.
   - `embedded` → clone the **`Text`-overlay prototype** onto the slide and
     populate it from the item's `overlay_fields` (camera, exposure,
     where…). Fresh GUID. (Especially valuable for portfolio Cuts where the
     hardware settings carry real value.)
     - **The overlay's look is authored, not hardcoded.** The prototype is a
       dedicated `Text` object in the **skeleton**, styled by the user
       (small, bottom-anchored, legible — size = the `KeyPoint`
       `ScaleX`/`ScaleY`, position = `Position=x,y` in %, plus
       font/shadow). Its `Text=` holds a placeholder marker (`{overlay}`)
       the generator recognises. The generator **clones the prototype
       verbatim and swaps only the `Text=` content** — so the user's
       authored size/position/style lands on every overlaid slide, and they
       only adjust exceptions in PTE. Mira never invents the text style.
     - Capture/authoring: in PTE, add a small bottom caption styled the way
       overlays should look (any placeholder text); the capture flow keeps
       it as the overlay prototype (so it's tuned visually, not by
       hand-editing `Scale` numbers).
     - **Recognition (structural, not string-matched):** the overlay
       prototype is the **single nested `Text` object inside the photo
       prototype's image object** (PTE nests it as a child after the image's
       `KeyPoint`). The generator does NOT match the placeholder content —
       any text is fine. Validated reference values from the authored
       example: `FontName=Arial Narrow`, `TextColor=#FFFFFF` (hex is
       accepted), `TextAlign=Center`, `ShadowEnable=1`, `KeyPoint`
       `ScaleX/ScaleY ≈ 3.9` (small), `Position` y ≈ 91.6 % (bottom).
       Overlay ON → clone + swap `Text=` + fresh GUID, style inherited
       verbatim; overlay OFF → strip the nested `Text` so the slide is
       clean. (Video prototype: same rule if a nested `Text` is present.)
5. **`[Times]`:** cumulative `opt_synchpos` — photo/separator slides use the
   Cut's per-slide seconds; **video slides use the clip `Duration`** (+ the
   transition time). Then `opt_slidescount = N`.
6. **Strip** any dangling `VideoClip` (`StartSlideIdx ≥ N`) and any baked
   `Text` content from prototypes before cloning.
7. **Audio:** replace the music stub with one item per exported track in
   `audio/` (fresh GUID, Windows path, real `Duration`, fades from the
   skeleton).
8. Write the finished `.pte` into the export folder, **absolute Windows
   paths** throughout, BOM + CRLF. Refresh `ProjectFilePath`/`ImagesFolder`.

## 4. Output naming + overwrite

Write `slideshow.pte` into the cut-export folder. If one exists,
**disambiguate** (`slideshow (2).pte`) so a re-export **never accidentally
clobbers** a project the user has since edited in PTE — **unless the user
explicitly confirms** "replace the existing slideshow," in which case
overwrite. Open it from that folder (absolute paths point there).

## 5. Cut-type-agnostic (event + cross-event)

The generator operates on the export *folder*, so it is identical for
per-event and cross-event Cuts. The only differences live **upstream** and
are handled by the dependencies: cross-event export must build `audio/`
(spec/112) and both exporters must honour the Cut aspect + render
aspect-matched separator/opener cards (spec/111). With those, "Open in PTE"
+ generation work the same for both.

## 6. Settings / UI

- `pte_path` (executable), `pte_skeleton.pte` (stored skeleton path, in
  `.mira/`), the "I use PTE" master toggle (gates all PTE UI).
- A **"Customize my PTE template"** flow (capture, §2) offered under the
  toggle; otherwise the bundled default is used.
- Export-complete summary: "Open folder" + "Open in PTE"; generation runs
  on export when the toggle is on and a skeleton resolves (default or
  captured).

## 7. Acceptance

- With "I use PTE" on and a skeleton present, exporting a Cut (photos +
  video + audio) writes `slideshow.pte` into the export folder; opening it
  in PTE shows all frames AND video clips in order, at the Cut's per-slide
  duration / clip lengths, at the Cut's aspect, with the exported audio —
  matching the skeleton's style.
- Video clips play (correct `VideoClip` + `StartSlideIdx` + GUID linkage);
  photos show the right images (GUIDs regenerated).
- `embedded`-overlay Cuts render per-slide camera/exposure/where text;
  `burn_in` Cuts show the baked text with no extra work.
- Cross-event Cuts behave identically (given spec/112).
- The skeleton is never required to open as a standalone `.pte`; no private
  content ships; re-export never silently clobbers an edited project.
- "I use PTE" off → no PTE UI anywhere; "Open folder" still available.

## 8. Tests

- `tests/test_pte_project.py` — given a skeleton + a member list
  (photos + a video + overlays) + durations + aspect + audio tracks, the
  generator emits: N `[Slide]` blocks with **unique** object GUIDs; photo
  slides repathed; **video slides as `:Video` with a matching `[Tracks]`
  `VideoClip`** (shared `ClipGUID`, `MasterID` = Cover GUID, `StartSlideIdx`
  = 0-based); `[Times]` cumulative with **clip-length** video slides;
  `opt_slidescount = N`; embedded overlays → populated `Text` objects;
  dangling `VideoClip`s + baked `Text` stripped; BOM+CRLF; `[Main]`
  aspect/duration overridden.
- `tests/test_pte_skeleton_capture.py` — capture strips all real media to
  placeholders, drops Text + dangling clips, keeps style/prototypes.
- Golden-file check against a regenerated reference.

## 9. Dependencies / order

1. **spec/106** (music picker) — cuts must carry audio.
2. **spec/111** (Cut aspect + aspect-matched cards — both exporters).
3. **spec/112** (cross-event audio parity).
4. **Tier 1** (launch) — independent, ship anytime.
5. **Tier 2** (generator) — after 106/111/112.

## 10. Resume pointer (validation artifacts, 2026-06-22)

`D:\Projetos_Nelson\Mira\PTE example\`: the stripped templates
(`pte_template.pte`, `mira_base_template.pte`), `photo and video
example.pte` (the source for the bundled skeleton — shows the video-slide +
`VideoClip` encoding), a real export (`001…069` frames + `audio/`), and the
validated photos-only output `slideshow.pte`. Port the proven generator
script (which produced `slideshow.pte`) into `pte_project.py`, extending it
with the video + overlay + skeleton handling above.
