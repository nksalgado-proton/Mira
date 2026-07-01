"""spec/159 §4.5 / §4.6 — the Exported Collection filter dropdown.

Pins both:

* The :class:`LineageFilter` predicate: ``matches`` returns True only
  when every active knob passes; ``is_active`` flips when at least one
  knob is non-default.
* The DCDetailPage application: the filter narrows the rendered cell
  list; a cluster cover survives when at least one inner version
  passes; a flat cell falls out when its own row fails. Open / close
  resets to the default (§4.6 — session-local).
"""
from __future__ import annotations

import itertools

import pytest

from mira.gateway.event_gateway import EventGateway
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.exported.filter_bar import FilterBar
from mira.ui.exported.filter_popup import LineageFilter
from mira.ui.shared.dc_detail_page import DCDetailPage

FIXED_NOW = "2026-06-30T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


# ── LineageFilter unit tests ──────────────────────────────────────


class _Row:
    """Duck-typed Lineage stand-in — only the four rating fields are
    read by :meth:`LineageFilter.matches`."""

    def __init__(
        self, *, stars=None, color_label=None,
        flag=False, to_delete=False,
    ):
        self.stars = stars
        self.color_label = color_label
        self.flag = flag
        self.to_delete = to_delete


def test_default_filter_matches_anything():
    f = LineageFilter()
    assert f.is_active() is False
    assert f.matches(_Row()) is True
    assert f.matches(_Row(stars=3, color_label="green", flag=True)) is True


def test_min_stars_rejects_below_threshold():
    f = LineageFilter(min_stars=3)
    assert f.is_active() is True
    assert f.matches(_Row(stars=4)) is True
    assert f.matches(_Row(stars=3)) is True
    assert f.matches(_Row(stars=2)) is False
    # Unrated rows fail when a minimum is set.
    assert f.matches(_Row(stars=None)) is False


def test_colour_labels_multi_select():
    f = LineageFilter(colour_labels={"red", "green"})
    assert f.matches(_Row(color_label="red")) is True
    assert f.matches(_Row(color_label="green")) is True
    assert f.matches(_Row(color_label="blue")) is False
    # Unlabelled fails when at least one label is required.
    assert f.matches(_Row(color_label=None)) is False


def test_flag_tristate():
    yes_only = LineageFilter(flag="yes")
    no_only = LineageFilter(flag="no")
    assert yes_only.matches(_Row(flag=True)) is True
    assert yes_only.matches(_Row(flag=False)) is False
    assert no_only.matches(_Row(flag=False)) is True
    assert no_only.matches(_Row(flag=True)) is False


def test_to_delete_tristate():
    """Any / only / hide for the marked-for-deletion knob."""
    any_ = LineageFilter(to_delete="any")
    only_ = LineageFilter(to_delete="only")
    hide_ = LineageFilter(to_delete="hide")
    assert any_.matches(_Row(to_delete=True)) is True
    assert any_.matches(_Row(to_delete=False)) is True
    assert only_.matches(_Row(to_delete=True)) is True
    assert only_.matches(_Row(to_delete=False)) is False
    assert hide_.matches(_Row(to_delete=False)) is True
    assert hide_.matches(_Row(to_delete=True)) is False


def test_filter_is_conjunctive():
    """A row must pass EVERY active knob."""
    f = LineageFilter(
        min_stars=4, colour_labels={"green"}, flag="yes")
    assert f.matches(
        _Row(stars=5, color_label="green", flag=True)) is True
    # Fail any one knob → reject.
    assert f.matches(
        _Row(stars=3, color_label="green", flag=True)) is False
    assert f.matches(
        _Row(stars=5, color_label="red", flag=True)) is False
    assert f.matches(
        _Row(stars=5, color_label="green", flag=False)) is False


# ── FilterBar round-trip tests ────────────────────────────────────


def test_filter_bar_set_filter_round_trips(qapp):
    bar = FilterBar()
    pushed = LineageFilter(
        min_stars=3, colour_labels={"red", "blue"},
        flag="yes", to_delete="hide")
    received = []
    bar.filter_changed.connect(received.append)
    bar.set_filter(pushed)
    # ``set_filter`` is programmatic — no signal.
    assert received == []
    cur = bar.filter()
    assert cur.min_stars == 3
    assert cur.colour_labels == {"red", "blue"}
    assert cur.flag == "yes"
    assert cur.to_delete == "hide"


def test_filter_bar_reset_emits(qapp):
    bar = FilterBar()
    bar.set_filter(LineageFilter(min_stars=4))
    received = []
    bar.filter_changed.connect(received.append)
    bar.reset()
    assert len(received) == 1
    assert received[0].is_active() is False


def test_filter_bar_rendered_count_indicator(qapp):
    """``setRenderedCount`` repaints the indicator without touching
    the predicate. Used by the host on every grid rebuild."""
    bar = FilterBar()
    bar.setRenderedCount(0, 0)
    bar.setRenderedCount(500, 500)
    assert "500" in bar._count_lbl.text()
    bar.setRenderedCount(120, 500)
    txt = bar._count_lbl.text()
    assert "120" in txt and "500" in txt


# ── DCDetailPage application ──────────────────────────────────────


