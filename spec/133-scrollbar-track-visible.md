# 133 — Scroll bar track is invisible (no clickable groove) in both themes

**Status: PROPOSED (Nelson 2026-06-23). In the grids view (and every
`QScrollArea` app-wide — the rule is global), the scroll bar **track** is
invisible: only the handle is painted. `redesign.qss` sets
`QScrollBar:vertical/horizontal { background: transparent; }` and
`QScrollBar::add-page, ::sub-page { background: none; }`, so there's no
visible groove to aim at for page-scroll clicks, in **both** light and dark.
Fix: paint the track with the existing **`{track}`** palette token (dark
`#222734` / light `#d3d7df`) — already proven visible in both themes (it
backs `QProgressBar#StageBar`). One QSS change in
`assets/themes/redesign.qss`. No code change.**

## 1. The bug

```qss
QScrollBar:vertical   { background: transparent; width: 12px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 0; }
...
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
```

Handle only (`{border_strong}`, `{primary}` on hover). The groove is
transparent → the user can't see the track area to click for a page jump,
and the bar is easy to miss entirely.

## 2. The fix

Give the scroll bar a visible track using the dedicated `{track}` token
(already substituted by `build_redesign_qss`; used at
`QProgressBar#StageBar`):

```qss
QScrollBar:vertical   { background: {track}; width: 12px; margin: 0; border-radius: 6px; }
QScrollBar:horizontal { background: {track}; height: 12px; margin: 0; border-radius: 6px; }
```

- The handle keeps its `margin: 2px`, so the `{track}` groove shows as a
  thin frame around it — a clear, clickable target.
- Leave `::add-page / ::sub-page` as is (the bar's own `{track}` background
  shows through), or set them to `{track}` too for explicitness — either
  way the page areas are now visible.
- Handle styling unchanged (`{border_strong}` / `{primary}` hover).

`{track}` is gentle (low contrast against the surface) so the bar stays
unobtrusive while being findable — the same balance the StageBar already
strikes in both themes.

## 3. Acceptance

- The scroll bar shows a visible track in both light and dark themes; the
  clickable groove for page-scroll is obvious.
- The handle still reads clearly (mid-tone, blue on hover), sitting on the
  track.
- Applies everywhere (global `QScrollBar` rule) — grids, navigator, every
  scroll surface.

## 4. Tests / verification

- This is QSS-only; no unit test asserts paint. Verify by eye in both themes
  on the Days Grid (and confirm `tests/test_no_inline_qss.py` /
  `scripts/qss_guard_baseline.json` still pass — the change is in the
  canonical template, not inline).
- Optional: a smoke that `build_redesign_qss` resolves `{track}` for both
  light and dark without leaving an unsubstituted `{track}` literal.
