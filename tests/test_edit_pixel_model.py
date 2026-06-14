"""spec/63 §6.1 — the 6b safety net, committed at clean HEAD BEFORE
any swap (the 5d recipe).

Pins the Edit pixel-model behavior that must SURVIVE the migration
(the §6.1 insurance list): the developed working view reaching the
display, the same-path decode cache, graceful unsupported files, the
set_state/get_state contract, look-change persistence, the crop
overlay's parentage, ``render_full_pixmap`` honesty + purity,
Toggle-Crop, Compare semantics, and ``_downsample``'s output shape
(its slow implementation is a named kill; the semantics are not).

ERA-PORTABLE BY DESIGN: the §6.1 model makes loading ASYNC (instant
browse pixels, settle-gated off-thread prep, developed flip), so
every load-dependent pin spins (``_wait_developed`` — immediate on
the synchronous pre-swap pipeline) and the display-seam spy watches
BOTH eras' seams (``MediaCanvas.set_preview_pixmap`` today, the
viewport's ``set_rendered_pixmap`` after the swap) so the net passes
UNEDITED across the migration. Same for the decode counter (the
page's module today, the prep worker's module after).

NOTE the module name deliberately dodges the conftest slice-B skip
list (test_edit_page / test_edit_page_rebuild are on it).
"""
from __future__ import annotations

import itertools
import time

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage

from mira.gateway.event_gateway import EventGateway
from mira.picked.model import CullBucket, CullItem
from mira.picked.status import BADGE_UNTOUCHED, BucketStatus
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.edited.adjustment_surface import (
    PREVIEW_MAX_WIDTH, AdjustmentSurface, _downsample)
from mira.ui.edited.edit_page import EditPage
from mira.ui.media.media_canvas import MediaCanvas

FIXED_NOW = "2026-06-12T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _spin_until(qapp, predicate, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _wait_developed(qapp, page, path=None) -> None:
    """Block until the current photo's working copy is loaded + shown.
    Immediate on the synchronous pre-swap pipeline; absorbs the
    settle-gated off-thread prep after the swap."""
    def ready() -> bool:
        if page._surface._preview_array is None:
            return False
        return path is None or page._cached_path == path
    assert _spin_until(qapp, ready), "working copy never landed"


