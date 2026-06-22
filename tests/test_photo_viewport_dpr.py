"""spec/95 §A — DPR-aware photo viewport target + clamp tests.

The normal viewing tier requests decode targets in PHYSICAL pixels
(viewport size × devicePixelRatio) and clamps to the machine-local
``display_quality`` ceiling. Tests cover:

* DPR multiplication at 1.0 / 1.5 / 2.0 (the common HiDPI / scaled
  Windows defaults).
* Ceiling clamp (``balanced`` = 3840, ``high`` = 5120).
* Quantisation (``_TARGET_STEP`` = 512) AFTER the ceiling so the
  cache key tracks the ceiling.
* The displayed pixmap carries ``setDevicePixelRatio`` so Qt paints
  it 1:1 on scaled displays.
* "Never upscale beyond native": a sub-viewport-sized photo is shown
  at native, not stretched.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage, QPixmap

from core import machine_settings
from mira.ui.media.photo_cache import PhotoCache
from mira.ui.media.photo_viewport import PhotoViewport, ViewportItem


def _spin_until(qapp, predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


@pytest.fixture
def viewport(qapp, cache):
    vp = PhotoViewport(cache=cache)
    vp.resize(1920, 1080)            # a common big-monitor viewport
    yield vp
    vp.deleteLater()


@pytest.fixture(autouse=True)
def _isolate_machine_settings(tmp_path, monkeypatch):
    """Point ``machine_settings_path`` at a tempdir so each test's
    ``display_quality`` reads/writes don't touch the production
    ``%LOCALAPPDATA%\\Mira\\machine.json`` and don't leak between
    tests."""
    machine_file = tmp_path / "machine.json"
    monkeypatch.setattr(
        machine_settings, "machine_settings_path",
        lambda: machine_file)
    return machine_file


def _patch_dpr(vp, dpr):
    """Override ``devicePixelRatioF`` on this viewport instance so
    the test exercises the DPR math without a real scaled display.
    """
    vp.devicePixelRatioF = lambda: float(dpr)        # type: ignore[method-assign]


# ── DPR multiplication ───────────────────────────────────────────


def test_target_size_at_dpr_1_is_logical_size_quantised(viewport):
    """spec/95 §A — DPR 1.0 leaves the target at logical pixels
    (quantised UP to the 512 step). No ceiling clamp at 1920×1080."""
    _patch_dpr(viewport, 1.0)
    target = viewport._target_size()
    # 1920 → 2048 (next 512 step), 1080 → 1536.
    assert target == QSize(2048, 1536)


def test_target_size_at_dpr_1_5_multiplies_logical(viewport):
    """A 1920×1080 logical viewport at DPR 1.5 wants 2880×1620
    physical pixels — quantised UP to the next 512-step → 3072×2048."""
    _patch_dpr(viewport, 1.5)
    target = viewport._target_size()
    assert target == QSize(3072, 2048)


def test_target_size_at_dpr_2_multiplies_logical(viewport):
    """DPR 2.0 doubles the logical request → 3840×2160 → quantised
    to 4096×2560. The ceiling clamp will apply via the next test."""
    # Resize down so the DPR-2 target doesn't hit the ceiling.
    viewport.resize(1280, 720)
    _patch_dpr(viewport, 2.0)
    target = viewport._target_size()
    # 1280*2=2560 → 2560 (already on a step). 720*2=1440 → 1536.
    assert target == QSize(2560, 1536)


# ── Display-quality ceiling ─────────────────────────────────────


def test_target_clamped_to_balanced_ceiling_at_high_dpr(viewport):
    """Balanced caps the long edge at 3840. A 1920×1080 viewport at
    DPR 2.0 would otherwise want 3840×2160 → the height (2160) is
    above the ceiling for THAT axis, but the long edge is what
    matters. Scale the whole pair so long ≤ 3840."""
    machine_settings.write_display_quality("balanced")
    _patch_dpr(viewport, 2.0)
    target = viewport._target_size()
    # Pre-quantisation: long edge clamped to 3840 (4K-class). After
    # the 512 step, both axes round UP.
    assert max(target.width(), target.height()) <= 4096
    # And the long edge after quantisation is the one at the ceiling
    # band: 3840 / step 512 = 7.5 → ceil to 8 → 4096.
    assert target.width() == 4096


def test_target_clamped_to_high_ceiling(viewport):
    """High caps the long edge at 5120. A 1920×1080 at DPR 3.0 would
    want 5760×3240 → clamp long to 5120, short ratio-scales."""
    machine_settings.write_display_quality("high")
    _patch_dpr(viewport, 3.0)
    target = viewport._target_size()
    long_edge = max(target.width(), target.height())
    # 5120 / 512 = 10 → 5120 exact.
    assert long_edge == 5120


def test_target_not_clamped_when_below_ceiling(viewport):
    """A laptop-class viewport (1366×768) at DPR 1.0 stays at its
    natural quantised size — the spec/95 §B settle-only upgrade
    never even fires here because the target ≤ proxy_edge (2560)."""
    machine_settings.write_display_quality("balanced")
    viewport.resize(1366, 768)
    _patch_dpr(viewport, 1.0)
    target = viewport._target_size()
    # 1366 → 1536, 768 → 1024.
    assert target == QSize(1536, 1024)
    assert max(target.width(), target.height()) < 3840


def test_target_never_exceeds_ceiling_at_any_dpr(viewport):
    """Sweep DPRs — the long edge of the returned target NEVER
    exceeds the post-quantisation ceiling band (ceiling rounded UP
    to the next 512 step). Pins the anti-lag invariant."""
    machine_settings.write_display_quality("balanced")
    ceiling = 3840
    step = 512
    max_quantised = ((ceiling + step - 1) // step) * step  # 4096
    for dpr in (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0):
        viewport.resize(1920, 1080)
        _patch_dpr(viewport, dpr)
        target = viewport._target_size()
        long_edge = max(target.width(), target.height())
        assert long_edge <= max_quantised, (
            f"DPR {dpr} → long {long_edge} > {max_quantised}")


def test_display_quality_change_takes_effect_on_next_target_call(viewport):
    """Reading the override every call (no caching) means a Settings
    dialog write applies on the next viewport target computation —
    no reload, no relaunch."""
    machine_settings.write_display_quality("balanced")
    viewport.resize(1920, 1080)
    _patch_dpr(viewport, 3.0)
    bal_long = max(viewport._target_size().width(),
                   viewport._target_size().height())

    machine_settings.write_display_quality("high")
    high_long = max(viewport._target_size().width(),
                    viewport._target_size().height())
    assert high_long > bal_long


# ── DPR tag on the displayed pixmap (§A "Qt paints 1:1") ────────


def _make_jpeg(tmp_path: Path, name: str, w: int, h: int) -> Path:
    p = tmp_path / name
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0xCC2244)
    assert img.save(str(p), "JPG", 90)
    return p


def test_displayed_pixmap_carries_device_pixel_ratio(
    qapp, viewport, tmp_path,
):
    """spec/95 §A — the pixmap handed to ``QLabel.setPixmap`` is
    tagged with the panel's DPR so Qt paints it 1:1.

    Reads from ``viewport._last_label_pixmap`` (the test seam) rather
    than ``self._label.pixmap()``: Qt6's ``QLabel.setPixmap``
    normalises a custom DPR to the host's screen DPR while
    preserving ``deviceIndependentSize``, so the post-setPixmap
    label value can't distinguish "we tagged 1.5" from "the host
    screen is 1.5". The seam tracks the value we set; production
    correctness rides on the screen DPR == widget DPR identity
    that holds outside test patching.
    """
    _patch_dpr(viewport, 1.5)
    p = _make_jpeg(tmp_path, "x.jpg", 1200, 800)
    viewport.set_items([ViewportItem(path=p)])
    assert _spin_until(
        qapp, lambda: viewport.sharp_pixmap_info() is not None)
    # Force a fit so the seam captures the most recent pixmap.
    viewport._fit()                                  # noqa: SLF001
    last_pm = viewport._last_label_pixmap            # noqa: SLF001
    assert isinstance(last_pm, QPixmap)
    assert last_pm.devicePixelRatio() == pytest.approx(1.5)


# ── "Never upscale beyond native" ───────────────────────────────


def test_sub_viewport_photo_shown_at_native_size(
    qapp, viewport, tmp_path,
):
    """spec/95 §A — a 320×240 photo in a 1920×1080 viewport must NOT
    upscale to fill the viewport. The QLabel pixmap stays at the
    photo's native pixel count (or smaller after KeepAspectRatio /
    DPR math), never larger."""
    _patch_dpr(viewport, 1.0)
    p = _make_jpeg(tmp_path, "small.jpg", 320, 240)
    viewport.set_items([ViewportItem(path=p)])
    assert _spin_until(
        qapp, lambda: viewport.sharp_pixmap_info() is not None)
    viewport._fit()                                  # noqa: SLF001
    label_pm = viewport._label.pixmap()              # noqa: SLF001
    # The label pixmap holds at most the native pixel count.
    assert label_pm.width() <= 320
    assert label_pm.height() <= 240


# ── §B — Navigation never waits + settle upgrade ────────────────


def test_held_arrow_requests_only_nav_target(qapp, viewport, tmp_path):
    """spec/95 §B anti-lag — a held-arrow burst over several items
    must NEVER issue a settle-target (> proxy edge) request: the
    nav target stays proxy-bounded so the worker rides the proxy
    fast path. The settle-target upgrade fires only when the user
    STOPS on an item, which the spec/63 settle timer enforces.
    Spy on every ``request_scaled_pixmap`` call and assert no
    long-edge target exceeds PROXY_MAX_EDGE during navigation."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from core import photo_proxy_cache

    # Force a settle target above the proxy edge so the nav/settle
    # split would matter.
    machine_settings.write_display_quality("high")
    _patch_dpr(viewport, 2.0)
    # 1920×1080 × DPR 2.0 → 3840×2160; settle=4096×2560 (quantised
    # post-ceiling 3840 long), well above PROXY_MAX_EDGE (2560).
    items = [
        ViewportItem(path=_make_jpeg(tmp_path, f"p{i}.jpg", 1200, 800))
        for i in range(5)
    ]
    nav_calls: list[QSize] = []
    real_request = viewport._cache.request_scaled_pixmap     # noqa: SLF001

    def spy_request(path, target, priority=0):
        nav_calls.append(target)
        return real_request(path, target, priority=priority)

    viewport._cache.request_scaled_pixmap = spy_request      # noqa: SLF001

    viewport.set_items(items, current=0)
    # Held-arrow burst: navigate fast without letting settle fire.
    for _ in range(4):
        QTest.keyClick(viewport, Qt.Key.Key_Right)
    # Any request made during nav must stay proxy-bounded.
    edge = photo_proxy_cache.PROXY_MAX_EDGE
    for size in nav_calls:
        long_edge = max(size.width(), size.height())
        assert long_edge <= edge + _TARGET_STEP, (
            f"nav request long={long_edge} exceeded proxy edge "
            f"({edge}px) — held-arrow would queue a slow original "
            f"decode (spec/95 §B anti-lag violation)")


