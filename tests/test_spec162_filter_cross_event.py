"""spec/162 §8 Round 3a — cross-event LineageFilter + FilterBar.

Pins the four cross-event dimensions:

* :class:`LineageFilter` now carries ``cameras`` / ``lenses`` /
  ``date_from`` / ``date_to`` / ``places`` fields. Each defaults to a
  match-anything state so event-scope callers stay unaffected.
* :class:`FilterBar` gains a ``scope`` property. At event scope the
  four cross-event widgets are hidden; at cross-event scope they
  render alongside the existing five.

Places is intentionally not yet wired at the widget layer (production
lineage rows don't carry a per-row place attribution) — the field on
LineageFilter is ready so a future row-enrichment lands the widget
without predicate churn.
"""
from __future__ import annotations

from datetime import date

import pytest

from mira.ui.exported.filter_bar import (
    SCOPE_CROSS_EVENT,
    SCOPE_EVENT,
    FilterBar,
)
from mira.ui.exported.filter_popup import LineageFilter


class _Row:
    """Duck-typed enriched lineage row — the cross-event
    :class:`DCDetailPage` will construct rows with these attributes
    joined from the source item + TripDay."""

    def __init__(
        self, *, camera=None, lens_model=None,
        capture_date=None, place=None,
        stars=None, color_label=None, flag=False, to_delete=False,
    ):
        self.camera = camera
        self.lens_model = lens_model
        self.capture_date = capture_date
        self.place = place
        self.stars = stars
        self.color_label = color_label
        self.flag = flag
        self.to_delete = to_delete


# ── LineageFilter — cross-event predicate unit tests ────────────────


def test_default_cross_event_fields_match_anything():
    f = LineageFilter()
    assert f.is_active() is False
    assert f.matches(_Row()) is True
    assert f.matches(_Row(camera="G9", lens_model="12-35",
                          capture_date="2026-04-01", place="Zambia")) is True


def test_cameras_multi_select_membership():
    f = LineageFilter(cameras={"G9", "R5"})
    assert f.is_active() is True
    assert f.matches(_Row(camera="G9")) is True
    assert f.matches(_Row(camera="R5")) is True
    assert f.matches(_Row(camera="A7IV")) is False
    # Rows with no camera fail when at least one is required — mirrors
    # the existing empty-attribute semantics on colour_labels.
    assert f.matches(_Row(camera=None)) is False


def test_lenses_multi_select_membership():
    f = LineageFilter(lenses={"12-35", "35 1.4"})
    assert f.matches(_Row(lens_model="12-35")) is True
    assert f.matches(_Row(lens_model="35 1.4")) is True
    assert f.matches(_Row(lens_model="50 1.8")) is False
    assert f.matches(_Row(lens_model=None)) is False


def test_date_from_only():
    f = LineageFilter(date_from=date(2026, 4, 1))
    assert f.is_active() is True
    assert f.matches(_Row(capture_date="2026-04-01")) is True
    assert f.matches(_Row(capture_date="2026-06-30")) is True
    assert f.matches(_Row(capture_date="2026-03-31")) is False
    assert f.matches(_Row(capture_date=None)) is False


def test_date_to_only():
    f = LineageFilter(date_to=date(2026, 4, 30))
    assert f.matches(_Row(capture_date="2026-04-30")) is True
    assert f.matches(_Row(capture_date="2026-01-01")) is True
    assert f.matches(_Row(capture_date="2026-05-01")) is False


def test_date_range_inclusive_both_ends():
    f = LineageFilter(
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
    )
    assert f.matches(_Row(capture_date="2026-04-15")) is True
    assert f.matches(_Row(capture_date="2026-04-01")) is True
    assert f.matches(_Row(capture_date="2026-04-30")) is True
    assert f.matches(_Row(capture_date="2026-03-31")) is False
    assert f.matches(_Row(capture_date="2026-05-01")) is False


def test_date_accepts_datetime_and_string():
    from datetime import datetime
    f = LineageFilter(date_from=date(2026, 4, 1),
                      date_to=date(2026, 4, 1))
    assert f.matches(_Row(capture_date=date(2026, 4, 1))) is True
    assert f.matches(_Row(
        capture_date=datetime(2026, 4, 1, 8, 0, 0))) is True
    assert f.matches(_Row(capture_date="2026-04-01T08:00:00")) is True
    assert f.matches(_Row(capture_date="not-a-date")) is False


