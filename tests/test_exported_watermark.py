"""spec/59 §8 — the Exported watermark.

Pins: the gateway driver (edit-phase lineage rows, NOT the
``edit_exported`` freshness flag; bracket/share rows ignored); the
``day_grid_cells`` Edit projection stamping PHOTO item cells only;
``CullCell.exported`` defaulting False (Pick untouched); the redesigned
:class:`Thumb` (and therefore the shared :class:`ThumbGrid`) painting
the exported badge for photo cells whose item has lineage; the
MediaCanvas host API; the legacy widget never taking the mouse; the
setting shipping ON.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

from mira.gateway.event_gateway import EventGateway
from mira.picked.model import (
    CullBucket,
    CullCell,
    CullItem,
    PickDay,
    day_grid_cells,
)
from mira.picked.status import BucketStatus, CellColor
from mira.settings.model import Settings
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.base.exported_watermark import ExportedWatermark
from mira.ui.design import ThumbGrid, ThumbGridItem


# --------------------------------------------------------------------------- #
# The gateway driver — edit-phase lineage, all writer shapes, nothing else
# --------------------------------------------------------------------------- #


def _make_eg(tmp_path) -> EventGateway:
    store = EventStore.create(tmp_path / "event.db", event_id="evt-wm")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-wm", name="WM", created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="C1"))
    store.upsert(m.TripDay(day_number=1, date="2026-04-01"))
    for iid in ("p1", "p2", "p3"):
        store.upsert(m.Item(
            id=iid, kind="photo", origin_relpath=f"d/{iid}.jpg",
            sha256=iid, byte_size=2, materialized_at="t",
            materialized_phase="ingest", camera_id="C1",
            capture_time_raw="2026-04-01T08:00:00",
            capture_time_corrected="2026-04-01T08:00:00",
            created_at="t", day_number=1, provenance="captured",
        ))
    return EventGateway(store, event_root=tmp_path, now=lambda: "t")


def test_exported_item_ids_empty_without_lineage(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_exported_item_ids_counts_only_exported_media_rows(tmp_path):
    """spec/66 §1.2 — ``exported_item_ids()`` returns the SHIPPED set
    (rows under ``Exported Media/``). Third-party returns sitting in
    ``Edited Media/`` are mere edit candidates and do NOT count until
    the Export run hardlinks them into ``Exported Media/``."""
    eg = _make_eg(tmp_path)
    try:
        # Shipped (Mira-rendered, in-app export) → counts.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        # Third-party return inbox (LRC/Helicon) → does NOT count.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p2-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p2"))
        # Shipped via hardlink-from-return → counts.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p3.jpg", phase="edit",
            source_kind="item", source_item_id="p3",
            recipe_json="{}", exported_at="t"))
        assert eg.exported_item_ids() == {"p1", "p3"}
    finally:
        eg.store.close()


def test_exported_item_ids_distinct_and_edit_only(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        # Two exports of the same photo → one id.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1"))
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1 (2).jpg", phase="edit",
            source_kind="item", source_item_id="p1"))
        # A share-phase row is NOT an Edit export.
        eg.record_lineage(m.Lineage(
            export_relpath="cut/p3.jpg", phase="share",
            source_kind="item", source_item_id="p3"))
        assert eg.exported_item_ids() == {"p1"}
    finally:
        eg.store.close()


def test_edit_exported_flag_is_not_the_driver(tmp_path):
    """The freshness flag must not feed the watermark (spec/59 §8)."""
    eg = _make_eg(tmp_path)
    try:
        eg.set_edit_exported("p1", True)
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def _make_eg_picked(tmp_path) -> EventGateway:
    """:func:`_make_eg` plus a Pick-kept phase_state row per item — the
    rescan needs the picked pool to do its stem match."""
    eg = _make_eg(tmp_path)
    for iid in ("p1", "p2", "p3"):
        eg.store.upsert(
            m.PhaseState(item_id=iid, phase="pick", state="picked"))
    return eg


# --------------------------------------------------------------------------- #
# rescan_exported_media — the self-heal for lost-commit Exports
# --------------------------------------------------------------------------- #


def test_rescan_backfills_orphan_files(tmp_path):
    """A JPEG sitting under ``Exported Media/`` with no lineage row is
    backfilled: the rescan matches it back to its source ``Item`` by
    source-filename stem, writes the row, and flips
    ``Adjustment.edit_exported``.

    Mirrors the spec/57 §3 returns scan: the next Edit / Share / Export
    entry catches files the Export run dropped on disk but forgot to
    commit a row for. The Inseto na Varanda silent fail (2026-06-15)
    would have left orphans here — this test guards the recovery path
    so a future regression doesn't reintroduce a lost-row gap."""
    eg = _make_eg_picked(tmp_path)
    try:
        # Drop a JPEG matching ``p1``'s origin stem (= ``p1`` for
        # ``origin_relpath="d/p1.jpg"``) into the ship tree. No lineage
        # row yet — the simulated lost commit.
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        # Pre-rescan: the watermark / Share view is empty.
        assert eg.exported_item_ids() == set()

        n = eg.rescan_exported_media()
        assert n == 1
        # Post-rescan: the source item now reads as shipped.
        assert eg.exported_item_ids() == {"p1"}
        # ``Adjustment.edit_exported`` flipped — the freshness chip lights.
        adj = eg.adjustment("p1")
        assert adj is not None and adj.edit_exported is True
        # The lineage row carries the correct relpath under Exported Media/.
        files = eg.exported_files()
        assert [f.export_relpath for f in files] == [
            "Exported Media/Dia 1/p1.jpg"]
    finally:
        eg.store.close()


