"""Nelson 2026-06-06 — 90° image rotation (independent from crop box).

Schema already had ``Adjustment.rotation`` (0/90/180/270); engine already
had ``core.photo_render.apply_rotation``. Wiring was the missing piece —
this file pins the AdjustmentSurface API + render-pipeline behaviour."""
from __future__ import annotations

import numpy as np
import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                  # pragma: no cover
    QApplication = None


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _surface(qapp):
    from mira.ui.edited.adjustment_surface import AdjustmentSurface
    s = AdjustmentSurface()
    # 80×60 distinguishable RGB array (the per-pixel value encodes its
    # position so we can verify rotation orientation).
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    img[10, 20] = (255, 0, 0)   # one identifiable pixel
    s.load_image(img)
    return s, img


def test_initial_rotation_is_zero(qapp):
    s, _ = _surface(qapp)
    assert s._rotation == 0


def test_rotate_image_cw_advances_in_90deg_steps(qapp):
    s, _ = _surface(qapp)
    s.rotate_image(90)
    assert s._rotation == 90
    s.rotate_image(90)
    assert s._rotation == 180
    s.rotate_image(90)
    assert s._rotation == 270
    s.rotate_image(90)
    assert s._rotation == 0


def test_rotate_image_ccw_walks_backwards(qapp):
    s, _ = _surface(qapp)
    s.rotate_image(-90)
    assert s._rotation == 270
    s.rotate_image(-90)
    assert s._rotation == 180


def test_rotate_image_emits_changed_rotation(qapp):
    s, _ = _surface(qapp)
    seen = []
    s.changed.connect(seen.append)
    s.rotate_image(90)
    assert seen == ["rotation"]


def test_rotate_image_noop_without_loaded_photo(qapp):
    from mira.ui.edited.adjustment_surface import AdjustmentSurface
    s = AdjustmentSurface()
    # No load_image → rotate_image is a defensive no-op.
    seen = []
    s.changed.connect(seen.append)
    s.rotate_image(90)
    assert s._rotation == 0
    assert seen == []


def test_rotate_image_resets_crop_and_box_angle(qapp):
    """Rotating swaps frame dimensions — the normalised crop rect would
    land in the wrong place. ``rotate_image`` clears the crop + box angle
    so the user is in a clean state to re-crop."""
    s, _ = _surface(qapp)
    s._crop_norm = (0.1, 0.1, 0.5, 0.5)
    s._box_angle = 12.0
    s.rotate_image(90)
    assert s._crop_norm is None
    assert s._box_angle == 0.0


def test_set_rotation_is_quiet(qapp):
    """``set_rotation`` is the load path (programmatic seed) — it must
    NOT fire ``changed`` so the loader can populate widgets without the
    host re-persisting."""
    s, _ = _surface(qapp)
    seen = []
    s.changed.connect(seen.append)
    s.set_rotation(180)
    assert s._rotation == 180
    assert seen == []


def test_set_rotation_normalises_wild_values(qapp):
    s, _ = _surface(qapp)
    s.set_rotation(450)   # wraps to 90
    assert s._rotation == 90
    s.set_rotation(45)    # rounds to nearest 90 → 0 (45 / 90 = 0.5 → 0 banker rounding) or 0
    # round(0.5) == 0 in Python's banker's rounding; the function uses
    # round() so the only contract we need is normalised to {0,90,180,270}.
    assert s._rotation in (0, 90)


def test_render_now_applies_rotation_to_canvas(qapp):
    """The canvas's preview pixmap dimensions flip on a 90° turn — a
    smoke test that ``apply_rotation`` is actually being called."""
    s, _ = _surface(qapp)
    # Before rotation: preview is 80 wide × 60 tall (or downsampled
    # variants of it). Capture the canvas pixmap size pre-rotation.
    s.render_now()
    cv = s.canvas()
    pix_before = cv._preview_pixmap if hasattr(cv, "_preview_pixmap") else None
    if pix_before is None:
        pytest.skip("MediaCanvas pixmap accessor unavailable in test env")
    w_before, h_before = pix_before.width(), pix_before.height()

    s.rotate_image(90)
    pix_after = cv._preview_pixmap
    w_after, h_after = pix_after.width(), pix_after.height()
    # Aspect should flip on a quarter turn.
    assert (w_before, h_before) == (h_after, w_after), \
        f"expected dim-swap on 90°: {w_before}x{h_before} -> {w_after}x{h_after}"


