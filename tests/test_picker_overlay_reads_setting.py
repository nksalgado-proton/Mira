"""spec/134 — the Picker / Editor viewer overlay reads
``viewer_overlay_fields`` from settings and reflects it on every
landing. Both surfaces share one helper, so the test asserts parity.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.cut_overlay import (
    FIELD_HOW1, FIELD_HOW2, FIELD_WHEN, FIELD_WHERE,
    FrameProvenance,
)
from mira.ui.media.viewer_overlay import (
    compose_viewer_overlay_html,
    viewer_overlay_fields_from_settings,
)


class _StubEg:
    """Minimal gateway stand-in. Returns the FrameProvenance the test
    pre-populates so the orchestration helper can be exercised without
    a real event.db."""

    def __init__(self, prov: FrameProvenance) -> None:
        self._prov = prov
        self.calls: list = []

    def item_provenance(self, item_id: str) -> FrameProvenance:
        self.calls.append(item_id)
        return self._prov


# ── viewer_overlay_fields_from_settings ───────────────────────────────


def test_default_setting_is_how2(monkeypatch, tmp_path):
    """Fresh repo (no file) → defaults seed → ``["how2"]``."""
    from mira.settings.repo import SettingsRepo
    monkeypatch.setattr(
        "mira.ui.media.viewer_overlay.SettingsRepo",
        lambda: SettingsRepo(path=tmp_path / "fresh.json"),
        raising=False,
    )
    # Module imports SettingsRepo lazily inside the function; patch the
    # canonical import path so both paths return the tmp repo.
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: tmp_path)
    assert viewer_overlay_fields_from_settings() == [FIELD_HOW2]


def test_setting_changes_propagate(monkeypatch, tmp_path):
    """Saving a different selection — the helper picks it up on the
    very next call (live re-read; no caching)."""
    from mira.settings.repo import SettingsRepo
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: tmp_path)
    repo = SettingsRepo(path=tmp_path / "settings.rebuild.json")
    repo.update(viewer_overlay_fields=[FIELD_WHEN, FIELD_WHERE,
                                       FIELD_HOW1, FIELD_HOW2])
    assert viewer_overlay_fields_from_settings() == [
        FIELD_WHEN, FIELD_WHERE, FIELD_HOW1, FIELD_HOW2]
    repo.update(viewer_overlay_fields=[])
    assert viewer_overlay_fields_from_settings() == []


# ── compose_viewer_overlay_html shared helper ─────────────────────────


def _prov_full() -> FrameProvenance:
    return FrameProvenance(
        when="2026-04-01T08:00:00",
        city="Arenal",
        country="Costa Rica",
        camera="Panasonic G9 II",
        lens_model="LEICA 12-60",
        flash_fired=False,
        aperture_f=2.8,
        shutter_speed_s=0.004,
        iso=400,
        focal_length_mm=85.0,
    )


def test_compose_default_exposure_only():
    """spec/134 default — ``["how2"]`` → only the exposure line."""
    eg = _StubEg(_prov_full())
    html = compose_viewer_overlay_html(eg, "i1", fields=[FIELD_HOW2])
    assert html == "85mm · f/2.8 · 1/250 · ISO 400"


def test_compose_all_fields_joins_on_one_line():
    """When + where + camera + exposure → ONE line, the field groups
    joined by the heavier ``•`` separator (the pill is a single strip
    along the photo's bottom edge; within-group items keep the ``·``)."""
    from mira.ui.media.viewer_overlay import _FIELD_SEPARATOR
    eg = _StubEg(_prov_full())
    html = compose_viewer_overlay_html(
        eg, "i1",
        fields=[FIELD_WHEN, FIELD_WHERE, FIELD_HOW1, FIELD_HOW2])
    assert html == _FIELD_SEPARATOR.join([
        "2026-04-01T08:00:00",
        "Arenal, Costa Rica",
        "Panasonic G9 II · LEICA 12-60 · no flash",
        "85mm · f/2.8 · 1/250 · ISO 400",
    ])
    assert "<br>" not in html


def test_compose_empty_selection_returns_empty():
    """Spec acceptance — empty selection hides the overlay (the
    PhotoExposureOverlay treats `""` as "hide")."""
    eg = _StubEg(_prov_full())
    assert compose_viewer_overlay_html(eg, "i1", fields=[]) == ""


def test_master_flag_off_hides_overlay(monkeypatch):
    """spec/134 — the ``show_photo_overlays`` master gate hides the pill
    on the settings-driven path regardless of the field selection."""
    import mira.ui.media.viewer_overlay as vo
    monkeypatch.setattr(vo, "viewer_overlay_fields_from_settings",
                        lambda: [FIELD_WHEN, FIELD_HOW2])
    eg = _StubEg(_prov_full())
    monkeypatch.setattr(vo, "photo_overlays_enabled", lambda: True)
    assert vo.compose_viewer_overlay_html(eg, "i1") != ""
    monkeypatch.setattr(vo, "photo_overlays_enabled", lambda: False)
    assert vo.compose_viewer_overlay_html(eg, "i1") == ""


def test_explicit_fields_bypass_master_flag(monkeypatch):
    """An explicit ``fields=`` (callers / tests) bypasses the settings
    master gate so unit tests stay deterministic."""
    import mira.ui.media.viewer_overlay as vo
    monkeypatch.setattr(vo, "photo_overlays_enabled", lambda: False)
    eg = _StubEg(_prov_full())
    assert vo.compose_viewer_overlay_html(
        eg, "i1", fields=[FIELD_HOW2]) != ""


def test_compose_no_gateway_returns_empty():
    """Defensive — paths-only / smoke mode (no gateway) yields an
    empty provenance, which yields ``""`` (overlay hidden)."""
    assert compose_viewer_overlay_html(None, None,
                                       fields=[FIELD_HOW2]) == ""


def test_compose_gateway_failure_returns_empty():
    """If ``item_provenance`` raises, the helper logs + returns ``""``
    (the overlay hides; the rest of the page keeps working)."""
    class _BadEg:
        def item_provenance(self, _id):
            raise RuntimeError("simulated lookup failure")
    assert compose_viewer_overlay_html(
        _BadEg(), "i1", fields=[FIELD_HOW2]) == ""


def test_compose_reads_from_settings_when_fields_omitted(
    monkeypatch, tmp_path,
):
    """When the caller doesn't pass ``fields``, the helper reads the
    setting (the live-update path the Settings dialog relies on)."""
    from mira.settings.repo import SettingsRepo
    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: tmp_path)
    repo = SettingsRepo(path=tmp_path / "settings.rebuild.json")
    repo.update(viewer_overlay_fields=[FIELD_WHEN])
    eg = _StubEg(_prov_full())
    assert compose_viewer_overlay_html(eg, "i1") == "2026-04-01T08:00:00"