def test_rescan_is_idempotent(tmp_path):
    """Running the rescan twice writes the row once — the second call
    is a no-op. Lets every Edit / Share / Export entry call it without
    risk of duplicating lineage."""
    eg = _make_eg_picked(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        assert eg.rescan_exported_media() == 1
        # Second pass — the row exists, nothing new.
        assert eg.rescan_exported_media() == 0
        # And the ship pool stays a single row, no duplication.
        assert len(eg.exported_files()) == 1
    finally:
        eg.store.close()


def test_rescan_skips_files_with_existing_rows(tmp_path):
    """A file that already has a lineage row pointing at it is left
    alone (the row keeps its original recipe / exported_at / etc.)."""
    eg = _make_eg_picked(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json='{"look": "natural"}',
            exported_at="2026-06-01T00:00:00"))
        assert eg.rescan_exported_media() == 0
        # The original row's recipe + timestamp survive.
        files = eg.exported_files()
        assert len(files) == 1
        assert files[0].recipe_json == '{"look": "natural"}'
        assert files[0].exported_at == "2026-06-01T00:00:00"
    finally:
        eg.store.close()


def test_rescan_skips_ambiguous_stems(tmp_path):
    """Two Pick-kept photos with the same filename stem (``DSC0001.cr3``
    on day 1 AND day 2) make the stem map ambiguous — the rescan
    refuses to guess and leaves both unresolved files alone."""
    eg = _make_eg(tmp_path)
    # _make_eg gives day 1 only — the second item lives on day 2.
    eg.store.upsert(m.TripDay(day_number=2, date="2026-04-02"))
    # Two items share the same source filename stem ``dup`` — different
    # paths, but a stem-keyed map can only point at one of them.
    eg.store.upsert(m.Item(
        id="d1", kind="photo", origin_relpath="day1/dup.jpg",
        sha256="d1", byte_size=2, materialized_at="t",
        materialized_phase="ingest", camera_id="C1",
        capture_time_raw="2026-04-01T08:00:00",
        capture_time_corrected="2026-04-01T08:00:00",
        created_at="t", day_number=1, provenance="captured"))
    eg.store.upsert(m.Item(
        id="d2", kind="photo", origin_relpath="day2/dup.jpg",
        sha256="d2", byte_size=2, materialized_at="t",
        materialized_phase="ingest", camera_id="C1",
        capture_time_raw="2026-04-02T08:00:00",
        capture_time_corrected="2026-04-02T08:00:00",
        created_at="t", day_number=2, provenance="captured"))
    eg.store.upsert(m.PhaseState(item_id="d1", phase="pick", state="picked"))
    eg.store.upsert(m.PhaseState(item_id="d2", phase="pick", state="picked"))
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "dup.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        # Ambiguous → skip. The user re-runs Export from the surface
        # to disambiguate; the rescan does not guess between days.
        assert eg.rescan_exported_media() == 0
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_rescan_no_op_when_no_exported_media_folder(tmp_path):
    """Nothing on disk → nothing to do. The rescan never creates the
    folder; it only observes."""
    eg = _make_eg_picked(tmp_path)
    try:
        assert not (tmp_path / "Exported Media").exists()
        assert eg.rescan_exported_media() == 0
        # No phantom row writes.
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_rescan_prunes_when_bytes_gone_and_cascades_cuts(tmp_path):
    """The bytes on disk are the source of truth (Nelson 2026-06-15). A
    lineage row under ``Exported Media/`` whose file is gone is pruned on
    rescan so ``#exported`` reconciles to empty, and the
    ``cut_member.export_relpath`` FK CASCADE removes the file from every
    Cut that held it — the Cut definition itself survives."""
    eg = _make_eg_picked(tmp_path)
    try:
        rel = "Exported Media/Dia 1/p1.jpg"
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="p1", recipe_json="{}", exported_at="t"))
        eg.set_edit_exported("p1", True)
        # The file is a member of a Cut.
        with eg.store.transaction() as conn:
            conn.execute(
                "INSERT INTO cut (id, tag, created_at, updated_at) "
                "VALUES ('c1', 'Highlights', 't', 't')")
            conn.execute(
                "INSERT INTO cut_member (cut_id, export_relpath, added_at) "
                "VALUES ('c1', ?, 't')", (rel,))
        assert eg.exported_item_ids() == {"p1"}

        # The bytes vanish — the user wiped Exported Media/.
        (ship / "p1.jpg").unlink()

        assert eg.rescan_exported_media() == 1
        # #exported reconciled to empty; the freshness flag cleared.
        assert eg.exported_item_ids() == set()
        assert eg.exported_files() == []
        adj = eg.adjustment("p1")
        assert adj is None or adj.edit_exported is False
        # The Cut lost the file via the FK CASCADE; the Cut row survives.
        members = eg.store.conn.execute(
            "SELECT 1 FROM cut_member WHERE cut_id = 'c1'").fetchall()
        assert members == []
        assert eg.store.conn.execute(
            "SELECT 1 FROM cut WHERE id = 'c1'").fetchone() is not None
    finally:
        eg.store.close()


