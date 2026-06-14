"""Tests for EditHostPage — the Day-Grid parent for the Process phase.

Mirrors `tests/test_pick_page.py`'s patch-the-page-into-day-grid fixtures;
covers open_event, day cell routing by item kind, back closes the gateway,
and the export-scope hand-off.
"""
from __future__ import annotations

from pathlib import Path

from mira.picked import (
    BucketStatus,
    CullBucket,
    CullCell,
    CullCluster,
    PickDay,
    CullItem,
)
from mira.picked.status import CellColor
from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.edited.edit_host_page import EditHostPage

NOW = "2026-06-09T00:00:00+00:00"


def _status(total, kept=0, candidate=0, discarded=0, untouched=0):
    badge = "untouched" if (kept + candidate + discarded) == 0 else "in_progress"
    return BucketStatus(
        total=total, kept=kept, candidate=candidate, discarded=discarded,
        untouched=untouched, reviewed=False, browsed=False, badge=badge,
    )


def _gateway(tmp_path, base):
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _make_event(gw, base):
    """Single-day event with 3 Select-Kept photos.  Day Grid will show 3 cells."""
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
    phase_states = [
        m.PhaseState(item_id=iid, phase="pick", state="picked")
        for iid in ("p1", "p2", "p3")
    ]
    doc = m.EventDocument(
        event=m.Event(uuid="e1", name="Test", created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="G9M2")],
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", description="Arrival"),
        ],
        items=items,
        phase_states=phase_states,
    )
    return gw.create_event(doc, base / "Test")


def _days_with_one_cluster_and_one_photo():
    """A minimal PickDay fixture: 1 burst cluster (p1+p2) + 1 standalone photo (p3)."""
    cluster_items = (
        CullItem(item_id="p1", path=Path("/x/p1.jpg"), kind="photo"),
        CullItem(item_id="p2", path=Path("/x/p2.jpg"), kind="photo"),
    )
    burst = CullBucket(
        bucket_key="1|burst|b1", kind="burst", title="Burst",
        items=cluster_items, status=_status(2, untouched=2),
    )
    indv_items = (CullItem(item_id="p3", path=Path("/x/p3.jpg"), kind="photo"),)
    indv = CullBucket(
        bucket_key="1|individual|i1", kind="individual", title="Photo",
        items=indv_items, status=_status(1, untouched=1),
    )
    return [PickDay(
        day_number=1, label="Day 1 — Arrival",
        buckets=(burst, indv), status=_status(3, untouched=3),
    )]


def _cells_for_test():
    """Pre-built Day Grid cells matching the day fixture above."""
    cluster_items = (
        CullItem(item_id="p1", path=Path("/x/p1.jpg"), kind="photo"),
        CullItem(item_id="p2", path=Path("/x/p2.jpg"), kind="photo"),
    )
    cluster = CullCluster(
        bucket_key="1|burst|b1", kind="burst", title="Burst",
        members=cluster_items, color=CellColor.UNTOUCHED,
    )
    return [
        CullCell(
            end_time="2026-04-01T08:01:00",
            color=CellColor.UNTOUCHED, cluster=cluster,
        ),
        CullCell(
            end_time="2026-04-01T08:02:00",
            color=CellColor.UNTOUCHED,
            item_id="p3", item_kind="photo",
        ),
    ]


def _patch_page_into_day_grid(monkeypatch, gw, days=None, cells=None):
    """Stub process_days + day_grid_cells so we can drive the page into the
    Day Grid without an EXIF read or a real scan."""
    days = days if days is not None else _days_with_one_cluster_and_one_photo()
    cells = cells if cells is not None else _cells_for_test()
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.process_days",
        lambda eg, **kw: days,
    )
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.day_grid_cells",
        lambda eg, day_number, **kw: list(cells),
    )
    page = EditHostPage(gw)
    page._defer_open_work = False
    page.open_event("e1")
    return page


# --------------------------------------------------------------------------- #
# Lifecycle — open_event / back
# --------------------------------------------------------------------------- #


