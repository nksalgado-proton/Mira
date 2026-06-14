"""Tests for the rebuilt Process editor — ``mira.ui.edited.edit_page``
(spec/32 §6.3).

Distinct from the legacy ``tests/test_edit_page.py`` (which exercises the
shipped ``ui/process/edit_page.py``).  These tests pin the data-wire contract
(gateway.adjustment / save_adjustment / set_edit_exported on per-surface-
change persistence) and the navigation contract (load(eg, bucket) takes a
CullBucket — synthetic single-item from Day-Grid centre-click OR a real cluster
sub-grid bucket; edge nav emits :attr:`navigate_at_edge` in day_grid context,
stops in cluster context).
"""
from __future__ import annotations

import json
from pathlib import Path

from mira.picked import (
    BucketStatus,
    CullBucket,
    CullItem,
)
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.edited.edit_page import EditPage

NOW = "2026-06-08T00:00:00+00:00"


def _bucket_status(total: int) -> BucketStatus:
    return BucketStatus(
        total=total, kept=0, candidate=0, discarded=0, untouched=total,
        reviewed=False, browsed=False, badge="untouched",
    )


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _make_event(gw, base):
    """Single-day event with three captured photos.  No real bytes on disk —
    tests that need a decode swap in their own ``_load_and_render_item`` stub."""
    items = [
        m.Item(
            id=iid, kind="photo", origin_relpath=f"d/{iid}.jpg",
            sha256=f"sha-{iid}", byte_size=1,
            materialized_at=NOW, materialized_phase="ingest",
            camera_id="G9M2",
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
            created_at=NOW, day_number=1, provenance="captured",
        )
        for i, iid in enumerate(("p1", "p2", "p3"))
    ]
    doc = m.EventDocument(
        event=m.Event(uuid="e1", name="Test", created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="G9M2")],
        trip_days=[m.TripDay(day_number=1, date="2026-04-01", description="Arrival")],
        items=items,
    )
    return gw.create_event(doc, base / "Test")


def _make_bucket(item_ids: tuple[str, ...], base: Path) -> CullBucket:
    items = tuple(
        CullItem(
            item_id=iid, path=base / "Test" / "d" / f"{iid}.jpg",
            kind="photo",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
            duration_ms=None,
        )
        for i, iid in enumerate(item_ids)
    )
    return CullBucket(
        bucket_key=f"1|individual|{item_ids[0]}",
        kind="individual",
        title=item_ids[0],
        items=items,
        status=_bucket_status(len(items)),
    )


def _no_render(self, ci):
    """Stub ``_load_and_render_item`` so the page exercises load() / persist
    / nav without touching the decoder or the AdjustmentSurface's render
    pipeline (which needs a real image)."""
    self._cached_path = ci.path


# --------------------------------------------------------------------------- #
# Construction + load
# --------------------------------------------------------------------------- #


def test_edit_page_load_takes_bucket(qapp, tmp_path, monkeypatch):
    """load(eg, bucket) accepts a synthetic single-item bucket and sets the
    page's internal state — same shape as PickPhotoSurface.load."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    assert page._eg is eg
    assert page._bucket is bucket
    assert page._items == list(bucket.items)
    assert page._index == 0
    assert page._nav_context == "day_grid"


def test_edit_page_load_cluster_bucket_carries_members(
    qapp, tmp_path, monkeypatch,
):
    """A cluster sub-grid passes a real multi-member bucket — page steps
    through internally; edge nav stops in cluster context."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1", "p2", "p3"), base)
    page.load(eg, bucket, nav_context="cluster", entry_override=1)
    assert page._nav_context == "cluster"
    assert page._index == 1            # entry_override honoured
    assert len(page._items) == 3


# --------------------------------------------------------------------------- #
# Persistence — every surface change kind routes to gateway.save_adjustment
# --------------------------------------------------------------------------- #


def test_edit_page_crop_change_writes_adjustment(
    qapp, tmp_path, monkeypatch,
):
    """Surface emits changed('crop') → page reads _crop_norm and saves the
    item's Adjustment row with crop_x/y/w/h."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    # Pretend the surface has a crop rect ready.
    page._surface._crop_norm = (0.1, 0.2, 0.6, 0.5)
    page._on_surface_changed("crop")
    adj = eg.adjustment("p1")
    assert adj is not None
    assert (adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h) == (0.1, 0.2, 0.6, 0.5)
    # Re-editing clears the exported flag.
    assert adj.edit_exported is False


def test_edit_page_angle_change_writes_adjustment(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    page._surface._box_angle = 12.5
    page._on_surface_changed("angle")
    adj = eg.adjustment("p1")
    assert adj.crop_angle == 12.5


def test_edit_page_aspect_change_writes_adjustment_per_item(
    qapp, tmp_path, monkeypatch,
):
    """Q1 locked: aspect is per-item via Adjustment.aspect_label."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    page._surface._aspect_label = "16:9"
    page._surface._crop_norm = None      # aspect change without an active rect
    page._on_surface_changed("aspect")
    adj = eg.adjustment("p1")
    assert adj.aspect_label == "16:9"