def test_rescan_prunes_even_when_whole_folder_wiped(tmp_path):
    """The old guard returned early when ``Exported Media/`` was absent,
    leaving orphan lineage rows as dirt. Now an entirely missing folder
    still prunes the now-orphan rows down to match the (empty) disk."""
    eg = _make_eg_picked(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        eg.set_edit_exported("p1", True)
        assert eg.exported_item_ids() == {"p1"}
        # No Exported Media/ folder exists at all.
        assert not (tmp_path / "Exported Media").exists()
        assert eg.rescan_exported_media() == 1
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_edit_candidate_helpers_for_third_party_returns(tmp_path):
    """spec/66 §1.2 — ``edit_candidate_item_ids`` returns items with a
    third-party return sitting in ``Edited Media/`` (and not yet shipped);
    ``edit_candidate_relpath`` returns the newest return relpath for an
    item (the Export hardlink path consumes both)."""
    eg = _make_eg(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p1-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T08:00:00"))
        # A newer return for the same item — the helper takes the latest.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p1-edit-v2.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T09:00:00"))
        # A shipped (Exported Media/) row should NOT register as a
        # candidate — it's already past the inbox stage.
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p2.jpg", phase="edit",
            source_kind="item", source_item_id="p2",
            exported_at="2026-06-14T10:00:00"))
        assert eg.edit_candidate_item_ids() == {"p1"}
        assert eg.edit_candidate_relpath("p1") == (
            "Edited Media/LRC/p1-edit-v2.jpg")
        assert eg.edit_candidate_relpath("p2") is None
        assert eg.edit_candidate_relpath("never-imported") is None
    finally:
        eg.store.close()


