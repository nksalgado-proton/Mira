# 120 — Fix: embedded overlays missing from the generated PTE (wrong provenance key)

**Status: PROPOSED (Nelson 2026-06-23). Embedded overlays (When / Where /
Camera / …) render correctly in the in-app **Play Cut** path but are
**absent from the generated `.pte`**. Root cause: the PTE generator resolves
each photo's overlay text with the WRONG lookup key, so
`frame_provenance` always misses and the overlay text comes back empty.
One-method fix in `mira/ui/pages/share_cuts_page.py::_cut_overlay_text`.
No data-model change; the generator's `embedded`→`:Text` plumbing
(spec/107) is already correct — it's just being fed empty strings.**

## 1. The bug

`_cut_overlay_text` (share_cuts_page.py ~line 2474) does:

```python
prov = self._eg.frame_provenance(
    str(photo.relative_to(photo.parent.parent)).replace("\\", "/"))
```

`photo` is the materialized copy in the **Cut handoff folder**, so this
key is something like `"<cut-tag>/007_IMG_1234.jpg"`. But
`EventGateway.frame_provenance(export_relpath)` (event_gateway.py:1668)
looks the row up by `WHERE l.export_relpath = ?`, and a `lineage` row's
`export_relpath` is the **Export-phase** path — `"Exported Media/IMG_1234.jpg"`
(Model B, spec/89 §1.5). The two never match → `frame_provenance` returns an
empty `FrameProvenance()` → `compose_overlay_lines` yields nothing →
`overlay_text=None` for every photo → the PTE `:Text` prototype is stripped
(spec/107 treats empty as "no overlay"). Meanwhile Play composes overlays
from each Cut member's real lineage row, which is why it looks right there.

## 2. The fix

Resolve each exported file back to its **member's `export_relpath`**, then
call `frame_provenance` with that. The export filename is
`"{seq:03d}_{Path(export_relpath).name}"` (cut_export.py:460), so the
basename after the `NNN_` prefix equals the lineage relpath's basename.

- Build a lookup once per generation from `gateway.cut_member_files(cut)` /
  `files_from_lineage`: `{ Path(export_relpath).name : export_relpath }`
  (the same member list the export just wrote — authoritative, no disk
  re-derivation).
- In `_cut_overlay_text`, strip the `NNN_` prefix from `photo.name`, look up
  the member `export_relpath`, and pass THAT to `frame_provenance`. Skip
  (return None) when no member matches (separators / opener have no
  provenance — correct).
- Disambiguate the rare same-basename collision by sequence (the `NNN_`
  prefix) if needed; otherwise basename is sufficient.

Keep the existing guards: overlay mode ≠ `embedded` → None; no
`cut_overlay_fields` → None.

## 3. Acceptance

- A Cut exported with `overlay_mode = embedded` + ≥1 field produces a `.pte`
  whose photo slides carry the overlay `:Text` with the composed lines
  (When / Where / Camera …) — matching what Play shows.
- A photo with no provenance facts still yields no overlay (graceful).
- Burn-in and Off modes are unchanged (still no `:Text` from this path).

## 4. Tests

- Extend `tests/test_pte_project.py` (or a new
  `tests/test_pte_overlay_wiring.py`): given a Cut with embedded overlays,
  the generated `.pte` contains the per-slide `:Text` with the expected
  composed lines; the provenance lookup matches members by `NNN_`-stripped
  basename; a member with empty provenance yields no `:Text`; Off / burn_in
  yield none.
- A regression asserting the old folder-relative key would have produced
  empty text (pin the fix).