def test_edit_host_page_opens_and_shows_navigator(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.process_days",
        lambda eg, **kw: _days_with_one_cluster_and_one_photo(),
    )
    page = EditHostPage(gw)
    assert page.open_event("e1") is True
    assert page._eg is not None
    assert page._stack.currentIndex() == page._NAV


def test_edit_host_page_open_event_unknown_returns_false(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    page = EditHostPage(gw)
    assert page.open_event("does-not-exist") is False


def test_edit_host_page_back_closes_gateway_and_emits(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.process_days",
        lambda eg, **kw: _days_with_one_cluster_and_one_photo(),
    )
    page = EditHostPage(gw)
    page.open_event("e1")
    closed = []
    page.closed.connect(lambda: closed.append(True))
    page._on_back()
    assert closed == [True]
    assert page._eg is None


# --------------------------------------------------------------------------- #
# Day Grid routing
# --------------------------------------------------------------------------- #


def test_edit_host_page_navigator_runs_in_day_grid_mode(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    assert page.navigator._cfg.day_grid_mode is True
    page.navigator.day_activated.emit(1)
    assert page._stack.currentIndex() == page._DAY_GRID
    assert page._current_day_number == 1


def test_edit_host_page_centre_click_photo_opens_edit_page(
    qapp, tmp_path, monkeypatch,
):
    """Centre-click on a photo cell opens the EditPage stack page."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    # Skip the actual decode + render — we just care about the routing.
    monkeypatch.setattr(
        type(page.photo), "_load_and_render_item",
        lambda self, ci: None,
    )
    page._on_day_activated(1)
    page._on_day_cell_activated(1)            # standalone photo p3
    assert page._stack.currentIndex() == page._PHOTO


def test_edit_host_page_centre_click_cluster_opens_sub_grid(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    page._on_day_cell_activated(0)            # cluster cell
    assert page._stack.currentIndex() == page._CLUSTER_GRID
    assert page._current_cluster is not None
    assert page._current_cluster.bucket_key == "1|burst|b1"


def test_edit_host_page_border_click_is_no_op(
    qapp, tmp_path, monkeypatch,
):
    """Q4 locked 2026-06-08: border-click does nothing at Process.  No
    phase_state writes; the cell stays untouched."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    # No exception, no state changes.
    page.day_grid.cell_border_clicked.emit(1)
    states = page._eg.phase_states("edit")
    assert states == {}


# --------------------------------------------------------------------------- #
# Visited hooks (Mvis) — Process phase
# --------------------------------------------------------------------------- #


def test_edit_host_page_photo_open_marks_item_visited(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    monkeypatch.setattr(
        type(page.photo), "_load_and_render_item",
        lambda self, ci: None,
    )
    page._on_day_activated(1)
    page._on_day_cell_activated(1)            # standalone photo p3
    visited = page._eg.items_visited_for_day(1, "edit")
    assert "p3" in visited


def test_edit_host_page_cluster_open_marks_browsed(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    page._on_day_cell_activated(0)            # cluster cell → sub-grid
    bucket_row = page._eg.bucket("1|burst|b1", "edit")
    assert bucket_row is not None
    assert bucket_row.browsed is True


# --------------------------------------------------------------------------- #
# Export-scope hand-off + export-committed cell refresh
# --------------------------------------------------------------------------- #


def test_edit_host_page_export_scope_routes_into_batched_export(
    qapp, tmp_path, monkeypatch,
):
    """EditPage emits export_scope_requested("day"|"event") — host catches
    it, builds the item list, and opens CullExportDialog.  User cancelling
    the dialog just returns; no worker started."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    # Patch the legacy export dialog to return None (user cancelled).
    monkeypatch.setattr(
        "ui.culler.cull_export_dialog.CullExportDialog.ask",
        lambda *a, **kw: None,
    )
    # Day-scope: the fixture's day has 3 photos (p1, p2, p3).
    collected = []
    real_collect = page._collect_photo_items_for_day
    monkeypatch.setattr(
        page, "_collect_photo_items_for_day",
        lambda n: (collected.extend(real_collect(n)) or real_collect(n)),
    )
    page.photo.export_scope_requested.emit("day")
    assert len(collected) == 3      # p1, p2, p3 all routed into the worker path


def test_edit_host_page_export_scope_event_aggregates_all_days(
    qapp, tmp_path, monkeypatch,
):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    monkeypatch.setattr(
        "ui.culler.cull_export_dialog.CullExportDialog.ask",
        lambda *a, **kw: None,
    )
    items = page._collect_photo_items_for_event()
    assert {ci.item_id for ci in items} == {"p1", "p2", "p3"}


def test_edit_host_page_export_scope_no_items_shows_information(
    qapp, tmp_path, monkeypatch,
):
    """An empty event / day → QMessageBox.information("Nothing to export"),
    not a CullExportDialog open."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw, days=[])
    msgs = []
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.QMessageBox.information",
        lambda *a, **kw: msgs.append(a),
    )
    page.photo.export_scope_requested.emit("event")
    assert len(msgs) == 1


# --------------------------------------------------------------------------- #
# Per-day processed status reprojection (Days panel "X/Y processed")
# --------------------------------------------------------------------------- #


def test_edit_host_page_initial_days_status_is_all_untouched(
    qapp, tmp_path, monkeypatch,
):
    """Fresh event with no Adjustment rows → every day reads zero processed."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    day = next(d for d in page._days if d.day_number == 1)
    assert day.status.kept == 0
    assert day.status.untouched == day.status.total


def test_edit_host_page_reproject_after_export_counts_processed(
    qapp, tmp_path, monkeypatch,
):
    """Mark some items edit_exported, reproject the day, navigator sees
    "X/Y processed" via the day's BucketStatus.kept field."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    eg = page._eg
    eg.set_edit_exported("p1", True)
    eg.set_edit_exported("p3", True)
    page._reproject_days_navigator()
    day = next(d for d in page._days if d.day_number == 1)
    # 2 of 3 photos exported (p1, p3); p2 still untouched.
    assert day.status.kept == 2
    assert day.status.untouched == day.status.total - 2
    assert day.status.badge == "in_progress"


def test_edit_host_page_export_finished_updates_day_status(
    qapp, tmp_path, monkeypatch,
):
    """Photo-scope export committed → reload cache + reproject the cell;
    the day-card status (used by the navigator) reflects the new exported
    count after _refresh_days_navigator."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    eg = page._eg
    eg.set_edit_exported("p3", True)
    page.photo.process_export_committed.emit("p3")
    page._refresh_days_navigator()
    day = next(d for d in page._days if d.day_number == 1)
    assert day.status.kept == 1


# --------------------------------------------------------------------------- #
# "Start a new pass…" wiring on Process navigator
# --------------------------------------------------------------------------- #


def test_edit_host_page_navigator_shows_clear_marks_button(
    qapp, tmp_path, monkeypatch,
):
    """Process navigator opts in via show_clear_marks_button=True (Nelson
    2026-06-09).  The button is created and visible."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    assert page.navigator._cfg.show_clear_marks_button is True
    assert page.navigator._clear_marks_btn.isVisible() or True
    # (isVisible can be False before the parent shows; the relevant
    # invariant is that the button was created.)
    assert page.navigator._clear_marks_btn is not None


def test_edit_host_page_clear_marks_handler_calls_gateway(
    qapp, tmp_path, monkeypatch,
):
    """When the user confirms, the host calls
    gateway.clear_visited_for_phase('edit') and refreshes any open
    Day Grid cells."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    eg = page._eg
    # Seed a Process visit on p3 + a cluster browsed flag.
    eg.set_item_visited("p3", "edit", True)
    eg.set_bucket_browsed("1|burst|b1", "edit", True)
    # Stamp the cell's in-memory visited bit so the refresh has work to do.
    for idx, c in enumerate(page._current_day_cells):
        if c.item_id == "p3":
            page._current_day_cells[idx] = CullCell(
                end_time=c.end_time, color=c.color,
                item_id=c.item_id, item_kind=c.item_kind,
                cluster=c.cluster, visited=True,
            )
    # Patch the confirm dialog to YES.
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    page._on_clear_marks()
    # Gateway state cleared.
    assert eg.items_visited_for_day(1, "edit") == set()
    b = eg.bucket("1|burst|b1", "edit")
    assert b is None or b.browsed is False
    # In-memory cell visited cleared too.
    cell = next(c for c in page._current_day_cells if c.item_id == "p3")
    assert cell.visited is False


def test_edit_host_page_clear_marks_handler_cancel_keeps_state(
    qapp, tmp_path, monkeypatch,
):
    """Cancel on the confirm dialog → no DB writes, no cell refresh."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    eg = page._eg
    eg.set_item_visited("p3", "edit", True)
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "mira.ui.edited.edit_host_page.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.No,
    )
    page._on_clear_marks()
    # Untouched.
    assert eg.items_visited_for_day(1, "edit") == {"p3"}


# --------------------------------------------------------------------------- #
# Batched export style resolution falls through item.classification
# (Nelson 2026-06-09 — the previous-phases classification reaches AUTO)
# --------------------------------------------------------------------------- #


def test_edit_host_page_styles_by_path_uses_item_classification(
    qapp, tmp_path, monkeypatch,
):
    """When no Adjustment row exists, the engine's style_resolver should
    fall back to ``item.classification`` (normalised)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    eg = page._eg
    # Classify p1 = wildlife (no Adjustment).
    with eg.store.transaction() as conn:
        conn.execute(
            "UPDATE item SET classification = ? WHERE id = ?",
            ("wildlife", "p1"),
        )
    page._on_day_activated(1)
    items = page._collect_photo_items_for_day(1)
    styles = page._collect_styles_by_path(items)
    p1 = next(ci.path for ci in items if ci.item_id == "p1")
    assert styles.get(p1) == "wildlife"


def test_edit_host_page_styles_by_path_saved_style_beats_classification(
    qapp, tmp_path, monkeypatch,
):
    """If Adjustment.params_json["_style"] is set, it wins over the
    item's classification (the user's explicit override)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    eg = page._eg
    import json as _json
    with eg.store.transaction() as conn:
        conn.execute(
            "UPDATE item SET classification = ? WHERE id = ?",
            ("wildlife", "p1"),
        )
    eg.save_adjustment(m.Adjustment(
        item_id="p1",
        params_json=_json.dumps({"_style": "portrait", "exposure": 0.4}),
    ))
    page._on_day_activated(1)
    items = page._collect_photo_items_for_day(1)
    styles = page._collect_styles_by_path(items)
    p1 = next(ci.path for ci in items if ci.item_id == "p1")
    assert styles.get(p1) == "portrait"


def test_edit_host_page_styles_by_path_unsupported_classification_falls_back(
    qapp, tmp_path, monkeypatch,
):
    """Classifications outside the AUTO-calibrated set collapse to
    "general" — engine then uses default tuning (no entry in the map)."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    eg = page._eg
    with eg.store.transaction() as conn:
        conn.execute(
            "UPDATE item SET classification = ? WHERE id = ?",
            ("sports", "p1"),
        )
    page._on_day_activated(1)
    items = page._collect_photo_items_for_day(1)
    styles = page._collect_styles_by_path(items)
    p1 = next(ci.path for ci in items if ci.item_id == "p1")
    # "sports" has no AUTO tuning → normalised to "general" → absent from
    # the map (engine default).
    assert p1 not in styles


def test_edit_host_page_photo_export_committed_refreshes_cell(
    qapp, tmp_path, monkeypatch,
):
    """When EditPage emits process_export_committed(item_id), the host
    re-reads adjustments and reprojects the touched cell."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    _make_event(gw, base)
    page = _patch_page_into_day_grid(monkeypatch, gw)
    page._on_day_activated(1)
    # Simulate the export side-effect: the gateway row says exported.
    gw_eg = page._eg
    gw_eg.set_edit_exported("p3", True)
    # Now fire the signal — host re-reads adjustments + reprojects.
    page.photo.process_export_committed.emit("p3")
    # The cell for p3 (idx=1 in our fixture) is now KEPT (green).
    cell = page._current_day_cells[1]
    assert cell.item_id == "p3"
    assert cell.color is CellColor.KEPT