def test_exported_files_excludes_edit_candidates(tmp_path):
    """spec/66 §1.2 + spec/61 §1.1 — the ``#exported`` Cut universe
    (returned by ``exported_files``) shows only shipped rows. A
    third-party return that hasn't been promoted is invisible to Cuts."""
    eg = _make_eg(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            exported_at="2026-06-14T08:00:00"))
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p2-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p2",
            exported_at="2026-06-14T08:00:00"))
        relpaths = [ln.export_relpath for ln in eg.exported_files()]
        assert "Exported Media/Dia 1/p1.jpg" in relpaths
        assert "Edited Media/LRC/p2-edit.jpg" not in relpaths
    finally:
        eg.store.close()


# --------------------------------------------------------------------------- #
# The model projection — photos stamped, videos/Pick untouched
# --------------------------------------------------------------------------- #


def test_cullcell_exported_defaults_false():
    cell = CullCell(end_time="", color=CellColor.KEPT, item_id="x")
    assert cell.exported is False


def _fake_day():
    items = (
        CullItem(item_id="p1", path=Path("p1.jpg"), kind="photo",
                 capture_time_corrected="2026-04-01T08:00:00"),
        CullItem(item_id="v1", path=Path("v1.mp4"), kind="video",
                 capture_time_corrected="2026-04-01T08:01:00",
                 duration_ms=1000),
    )
    status = BucketStatus(
        total=2, kept=0, candidate=0, discarded=0, untouched=2,
        reviewed=False, browsed=False, badge="untouched")
    bucket = CullBucket(
        bucket_key="1|individual|k", kind="individual", title="b",
        items=items, status=status)
    return [PickDay(day_number=1, label="Dia 1", buckets=(bucket,),
                    status=status)]


def _fake_gateway():
    return SimpleNamespace(
        phase_states=lambda _phase: {},
        items_visited_for_day=lambda _d, _p: set(),
        video_segments=lambda _vid: [],
        video_snapshots=lambda _vid: [],
    )


def test_day_grid_cells_stamps_exported_photos_only():
    cells = day_grid_cells(
        _fake_gateway(), 1, phase="edit", days=_fake_day(),
        default_state="picked", exported_ids={"p1", "v1"},
    )
    by_id = {c.item_id: c for c in cells}
    assert by_id["p1"].exported is True
    # Videos never wear the watermark, even when their id has lineage.
    assert by_id["v1"].exported is False


def test_day_grid_cells_default_stamps_nothing():
    """No ``exported_ids`` (every Pick caller) → all False."""
    cells = day_grid_cells(
        _fake_gateway(), 1, phase="edit", days=_fake_day(),
        default_state="picked",
    )
    assert all(c.exported is False for c in cells)


# --------------------------------------------------------------------------- #
# The widgets
# --------------------------------------------------------------------------- #


def _grid_with(exported: bool) -> ThumbGrid:
    """One-cell :class:`ThumbGrid` carrying the redesigned exported
    badge state — the cell-level ``exported`` flag drives the
    bottom-left "↑ Exported" chip painted by :class:`Thumb` (spec/59
    §8). The shared widget replaces the legacy ``DayGridCell``
    overlay; the contract pinned is "exported=True paints the chip;
    False does not, even after a state cycle"."""
    g = ThumbGrid()
    g.set_items([ThumbGridItem(exported=exported)])
    return g


def test_thumb_grid_cell_carries_exported_flag_when_set(qapp):
    g = _grid_with(True)
    cell = g.cell_at(0)
    assert cell is not None
    assert cell._exported is True


def test_thumb_grid_cell_no_exported_flag_by_default(qapp):
    g = _grid_with(False)
    cell = g.cell_at(0)
    assert cell is not None
    assert cell._exported is False