# ── Picker + Editor parity: both surfaces wire the same helper ─────────


def test_picker_has_overlay_widget(qapp, tmp_path):
    """The Picker still owns its PhotoExposureOverlay; spec/134
    rewired the *content* generation, not the widget. Pin the widget
    survives."""
    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.media.photo_overlay import PhotoExposureOverlay
    from mira.ui.pages.picker_page import PickerPage
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    page = PickerPage(gw)
    try:
        assert isinstance(page._expo_overlay, PhotoExposureOverlay)
    finally:
        page.deleteLater()


def test_editor_has_overlay_widget(qapp, tmp_path):
    """spec/134 — the Editor (AdjustmentSurface) gains the same
    overlay pill as the Picker, so both surfaces look identical when
    the user toggles fields."""
    from mira.ui.edited.adjustment_surface import AdjustmentSurface
    from mira.ui.media.photo_overlay import PhotoExposureOverlay
    surface = AdjustmentSurface()
    try:
        assert isinstance(surface._viewer_overlay, PhotoExposureOverlay)
    finally:
        surface.deleteLater()


def test_editor_set_viewer_overlay_html_drives_the_pill(qapp, tmp_path):
    """The Editor host pushes content via
    :meth:`AdjustmentSurface.set_viewer_overlay_html`; that proxies to
    the overlay widget's ``set_html``. Empty string → hidden."""
    from mira.ui.edited.adjustment_surface import AdjustmentSurface
    surface = AdjustmentSurface()
    try:
        surface.set_viewer_overlay_html("hello<br>world")
        assert surface._viewer_overlay.text() == "hello<br>world"
        assert surface._viewer_overlay.isHidden() is False
        surface.set_viewer_overlay_html("")
        assert surface._viewer_overlay.text() == ""
        assert surface._viewer_overlay.isHidden() is True
    finally:
        surface.deleteLater()


def test_picker_compose_helper_returns_default_html(
    qapp, tmp_path, monkeypatch,
):
    """The Picker's ``_compose_viewer_overlay_html`` routes through
    the shared module helper. Stub the gateway's item_provenance to
    return a known FrameProvenance and confirm the default
    (`["how2"]`) yields the exposure line."""
    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.pages.picker_page import PickerPage

    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: tmp_path)
    settings = SettingsRepo(path=tmp_path / "settings.rebuild.json")
    settings.save(settings.load())              # seed the file
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    page = PickerPage(gw)
    try:
        page._eg = _StubEg(_prov_full())
        item = SimpleNamespace(item_id="i1",
                               path=Path(tmp_path) / "p.jpg",
                               kind="photo")
        html = page._compose_viewer_overlay_html(item)
        assert html == "85mm · f/2.8 · 1/250 · ISO 400"
    finally:
        page.deleteLater()


def test_picker_compose_reflects_setting_change(
    qapp, tmp_path, monkeypatch,
):
    """After flipping the setting to a different selection, the very
    next compose call reflects the new selection — the Settings
    dialog's Apply path doesn't need a relaunch."""
    from mira.gateway import EventsIndex, Gateway
    from mira.settings.repo import SettingsRepo
    from mira.ui.pages.picker_page import PickerPage

    monkeypatch.setattr(
        "mira.settings.repo.user_data_dir", lambda: tmp_path)
    settings = SettingsRepo(path=tmp_path / "settings.rebuild.json")
    settings.save(settings.load())
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    page = PickerPage(gw)
    try:
        page._eg = _StubEg(_prov_full())
        item = SimpleNamespace(item_id="i1",
                               path=Path(tmp_path) / "p.jpg",
                               kind="photo")
        # Flip to when + where (no how2).
        from mira.ui.media.viewer_overlay import _FIELD_SEPARATOR
        settings.update(viewer_overlay_fields=[FIELD_WHEN, FIELD_WHERE])
        html = page._compose_viewer_overlay_html(item)
        assert html == _FIELD_SEPARATOR.join(
            ["2026-04-01T08:00:00", "Arenal, Costa Rica"])
        # Clear it — overlay hides.
        settings.update(viewer_overlay_fields=[])
        assert page._compose_viewer_overlay_html(item) == ""
    finally:
        page.deleteLater()
