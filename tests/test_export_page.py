"""spec/66 §1.1 + spec/68 §3 — the Export-phase surface.

Pins:

* The pool is **all picked photos** in the event (the spec/66 "all picked
  keepers" rule, photo-only in the MVP — videos are a follow-up).
* The initial state is **green / picked** by default ("born green",
  spec/59 §8 carried over by spec/66): an item with no edit-phase row
  reads as ``picked`` here. An explicit ``edit/skipped`` row stays red.
* Clicking a Thumb **toggles** the item's edit-phase state — green ↔
  red, mirroring the locked spec/63 §4 binary ledger (P / X / Space / C
  reduce to "set the mark" on this surface, the cut-session precedent).
* The toolbar's **Pick all / Skip all** writes the bulk state through
  the gateway's ``set_items_phase_state`` (one-trip write).
* The header's "Export green (N)" primary disables when zero are green
  and matches the live counter.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.picked.status import STATE_PICKED, STATE_SKIPPED
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.ui.exported import ExportPage

NOW = "2026-06-14T00:00:00+00:00"


def _gateway(tmp_path: Path, base: Path) -> Gateway:
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
        index=EventsIndex(tmp_path / "events_index.json"),
    )
    gw.set_photos_base_path(str(base))
    return gw


def _make_event(gw: Gateway, base: Path, *, picked: tuple[str, ...],
                skipped: tuple[str, ...] = ()) -> "EventGateway":
    """A single-day event with ``picked`` photos kept at Pick, plus
    ``skipped`` photos that Pick discarded (they must NOT show up in the
    Export pool — the spec/66 pool is picked keepers only)."""
    items = []
    for i, iid in enumerate(picked + skipped):
        items.append(m.Item(
            id=iid, kind="photo", origin_relpath=f"d/{iid}.jpg",
            sha256=f"sha-{iid}", byte_size=1,
            materialized_at=NOW, materialized_phase="ingest",
            camera_id="G9M2",
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
            created_at=NOW, day_number=1, provenance="captured",
        ))
    states = [
        m.PhaseState(item_id=iid, phase="pick", state="picked")
        for iid in picked
    ] + [
        m.PhaseState(item_id=iid, phase="pick", state="skipped")
        for iid in skipped
    ]
    doc = m.EventDocument(
        event=m.Event(uuid="e1", name="Test", created_at=NOW, updated_at=NOW),
        cameras=[m.Camera(camera_id="G9M2")],
        trip_days=[
            m.TripDay(day_number=1, date="2026-04-01", description="Arrival"),
        ],
        items=items,
        phase_states=states,
    )
    return gw.create_event(doc, base / "Test")


@pytest.fixture
def gw_and_event(qapp, tmp_path):
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base, picked=("p1", "p2", "p3"), skipped=("p4",))
    # The gateway returned eg is owned by us; close at teardown.
    yield gw, "e1"
    try:
        eg.close()
    except Exception:                                            # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Pool
# --------------------------------------------------------------------------- #


def test_export_page_pool_is_pick_kept_photos_only(gw_and_event):
    """Pick-kept photos appear as cells; Pick-skipped photos do not."""
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    assert page.open_event(event_id) is True
    ids = {c.item_id for c in page._cells}
    assert ids == {"p1", "p2", "p3"}
    page._close_gateway()


def test_export_page_default_state_is_green(gw_and_event):
    """spec/59 §8 born-green default — an item with no edit-phase row
    reads as picked (green border) here."""
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    assert all(c.state == STATE_PICKED for c in page._cells)
    page._close_gateway()


def test_export_page_existing_skipped_row_paints_red(gw_and_event):
    """A pre-existing edit/skipped row survives; the cell paints red."""
    gw, event_id = gw_and_event
    eg = gw.open_event(event_id)
    try:
        eg.set_phase_state("p2", "edit", "skipped")
    finally:
        eg.close()
    page = ExportPage(gw)
    page.open_event(event_id)
    by_id = {c.item_id: c for c in page._cells}
    assert by_id["p2"].state == STATE_SKIPPED
    assert by_id["p1"].state == STATE_PICKED
    page._close_gateway()


# --------------------------------------------------------------------------- #
# Click + bulk toggles
# --------------------------------------------------------------------------- #


def test_export_page_click_toggles_state(gw_and_event):
    """Clicking a Thumb flips its edit-phase row green↔red and writes
    through the gateway."""
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    cell = page._find_cell("p1")
    assert cell is not None and cell.state == STATE_PICKED
    page._on_thumb_clicked("p1")
    assert cell.state == STATE_SKIPPED
    ps = page._eg.phase_state("p1", "edit")
    assert ps is not None and ps.state == STATE_SKIPPED
    page._on_thumb_clicked("p1")
    assert cell.state == STATE_PICKED
    page._close_gateway()


def test_export_page_pick_all_sets_every_cell_green(gw_and_event):
    """Pick all writes ``picked`` for every cell in one transaction."""
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    # Flip them all to skipped first so Pick all has work to do.
    for cid in ("p1", "p2", "p3"):
        page._on_thumb_clicked(cid)
    assert all(c.state == STATE_SKIPPED for c in page._cells)
    page._on_pick_all()
    assert all(c.state == STATE_PICKED for c in page._cells)
    # The gateway sees the writes.
    states = page._eg.phase_states("edit")
    assert all(states[cid].state == STATE_PICKED
               for cid in ("p1", "p2", "p3"))
    page._close_gateway()


def test_export_page_skip_all_drops_every_cell(monkeypatch, gw_and_event):
    """Skip all is destructive — gated by a design-system confirm. With
    the conftest auto-stub answering 'Yes' the bulk write proceeds."""
    # The conftest neutralises QMessageBox.question to Yes, but the
    # design-system dialog is a custom QDialog — stub the helper used.
    monkeypatch.setattr(
        "mira.ui.exported.export_page.confirm",
        lambda *args, **kwargs: True,
    )
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    page._on_skip_all()
    assert all(c.state == STATE_SKIPPED for c in page._cells)
    page._close_gateway()


def test_export_page_skip_all_cancelled_keeps_state(monkeypatch, gw_and_event):
    """If the user cancels the destructive confirm, nothing changes."""
    monkeypatch.setattr(
        "mira.ui.exported.export_page.confirm",
        lambda *args, **kwargs: False,
    )
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    before = [c.state for c in page._cells]
    page._on_skip_all()
    after = [c.state for c in page._cells]
    assert before == after
    page._close_gateway()


# --------------------------------------------------------------------------- #
# Header / counters
# --------------------------------------------------------------------------- #


def test_export_page_counter_reflects_green_fraction(gw_and_event):
    """The header counter + Export-green button text live-update."""
    gw, event_id = gw_and_event
    page = ExportPage(gw)
    page.open_event(event_id)
    assert "3 / 3" in page._count_label.text()
    assert "Export green (3)" in page._export_btn.text()
    assert page._export_btn.isEnabled()
    # Drop one — counter falls, button text follows.
    page._on_thumb_clicked("p1")
    assert "2 / 3" in page._count_label.text()
    assert "Export green (2)" in page._export_btn.text()
    # Drop them all — button disables, label shows 0/3.
    page._on_thumb_clicked("p2")
    page._on_thumb_clicked("p3")
    assert "0 / 3" in page._count_label.text()
    assert not page._export_btn.isEnabled()
    page._close_gateway()


# --------------------------------------------------------------------------- #
# Empty-state
# --------------------------------------------------------------------------- #


def test_export_page_empty_event_shows_empty_state(qapp, tmp_path):
    """No picked keepers → the empty-state label appears; the grid
    hides; the Export button is disabled."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base, picked=())
    try:
        page = ExportPage(gw)
        assert page.open_event("e1") is True
        # ``isVisibleTo(parent)`` checks the visibility flag without
        # requiring the page to be shown (the test harness is headless).
        assert page._empty_state.isVisibleTo(page)
        assert not page._scroll.isVisibleTo(page)
        assert not page._export_btn.isEnabled()
        page._close_gateway()
    finally:
        try:
            eg.close()
        except Exception:                                        # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# The Export round trip — the regression that the Inseto silent-fail
