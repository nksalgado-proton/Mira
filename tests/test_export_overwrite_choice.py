"""spec/118 §3 — Overwrite / Keep both choice at export.

Pins:

* The single-item ``Export this`` path replaces the legacy yes/no
  "re-render?" with a three-way Overwrite / Keep both / Cancel.
* Overwrite (CollisionPolicy.OVERRIDE) reuses the existing lineage row
  and ``export_relpath`` — recipe_json + exported_at refresh in place;
  Cut membership stays stable.
* Keep both (CollisionPolicy.UNIQUE) adds a "(2)" file with its own
  lineage row → forms a versions cluster.
* The batch ``↑ Export now`` confirm modal threads the run-level
  collision policy into ``submit_export_batch`` when ≥1 cell is
  edited-since-export.
* The :class:`CollisionPolicy` engine itself still honours OVERRIDE
  (atomic same-name replace) vs UNIQUE ((2) suffix).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.exported.batch import ExportCell, submit_export_batch
from mira.ui.exported.collision_dialog import (
    KEEP_BOTH, OVERWRITE,
)


FIXED_NOW = "2026-06-23T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(160, 100, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 67) % 360, 140, 220))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 88)


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-c", name="Collision fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in (1, 2):
        doc.items.append(m.Item(
            id=f"c{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/c{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
        doc.phase_states.append(m.PhaseState(
            item_id=f"c{i}", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    for i in (1, 2):
        _write_jpeg(tmp_path / "Original Media" / f"c{i}.jpg", i)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-c")
    store.save_document(_doc())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch, tmp_path):
    store, _ = store_and_gateway
    gw = Gateway(settings=SettingsRepo(tmp_path / "settings.json"))
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _ship_mira(eg: EventGateway, event_dir: Path, item_id: str,
               recipe_dict: dict, *, day_folder: str = "Dia 1") -> str:
    rel = f"Exported Media/{day_folder}/{item_id}.jpg"
    on_disk = event_dir / rel
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel, phase="edit", source_kind="item",
        source_item_id=item_id,
        recipe_json=json.dumps(recipe_dict),
        exported_at="2026-06-20T10:00:00",
        provenance="mira_render", intent_state="picked",
    ))
    eg.set_edit_exported(item_id, True)
    return rel


def _set_look(eg: EventGateway, item_id: str, look: str) -> None:
    adj = eg.adjustment(item_id) or m.Adjustment(item_id=item_id)
    adj.look = look
    eg.save_adjustment(adj)


# --------------------------------------------------------------------- #
# CollisionPolicy engine — regression
# --------------------------------------------------------------------- #


def test_collision_policy_override_replaces_in_place(tmp_path):
    """The engine's OVERRIDE policy must atomically replace the
    existing file at the SAME path — same dest_name, no "(2)" suffix.
    UNIQUE walks to ``stem (2).jpg``."""
    from core.cull_export import (
        CollisionPolicy, ExportItem, export_items,
    )
    src = tmp_path / "src.jpg"
    _write_jpeg(src, 7)
    dest_dir = tmp_path / "out"
    # First ship — lands as src.jpg.
    item = ExportItem(src=src, dest_dir=dest_dir, dest_name="src.jpg")
    r1 = export_items([item], collision=CollisionPolicy.UNIQUE)
    assert r1.written == [dest_dir / "src.jpg"]
    # Second ship under OVERRIDE — replaces in place, same path.
    r2 = export_items([item], collision=CollisionPolicy.OVERRIDE)
    assert (dest_dir / "src.jpg") in (r2.written + r2.overwritten)
    assert not (dest_dir / "src (2).jpg").exists()
    # Third ship under UNIQUE — keeps both: src.jpg + src (2).jpg.
    r3 = export_items([item], collision=CollisionPolicy.UNIQUE)
    assert any(
        Path(dest) == dest_dir / "src (2).jpg"
        for (_src, dest) in r3.renamed
    )


# --------------------------------------------------------------------- #
# Three-way dialog
# --------------------------------------------------------------------- #


def test_export_this_dialog_returns_three_outcomes(qapp):
    """The single-item dialog must report all three outcomes: primary
    → OVERWRITE, secondary → KEEP_BOTH, ghost/Esc → None."""
    from mira.ui.design.dialogs import MessageDialog
    from mira.ui.exported.collision_dialog import ask_overwrite_or_keep_both

    # Stub MessageDialog.exec to drive each branch directly via
    # _result_kind without showing a real modal.
    def _make_stub(kind):
        def _run(self):
            self._result_kind = kind
            return 0
        return _run

    with patch.object(MessageDialog, "exec", _make_stub("primary")):
        assert ask_overwrite_or_keep_both(None) == OVERWRITE
    with patch.object(MessageDialog, "exec", _make_stub("secondary")):
        assert ask_overwrite_or_keep_both(None) == KEEP_BOTH
    with patch.object(MessageDialog, "exec", _make_stub("cancel")):
        assert ask_overwrite_or_keep_both(None) is None


def test_batch_dialog_defaults_to_user_last_choice(qapp):
    """The batch run-level dialog flips which button is primary based
    on ``default`` so the user's last pick rides as the default."""
    from mira.ui.design.dialogs import MessageDialog
    from mira.ui.exported.collision_dialog import ask_batch_collision_policy

    def _make_stub(kind):
        def _run(self):
            self._result_kind = kind
            return 0
        return _run

    # default=KEEP_BOTH → primary is "Keep both" → primary click =
    # KEEP_BOTH; secondary click = OVERWRITE.
    with patch.object(MessageDialog, "exec", _make_stub("primary")):
        assert ask_batch_collision_policy(
            None, n_render=3, m_delete=0, n_stale=2,
            default=KEEP_BOTH,
        ) == KEEP_BOTH
    with patch.object(MessageDialog, "exec", _make_stub("secondary")):
        assert ask_batch_collision_policy(
            None, n_render=3, m_delete=0, n_stale=2,
            default=KEEP_BOTH,
        ) == OVERWRITE

    # default=OVERWRITE → primary is "Overwrite all" → primary click =
    # OVERWRITE.
    with patch.object(MessageDialog, "exec", _make_stub("primary")):
        assert ask_batch_collision_policy(
            None, n_render=3, m_delete=0, n_stale=2,
            default=OVERWRITE,
        ) == OVERWRITE