def test_places_multi_select_membership():
    f = LineageFilter(places={"Zambia", "Namibia"})
    assert f.matches(_Row(place="Zambia")) is True
    assert f.matches(_Row(place="Botswana")) is False
    assert f.matches(_Row(place=None)) is False


def test_cross_event_predicate_is_conjunctive_with_event_knobs():
    """A row must pass EVERY active knob — event and cross-event
    together."""
    f = LineageFilter(
        min_stars=4,
        cameras={"G9"},
        date_from=date(2026, 4, 1),
    )
    assert f.matches(_Row(
        stars=5, camera="G9", capture_date="2026-04-15")) is True
    # Fail min_stars.
    assert f.matches(_Row(
        stars=3, camera="G9", capture_date="2026-04-15")) is False
    # Fail camera.
    assert f.matches(_Row(
        stars=5, camera="R5", capture_date="2026-04-15")) is False
    # Fail date.
    assert f.matches(_Row(
        stars=5, camera="G9", capture_date="2026-03-31")) is False


# ── FilterBar — scope property gates the cross-event widgets ────────


def test_filter_bar_defaults_to_event_scope(qapp):
    bar = FilterBar()
    assert bar.scope == SCOPE_EVENT
    # The four cross-event group boxes exist but are not visible at
    # event scope.
    assert bar._camera_box.isVisibleTo(bar) is False
    assert bar._lens_box.isVisibleTo(bar) is False
    assert bar._date_box.isVisibleTo(bar) is False


def test_filter_bar_cross_event_scope_reveals_new_widgets(qapp):
    bar = FilterBar(scope=SCOPE_CROSS_EVENT)
    assert bar.scope == SCOPE_CROSS_EVENT
    # The 3 built-out cross-event group boxes must be visible.
    assert bar._camera_box.isVisibleTo(bar) is True
    assert bar._lens_box.isVisibleTo(bar) is True
    assert bar._date_box.isVisibleTo(bar) is True


def test_filter_bar_scope_switch_toggles_widget_visibility(qapp):
    bar = FilterBar()
    assert bar._camera_box.isVisibleTo(bar) is False
    bar.setScope(SCOPE_CROSS_EVENT)
    assert bar._camera_box.isVisibleTo(bar) is True
    bar.setScope(SCOPE_EVENT)
    assert bar._camera_box.isVisibleTo(bar) is False


def test_filter_bar_set_available_cameras_populates_combo(qapp):
    bar = FilterBar(scope=SCOPE_CROSS_EVENT)
    bar.set_available_cameras(["G9", "R5", "A7IV"])
    # The combo model carries one row per camera.
    assert bar._camera_combo._model.rowCount() == 3


def test_filter_bar_set_filter_round_trips_cross_event_fields(qapp):
    bar = FilterBar(scope=SCOPE_CROSS_EVENT)
    bar.set_available_cameras(["G9", "R5"])
    bar.set_available_lenses(["12-35", "35 1.4"])
    pushed = LineageFilter(
        cameras={"G9"},
        lenses={"12-35"},
        date_from=date(2026, 4, 1),
        date_to=date(2026, 4, 30),
    )
    received = []
    bar.filter_changed.connect(received.append)
    bar.set_filter(pushed)
    # set_filter is programmatic — no signal fired.
    assert received == []
    cur = bar.filter()
    assert cur.cameras == {"G9"}
    assert cur.lenses == {"12-35"}
    assert cur.date_from == date(2026, 4, 1)
    assert cur.date_to == date(2026, 4, 30)


def test_filter_bar_reset_clears_cross_event_fields(qapp):
    bar = FilterBar(scope=SCOPE_CROSS_EVENT)
    bar.set_available_cameras(["G9"])
    bar.set_filter(LineageFilter(cameras={"G9"}))
    bar.reset()
    cur = bar.filter()
    assert cur.cameras == set()
    assert cur.is_active() is False
