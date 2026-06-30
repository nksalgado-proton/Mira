"""spec/159 — the DCDetailPage's review-grid behaviours.

Pins the surface delta:

* Both border-click AND center-click on a single-version cell route
  to the review viewer (Nelson 2026-06-30 follow-up — accidental
  border-clicks used to toggle ``to_delete`` and that was too easy;
  marking now happens only inside the dialog or via the toolbar).
* The cell's badge follows ``lineage.to_delete`` regardless of how
  the flag was set.
* "Clear marks" releases ``to_delete`` on every visible row in one
  shot, persisting through the gateway.
* The toolbar's delete confirm fires
  :meth:`EventGateway.delete_marked_exported_files` once.

Loads the same lineage / file fixture the gateway tests use so the
cascade-side behaviour stays realistic (an actual ``Exported Media/``
tree exists on disk so the unlink path doesn't no-op).
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.shared.dc_detail_page import DCDetailPage

FIXED_NOW = "2026-06-30T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-d", name="DC review fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    doc.items = [
        m.Item(id="p1", kind="photo", created_at=FIXED_NOW,
               provenance="captured",
               origin_relpath="Original Media/p1.jpg", sha256="a" * 64,
               byte_size=1000, materialized_at=FIXED_NOW,
               materialized_phase="ingest",
               camera_id="G9", day_number=1,
               capture_time_raw="2026-04-01T08:00:00",
               capture_time_corrected="2026-04-01T08:00:00"),
        m.Item(id="p2", kind="photo", created_at=FIXED_NOW,
               provenance="captured",
               origin_relpath="Original Media/p2.jpg", sha256="b" * 64,
               byte_size=1000, materialized_at=FIXED_NOW,
               materialized_phase="ingest",
               camera_id="G9", day_number=1,
               capture_time_raw="2026-04-01T09:00:00",
               capture_time_corrected="2026-04-01T09:00:00"),
    ]
    doc.lineage = [
        m.Lineage(export_relpath="Exported Media/Dia 1/p1.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t1"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p2", exported_at="t2"),
    ]
    doc.adjustments = [
        m.Adjustment(item_id="p1", edit_exported=True),
        m.Adjustment(item_id="p2", edit_exported=True),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for name in ("p1.jpg", "p2.jpg"):
        (tmp_path / "Exported Media" / "Dia 1" / name).write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-d")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page(qapp, gw, monkeypatch):
    """The page is bound to a live gateway. The review viewer's
    ``exec()`` is stubbed by default so any test that triggers a click
    doesn't pop a modal; opt-in tests can read the captured calls
    through the patched method's marker list."""
    p = DCDetailPage()
    # Stub the modal open. Tests that care can read p._review_calls.
    p._review_calls = []                                       # type: ignore[attr-defined]
    monkeypatch.setattr(
        p, "_open_review_dialog_for_cell",
        lambda i: p._review_calls.append(i),                   # type: ignore[attr-defined]
    )
    p.open_pool(gw)
    yield p
    p.close_event()


def _mark(gw, page, rel: str, value: bool = True) -> None:
    """Mark a row for deletion via the only path that still does that:
    the gateway helper the review dialog routes through. Refresh so the
    page picks up the new state."""
    gw.set_lineage_to_delete(rel, value)
    page._refresh()


# ── single-version cell click routes to the review viewer ───────────


def test_center_click_emits_review_requested(page):
    received = []
    page.review_requested.connect(received.append)
    page._on_cell_activated(0)
    assert received == ["Exported Media/Dia 1/p1.jpg"]


def test_center_click_does_not_touch_to_delete(page, gw):
    """The review viewer is the only place ``to_delete`` flips now —
    opening it must NOT side-effect the flag."""
    rel = "Exported Media/Dia 1/p1.jpg"
    page._on_cell_activated(0)
    assert gw.lineage_ratings(rel).to_delete is False


def test_border_signal_routes_to_review(page):
    """Border-click on a single-version cell opens the viewer (same
    handler as center-click) — no toggle path."""
    received = []
    page.review_requested.connect(received.append)
    # Emit the grid's border signal as the live click pipeline would.
    page._grid.cell_border_clicked.emit(0)
    assert received == ["Exported Media/Dia 1/p1.jpg"]


def test_border_signal_does_not_touch_to_delete(page, gw):
    rel = "Exported Media/Dia 1/p1.jpg"
    page._grid.cell_border_clicked.emit(0)
    assert gw.lineage_ratings(rel).to_delete is False


# ── chrome reflects the marked count ────────────────────────────────


def test_delete_button_hidden_when_no_marks(page):
    assert page._delete_btn.isHidden() is True
    assert page._clear_btn.isHidden() is True