# --------------------------------------------------------------------- #
# Single-item Export this — OVERRIDE vs UNIQUE round trips
# --------------------------------------------------------------------- #


class _FakeBatchQueue:
    """In-memory stand-in for the app's :class:`BatchJobQueue`.
    Captures the manifest + invokes the commit closure synchronously
    with a hand-rolled OK result so the lineage write path runs in
    tests without spawning the render worker."""

    def __init__(self):
        self.calls: list = []

    def enqueue(self, worker, label, commit, *, job_type=None):
        self.calls.append({
            "manifest": worker._manifest,
            "label": label,
            "job_type": job_type,
            "commit": commit,
            "worker": worker,
        })


def _simulate_ok_render(call, dest_path: Path, *,
                        renamed_from: Path | None = None):
    """Fire the captured commit closure with a fake OK result so the
    lineage row + edit_exported flip land. ``dest_path`` is the file
    the engine would have written; ``renamed_from`` (the source path)
    is set for the UNIQUE "(2)" collision case so the lineage writer
    matches by source stem and routes the row to ``dest_path``."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"\xff\xd8\xff\xd9")
    unit = call["manifest"].units[0]

    class _Result:
        ok_unit_ids = {unit.unit_id}
        ok_clip_results = []
        resolved_by_name = {}
        already_present = []
        if renamed_from is not None:
            written: list = []
            overwritten: list = []
            renamed = [(renamed_from, dest_path)]
        else:
            written = [dest_path]
            overwritten: list = []
            renamed: list = []
    call["commit"](_Result())


def test_export_this_overwrite_reuses_lineage_row(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/118 §3 — OVERRIDE refreshes recipe_json + exported_at on
    the EXISTING lineage row (same export_relpath PK), so any Cut
    referencing it keeps the same membership."""
    _, eg_seed = store_and_gateway
    rel = _ship_mira(eg_seed, event_dir, "c1", {"look": "natural"})
    _set_look(eg_seed, "c1", "punchy")
    # Snapshot existing rows so we can compare counts.
    rows_before = eg_seed.versions_for_item("c1")
    assert len(rows_before) == 1
    assert rows_before[0].export_relpath == rel
    assert json.loads(rows_before[0].recipe_json)["look"] == "natural"

    from mira.ui.pages.days_grid_page import DaysGridPage
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-c", 1, title="Day", date_iso="2026-04-01", phase="export")

    # Drive _on_preview_export_this with OVERWRITE chosen.
    from mira.ui.exported import collision_dialog as cd
    queue = _FakeBatchQueue()
    page.window().batch_queue = queue  # ad-hoc attach for the test

    class _DummyDlg:
        def __init__(self):
            self.accepted = False

        def accept(self):
            self.accepted = True

    dlg = _DummyDlg()
    with patch.object(
            cd, "ask_overwrite_or_keep_both", lambda _: OVERWRITE):
        page._on_preview_export_this(dlg, "c1")

    assert dlg.accepted
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["manifest"].collision == "override"
    # The PhotoUnit's dest_dir must point at the EXISTING parent so
    # the OVERRIDE lands at the same export_relpath.
    unit = call["manifest"].units[0]
    expected_dest = event_dir / Path(rel).parent
    assert Path(unit.dest_dir) == expected_dest

    # Fire the commit with an OVERWRITE landing at the same path.
    _simulate_ok_render(call, event_dir / rel)
    rows_after = page._eg.versions_for_item("c1")
    assert len(rows_after) == 1, "OVERRIDE must not add a new lineage row"
    assert rows_after[0].export_relpath == rel
    # recipe_json refreshes to the new look + exported_at stamps now.
    assert json.loads(rows_after[0].recipe_json)["look"] == "punchy"
    assert rows_after[0].exported_at  # fresh stamp present
    page.close_event()