from mira.ui.media.photo_viewport import _TARGET_STEP  # noqa: E402


def test_settle_upgrade_fires_only_after_settle_timer(
    qapp, viewport, tmp_path,
):
    """spec/95 §B — the §B settle upgrade request happens ONLY when
    the settle timer fires (the user stopped). On a viewport whose
    settle target > nav target, settle issues the bigger target
    request; before settle, only the nav target appears."""
    from core import photo_proxy_cache

    machine_settings.write_display_quality("high")
    _patch_dpr(viewport, 2.0)
    item = ViewportItem(
        path=_make_jpeg(tmp_path, "x.jpg", 1200, 800))

    calls: list[QSize] = []
    real_request = viewport._cache.request_scaled_pixmap     # noqa: SLF001

    def spy_request(path, target, priority=0):
        calls.append(target)
        return real_request(path, target, priority=priority)

    viewport._cache.request_scaled_pixmap = spy_request      # noqa: SLF001
    viewport.set_items([item])

    # Pre-settle: only nav-target requests went out.
    edge = photo_proxy_cache.PROXY_MAX_EDGE
    pre_settle = list(calls)
    for size in pre_settle:
        assert max(size.width(), size.height()) <= edge + _TARGET_STEP

    # Trigger settle directly (the timer is fired-once on
    # bucket change; just call the slot).
    viewport._on_settle()                                    # noqa: SLF001

    # After settle: at least ONE call exceeded the proxy edge — the
    # upgrade request landed.
    post_settle = calls[len(pre_settle):]
    assert any(
        max(s.width(), s.height()) > edge for s in post_settle), (
        "settle did not issue a settle-target (>proxy_edge) "
        "request — the §B original-decode upgrade never fires.")