def test_thumb_grid_update_item_flips_exported_flag(qapp):
    g = _grid_with(False)
    cell = g.cell_at(0)
    assert cell._exported is False
    g.update_item(0, ThumbGridItem(exported=True))
    assert cell._exported is True


def test_watermark_widget_never_takes_the_mouse(qapp):
    wm = ExportedWatermark()
    assert wm.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert not wm.isVisible()


def test_media_canvas_watermark_host_api(qapp):
    from mira.ui.media.media_canvas import MediaCanvas
    canvas = MediaCanvas()
    # No image loaded → stays hidden even when asked.
    canvas.set_exported_watermark(True)
    assert not canvas._exported_watermark.isVisibleTo(canvas)
    # Deterministic pre-show geometry so the displayed-pixmap path runs
    # (it early-outs on a zero-sized label).
    canvas.resize(400, 300)
    canvas._photo_label.resize(300, 200)
    # Image present → shows; off → hides.
    pm = QPixmap(40, 40)
    pm.fill(Qt.GlobalColor.darkGray)
    canvas.set_preview_pixmap(pm)
    canvas.set_exported_watermark(True)
    assert canvas._exported_watermark.isVisibleTo(canvas)
    canvas.set_exported_watermark(False)
    assert not canvas._exported_watermark.isVisibleTo(canvas)


# --------------------------------------------------------------------------- #
# The single-export commit order contract moves to the Export surface
# (spec/66 Slice 5) — the equivalent pin will live in tests for that
# surface once it lands. The old EditPage-side contract is gone with
# Slice 4 (spec/66 §1.1 — Edit no longer triggers export).
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The setting
# --------------------------------------------------------------------------- #


def test_watermark_setting_ships_on():
    assert Settings().show_exported_watermark is True


# --------------------------------------------------------------------------- #
# delete_exported_file — the engine behind X-on-shipped (Commit B
# wires it from the Days Grid Export-phase toggle; here we pin the
# gateway contract in isolation).
# --------------------------------------------------------------------------- #


def test_delete_exported_file_unlinks_drops_lineage_and_clears_flag(tmp_path):
    """Happy path: a real file under ``Exported Media/`` disappears, its
    lineage row drops, ``edit_exported`` flips back to False, and the
    return struct reports the counts honestly."""
    eg = _make_eg(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        f = ship / "p1.jpg"
        f.write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json='{"look": "natural"}', exported_at="t"))
        eg.set_edit_exported("p1", True)
        assert eg.exported_item_ids() == {"p1"}
        assert f.is_file()

        result = eg.delete_exported_file("p1")
        assert result["rows_deleted"] == 1
        assert len(result["deleted_files"]) == 1
        assert result["missing_files"] == []
        assert not f.is_file()
        assert eg.exported_item_ids() == set()
        adj = eg.adjustment("p1")
        assert adj is not None and adj.edit_exported is False
    finally:
        eg.store.close()


def test_delete_exported_file_handles_already_missing_file(tmp_path):
    """The lineage row's file was manually removed off-disk — the rescan
    would clean it next pass, but if the user fires the action and the
    file is already gone, we still drop the row + flip the flag so the
    state is consistent. ``missing_files`` reports what was already
    absent for honesty."""
    eg = _make_eg(tmp_path)
    try:
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        eg.set_edit_exported("p1", True)

        result = eg.delete_exported_file("p1")
        assert result["rows_deleted"] == 1
        assert result["deleted_files"] == []
        assert result["missing_files"] == ["Exported Media/Dia 1/p1.jpg"]
        assert eg.exported_item_ids() == set()
        adj = eg.adjustment("p1")
        assert adj is not None and adj.edit_exported is False
    finally:
        eg.store.close()


def test_delete_exported_file_no_op_when_no_rows(tmp_path):
    eg = _make_eg(tmp_path)
    try:
        result = eg.delete_exported_file("p1")
        assert result == {
            "deleted_files": [], "missing_files": [], "rows_deleted": 0}
    finally:
        eg.store.close()