def test_export_this_keep_both_creates_cluster(
        qapp, app_gateway, event_dir, store_and_gateway):
    """KEEP_BOTH ships under CollisionPolicy.UNIQUE → a "(2)" file +
    its own lineage row; the item becomes a versions cluster."""
    _, eg_seed = store_and_gateway
    rel = _ship_mira(eg_seed, event_dir, "c1", {"look": "natural"})
    _set_look(eg_seed, "c1", "punchy")

    from mira.ui.pages.days_grid_page import DaysGridPage
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-c", 1, title="Day", date_iso="2026-04-01", phase="export")

    from mira.ui.exported import collision_dialog as cd
    queue = _FakeBatchQueue()
    page.window().batch_queue = queue

    class _DummyDlg:
        def accept(self):
            pass

    with patch.object(
            cd, "ask_overwrite_or_keep_both", lambda _: KEEP_BOTH):
        page._on_preview_export_this(_DummyDlg(), "c1")

    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["manifest"].collision == "unique"
    # KEEP_BOTH never sets dest_dir_override; the manifest routes via
    # ``day_labels``. Simulate the engine landing at a "(2)" sibling
    # of the original, mirroring what UNIQUE collision would do.
    unit = call["manifest"].units[0]
    final_dest = Path(unit.dest_dir) / "c1 (2).jpg"
    # Source-path stem keeps the lineage writer's match key as "c1";
    # the renamed bucket routes the row at final_dest (the engine's
    # UNIQUE collision walk).
    src_path = event_dir / "Original Media" / "c1.jpg"
    _simulate_ok_render(call, final_dest, renamed_from=src_path)
    rows_after = page._eg.versions_for_item("c1")
    assert len(rows_after) == 2, (
        "KEEP_BOTH must add a second lineage row alongside the existing one")
    relpaths = sorted(r.export_relpath for r in rows_after)
    assert rel in relpaths
    # The "(2)" file's lineage row is a new row, separate from the
    # original (different export_relpath PK).
    assert any("(2)" in r for r in relpaths if r != rel)
    page.close_event()