def test_delete_button_visible_with_count_after_mark(page, gw):
    _mark(gw, page, "Exported Media/Dia 1/p1.jpg")
    assert page._delete_btn.isHidden() is False
    assert "1" in page._delete_btn.text()
    _mark(gw, page, "Exported Media/Dia 1/p2.jpg")
    assert "2" in page._delete_btn.text()


def test_delete_button_label_carries_the_marked_emoji_glyph(page, gw):
    _mark(gw, page, "Exported Media/Dia 1/p1.jpg")
    assert page._delete_btn.text().startswith("⌫")


# ── clear marks ─────────────────────────────────────────────────────


def test_clear_marks_releases_every_visible_row(page, gw):
    _mark(gw, page, "Exported Media/Dia 1/p1.jpg")
    _mark(gw, page, "Exported Media/Dia 1/p2.jpg")
    assert len(page._marked_relpaths()) == 2
    page._clear_marks()
    assert page._marked_relpaths() == []
    for rel in ("Exported Media/Dia 1/p1.jpg",
                "Exported Media/Dia 1/p2.jpg"):
        assert gw.lineage_ratings(rel).to_delete is False


# ── confirm dialog runs the batch delete ────────────────────────────


def test_delete_clicked_runs_batch_when_user_accepts(
        page, gw, event_dir, monkeypatch):
    """Stub the confirm dialog to ACCEPT and check the batch helper
    fired."""
    _mark(gw, page, "Exported Media/Dia 1/p1.jpg")
    _mark(gw, page, "Exported Media/Dia 1/p2.jpg")

    def _accept(relpaths):
        gw.delete_marked_exported_files()
        page._refresh()

    monkeypatch.setattr(page, "_delete_batch_with_confirm", _accept)

    page._on_delete_clicked()

    assert not (event_dir / "Exported Media/Dia 1/p1.jpg").exists()
    assert not (event_dir / "Exported Media/Dia 1/p2.jpg").exists()
    assert page._marked_relpaths() == []


def test_delete_clicked_noop_when_nothing_marked(page, monkeypatch):
    """No mark → no confirm dialog → no-op."""
    called = []
    monkeypatch.setattr(
        page, "_delete_batch_with_confirm",
        lambda relpaths: called.append(relpaths))
    page._on_delete_clicked()
    assert called == []


# ── versions cluster surfacing (§4.4) ───────────────────────────────


def _doc_with_versions() -> m.EventDocument:
    """Same fixture as ``_doc`` but with TWO lineage rows for p1 so
    the flat grid folds them into a versions cluster cover."""
    doc = _doc()
    # Add a second exported version for p1 (e.g. a third-party return).
    doc.lineage.append(m.Lineage(
        export_relpath="Exported Media/Dia 1/p1_v2.jpg",
        phase="edit", source_kind="item",
        source_item_id="p1", exported_at="t3"))
    return doc


@pytest.fixture
def gw_versions(tmp_path):
    """Gateway with p1 shipped twice + p2 once. The on-disk files
    match so unlink paths don't no-op."""
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for name in ("p1.jpg", "p1_v2.jpg", "p2.jpg"):
        (tmp_path / "Exported Media" / "Dia 1" / name).write_bytes(
            b"\xff\xd8\xff\xd9")
    store = EventStore.create(tmp_path / "event.db", event_id="evt-v")
    store.save_document(_doc_with_versions())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=tmp_path,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page_versions(qapp, gw_versions, monkeypatch):
    p = DCDetailPage()
    p._review_calls = []                                       # type: ignore[attr-defined]
    monkeypatch.setattr(
        p, "_open_review_dialog_for_cell",
        lambda i: p._review_calls.append(i))                   # type: ignore[attr-defined]
    p.open_pool(gw_versions)
    yield p
    p.close_event()


def test_versions_fold_into_one_cluster_cover(page_versions):
    """p1 has 2 rows → one cluster cell; p2 has 1 → flat. Two cells
    total in flat mode."""
    cells = page_versions._cells
    kinds = [c.kind for c in cells]
    assert kinds.count("cluster") == 1
    assert kinds.count("flat") == 1
    cluster = next(c for c in cells if c.kind == "cluster")
    assert cluster.source_item_id == "p1"
    assert len(cluster.rows) == 2