# missed: an actual export run writes lineage rows, and
# ``EventGateway.exported_item_ids()`` returns the source item ids.
# This drives the inline render path (no subprocess) so the test does
# not depend on the worker spawn working in the test sandbox.
# --------------------------------------------------------------------------- #


def test_inline_export_round_trip_yields_exported_item_ids(qapp, tmp_path):
    """End-to-end through the inline render path:

    items_with_sources  →  ``run_manifest_inline``  →  ``build_batch_result``
    →  the ExportPage commit logic  →  ``record_edit_export_lineage``
    →  ``EventGateway.exported_item_ids()`` returns the source ids.

    The bug this guards against was visible on Inseto na Varanda
    (2026-06-15): five Export runs reported finished, zero lineage
    rows, zero ``Exported Media/`` JPEGs. The commit closure
    short-circuited on empty ``ok_unit_ids`` and the gap was silent —
    the queue line still said *finished*. This pin would have flagged a
    regression that broke the writer well before it shipped.
    """
    from PIL import Image

    from core.cull_export import ExportFileType
    from core.export_manifest import ExportManifest, PhotoUnit
    from core.path_builder import exported_media_dir
    from core.render_worker import run_manifest_inline
    from core.worker_job import build_batch_result
    from mira.ui.edited._lineage import record_edit_export_lineage

    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    eg = _make_event(gw, base, picked=("p1", "p2", "p3"))
    try:
        # The fixture's items use ``origin_relpath = f"d/{iid}.jpg"`` —
        # write real JPEGs at those paths so the render can read them.
        event_root = base / "Test"
        for iid in ("p1", "p2", "p3"):
            src = event_root / "d" / f"{iid}.jpg"
            src.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 12), (100, 100, 100)).save(
                str(src), "JPEG", quality=92)

        # Manifest mirroring what ExportPage._submit_batch builds.
        dest_dir = exported_media_dir(event_root) / "Dia 1"
        units = tuple(
            PhotoUnit(
                unit_id=iid,
                source=str(event_root / "d" / f"{iid}.jpg"),
                dest_dir=str(dest_dir),
                # PhotoUnit.file_type holds the Enum *value*; passing the
                # name-cased ``"JPEG"`` was the Inseto na Varanda silent-
                # fail — every unit errored ("not a valid ExportFileType")
                # and the commit closure short-circuited on empty
                # ok_unit_ids.
                file_type=ExportFileType.JPEG.value,
                jpeg_quality=92,
                auto_on=False,
            )
            for iid in ("p1", "p2", "p3")
        )
        manifest = ExportManifest(units=units, clips=(), collision="unique")
        source_by_unit_id = {
            iid: event_root / "d" / f"{iid}.jpg"
            for iid in ("p1", "p2", "p3")
        }

        # Drive the inline path the same way ``BatchExportJob._run_inline``
        # does — same engine, no QThread/subprocess.
        messages = run_manifest_inline(manifest)
        result = build_batch_result(
            messages, source_by_unit_id, ran_inline=True)

        # Per-unit truth — every unit landed and the dest exists.
        assert result.ok_unit_ids == {"p1", "p2", "p3"}
        for iid in ("p1", "p2", "p3"):
            assert (dest_dir / f"{iid}.jpg").is_file()

        # The commit half of ExportPage._submit_batch — set the flag,
        # write the rows.
        ok_cells = [
            {"item_id": iid, "path": event_root / "d" / f"{iid}.jpg"}
            for iid in result.ok_unit_ids
        ]
        for c in ok_cells:
            eg.set_edit_exported(c["item_id"], True)
        record_edit_export_lineage(
            eg, event_root,
            items_with_sources=[(c["item_id"], c["path"]) for c in ok_cells],
            result=result,
        )

        # The verify: every source item shows up under ``Exported
        # Media/`` and the watermark / Share #exported queries see them.
        assert eg.exported_item_ids() == {"p1", "p2", "p3"}
        files = eg.exported_files()
        assert {Path(f.export_relpath).name for f in files} == {
            "p1.jpg", "p2.jpg", "p3.jpg"}
        # Every relpath sits under the ``Exported Media/`` prefix the
        # consumer queries filter on.
        for f in files:
            assert f.export_relpath.startswith("Exported Media/")
    finally:
        try:
            eg.close()
        except Exception:                                        # noqa: BLE001
            pass
