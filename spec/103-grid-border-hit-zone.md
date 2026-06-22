# 103 — Days-grid status-toggle hit zone: "closer to the edge than the centre"

**Status: PROPOSED (Nelson 2026-06-22). Fixes a usability bug: in the
Picker / days grid the border (status-toggle) click zone is a thin fixed
band (≤ 32 px from each edge), so clicking "near the border" to cycle
Skip→Pick→Compare misses constantly. The painted 3 px border is fine; the
*click* zone is too small. Touches one widget,
`mira/ui/design/thumb_grid.py` (`_GridCell` hit-test). No keymap /
charter-invariant impact; the two-zone grammar (border = status, centre =
open) is unchanged — only the boundary between the zones moves.**

## 1. The bug

`_GridCell.mousePressEvent` splits border vs centre by a fixed pixel band:

```python
BORDER_RATIO = 0.15; MIN_BORDER_PX = 10; MAX_BORDER_PX = 32
b = max(MIN_BORDER_PX, min(MAX_BORDER_PX, int(min(w, h) * BORDER_RATIO)))  # ≤ 32 px
in_border = x < b or x >= w - b or y < b or y >= h - b
```

On a ~280 px tile, `b` caps at 32 px — about 11 % in from the edge — so the
inner ~78 % counts as "centre" (open / drill-in) and the toggle band is a
thin frame the user can't reliably hit (Nelson 2026-06-22).

## 2. The fix — the toggle zone is the outer quarter on each axis

The intended rule is "a click closer to an edge than to the centre toggles
status." On one axis that is exactly the outer quarter: `x < w/4` means the
click is nearer the left edge than the centre line. So:

```python
# border (status toggle) = closer to an edge than to the centre on either axis
bx, by = self.width() // 4, self.height() // 4
in_border = (pos.x() < bx or pos.x() >= self.width() - bx
             or pos.y() < by or pos.y() >= self.height() - by)
```

- The central 50 % × 50 % rectangle opens (drill-in / in-place open); the
  surrounding "L" toggles status — a large, easy target that scales with
  the tile.
- Apply the same change to the `hit_zone(x, y)` test helper so it stays in
  lockstep.
- `BORDER_RATIO` / `MIN_BORDER_PX` / `MAX_BORDER_PX` become obsolete —
  remove them (and the `_border_px` helper) or leave a comment; nothing
  else references them.
- Only two-zone cells (`self._two_zone`) are affected; single-zone grids
  (whole-tile click) keep today's behaviour.

## 3. Acceptance

- In the Picker / days grid, a click anywhere in the outer quarter of a
  tile (the area visually "near the border") cycles the status; a click in
  the central half opens / drills in.
- The painted 3 px state border is unchanged.
- Cluster cells, Edit grid, and Export grid keep their respective
  centre-actions; only the toggle band widened.

## 4. Tests

- `tests/test_grid_cell_hit_zone.py` — `hit_zone(w/2, h/2)` → "center";
  a point at 20 % of the width/height → "border"; a point at 40 % →
  "center"; each corner → "border". Verify across a small (140 px) and a
  large (280 px) cell so the quarter rule scales.
- Regress any existing hit-zone / DayGridCell tests.

## 5. Open question

- **Quarter vs third.** Quarter (central 50 % opens) matches the literal
  "closer to the border than the centre" and gives the biggest toggle
  target. If the centre/open action feels cramped on small tiles, use a
  third instead (central ~66 % opens, outer ~33 % toggles). Default:
  quarter; confirm on the smallest tile size in use.
