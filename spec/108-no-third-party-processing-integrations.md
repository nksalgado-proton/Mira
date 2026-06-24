# 108 — No third-party processing integrations: the round-trip contract + Helicon removal

**Status: PROPOSED (Nelson 2026-06-22). Establishes a charter-level
principle — Mira integrates with **no** third-party *processing* tool;
the contract for any external work is a **folder + naming convention**,
and PTE is the **sole** *output*-side exception (spec/107). Then it does
the concrete cleanup that principle implies (remove the dead Helicon
integration, flatten origin badges) and commits to writing the one thing
that's currently missing: a **user-facing round-trip contract doc**.
Touches `mira/settings/model.py`, `core/settings.py`,
`mira/ui/base/settings_dialog.py`, the origin-wordmark surfaces
(`mira/ui/design/thumbs.py` / `thumb_grid.py`, `store/models.py` +
`schema.py` comments), and adds `docs/round-trip-contract.md`. Relates to
spec/00 (charter), spec/57 (folders + round trip), spec/72 (Model B
returns), spec/89 (origin badges), spec/107 (PTE — the exception).**

## 1. The principle (charter-level)

**Mira does not integrate with any third-party *processing* application.**
The user may use whatever tool they like for any processing step — focus
stacking, exposure/HDR merge, RAW develop, retouch (Helicon, Zerene,
Lightroom/LRC, Capture One, Photoshop, …) — and Mira neither launches nor
knows about that tool. The only contract is:

> **Drop the result in the sanctioned folder, named by the sanctioned
> convention. Mira discovers it on the next scan, links it to its source,
> and badges it as external.**

This keeps Mira tool-agnostic, removes a class of fragile integrations,
and ages well (no per-tool launch code to maintain). **The single
exception is PTE** (spec/107), on the *output* side, because producing a
slideshow requires format-specific generation + launching the editor —
and even that is opt-in and template-driven, not a hard dependency.

Internal bundled binaries (ExifTool, FFmpeg under `bin/`) are **not**
third-party integrations in this sense — they are Mira's own runtime
dependencies. Their path settings (`exiftool_path`) stay as advanced
overrides.

## 2. The round-trip contract (already in code — must be documented)

The mechanics already exist (spec/57 §3, spec/72 §1; implemented in
`mira/picked/external_returns.py`). What's missing is a **user-facing
doc**. Record the contract here and ship it as `docs/round-trip-contract.md`:

**Where external tools read.** Mira projects the picked set as links under
`Picked Media/`. External tools open those.

**Where results go back — two lanes:**