def test_edit_page_reset_clears_adjustment_fields(
    qapp, tmp_path, monkeypatch,
):
    """Reset wipes crop / angle / params / aspect on THIS item; the row
    persists so edit_exported can be cleared too."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    # Seed an existing adjustment so we can prove reset clears it.
    eg.save_adjustment(m.Adjustment(
        item_id="p1", crop_x=0.1, crop_y=0.1, crop_w=0.5, crop_h=0.5,
        crop_angle=5.0, params_json='{"exposure": 0.3}',
        aspect_label="16:9", edit_exported=True,
    ))
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    page._on_surface_changed("reset")
    adj = eg.adjustment("p1")
    assert adj.crop_x is None and adj.crop_w is None
    assert adj.crop_angle == 0.0
    assert adj.params_json is None
    assert adj.aspect_label is None
    assert adj.edit_exported is False


def test_edit_page_persist_current_params_writes_params_json(
    qapp, tmp_path, monkeypatch,
):
    """The debounced tone writer serialises slider values into Adjustment.params_json."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    page._persist_current_params()
    adj = eg.adjustment("p1")
    assert adj is not None
    assert adj.params_json is not None
    blob = json.loads(adj.params_json)
    # The blob carries the slider keys (exposure / contrast / etc.) plus
    # the style hint.
    assert "_style" in blob


# --------------------------------------------------------------------------- #
# Navigation — internal step + edge signals
# --------------------------------------------------------------------------- #


def test_edit_page_arrow_steps_internally_inside_cluster_bucket(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1", "p2", "p3"), base)
    page.load(eg, bucket, nav_context="cluster")
    page._on_next()
    assert page._index == 1
    page._on_next()
    assert page._index == 2


def test_edit_page_edge_emits_navigate_at_edge_in_day_grid(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)   # synthetic single-item
    page.load(eg, bucket, nav_context="day_grid")
    edges = []
    page.navigate_at_edge.connect(edges.append)
    page._on_next()
    assert edges == [+1]
    page._on_prev()
    assert edges == [+1, -1]


def test_edit_page_edge_stops_in_cluster_context(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1", "p2"), base)
    page.load(eg, bucket, nav_context="cluster")
    edges = []
    page.navigate_at_edge.connect(edges.append)
    page._index = 1
    page._on_next()   # at last; cluster context stops
    assert edges == []
    page._index = 0
    page._on_prev()   # at first; cluster context stops
    assert edges == []


# --------------------------------------------------------------------------- #
# Export scopes — photo runs local, day/event hand off to parent
# --------------------------------------------------------------------------- #


def test_edit_page_export_scope_signal_for_day_and_event(
    qapp, tmp_path, monkeypatch,
):
    """Photo scope is local (worker on the page); day / event scope is a
    signal so the parent (which holds the day / event item lists) drives."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    scopes = []
    page.export_scope_requested.connect(scopes.append)
    page.export_scope_requested.emit("day")
    page.export_scope_requested.emit("event")
    assert scopes == ["day", "event"]


# --------------------------------------------------------------------------- #
# Unpacking an Adjustment row back into the surface
# --------------------------------------------------------------------------- #


def test_edit_page_unpack_adjustment_reads_every_field(
    qapp, tmp_path, monkeypatch,
):
    """The Adjustment row → surface state translation must restore params,
    crop, angle, aspect, and style without dropping any."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    adj = m.Adjustment(
        item_id="p1",
        crop_x=0.1, crop_y=0.2, crop_w=0.5, crop_h=0.4, crop_angle=8.0,
        params_json=json.dumps({"exposure": 0.4, "_style": "landscape"}),
        aspect_label="3:2",
    )
    style, params, crop, angle, aspect = page._unpack_adjustment(adj)
    assert style == "landscape"
    assert crop == (0.1, 0.2, 0.5, 0.4)
    assert angle == 8.0
    assert aspect == "3:2"
    assert params is not None
    assert params.exposure == 0.4


def test_edit_page_unpack_adjustment_handles_none(qapp, tmp_path, monkeypatch):
    """No Adjustment row → defaults: style=general, no crop, angle=0,
    aspect from settings."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    style, params, crop, angle, aspect = page._unpack_adjustment(None)
    assert style == "general"
    assert params is None
    assert crop is None
    assert angle == 0.0
    assert aspect == page._aspect_default


def test_edit_page_unpack_adjustment_uses_default_style_when_no_saved(
    qapp, tmp_path, monkeypatch,
):
    """When the caller passes default_style (the item's classification) and
    no saved ``_style`` exists, the default carries through to the surface.
    Nelson 2026-06-09 — the classification from previous phases must reach
    AUTO."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    style, *_ = page._unpack_adjustment(None, default_style="wildlife")
    assert style == "wildlife"


def test_edit_page_unpack_adjustment_saved_style_beats_default(
    qapp, tmp_path, monkeypatch,
):
    """A saved ``_style`` on Adjustment.params_json takes precedence over
    the caller's default — the user may have overridden AUTO for THIS edit."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base)
    monkeypatch.setattr(EditPage, "_load_and_render_item", _no_render)
    page = EditPage()
    bucket = _make_bucket(("p1",), base)
    page.load(eg, bucket)
    adj = m.Adjustment(
        item_id="p1",
        params_json=json.dumps({"exposure": 0.5, "_style": "portrait"}),
    )
    style, *_ = page._unpack_adjustment(adj, default_style="wildlife")
    assert style == "portrait"


def test_edit_page_normalize_style_clamps_unsupported(
    qapp, tmp_path, monkeypatch,
):
    """Sports / street / travel etc. classifications collapse to "general"
    (no AUTO calibration yet — backlog_video_adjustment_calibration)."""
    from mira.ui.edited.edit_page import _normalize_style
    assert _normalize_style("wildlife") == "wildlife"
    assert _normalize_style("landscape") == "landscape"
    assert _normalize_style("sports") == "general"
    assert _normalize_style("street") == "general"
    assert _normalize_style("travel") == "general"
    assert _normalize_style(None) == "general"
    assert _normalize_style("") == "general"