def test_export_this_cancel_skips_submit(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Cancel — neither overwrite nor keep both — must NOT enqueue a
    batch (no submit, no lineage write)."""
    _, eg_seed = store_and_gateway
    _ship_mira(eg_seed, event_dir, "c1", {"look": "natural"})
    _set_look(eg_seed, "c1", "punchy")

    from mira.ui.pages.days_grid_page import DaysGridPage
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-c", 1, title="Day", date_iso="2026-04-01", phase="export")

    from mira.ui.exported import collision_dialog as cd
    queue = _FakeBatchQueue()
    page.window().batch_queue = queue

    class _DummyDlg:
        def __init__(self):
            self.accepted = False

        def accept(self):
            self.accepted = True

    dlg = _DummyDlg()
    with patch.object(
            cd, "ask_overwrite_or_keep_both", lambda _: None):
        page._on_preview_export_this(dlg, "c1")
    assert queue.calls == []
    # The dialog should not have been .accept()ed — the user's still in
    # the preview viewer.
    assert dlg.accepted is False
    page.close_event()


# --------------------------------------------------------------------- #
# Batch confirm modal threads the run-level policy
# --------------------------------------------------------------------- #


def test_batch_export_now_threads_collision_into_submit_when_stale(
        qapp, app_gateway, event_dir, store_and_gateway):
    """↑ Export now: with ≥1 stale cell + user picks OVERWRITE all,
    submit_export_batch receives ``collision="override"`` and the
    manifest is built with COLLISION_OVERRIDE.

    The stale-flat-cell scenario: the user shipped with a non-default
    recipe baked into recipe_json, then reset the Adjustment back to
    default. The live recipe ({"look": "natural"}) diverges from the
    shipped recipe ({"look": "vivid"}) → stale. Because the Adjustment
    is at default, EDITED_SQL stays False → no Mira intent → the cell
    stays flat (no versions cluster forms), so the batch picks it up
    directly as a render cell."""
    _, eg_seed = store_and_gateway
    _ship_mira(eg_seed, event_dir, "c1", {"look": "vivid"})
    # Adjustment.look stays at default (None) → recipe drifts to the
    # default {"look": "natural"} but EDITED_SQL is False → flat cell.

    from mira.ui.pages.days_grid_page import DaysGridPage
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-c", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Confirm c1 IS a stale flat cell (sanity check the scenario).
    c1 = next(it for it in page._items if it.item_id == "c1")
    assert c1.item_kind == "photo", "stale c1 must remain a flat cell"
    assert c1.edited_since_export is True

    from mira.ui.exported import collision_dialog as cd
    queue = _FakeBatchQueue()
    page.window().batch_queue = queue

    with patch.object(
            cd, "ask_batch_collision_policy",
            lambda *a, **kw: OVERWRITE):
        page._on_export_clicked()

    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["manifest"].collision == "override"
    # The PhotoUnit was pinned to the existing lineage row's parent
    # so the atomic replace lands at the exact existing path.
    unit = call["manifest"].units[0]
    expected_dest = event_dir / "Exported Media" / "Dia 1"
    assert Path(unit.dest_dir) == expected_dest
    # And the page remembers the user's pick for next time.
    assert DaysGridPage._last_batch_collision == OVERWRITE
    page.close_event()


def test_render_cell_stale_check_routes_collision_dialog(
        qapp, app_gateway, event_dir, store_and_gateway):
    """The page-level helper ``_render_cell_is_stale`` is what gates
    the collision-dialog branch from the legacy confirm: any True
    entry in the run's render cells triggers the LRC-style ask.

    Pinning the helper directly keeps this test fast + free of the
    full ``_on_export_clicked`` event-loop machinery (which other
    tests in this file cover end-to-end)."""
    _, eg_seed = store_and_gateway
    _ship_mira(eg_seed, event_dir, "c1", {"look": "vivid"})
    from mira.ui.pages.days_grid_page import DaysGridPage
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-c", 1, title="Day", date_iso="2026-04-01", phase="export")

    # c1 stale (shipped recipe vivid, live default → diverged); c2
    # never shipped → not stale.
    stale = ExportCell(item_id="c1",
                       path=event_dir / "Original Media/c1.jpg",
                       day_number=1)
    fresh = ExportCell(item_id="c2",
                       path=event_dir / "Original Media/c2.jpg",
                       day_number=1)
    assert page._render_cell_is_stale(stale) is True
    assert page._render_cell_is_stale(fresh) is False
    # When OVERRIDE is chosen, _cell_with_override_dest pins the
    # stale cell to its existing lineage row's parent.
    pinned = page._cell_with_override_dest(stale)
    assert pinned.dest_dir_override == "Exported Media/Dia 1"
    # Fresh cell with no existing Mira row is left unchanged.
    pinned_fresh = page._cell_with_override_dest(fresh)
    assert pinned_fresh.dest_dir_override is None
    page.close_event()


def test_submit_export_batch_collision_argument_threads_through(
        qapp, app_gateway, event_dir, store_and_gateway):
    """submit_export_batch's new ``collision`` kwarg lands on the
    ExportManifest verbatim (mapped to the wire-string)."""
    _, eg_seed = store_and_gateway
    queue = _FakeBatchQueue()
    src = event_dir / "Original Media" / "c1.jpg"
    ok = submit_export_batch(
        eg_seed, app_gateway.settings, queue,
        event_name="t",
        cells=[ExportCell(item_id="c1", path=src, day_number=1)],
        day_labels={1: "Dia 1"},
        parent_widget=None,
        collision="override",
    )
    assert ok
    assert len(queue.calls) == 1
    assert queue.calls[0]["manifest"].collision == "override"

    queue2 = _FakeBatchQueue()
    submit_export_batch(
        eg_seed, app_gateway.settings, queue2,
        event_name="t",
        cells=[ExportCell(item_id="c1", path=src, day_number=1)],
        day_labels={1: "Dia 1"},
        parent_widget=None,
        # default is keep-both ("unique")
    )
    assert queue2.calls[0]["manifest"].collision == "unique"