def test_reset_all_clears_rotation(qapp):
    """Reset is "back to file as it was" — image rotation undoes too."""
    s, _ = _surface(qapp)
    s.rotate_image(90)
    s.rotate_image(90)
    assert s._rotation == 180
    s._on_reset_all()
    assert s._rotation == 0


def test_set_state_seeds_rotation(qapp):
    """The host's ``set_state`` path feeds the rotation from
    ``Adjustment.rotation``; verify the round-trip. (spec/54: the
    state is the Look CHOICE now — no Params in the signature.)"""
    s, _ = _surface(qapp)
    s.set_state(
        look="natural", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original",
        rotation=90,
    )
    assert s._rotation == 90


def test_displayed_dims_helper_swaps_on_quarter_turn(qapp):
    """``_displayed_dims`` is the canonical (w, h) for everything that
    needs to think in the user's-eye view (overlay geometry, default
    crop, etc.). Verify it swaps on 90/270 and not on 0/180."""
    s, _ = _surface(qapp)  # source 80×60
    assert s._displayed_dims() == (80, 60)
    s.rotate_image(90)
    assert s._displayed_dims() == (60, 80)
    s.rotate_image(90)   # 180
    assert s._displayed_dims() == (80, 60)
    s.rotate_image(90)   # 270
    assert s._displayed_dims() == (60, 80)


def test_aspect_change_after_rotation_picks_rotated_crop(qapp):
    """Regression (Nelson 2026-06-06 — "when I toggle the crop the photo
    rotates back"). Picking an aspect ratio on a rotated photo used to
    compute the default crop from the SOURCE array's dims — so a 3:2
    aspect on a 80×60 source rotated to 60×80 portrait produced a
    landscape-shaped rect (0, 0.056, 1.0, 0.889) interpreted on the
    portrait frame. The visible result was a horizontally-stretched
    overlay that read as "the photo went back to landscape". Fix:
    compute the default crop against ``_displayed_dims`` so the rect
    sits naturally on the rotated frame."""
    s, _ = _surface(qapp)
    s.rotate_image(90)   # 80×60 source → 60×80 displayed
    s._on_aspect_changed("3:2")
    # 3:2 target (ratio 1.5) on a 0.75 source ratio → fit by width.
    # rect = (0, (80-40)/2/80, 1.0, 40/80) = (0, 0.25, 1.0, 0.5)
    assert s._rotation == 90, "rotation must survive aspect change"
    assert s._crop_norm == (0.0, 0.25, 1.0, 0.5)


def test_overlay_geometry_uses_rotated_dimensions(qapp):
    """Regression: dragging the crop on a 90°/270° photo USED to crash
    because ``_sync_crop_overlay_geometry`` fed the overlay the source
    array's (w, h) — but the displayed frame's dimensions are swapped on
    a quarter turn. The overlay would compute normalised coords against
    the wrong axis, producing an invalid crop_norm that ``apply_crop_norm``
    would then slice out-of-range. The fix: swap (w, h) before calling
    ``set_image_geometry`` when ``_rotation`` is 90 or 270."""
    s, _ = _surface(qapp)
    # 80×60 source array → at 0°/180° the overlay sees (80, 60);
    # at 90°/270° it sees (60, 80).
    captured: list[tuple[int, int]] = []
    orig_set = s._crop_overlay.set_image_geometry

    def _spy(image_rect, image_dims):
        captured.append(tuple(image_dims))
        return orig_set(image_rect, image_dims)
    s._crop_overlay.set_image_geometry = _spy

    s._sync_crop_overlay_geometry()
    assert captured[-1] == (80, 60), \
        f"upright: expected (80,60) got {captured[-1]}"

    s.rotate_image(90)   # triggers a sync internally
    # The most recent sync after rotation must report swapped dims.
    assert captured[-1] == (60, 80), \
        f"after 90°: expected dim-swap (60,80) got {captured[-1]}"

    s.rotate_image(90)   # to 180 — dims back to original
    assert captured[-1] == (80, 60), \
        f"after 180°: expected (80,60) got {captured[-1]}"

    s.rotate_image(90)   # to 270 — swapped again
    assert captured[-1] == (60, 80), \
        f"after 270°: expected dim-swap (60,80) got {captured[-1]}"