def _doc():
    """Two source items, three lineage rows. p1 has two ratings-rich
    versions (one 5★ green flagged, one 2★ red), p2 has one 5★
    unflagged row marked-for-deletion."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-f", name="Filter fixture",
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
                  source_item_id="p1", exported_at="t1",
                  provenance="mira_render",
                  stars=5, color_label="green", flag=True),
        m.Lineage(export_relpath="Exported Media/Dia 1/p1_v2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p1", exported_at="t2",
                  provenance="third_party",
                  stars=2, color_label="red"),
        m.Lineage(export_relpath="Exported Media/Dia 1/p2.jpg",
                  phase="edit", source_kind="item",
                  source_item_id="p2", exported_at="t3",
                  stars=5, to_delete=True),
    ]
    doc.adjustments = [
        m.Adjustment(item_id="p1", edit_exported=True),
        m.Adjustment(item_id="p2", edit_exported=True),
    ]
    return doc


@pytest.fixture
def event_dir(tmp_path):
    (tmp_path / "Exported Media" / "Dia 1").mkdir(parents=True)
    for name in ("p1.jpg", "p1_v2.jpg", "p2.jpg"):
        (tmp_path / "Exported Media" / "Dia 1" / name).write_bytes(
            b"\xff\xd8\xff\xd9")
    return tmp_path


@pytest.fixture
def gw(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-f")
    store.save_document(_doc())
    counter = itertools.count(1)
    g = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield g
    g.close()


@pytest.fixture
def page(qapp, gw, monkeypatch):
    p = DCDetailPage()
    p._review_calls = []                                       # type: ignore[attr-defined]
    monkeypatch.setattr(
        p, "_open_review_dialog_for_cell",
        lambda i: p._review_calls.append(i))                   # type: ignore[attr-defined]
    p.open_pool(gw)
    yield p
    p.close_event()


def test_filter_starts_inactive_on_open(page):
    """A fresh open lands on the unfiltered view (§4.6)."""
    assert page._filter.is_active() is False
    # All three lineage rows visible: p2 is flat, p1 folds into a cluster.
    assert len(page._cells) == 2


def test_filter_min_stars_drops_low_versions(page):
    """Min stars = 5 keeps the flat p2 and the cluster (p1's first
    version is 5★) but the cluster's drill-in shows only the 5★
    version, not the 2★ sibling."""
    page._on_filter_changed(LineageFilter(min_stars=5))
    cluster = next(c for c in page._cells if c.kind == "cluster")
    # Cover still there — at least one member passes.
    assert cluster.source_item_id == "p1"
    # Drill in: only the 5★ row should remain.
    page._open_cluster("p1")
    drilled = page._cells
    assert all(c.kind in ("flat", "mira_pending") for c in drilled)
    flat_in_drill = [c for c in drilled if c.kind == "flat"]
    assert len(flat_in_drill) == 1
    assert flat_in_drill[0].rows[0].stars == 5


def test_filter_cluster_hides_when_no_member_passes(page):
    """If no inner version passes the filter, the cluster cover drops
    out of the flat grid entirely."""
    # Min stars 3: p1's 2★ version fails but the 5★ version passes →
    # cluster stays. p2's 5★ passes → flat cell stays.
    page._on_filter_changed(LineageFilter(min_stars=3))
    assert len(page._cells) == 2
    # Crank it to 6 (impossible): every row fails, every cluster
    # falls out, every flat cell falls out.
    page._on_filter_changed(LineageFilter(min_stars=6))
    assert len(page._cells) == 0


def test_filter_colour_label_drops_unmatched(page):
    """Picking colour=red keeps p1's LRC return (red) but rejects
    p2 (no label) and the Mira render of p1 (green)."""
    page._on_filter_changed(
        LineageFilter(colour_labels={"red"}))
    # p1 cluster cover survives (one inner is red).
    # p2 has no label, so its flat cell drops out.
    cells = page._cells
    cluster_covers = [c for c in cells if c.kind == "cluster"]
    flat_cells = [c for c in cells if c.kind == "flat"]
    assert len(cluster_covers) == 1
    assert len(flat_cells) == 0


def test_filter_flag_yes_only(page):
    """Flag=yes keeps p1's flagged Mira-render version → cluster
    survives; p2 isn't flagged → flat cell drops out."""
    page._on_filter_changed(LineageFilter(flag="yes"))
    flat_cells = [c for c in page._cells if c.kind == "flat"]
    assert flat_cells == []


def test_filter_to_delete_hide(page):
    """``to_delete='hide'`` drops p2 (to_delete=True). p1's cluster
    survives — neither inner is marked."""
    page._on_filter_changed(LineageFilter(to_delete="hide"))
    cells = page._cells
    flat_cells = [c for c in cells if c.kind == "flat"]
    assert flat_cells == []
    assert any(c.kind == "cluster" for c in cells)


def test_filter_to_delete_only_keeps_only_marked(page):
    """``to_delete='only'`` keeps p2 (marked) and drops p1's cluster
    (no member marked) — the 'show me what I'm about to delete' lens."""
    page._on_filter_changed(LineageFilter(to_delete="only"))
    cells = page._cells
    flat_cells = [c for c in cells if c.kind == "flat"]
    assert len(flat_cells) == 1
    assert flat_cells[0].rows[0].export_relpath \
        == "Exported Media/Dia 1/p2.jpg"
    assert all(c.kind != "cluster" for c in cells)


def test_filter_resets_on_reopen(page, gw):
    """Closing + reopening returns to the unfiltered view (§4.6).

    The FilterBar's own state mirrors the host's predicate."""
    page._on_filter_changed(LineageFilter(min_stars=5))
    assert page._filter.is_active() is True
    page.close_event()
    page.open_pool(gw)
    assert page._filter.is_active() is False
    # The bar reflects the cleared state.
    assert page._filter_bar.filter().is_active() is False