1. **Stack merges** (focus OR exposure → one master): save the merged
   file **at the `Picked Media/` root**, with a filename whose **stem
   starts with the bracket member's link stem** (`D{day}_{camera}_
   {originalname}`) or its bare origin stem (`IMG_1234`). Mira adopts it
   into `Original Media/Merged/` as the bracket's final master
   (`adopt_stack_output`); the master is picked-by-construction.
2. **Editor returns** (develop / retouch — one in, one out): save into a
   subdir of **`Edited Media/`**, with a filename whose **stem starts with
   the source's link or origin stem** (so `IMG_1234-Edit.jpg` matches
   `IMG_1234`). Mira hardlinks it into `Exported Media/<filename>` with
   `provenance = 'third_party'` (Model B), so it enters the ship set
   immediately.

**Matching rule:** longest-prefix-wins on the stem (strict link-stem
preferred over the bare origin stem). **Unmatched files are flagged** in
the scan report, never silently dropped. Sidecars (`.xmp`, `.tmp`, `.ini`,
`.db`, `.json`, `.txt`) are ignored without flagging. Scans run on
entering Edit / Export and from the menu — **no watchers**.

The doc must spell out the folder paths, the stem rule, a worked example
per lane, and the "unmatched → flagged" behaviour, so a user with *any*
tool can reliably get results back in and correctly badged.

## 3. Cleanup (what the principle removes / changes)

- **Remove the dead Helicon integration:** `helicon_path` +
  `prefer_helicon_for_focus` (in `mira/settings/model.py` AND
  `core/settings.py` defaults) and their entries in
  `mira/ui/base/settings_dialog.py`. Verified dead — **no code consumes
  them** beyond the defaults; there is no Helicon launch and no embedded
  focus-stack implementation behind the "else embedded OpenCV" promise. So
  this is pure subtraction, zero behaviour change.
- **Flatten origin badges:** the wordmark currently distinguishes
  `Mira / LRC / Helicon / CO / ext` (spec/89 §2.1). Collapse all
  *external* origins to a single generic badge (e.g. **`ext`** /
  "External"); keep **`Mira`** (Mira-rendered) as the one distinct case.
  Update `thumbs.py` / `thumb_grid.py` + the `models.py` / `schema.py`
  comments. Provenance stays `third_party`; only the displayed label
  flattens.
- **Keep** the bundled internal binaries (ExifTool, FFmpeg) and their
  override settings — they are not third-party integrations.

## 4. Bracket merge — the honest current state (and an opportunity)

To avoid the confusion that prompted this spec:

- **Focus brackets:** **external-only.** No built-in focus stacker exists;
  the user merges externally (Helicon/Zerene/…) and returns via lane 1.
- **Exposure brackets:** Mira has a **built-in Mertens fusion**
  (`core/exposure_fusion.py::fuse_exposures`, OpenCV) — but today it powers
  only the Picker's **"Combined" preview** (a decision aid). The
  materialized merged master still comes from an external tool via lane 1
  (the scan even reminds on "picked focus/exposure brackets with no merged
  result").
- **Opportunity (open question):** since Mira already computes the Mertens
  fusion, it could optionally **materialize it in-app** as the exposure
  bracket's merged master (badged `Mira`), removing the need for any
  external exposure-merge tool. This would make exposure merge the one
  processing step Mira does natively — worth deciding separately. (Verify
  first whether any current Export path already materializes it; from the
  read it appears preview-only.)

## 5. PTE — the documented exception (cross-ref)

PTE (spec/107) is the only sanctioned third-party *integration*, and only
on the output side: opt-in `pte_path` launch + template-driven `.pte`
generation. It is explicitly carved out of §1 and must be documented as
such so the principle reads as deliberate, not violated.

## 6. Acceptance

- No setting, dialog field, or code path references Helicon (or any named
  processing tool) as a launch target. `grep -ri helicon` over `mira/` +
  `core/` returns only historical spec/doc mentions, not live config.
- A returned file placed per §2 is adopted + linked + badged `ext` on the
  next scan; an off-convention file is flagged, not silently lost.
- `docs/round-trip-contract.md` exists and documents both lanes, the stem
  rule, worked examples, and the unmatched-flag behaviour.
- ExifTool/FFmpeg overrides and the built-in Mertens preview are unchanged.

## 7. Tests / verification

- `grep` guard (extend `tests/` or a CI check): no live `helicon` /
  per-tool-launch references in `mira/` + `core/` config or UI.
- `tests/test_round_trip_contract.py` — a stack-merge file at the
  `Picked Media/` root and an editor return under `Edited Media/`, named
  per §2, are matched to the right source items (longest-prefix-wins);
  an off-convention name lands in `unmatched`.
- Badge test: an external return renders the flattened `ext` wordmark; a
  Mira render renders `Mira`.

## 8. Open questions

1. **Materialize Mertens in-app** (§4) — make Mira the native exposure
   merger, or keep exposure stacks external-only like focus?
2. **Badge granularity** — is a single `ext` enough, or keep a 2-way
   `Mira` / `ext` only (proposed), dropping LRC/Helicon/CO entirely?
3. **Doc home** — `docs/round-trip-contract.md` plus a first-run /
   Settings link, so users find the convention before they need it.