def test_delete_exported_file_drops_every_shipped_row_for_the_item(tmp_path):
    """A re-export under the spec/54 §8 versions-as-exports policy gives
    a single item multiple ``Exported Media/`` lineage rows (the second
    landed as ``name (2).jpg``). Undoing the ship removes every shipped
    file + every row for that item — the user gets back to "not exported"
    cleanly, no half-state."""
    eg = _make_eg(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        f1 = ship / "p1.jpg"
        f2 = ship / "p1 (2).jpg"
        f1.write_bytes(b"\xff\xd8\xff\xd9")
        f2.write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1 (2).jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))

        result = eg.delete_exported_file("p1")
        assert result["rows_deleted"] == 2
        assert not f1.is_file() and not f2.is_file()
        assert eg.exported_item_ids() == set()
    finally:
        eg.store.close()


def test_delete_exported_file_charter_safe_leaves_edited_media_alone(tmp_path):
    """``Edited Media/`` rows are third-party return *candidates*
    (spec/57 §3), not shipped finals. The helper only touches
    ``Exported Media/`` — never the third-party inbox, never the
    immutable ``Original Media/`` tree (the charter §7 invariant)."""
    eg = _make_eg(tmp_path)
    try:
        # A third-party return candidate — must survive the delete.
        eg.record_lineage(m.Lineage(
            export_relpath="Edited Media/LRC/p1-edit.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        # A real ship row alongside it.
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))

        result = eg.delete_exported_file("p1")
        assert result["rows_deleted"] == 1
        # The Edited Media/ inbox row survives — Mira's hands stay off
        # the third-party returns; only the shipped final is undone.
        assert eg.edit_candidate_relpath("p1") == (
            "Edited Media/LRC/p1-edit.jpg")
    finally:
        eg.store.close()


def test_delete_exported_file_cascades_to_cut_membership(tmp_path):
    """spec/61 §1.4 — cut_member.export_relpath REFERENCES
    lineage(export_relpath) ON DELETE CASCADE, so dropping the lineage
    row also drops every Cut membership pointing at that file. The Cut
    *definition* survives; only this file's membership goes."""
    eg = _make_eg(tmp_path)
    try:
        ship = tmp_path / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True)
        (ship / "p1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
            source_kind="item", source_item_id="p1",
            recipe_json="{}", exported_at="t"))
        # A Cut definition + a membership row pointing at the file.
        # Spec/81: Cut is frozen — expr_snapshot_json holds the formula,
        # filters live on the source DC (none here = ad-hoc Cut).
        eg.store.upsert(m.Cut(
            id="cut-1", tag="best", target_s=60, max_s=120,
            photo_s=4, expr_snapshot_json="[]",
            default_state="picked", music_category=None,
            created_at="t", updated_at="t",
            last_exported_at=None))
        eg.store.upsert(m.CutMember(
            cut_id="cut-1",
            export_relpath="Exported Media/Dia 1/p1.jpg",
            added_at="t"))
        # Sanity: membership is in place.
        rows = eg.store.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id='cut-1'"
        ).fetchone()[0]
        assert rows == 1

        eg.delete_exported_file("p1")
        # Cut definition survives.
        cut_rows = eg.store.conn.execute(
            "SELECT id FROM cut WHERE id='cut-1'"
        ).fetchall()
        assert len(cut_rows) == 1
        # Membership cascaded out via the FK.
        rows_after = eg.store.conn.execute(
            "SELECT COUNT(*) FROM cut_member WHERE cut_id='cut-1'"
        ).fetchone()[0]
        assert rows_after == 0
    finally:
        eg.store.close()


# --------------------------------------------------------------------------- #
# DaysGridPage._exported_ids_for_grid — settings gate on the corner
# exported badge wiring (spec/59 §8 / spec/66 §1.2). The wiring itself
# (passing the set into day_grid_cells) is covered by the existing
# day_grid_cells tests above; here we pin the per-page gate.
# --------------------------------------------------------------------------- #