def _write_jpeg(path, hue: int = 80) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv(hue % 360, 130, 190))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc(kinds=("photo", "photo")) -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-net", name="Edit pixel-model net",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i, kind in enumerate(kinds, start=1):
        ext = "jpg" if kind == "photo" else "mp4"
        item = m.Item(
            id=f"n{i}", kind=kind, created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/n{i}.{ext}",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        )
        if kind == "video":
            item.duration_ms = 4_000
        doc.items.append(item)
    return doc


def _gateway(tmp_path, kinds=("photo", "photo")) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-net")
    store.save_document(_doc(kinds))
    counter = itertools.count(1)
    return EventGateway(
        store, event_root=tmp_path,
        now=_now, new_id=lambda: f"id-{next(counter)}")


def _bucket(tmp_path, kinds=("photo", "photo")) -> CullBucket:
    items = []
    for i, kind in enumerate(kinds, start=1):
        ext = "jpg" if kind == "photo" else "mp4"
        p = tmp_path / "Original Media" / f"n{i}.{ext}"
        if kind == "photo":
            _write_jpeg(p, hue=i * 67)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x00" * 64)
        items.append(CullItem(
            item_id=f"n{i}", path=p, kind=kind,
            capture_time_corrected=f"2026-04-01T08:0{i}:00"))
    return CullBucket(
        bucket_key="1|individual|net", kind="individual",
        title="pixel net", items=tuple(items),
        status=BucketStatus(
            total=len(items), kept=0, candidate=0, discarded=0,
            untouched=len(items), reviewed=False, browsed=False,
            badge=BADGE_UNTOUCHED))


def _spy_developed_pushes(monkeypatch) -> list:
    """Class-level spy on the DISPLAY SEAM — both eras (§6.1): the
    MediaCanvas preview push today, the viewport's rendered-pixmap
    push after the swap. One list collects whichever fires."""
    calls = []

    def _wrap(cls, name):
        orig = getattr(cls, name, None)
        if orig is None:
            return

        def wrapper(self, pm, *a, **k):
            calls.append(pm)
            return orig(self, pm, *a, **k)

        monkeypatch.setattr(cls, name, wrapper)

    _wrap(MediaCanvas, "set_preview_pixmap")
    try:
        from mira.ui.media.photo_viewport import PhotoViewport
        _wrap(PhotoViewport, "set_rendered_pixmap")
    except Exception:                                              # noqa: BLE001
        pass
    return calls


def _count_decodes(monkeypatch) -> list:
    """Counting wrapper over ``decode_image`` at every era's call
    site: the page module today, the prep-worker module after the
    swap. Thread-safe (list.append)."""
    decodes = []
    import mira.ui.edited.edit_page as ep
    modules = [ep]
    try:
        import mira.ui.edited.edit_prep as eprep
        modules.append(eprep)
    except ImportError:
        pass
    for mod in modules:
        orig = mod.decode_image

        def wrapper(path, *a, _orig=orig, **k):
            decodes.append(path)
            return _orig(path, *a, **k)

        monkeypatch.setattr(mod, "decode_image", wrapper)
    return decodes


def _page(qapp, gw, tmp_path, kinds=("photo", "photo")) -> EditPage:
    pg = EditPage()
    pg.load(gw, _bucket(tmp_path, kinds))
    return pg


# ── the display seam ─────────────────────────────────────────────────


def test_load_pushes_the_developed_working_view(qapp, tmp_path, monkeypatch):
    pushes = _spy_developed_pushes(monkeypatch)
    gw = _gateway(tmp_path)
    pg = _page(qapp, gw, tmp_path)
    try:
        _wait_developed(qapp, pg)
        assert _spin_until(qapp, lambda: bool(pushes)), \
            "no developed working view reached the display"
        last = pushes[-1]
        assert not last.isNull()
        # The working view: full frame at preview scale (the test JPEG
        # is below PREVIEW_MAX_WIDTH, so dims pass through 1:1).
        assert last.width() == 320 and last.height() == 214
        assert last.width() <= PREVIEW_MAX_WIDTH
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


def test_same_path_renavigation_skips_redecode(qapp, tmp_path, monkeypatch):
    decodes = _count_decodes(monkeypatch)
    gw = _gateway(tmp_path)
    pg = _page(qapp, gw, tmp_path)
    try:
        _wait_developed(qapp, pg)
        assert len(decodes) == 1            # the landing decode
        pg._show(0)                          # same photo again
        _wait_developed(qapp, pg)
        assert len(decodes) == 1            # cached — no re-decode
        pg._show(1)
        assert _spin_until(qapp, lambda: len(decodes) == 2)
        _wait_developed(qapp, pg)
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


def test_unsupported_file_degrades_gracefully(qapp, tmp_path):
    """A video item on the photo page: the surface holds no working
    copy (nothing to edit) and the page survives. (How the file is
    DISPLAYED is era-specific: canvas.set_photo today, the viewport's
    native poster path after the swap — deliberately not pinned.)"""
    gw = _gateway(tmp_path, kinds=("video",))
    pg = _page(qapp, gw, tmp_path, kinds=("video",))
    try:
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert pg._surface._full_array is None      # surface cleared
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


# ── the surface contract ─────────────────────────────────────────────


def _loaded_surface(qapp, w=1000, h=600) -> AdjustmentSurface:
    s = AdjustmentSurface()
    rng = np.random.default_rng(3)
    s.load_image(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
    return s


def test_set_state_get_state_round_trip(qapp):
    s = _loaded_surface(qapp)
    s.set_state(
        look="brighter", crop_norm=(0.25, 0.25, 0.5, 0.5),
        box_angle=3.0, style="macro", aspect_label="Original",
        rotation=90, creative_filter=None)
    st = s.get_state()
    assert st.look == "brighter"
    assert st.crop_norm == (0.25, 0.25, 0.5, 0.5)
    assert st.box_angle == 3.0
    assert st.style == "macro"
    assert st.aspect_label == "Original"
    s.deleteLater()


def test_look_change_persists_an_adjustment_row(qapp, tmp_path):
    gw = _gateway(tmp_path)
    pg = _page(qapp, gw, tmp_path)
    try:
        _wait_developed(qapp, pg)
        pg._surface.set_look("brighter")
        adj = gw.adjustment("n1")
        assert adj is not None and adj.look == "brighter"
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


def test_crop_overlay_parents_on_the_photo_area(qapp, tmp_path):
    gw = _gateway(tmp_path)
    pg = _page(qapp, gw, tmp_path)
    try:
        _wait_developed(qapp, pg)
        surface = pg._surface
        overlay = surface._crop_overlay
        assert overlay is not None
        assert overlay.parent() is surface.canvas().photo_area_widget()
        surface._sync_crop_overlay_geometry()       # never raises
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


def test_render_full_pixmap_is_full_res_crop_baked_and_pure(
        qapp, monkeypatch):
    pushes = _spy_developed_pushes(monkeypatch)
    s = _loaded_surface(qapp, w=1000, h=600)
    s.set_state(
        look="natural", crop_norm=(0.25, 0.25, 0.5, 0.5), box_angle=0.0,
        style="general", aspect_label="Original", rotation=0)
    before_state = s.get_state()
    before_pushes = len(pushes)
    pm = s.render_full_pixmap()
    assert pm is not None and not pm.isNull()
    assert (pm.width(), pm.height()) == (500, 300)   # crop of FULL res
    assert s.get_state() == before_state             # pure read
    assert len(pushes) == before_pushes              # display untouched
    s.deleteLater()


def test_toggle_crop_preview_renders_full_then_restores(
        qapp, tmp_path, monkeypatch):
    pushes = _spy_developed_pushes(monkeypatch)
    gw = _gateway(tmp_path)
    pg = _page(qapp, gw, tmp_path)
    try:
        _wait_developed(qapp, pg)
        pg._surface.set_state(
            look="natural", crop_norm=(0.0, 0.0, 0.5, 0.5),
            box_angle=0.0, style="general", aspect_label="Original",
            rotation=0)
        pg._preview_toggle.click()                   # Toggle-Crop ON
        on_pm = pushes[-1]
        assert (on_pm.width(), on_pm.height()) == (160, 107)
        pg._preview_toggle.click()                   # OFF → working view
        off_pm = pushes[-1]
        assert (off_pm.width(), off_pm.height()) == (320, 214)
    finally:
        pg.shutdown()                    # the defined lifecycle end (6b)
        pg.deleteLater()
        gw.close()


def test_compare_never_applies_tone(qapp, monkeypatch):
    import mira.ui.edited.adjustment_surface as asur
    applied = []
    orig = asur.apply_params
    monkeypatch.setattr(
        asur, "apply_params", lambda a, p: (applied.append(1), orig(a, p))[1])
    s = _loaded_surface(qapp)
    s.set_state(
        look="brighter", crop_norm=None, box_angle=0.0,
        style="general", aspect_label="Original", rotation=0)
    applied.clear()
    s._on_compare_toggled(True)                      # Compare ON
    assert applied == []                             # the original, untoned
    s._on_compare_toggled(False)
    s.deleteLater()


def test_downsample_semantics_survive_reimplementation(qapp):
    """The slow implementation is a §6 named kill; the SEMANTICS are
    pinned: bound the width, keep aspect, keep dtype, pass small
    frames through untouched."""
    big = np.zeros((1000, 2000, 3), dtype=np.uint8)
    out = _downsample(big, 1280)
    assert out.shape[1] == 1280
    assert abs(out.shape[0] - 640) <= 2              # aspect preserved
    assert out.dtype == np.uint8
    small = np.zeros((214, 320, 3), dtype=np.uint8)
    assert _downsample(small, 1280).shape == small.shape
