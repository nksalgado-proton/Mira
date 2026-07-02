"""spec/136 §2a — splash decoration: title + when·where caption + scrim.

The composited pixmap carries:

  * Top-left, large: the ``SPLASH_TITLE`` wordmark + loading cue.
  * Bottom, small: the chosen frame's *when · where* caption, where
    *when* = ``capture_time_corrected`` ONLY (raw EXIF never leaks)
    and *where* = :func:`core.cut_overlay._where_text`.

The bundled-mark fallback (no source frame) draws the title only — no
caption.
"""
from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPixmap

from core.cut_overlay import FrameProvenance
from mira.ui.shell import splash as splash_mod


# ── format_when_for_splash — corrected only, never raw ────────────────


def test_format_when_uses_corrected_iso_string():
    """A normal ISO timestamp parses to ``Month YYYY``."""
    assert splash_mod.format_when_for_splash(
        "2025-10-15T14:30:00") == "October 2025"


def test_format_when_handles_timezone_offset():
    """A timestamp with an explicit TZ offset (the Nepal pair shape)
    parses cleanly — ``fromisoformat`` on Python 3.11+ handles it."""
    assert splash_mod.format_when_for_splash(
        "2025-10-15T14:30:00+05:45") == "October 2025"


def test_format_when_handles_zulu_suffix():
    """The 'Z' UTC suffix (older Mira writers) must not break the parse."""
    assert splash_mod.format_when_for_splash(
        "2025-10-15T14:30:00Z") == "October 2025"


def test_format_when_handles_date_only():
    """``capture_time_corrected`` can be a bare date — still resolves
    to the month/year heading."""
    assert splash_mod.format_when_for_splash("2025-10-15") == "October 2025"


def test_format_when_returns_none_on_empty():
    assert splash_mod.format_when_for_splash(None) is None
    assert splash_mod.format_when_for_splash("") is None
    assert splash_mod.format_when_for_splash("   ") is None


def test_format_when_returns_none_on_malformed():
    assert splash_mod.format_when_for_splash("not a date") is None
    assert splash_mod.format_when_for_splash("2025/10/15") is None


def test_format_caption_never_consults_raw_capture_time():
    """The picker hands :func:`format_caption` a :class:`FrameProvenance`
    whose ``when`` is the gateway-resolved CORRECTED timestamp. The
    formatter must not invent a raw-EXIF fallback of its own — it
    reads ``provenance.when`` and that's it (the gateway already chose
    corrected over raw, spec/134 :func:`resolve_when`).
    """
    # Provenance with ONLY corrected → caption uses it.
    fp = FrameProvenance(when="2025-10-15T14:30:00",
                         city="Kathmandu", country="Nepal")
    assert splash_mod.format_caption(fp) == "October 2025 · Kathmandu, Nepal"

    # Provenance with EMPTY when → caption skips the date half;
    # the formatter MUST NOT manufacture a raw fallback from elsewhere.
    fp_no_when = FrameProvenance(
        when=None, city="Kathmandu", country="Nepal")
    assert splash_mod.format_caption(fp_no_when) == "Kathmandu, Nepal"


# ── format_caption — date + where joined, parts may be missing ────────


def test_format_caption_full_provenance_joins_when_and_where():
    fp = FrameProvenance(
        when="2025-10-15T14:30:00",
        city="Kathmandu", country="Nepal",
    )
    assert splash_mod.format_caption(fp) == "October 2025 · Kathmandu, Nepal"


def test_format_caption_missing_where_returns_date_only():
    fp = FrameProvenance(when="2025-10-15T14:30:00")
    assert splash_mod.format_caption(fp) == "October 2025"


def test_format_caption_missing_when_returns_where_only():
    fp = FrameProvenance(city="Kathmandu", country="Nepal")
    assert splash_mod.format_caption(fp) == "Kathmandu, Nepal"


def test_format_caption_empty_provenance_returns_empty_string():
    assert splash_mod.format_caption(FrameProvenance()) == ""


def test_format_caption_none_provenance_returns_empty_string():
    assert splash_mod.format_caption(None) == ""


def test_format_caption_city_only_returns_city():
    fp = FrameProvenance(
        when="2025-10-15T14:30:00", city="Kathmandu")
    assert splash_mod.format_caption(fp) == "October 2025 · Kathmandu"


# ── decorate_splash_pixmap — title baked in, no caption fallback ──────