def test_cluster_cover_click_drills_in(page_versions):
    """Activating the cluster cover switches the page into cluster
    mode and re-renders with both p1 versions as flat cells."""
    cells = page_versions._cells
    cluster_idx = next(
        i for i, c in enumerate(cells) if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert page_versions._mode == "cluster"
    assert page_versions._cluster_item_id == "p1"
    after = page_versions._cells
    assert len(after) == 2
    assert all(c.kind == "flat" for c in after)


def test_cluster_back_pops_to_flat(page_versions):
    cells = page_versions._cells
    cluster_idx = next(
        i for i, c in enumerate(cells) if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    page_versions._on_grid_back_requested()
    assert page_versions._mode == "flat"
    assert page_versions._cluster_item_id is None


def test_flat_back_propagates(page_versions):
    received = []
    page_versions.back_requested.connect(lambda: received.append(True))
    page_versions._on_grid_back_requested()
    assert received == [True]


def test_titlebar_back_pops_cluster_first(page_versions):
    """The shared title-bar Back hits ``on_titlebar_back``; in cluster
    mode it must pop the cluster and stay on the page, NOT propagate
    ``back_requested`` (Nelson 2026-06-30 round 4 — without this the
    Back button on the cluster sub-grid dumped the user back at Cuts)."""
    received = []
    page_versions.back_requested.connect(lambda: received.append(True))
    cluster_idx = next(
        i for i, c in enumerate(page_versions._cells)
        if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert page_versions._mode == "cluster"

    page_versions.on_titlebar_back()
    assert page_versions._mode == "flat"
    assert received == []                       # didn't fall through

    # On the flat grid the same hook propagates.
    page_versions.on_titlebar_back()
    assert received == [True]


def test_reopen_resets_to_flat(page_versions, gw_versions):
    """Re-opening the page after a cluster drill-in must land on the
    flat grid, not where the user left off."""
    cluster_idx = next(
        i for i, c in enumerate(page_versions._cells)
        if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert page_versions._mode == "cluster"

    page_versions.close_event()
    assert page_versions._mode == "flat"
    assert page_versions._cluster_item_id is None

    page_versions.open_pool(gw_versions)
    assert page_versions._mode == "flat"
    assert page_versions._cluster_item_id is None


def test_to_delete_split_chip_reflects_inner_marks(
        page_versions, gw_versions):
    """When at least one inner version is marked, the cluster cover's
    grid item carries a (marked, total) split."""
    rel = "Exported Media/Dia 1/p1_v2.jpg"
    gw_versions.set_lineage_to_delete(rel, True)
    page_versions._refresh()
    cluster = next(
        c for c in page_versions._cells if c.kind == "cluster")
    item = page_versions._make_grid_item(cluster)
    assert item.to_delete_split == (1, 2)
    # Clearing returns the split to None.
    gw_versions.set_lineage_to_delete(rel, False)
    page_versions._refresh()
    cluster = next(
        c for c in page_versions._cells if c.kind == "cluster")
    item = page_versions._make_grid_item(cluster)
    assert item.to_delete_split is None


def test_cluster_cover_does_not_open_viewer(page_versions):
    """Activating a cluster cover triggers the drill-in only — no
    review_requested signal, no _open_review_dialog_for_cell call."""
    received = []
    page_versions.review_requested.connect(received.append)
    cluster_idx = next(
        i for i, c in enumerate(page_versions._cells)
        if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert received == []
    assert page_versions._review_calls == []                   # type: ignore[attr-defined]


# ── Compare button (§6) ─────────────────────────────────────────────


def test_compare_button_hidden_when_under_two_marks(page):
    """Flat mode + nothing marked → button is hidden."""
    assert page._compare_btn.isHidden() is True


def test_compare_button_shows_with_two_marks(page):
    """Marking two flat cells reveals the button with a (N) label."""
    cells = page._cells
    flat = [c for c in cells if c.kind == "flat"]
    page._compare_marked.add(flat[0].cover_relpath)
    page._rebuild_cells()
    page._update_chrome()
    assert page._compare_btn.isHidden() is True       # only 1 marked

    page._compare_marked.add(flat[1].cover_relpath)
    page._rebuild_cells()
    page._update_chrome()
    assert page._compare_btn.isHidden() is False
    assert "2" in page._compare_btn.text()


def test_compare_marked_paints_compare_state(page):
    """A marked flat cell hands a ``state='compare'`` ThumbGridItem to
    the grid so the existing orange state-border paints."""
    cells = page._cells
    flat = next(c for c in cells if c.kind == "flat")
    page._compare_marked.add(flat.cover_relpath)
    item = page._make_grid_item(flat)
    assert item.state == "compare"
    page._compare_marked.discard(flat.cover_relpath)
    item = page._make_grid_item(flat)
    assert item.state is None


def test_compare_button_in_cluster_mode_always_visible(page_versions):
    """Inside a versions cluster sub-grid, the Compare button is
    always visible (spec/89 §11.3) regardless of any per-cell mark."""
    cluster_idx = next(
        i for i, c in enumerate(page_versions._cells)
        if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert page_versions._mode == "cluster"
    assert page_versions._compare_btn.isHidden() is False
    assert "versions" in page_versions._compare_btn.text().lower()


def test_compare_marks_reset_on_mode_change(page_versions):
    """Drill-in clears the flat-mode mark set so the cluster's Compare
    button has its own clean state."""
    flat = next(
        c for c in page_versions._cells if c.kind == "flat")
    page_versions._compare_marked.add(flat.cover_relpath)
    cluster_idx = next(
        i for i, c in enumerate(page_versions._cells)
        if c.kind == "cluster")
    page_versions._on_cell_activated(cluster_idx)
    assert page_versions._compare_marked == set()
    page_versions._on_grid_back_requested()
    assert page_versions._compare_marked == set()


# ── Preferred-version flow (§6+) ────────────────────────────────────


def test_review_preferred_writes_through_gateway(page_versions, gw_versions):
    """The review-dialog signal writes the flag through
    ``set_lineage_preferred`` and locally mirrors the new state
    onto the cached lineage row."""
    rel = "Exported Media/Dia 1/p1.jpg"
    page_versions._on_review_preferred_changed(rel, True)
    assert gw_versions.lineage_ratings(rel).is_preferred is True
    page_versions._on_review_preferred_changed(rel, False)
    assert gw_versions.lineage_ratings(rel).is_preferred is False


def test_review_preferred_clears_siblings_locally(
        page_versions, gw_versions):
    """When the user marks a version preferred, the gateway clears the
    sibling; the page's cached lineage rows must reflect that so the
    grid repaints correctly without a fresh ``_refresh``."""
    mira_rel = "Exported Media/Dia 1/p1.jpg"
    lrc_rel = "Exported Media/Dia 1/p1_v2.jpg"
    page_versions._on_review_preferred_changed(mira_rel, True)
    page_versions._on_review_preferred_changed(lrc_rel, True)
    # The cached rows reflect the gateway: only the LRC version is
    # marked preferred.
    by_rel = {f.export_relpath: f for f in page_versions._files}
    assert bool(getattr(by_rel[lrc_rel], "is_preferred", False)) is True
    assert bool(getattr(by_rel[mira_rel], "is_preferred", False)) is False


def test_compare_use_this_routes_to_gateway(page_versions, gw_versions):
    """The CompareVersionsDialog's ``use_this_requested`` lands on the
    page's handler which writes through the gateway."""
    rel = "Exported Media/Dia 1/p1_v2.jpg"
    page_versions._on_compare_use_this(rel)
    assert gw_versions.lineage_ratings(rel).is_preferred is True


def test_compare_use_this_clear_falls_back_to_cached_preferred(
        page_versions, gw_versions):
    """An empty ``item_id`` payload from the dialog means 'toggle the
    current preferred OFF' — the handler finds the marked row and
    clears it."""
    rel = "Exported Media/Dia 1/p1.jpg"
    page_versions._on_compare_use_this(rel)            # set
    assert gw_versions.lineage_ratings(rel).is_preferred is True
    page_versions._on_compare_use_this("")             # clear
    assert gw_versions.lineage_ratings(rel).is_preferred is False


def test_cluster_cover_paints_preferred_origin_chip(
        page_versions, gw_versions):
    """A cluster cover whose preferred member is the LRC return
    surfaces an ``preferred_origin='ext'`` (or similar) chip so the
    grid reads at a glance."""
    lrc_rel = "Exported Media/Dia 1/p1_v2.jpg"
    gw_versions.set_lineage_preferred(lrc_rel, True)
    page_versions._refresh()
    cluster = next(
        c for c in page_versions._cells if c.kind == "cluster")
    item = page_versions._make_grid_item(cluster)
    # Some origin wordmark is present; the exact label depends on the
    # filename ("_v2" doesn't trigger any specific suffix matcher so
    # falls back to "ext" — what matters is the chip is populated).
    assert item.preferred_origin is not None
    assert item.preferred_origin != ""


def test_flat_cell_paints_preferred_pill(page_versions, gw_versions):
    """A non-cluster cell carrying ``is_preferred=True`` hands the
    grid a ``preferred=True`` ThumbGridItem so the ✓ pill paints."""
    p2_rel = "Exported Media/Dia 1/p2.jpg"
    gw_versions.set_lineage_preferred(p2_rel, True)
    page_versions._refresh()
    flat = next(
        c for c in page_versions._cells
        if c.kind == "flat" and c.cover_relpath == p2_rel)
    item = page_versions._make_grid_item(flat)
    assert item.preferred is True