def test_days_grid_exported_ids_returns_set_when_setting_on(qapp, tmp_path):
    """Indicator on (the default) → the page hands ``day_grid_cells``
    the shipped set so cells stamp ``exported=True`` for shipped items."""
    from mira.ui.pages.days_grid_page import DaysGridPage

    eg = _make_eg_picked(tmp_path)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
        source_kind="item", source_item_id="p1",
        recipe_json="{}", exported_at="t"))
    try:
        page = DaysGridPage()
        page.gateway = SimpleNamespace(
            settings=SimpleNamespace(load=lambda: Settings(
                show_exported_watermark=True)))
        page._eg = eg
        got = page._exported_ids_for_grid()
        assert got == {"p1"}
    finally:
        eg.store.close()


def test_days_grid_exported_ids_returns_none_when_setting_off(qapp, tmp_path):
    """Setting off → ``None`` so ``day_grid_cells`` stamps nothing and
    the corner badge stays off across every cell."""
    from mira.ui.pages.days_grid_page import DaysGridPage

    eg = _make_eg_picked(tmp_path)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/p1.jpg", phase="edit",
        source_kind="item", source_item_id="p1",
        recipe_json="{}", exported_at="t"))
    try:
        page = DaysGridPage()
        page.gateway = SimpleNamespace(
            settings=SimpleNamespace(load=lambda: Settings(
                show_exported_watermark=False)))
        page._eg = eg
        assert page._exported_ids_for_grid() is None
    finally:
        eg.store.close()


def test_days_grid_exported_ids_no_gateway_returns_none(qapp):
    """Smoke / no-gateway mode → no set (the smoke path drives
    ``exported`` straight on the GridItem instead)."""
    from mira.ui.pages.days_grid_page import DaysGridPage

    page = DaysGridPage()
    assert page._exported_ids_for_grid() is None


# --------------------------------------------------------------------------- #
# Picker / Editor viewport — the diagonal watermark per nav. The
# helper is the seam; we drive the chrome refresh directly with a fake
# viewport to keep the test compact (the full Picker/Editor end-to-end
# is covered by their own page tests).
# --------------------------------------------------------------------------- #


class _FakeViewport:
    """Records the boolean handed to ``set_exported_watermark`` so the
    test can assert per-navigation behaviour."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_exported_watermark(self, on: bool) -> None:
        self.calls.append(bool(on))


def test_picker_load_exported_state_reads_setting_and_lineage(
        qapp, tmp_path):
    """``PickerPage._load_exported_state`` returns the shipped set +
    the watermark gate. Setting on → (set, True); setting off → (set,
    False)."""
    from mira.ui.pages.picker_page import PickerPage

    eg = _make_eg(tmp_path)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/p2.jpg", phase="edit",
        source_kind="item", source_item_id="p2",
        recipe_json="{}", exported_at="t"))
    try:
        page = PickerPage()
        page.gateway = SimpleNamespace(
            settings=SimpleNamespace(load=lambda: Settings(
                show_exported_watermark=True)))
        page._eg = eg
        shipped, enabled = page._load_exported_state()
        assert shipped == {"p2"}
        assert enabled is True

        page.gateway = SimpleNamespace(
            settings=SimpleNamespace(load=lambda: Settings(
                show_exported_watermark=False)))
        shipped, enabled = page._load_exported_state()
        assert shipped == {"p2"}
        assert enabled is False
    finally:
        eg.store.close()


def test_editor_load_exported_state_reads_setting_and_lineage(
        qapp, tmp_path):
    """``EditorPage._load_exported_state`` mirrors ``PickerPage``."""
    from mira.ui.pages.editor_page import EditorPage

    eg = _make_eg(tmp_path)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/p3.jpg", phase="edit",
        source_kind="item", source_item_id="p3",
        recipe_json="{}", exported_at="t"))
    try:
        page = EditorPage()
        page.gateway = SimpleNamespace(
            settings=SimpleNamespace(load=lambda: Settings(
                show_exported_watermark=True)))
        page._eg = eg
        shipped, enabled = page._load_exported_state()
        assert shipped == {"p3"}
        assert enabled is True
    finally:
        eg.store.close()