def _solid_pixmap(w: int = 540, h: int = 360,
                  color: QColor = QColor(64, 96, 128)) -> QPixmap:
    """Solid-colour pixmap for visual diffs in tests."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(color)
    return QPixmap.fromImage(img)


def _has_painted_pixel(pix: QPixmap, base_color: QColor,
                       rect_x: int, rect_y: int,
                       rect_w: int, rect_h: int) -> bool:
    """Whether any pixel in the rect differs from ``base_color`` —
    cheap proxy for "something was painted here". Avoids brittle
    glyph-exact checks while still catching "nothing happened"."""
    img = pix.toImage()
    base_rgb = (base_color.red(), base_color.green(), base_color.blue())
    for y in range(rect_y, min(rect_y + rect_h, img.height())):
        for x in range(rect_x, min(rect_x + rect_w, img.width())):
            c = img.pixelColor(x, y)
            if (c.red(), c.green(), c.blue()) != base_rgb:
                return True
    return False


def test_decorate_returns_non_null_same_size_pixmap(qapp):
    """The decoration is non-destructive: the returned pixmap has the
    same dimensions as the input and is not null."""
    src = _solid_pixmap()
    out = splash_mod.decorate_splash_pixmap(
        src, title=splash_mod.SPLASH_TITLE,
        caption="October 2025 · Kathmandu, Nepal")
    assert out is not None and not out.isNull()
    assert out.width() == src.width() and out.height() == src.height()


def test_decorate_paints_top_left_title_region(qapp):
    """Title pixels must land in the top-left region of the pixmap
    (proves the title block was drawn — empty inputs would leave the
    region untouched)."""
    base = QColor(64, 96, 128)
    src = _solid_pixmap(color=base)
    out = splash_mod.decorate_splash_pixmap(
        src, title=splash_mod.SPLASH_TITLE, caption=None)
    # Sample the top-left band where the title + scrim land.
    assert _has_painted_pixel(out, base, 12, 12, 200, 70), (
        "top-left title region was not painted — the title block "
        "must always render (photo path AND bundled fallback)"
    )


def test_decorate_paints_bottom_caption_region_when_caption_present(qapp):
    """Caption pixels must land in the bottom band when a caption is
    supplied — proves the bottom decoration ran."""
    base = QColor(64, 96, 128)
    src = _solid_pixmap(color=base)
    out = splash_mod.decorate_splash_pixmap(
        src, title=splash_mod.SPLASH_TITLE,
        caption="October 2025 · Kathmandu, Nepal")
    h = out.height()
    assert _has_painted_pixel(out, base, 12, h - 60, 300, 50), (
        "bottom caption region was not painted when a caption was "
        "supplied"
    )


def test_decorate_skips_bottom_band_when_no_caption(qapp):
    """The bundled-fallback contract: no caption → no bottom paint.
    Compare a paint-with-caption vs paint-without-caption on the same
    source: the no-caption variant must leave the bottom-middle row
    visibly closer to the base colour than the with-caption variant."""
    base = QColor(64, 96, 128)

    def _band_painted_count(pix: QPixmap) -> int:
        img = pix.toImage()
        # Sample one row 12 px above the bottom edge — far enough above
        # the very edge to avoid bleed but inside the caption band.
        y = img.height() - 12
        base_rgb = (base.red(), base.green(), base.blue())
        return sum(
            1 for x in range(0, img.width(), 4)
            if (img.pixelColor(x, y).red(),
                img.pixelColor(x, y).green(),
                img.pixelColor(x, y).blue()) != base_rgb
        )

    with_cap = splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        caption="October 2025 · Kathmandu, Nepal")
    no_cap = splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        caption=None)
    # With-caption variant paints meaningfully more pixels on the
    # bottom row than the no-caption variant (scrim + text).
    with_count = _band_painted_count(with_cap)
    no_count = _band_painted_count(no_cap)
    assert with_count > no_count + 10, (
        f"caption=None must skip the bottom band — with_caption "
        f"painted {with_count} sampled pixels, no_caption painted "
        f"{no_count}"
    )


def test_decorate_paints_icon_left_of_title_when_provided(qapp):
    """With ``icon=`` supplied, the icon's signature colour shows up
    in the left part of the title row. The base + scrim alone (no
    icon) leaves that band scrim-darkened toward black; the bright-
    red icon paints recognisably red pixels there."""
    base = QColor(64, 96, 128)
    icon = QPixmap(64, 64)
    icon.fill(QColor(220, 60, 60))      # bright red icon, easy to detect

    with_icon = splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        icon=icon)
    no_icon = splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        icon=None)

    def _reddish_count(pix: QPixmap) -> int:
        img = pix.toImage()
        n = 0
        # Walk the icon's expected band: just inside the frame, at the
        # title row's vertical band (~16-60 px high for our pad_y).
        x0 = splash_mod._SPLASH_FRAME_PX + 8
        for y in range(20, 70):
            for x in range(x0, x0 + 60):
                c = img.pixelColor(x, y)
                if c.red() > 150 and c.green() < 110 and c.blue() < 110:
                    n += 1
        return n

    with_red = _reddish_count(with_icon)
    no_red = _reddish_count(no_icon)
    # The icon-band sample has a tiny baseline of text anti-aliasing
    # artefacts that occasionally trip the loose red-pixel threshold;
    # the test asserts a CLEAR dominance over that noise floor.
    assert with_red > no_red + 200, (
        f"icon was not painted to the left of the title — found "
        f"{with_red} red pixels with icon vs {no_red} without (need "
        f"a clear margin over the no-icon noise floor)"
    )


def test_decorate_uses_icon_render_callable_for_crisp_sizing(qapp):
    """When ``icon_render`` is supplied, ``decorate_splash_pixmap``
    calls it with the computed title-row target height — the contract
    the build path uses to render the vector mark at the exact
    physical pixel size needed (avoids the down-scale-then-up-scale
    blur on HiDPI displays)."""
    base = QColor(64, 96, 128)
    icon_calls: list[int] = []

    def _renderer(target_h: int) -> QPixmap:
        icon_calls.append(target_h)
        pm = QPixmap(target_h, target_h)
        pm.fill(QColor(220, 60, 60))
        return pm

    splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        icon_render=_renderer)
    assert len(icon_calls) == 1, (
        f"icon_render must be called exactly once during decoration; "
        f"got {len(icon_calls)} calls"
    )
    # The target height should match decorate's internal formula:
    # max(24, int(title_h * 1.15)) — a reasonable lower bound is 24.
    assert icon_calls[0] >= 24


def test_build_splash_renders_icon_from_svg_when_available(qapp, tmp_path):
    """``build_splash_pixmap`` must consult the SVG sibling of the
    bundled fallback so the icon is painted at the EXACT pixel size
    the title row needs (crisp, not down-scaled-from-256-PNG)."""
    bundled_root = Path(__file__).resolve().parents[1] / "assets" / "icons"
    bundled_png = bundled_root / "mira.png"
    svg_sibling = bundled_root / "mira-mark.svg"
    assert svg_sibling.is_file(), (
        "this test depends on the bundled vector mark — confirm it "
        "ships alongside mira.png")
    # Spy on tinted_svg_pixmap to confirm the SVG path is actually
    # exercised at the title-row target size.
    from mira.ui.design import icons as icons_mod
    calls: list[tuple[Path, int]] = []
    real = icons_mod.tinted_svg_pixmap

    def _spy(path, size, color):
        calls.append((Path(path), int(size)))
        return real(path, size, color)
    with mock.patch.object(icons_mod, "tinted_svg_pixmap", _spy):
        # Also have to patch the imported reference inside splash_mod
        # since `from mira.ui.design.icons import tinted_svg_pixmap`
        # is performed inside _build_icon_renderer; here it goes
        # through the module attribute so the spy lands.
        pix = splash_mod.build_splash_pixmap(
            _StubGw(), bundled_fallback=bundled_png, photo_enabled=True)
    assert pix is not None and not pix.isNull()
    assert any(p.name == "mira-mark.svg" for p, _ in calls), (
        f"build_splash_pixmap must render the vector mark for a crisp "
        f"icon; tinted_svg_pixmap calls were {calls!r}"
    )


def test_decorate_paints_white_perimeter_frame(qapp):
    """A white frame must land along the splash perimeter so it lifts
    off the desktop. Sample one pixel per side a quarter-stroke in
    from the edge (well inside the frame's stroke band)."""
    base = QColor(64, 96, 128)
    out = splash_mod.decorate_splash_pixmap(
        _solid_pixmap(color=base), title=splash_mod.SPLASH_TITLE,
        caption="October 2025 · Kathmandu, Nepal")
    img = out.toImage()
    w, h = img.width(), img.height()
    sample = max(1, splash_mod._SPLASH_FRAME_PX // 2)

    white = (255, 255, 255)

    def _rgb(x, y):
        c = img.pixelColor(x, y)
        return (c.red(), c.green(), c.blue())

    # One sample per side at the canvas centre on that axis.
    assert _rgb(w // 2, sample) == white, "top edge missing white frame"
    assert _rgb(w // 2, h - 1 - sample) == white, (
        "bottom edge missing white frame")
    assert _rgb(sample, h // 2) == white, "left edge missing white frame"
    assert _rgb(w - 1 - sample, h // 2) == white, (
        "right edge missing white frame")


def test_decorate_returns_input_unchanged_for_null_or_tiny_pixmap(qapp):
    """Defensive — never crash on a null / tiny pixmap; just hand it
    back so the splash still constructs cleanly."""
    null_pix = QPixmap()
    assert splash_mod.decorate_splash_pixmap(
        null_pix, title=splash_mod.SPLASH_TITLE).isNull()

    tiny = QPixmap(8, 8)
    tiny.fill(QColor(0, 0, 0))
    out = splash_mod.decorate_splash_pixmap(
        tiny, title=splash_mod.SPLASH_TITLE)
    assert out.width() == 8 and out.height() == 8


# ── build_splash_pixmap end-to-end: photo vs bundled-fallback paths ──


class _StubGw:
    def list_events(self):
        return []


def test_build_splash_bundled_fallback_paints_title_only(qapp, tmp_path):
    """No photo source → bundled mark canvas + title, no caption.
    The bottom band stays at the dark canvas colour (no caption paint)
    while the top-left carries the title."""
    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    pix = splash_mod.build_splash_pixmap(
        _StubGw(), bundled_fallback=bundled, photo_enabled=True)
    assert pix is not None and not pix.isNull()
    # Top-left painted (title + scrim).
    canvas_bg = QColor(18, 18, 22)
    assert _has_painted_pixel(pix, canvas_bg, 12, 12, 220, 70), (
        "bundled-fallback splash must paint the top-left title"
    )


def test_build_splash_photo_path_paints_title_and_caption(qapp, tmp_path):
    """Photo path with a chosen frame → the title AND the when·where
    caption are baked into the returned pixmap."""
    from PIL import Image

    # A real on-disk JPEG for the chosen frame.
    export = tmp_path / "exp.jpg"
    Image.new("RGB", (640, 480), (32, 96, 160)).save(export, "JPEG")

    chosen = splash_mod.ChosenFrame(
        event_name="Nepal trip",
        export_path=export,
        proxy_path=None,
        provenance=FrameProvenance(
            when="2025-10-15T14:30:00",
            city="Kathmandu", country="Nepal"),
    )

    def _fake_pick(_gw, **_kw):
        return chosen

    bundled = Path(__file__).resolve().parents[1] / "assets" / "icons" / "mira.png"
    with mock.patch.object(
            splash_mod, "pick_random_exported_frame", _fake_pick):
        pix = splash_mod.build_splash_pixmap(
            _StubGw(), bundled_fallback=bundled, photo_enabled=True,
            max_edge=480)
    assert pix is not None and not pix.isNull()
    # Photo base colour is the solid (32, 96, 160) fill. The painted
    # decoration shows up as pixels OFF that base in both regions.
    base = QColor(32, 96, 160)
    assert _has_painted_pixel(pix, base, 8, 8, 220, 70), (
        "photo splash must paint the top-left title"
    )
    h = pix.height()
    assert _has_painted_pixel(pix, base, 8, h - 50, 260, 40), (
        "photo splash must paint the bottom when·where caption"
    )


def test_chosen_frame_carries_provenance_field(qapp):
    """Schema contract: ``ChosenFrame.provenance`` exists and defaults
    to ``None`` so the decoration step degrades to a date-less caption
    when the picker couldn't resolve it."""
    cf = splash_mod.ChosenFrame(
        event_name="x", export_path=Path("a.jpg"), proxy_path=None)
    assert cf.provenance is None
    # And a non-None FrameProvenance round-trips.
    fp = FrameProvenance(when="2025-10-15T14:30:00", city="K")
    cf2 = splash_mod.ChosenFrame(
        event_name="x", export_path=Path("a.jpg"),
        proxy_path=None, provenance=fp)
    assert cf2.provenance is fp


def test_pick_calls_frame_provenance_on_event_gateway(qapp, tmp_path):
    """The picker must stash the spec/134 provenance on the
    ChosenFrame while the event gateway is open — at decoration time
    the gateway is long-closed."""
    from types import SimpleNamespace

    root = tmp_path / "evt"
    root.mkdir()
    (root / "event.db").write_bytes(b"")
    export_dir = root / "Exported Media"
    export_dir.mkdir()
    (export_dir / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    lineage = SimpleNamespace(
        export_relpath="Exported Media/a.jpg",
        source_item_id=None,
        source_bracket_id=None,
    )
    sentinel_provenance = FrameProvenance(
        when="2025-10-15T14:30:00", city="Kathmandu", country="Nepal")

    class _StubEg:
        def __init__(self):
            self.closed = False
            self.provenance_calls: list[str] = []

        def exported_edited_files(self):
            return [lineage]

        def frame_provenance(self, relpath):
            self.provenance_calls.append(relpath)
            return sentinel_provenance

        def close(self):
            self.closed = True

    eg = _StubEg()

    class _GwStub:
        def list_events(self):
            return [{"id": "evt", "is_closed": True,
                     "event_root": root, "name": "X"}]

    import random
    chosen = splash_mod.pick_random_exported_frame(
        _GwStub(), rng=random.Random(0),
        open_event=lambda r: eg, deadline_ms=10_000)
    assert chosen is not None
    assert chosen.provenance is sentinel_provenance
    assert eg.provenance_calls == ["Exported Media/a.jpg"]
    assert eg.closed