def test_settle_upgrade_skipped_when_nav_already_covers_target(
    qapp, viewport, tmp_path,
):
    """Laptop-class viewport: settle target == nav target (both fit
    in the proxy edge), so settle issues NO extra upgrade request.
    The laptop's footprint is unchanged from today."""
    from core import photo_proxy_cache

    machine_settings.write_display_quality("balanced")
    _patch_dpr(viewport, 1.0)
    viewport.resize(1366, 768)
    item = ViewportItem(
        path=_make_jpeg(tmp_path, "y.jpg", 800, 600))

    calls: list[QSize] = []
    real_request = viewport._cache.request_scaled_pixmap     # noqa: SLF001

    def spy_request(path, target, priority=0):
        calls.append(target)
        return real_request(path, target, priority=priority)

    viewport._cache.request_scaled_pixmap = spy_request      # noqa: SLF001
    viewport.set_items([item])
    pre_settle = len(calls)
    viewport._on_settle()                                    # noqa: SLF001
    post_settle = calls[pre_settle:]

    edge = photo_proxy_cache.PROXY_MAX_EDGE
    for s in post_settle:
        long_edge = max(s.width(), s.height())
        assert long_edge <= edge + _TARGET_STEP, (
            "settle issued a >proxy_edge target on a laptop-class "
            "viewport — the §B upgrade should be a no-op here.")
