# Research note — Faces / Maps / Collages (B4 · B5 · B6)

**Compiled 2026-06-15.** Offline-feasibility + library/licensing research for the
three research-first items in `DEVELOPMENT-BACKLOG.md`. The hard constraint
throughout: **charter invariant #3 — no network calls**, and Mira ships as
**closed-source freeware**, so *any* dependency or model with a non-commercial /
copyleft license is a liability even though the app is free. Everything below is
chosen to be both **fully offline** and **permissively licensed (bundle-safe)**.

The headline finding for all three: each is buildable offline with permissive
components, **but** the two "easy" libraries you'd reach for first
(InsightFace's models, PhotoCollage) carry licenses that disqualify them — the
recommended path swaps each for a permissive equivalent.

---

## B4 — Face recognition as a filter

### Verdict
Feasible and a good fit. Recognition runs fully offline (it's just matrix math
on a bundled model). The pipeline is: **detect** faces → **align** → **embed**
to a vector → **cluster/match** vectors. A face becomes a filterable field that
feeds the B2 library search.

### The licensing trap
- **InsightFace** is the obvious pick and the most accurate (ArcFace, ~99.4% on
  LFW), but its *code* is MIT while its *pretrained models* (`buffalo_l`,
  `antelopev2`, etc.) are **non-commercial / research-only** — commercial use
  needs a paid license. Disqualified for freeware shipping.
- **dlib / `face_recognition`** (the "simplest API", ~99.38% LFW) has two
  problems: (1) the recognition model's training data carries a no-commercial-use
  note, so dlib's own author says talk to a lawyer before shipping it in a
  product; (2) it's a **C++/CMake build** that is notoriously painful to install
  and bundle on Windows (needs Visual Studio C++ tools). Disqualified on both
  counts.

### Recommended stack (permissive + offline)
- **Runtime:** `onnxruntime` (MIT) — pure pip wheel, no compiler, trivial to
  bundle into the Nuitka build. CPU is fine; GPU optional.
- **Embedding model:** **ArcFace ResNet100 ONNX** from OpenVINO's Open Model Zoo
  — **Apache-2.0**, 512-d embeddings, same architecture class as InsightFace's
  accuracy leader, but redistributable. (Alternatives also permissive:
  **FaceONNX**, MIT; the "VirtuoTuring" 512-d embedder, MIT.)
- **Detector/aligner:** a permissive RetinaFace/SCRFD ONNX export, or FaceONNX's
  bundled detector (MIT).
- **Clustering:** scikit-learn (BSD) DBSCAN/agglomerative over the embeddings to
  group "same person," then the user names a cluster once.

### Sizing & integration notes
- Model footprint ~100–250 MB for ResNet100; a lighter MobileFace-class model
  cuts that if bundle size matters.
- Run it as a **background classification pass** (mirrors the existing
  classification model in `spec/58`), writing a per-face embedding + cluster id
  into the store; the filter then queries cluster ids.
- This is a **person filter feeding B2**, not a standalone surface — design the
  field so B2's media filter can say "photos containing person X."

### Open questions
- Bundle size budget for the model(s)?
- Privacy framing: clusters are local-only (fits offline/no-telemetry), but
  faces are sensitive — confirm UX for naming/forgetting a person.

---

## B5 — Maps for slideshows

### Verdict
Feasible offline, but **scale dictates the approach**, and the obvious tile
route has a license catch.

### The two scales
1. **Region/country-scale "where these photos were taken" map** (most slideshow
   maps): **fully clean and recommended.** Render vector basemaps from **Natural
   Earth** data (explicitly **public domain**, commercial use fine, no
   attribution required) with **Cartopy** (BSD-3) or **GeoPandas** (BSD-3) +
   Matplotlib, plotting photo GPS points/tracks. No network, no license strings,
   modest bundle (Natural Earth at 1:50m is small).
2. **Street-level detail map:** requires raster tiles. Here's the catch — OSM's
   public tile server (`tile.openstreetmap.org`) **prohibits offline use /
   bulk prefetch**, so you cannot bundle its tiles. Legitimate offline options:
   render your own tiles from OSM data (the rendered output is an ODbL "produced
   work" — allowed, but you must keep **attribution** and point to the source),
   or ship an **MBTiles** pack. This is heavier (bundle size + attribution
   overlay) and probably overkill for a slideshow.

### Recommended path
Start with **scale 1** (Natural Earth + Cartopy/GeoPandas): a generated
"journey map" slide showing the event's photo locations as pins or a path. It's
the common slideshow use, it's license-clean, and it's offline by construction.
Treat street-level/MBTiles as a later opt-in if users ask.

### Handoff to PTE
Generate the map as a **static image slide** (PNG, slideshow resolution) that
drops into a Cut like any other exported frame — PTE then handles
transitions/animation. Optionally generate a short sequence (zoom-in, pins
appearing) as pre-rendered frames, but the baseline is one image per map.

### Open questions
- Confirm the **no-network stance for tiles**: stick to public-domain Natural
  Earth (truly zero network), or is an allow-listed local tile pack acceptable
  for street detail?
- How many photos actually carry GPS EXIF in your library? (Drives whether this
  is worth it and whether to offer manual pin placement.)

---

## B6 — Collages from Cuts

### Verdict
Feasible and self-contained — but **build it, don't depend on PhotoCollage.**

### The licensing trap
- **PhotoCollage** (PyPI `photocollage`) does exactly this — auto-arranges photos
  to fill a canvas keeping each as large as possible — but it's **GPL-2.0-or-
  later**. Bundling/linking GPL into closed-source freeware is the classic
  copyleft conflict. Disqualified as a dependency. The good news: its core
  **algorithm is standard and reimplementable.**

### Recommended approach (permissive + offline)
- **Engine:** **Pillow** (HPND / MIT-CMU — permissive) for all compositing.
  Everything runs locally; no network.
- **Layout algorithm:** the well-documented **binary-tree recursive slicing**
  (a.k.a. BRIC / "guillotine" partition): recursively split the canvas with
  horizontal/vertical cuts into a binary tree, assign each photo to a leaf,
  size leaves to preserve aspect ratio, then balance so no photo is tiny. This
  is the same method PhotoCollage and the academic "content-preserved collage"
  papers use — reimplementing it from the published description is a few hundred
  lines and carries no license baggage.
- **Simpler tier:** a **justified-rows** layout (like a web photo gallery —
  fixed row height, variable widths to fill the line) is even simpler and looks
  clean for a Cut; good default before the tree layout.

### Integration notes
- Input is a **Cut's exported files** (chronological set already in hand), output
  is a single composed image (PNG/JPEG) — a natural "export a collage from this
  Cut" action, parallel to the existing Cut Play/Export.
- Keep it **non-destructive**: the collage is a generated artifact, originals
  untouched (charter invariant #7).
- Options to expose: canvas size/aspect, border/spacing, background, max photos.

### Open questions
- In-app composition only, or also a "send these to an external tool" path?
- Should collage output re-enter as an exported artifact (joinable to other
  Cuts), or be a terminal export?

---

## Cross-cutting takeaways

- **All three are offline-clean** with the recommended stacks — none needs a
  network exception. The only place that question even arises is B5 *street-level*
  tiles, which the recommendation sidesteps.
- **License is the real gate, not feasibility.** The first-choice library in each
  area (InsightFace models, OSM tiles for offline, PhotoCollage) is unusable for
  closed-source freeware; each has a permissive substitute (Apache/MIT ONNX
  models, public-domain Natural Earth, Pillow + own layout code).
- **Shared dependency:** B4 and B6 both lean on having photo metadata indexed
  (EXIF for B5's GPS, embeddings for B4) — which is the same indexing work B2
  needs. Sequence B2's metadata index first and these get cheaper.
- **Recommended build order:** B6 (smallest, self-contained, Pillow-only) →
  B5 scale-1 (Natural Earth + Cartopy) → B4 (largest; background pass + model
  bundling + clustering).

## Sources
- Face recognition libraries / accuracy: [face_recognition (GitHub)](https://github.com/ageitgey/face_recognition), [InsightFace (GitHub)](https://github.com/deepinsight/insightface)
- InsightFace model licensing: [InsightFace commercial licensing](https://www.insightface.ai/services/models-commercial-licensing), [Buffalo model pricing issue #2587](https://github.com/deepinsight/insightface/issues/2587)
- dlib model license + Windows build: [dlib license](https://dlib.net/license.html), [dlib high-quality face recognition](http://blog.dlib.net/2017/02/high-quality-face-recognition-with-deep.html), [dlib-models](https://github.com/davisking/dlib-models), [face_recognition install issue #339](https://github.com/ageitgey/face_recognition/issues/339)
- Permissive ONNX face models: [OpenVINO ArcFace ResNet100 ONNX (Apache-2.0)](https://github.com/openvinotoolkit/open_model_zoo/blob/master/models/public/face-recognition-resnet100-arcface-onnx/README.md), [FaceONNX (MIT)](https://github.com/FaceONNX/FaceONNX)
- Offline maps libraries: [py-staticmaps](https://github.com/flopp/py-staticmaps), [komoot/staticmap](https://github.com/komoot/staticmap), [static maps with OSM + Pillow](https://alexwlchan.net/2025/static-maps/)
- Map data licensing: [OSM Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/), [Natural Earth Terms of Use (public domain)](https://www.naturalearthdata.com/about/terms-of-use/), [natural-earth-vector](https://github.com/nvkelso/natural-earth-vector)
- Cartopy / GeoPandas licenses: [Cartopy relicensed to BSD](https://scitools.org.uk/cartopy/docs/latest/whatsnew/v0.23.html), [GeoPandas (BSD-3)](https://pypi.org/project/geopandas/)
- Collage tools + algorithm: [PhotoCollage (GPL, PyPI)](https://pypi.org/project/photocollage/), [SoftCollage (CVPR 2022, binary-tree partition)](https://openaccess.thecvf.com/content/CVPR2022/papers/Yu_SoftCollage_A_Differentiable_Probabilistic_Tree_Generator_for_Image_Collage_CVPR_2022_paper.pdf), [content-preserved collage (Springer)](https://link.springer.com/article/10.1007/s11042-014-2375-6)
- Pillow license: [Pillow LICENSE (HPND/MIT-CMU)](https://github.com/python-pillow/Pillow/blob/main/LICENSE)
