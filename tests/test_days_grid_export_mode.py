"""DaysGridPage in ``phase="export"`` mode — the spec/68 §3 reroute.

The Export phase no longer carries its own flat-grid surface; it
reuses the shared Phases → Days Lists → Days Grid spine like Pick and
Edit. These tests pin what the spec/68 §3 amendment specifies:

* Click toggles in place (no drill-in — ``item_activated`` doesn't fire).
* Born-green default: undecided cells render as ``picked``.
* X on a shipped cell calls
  :meth:`EventGateway.delete_exported_file` (the on-disk file
  disappears, the lineage row drops, the freshness flag clears, the
  Cut membership cascades — spec/61 §1.4).
* The Export-green trigger filters out items that are already shipped
  (re-ship is a deliberate un-export → re-export, not a hidden no-op).
"""
from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked.status import STATE_PICKED, STATE_SKIPPED
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.pages.days_grid_page import DaysGridPage

FIXED_NOW = "2026-06-15T12:00:00+00:00"
N_PHOTOS = 4


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 47) % 360, 120, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    p.setFont(QFont("Arial", 48, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"E{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)


def _doc() -> m.EventDocument:
    """Event with N photos, all Pick-kept (the Export pool)."""
    doc = m.EventDocument(event=m.Event(
        uuid="evt-x", name="Export reroute fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in range(1, N_PHOTOS + 1):
        doc.items.append(m.Item(
            id=f"x{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/x{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
        doc.phase_states.append(m.PhaseState(
            item_id=f"x{i}", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    for i in range(1, N_PHOTOS + 1):
        _write_jpeg(tmp_path / "Original Media" / f"x{i}.jpg", i)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-x")
    store.save_document(_doc())
    counter = itertools.count(1)
    eg = EventGateway(
        store, event_root=event_dir,
        now=_now, new_id=lambda: f"id-{next(counter)}")
    yield store, eg
    eg.close()


@pytest.fixture
def app_gateway(event_dir, store_and_gateway, monkeypatch, tmp_path):
    """An app-level ``Gateway`` whose ``open_event`` hands back a fresh
    EventGateway anchored at the same tmp event_root."""
    store, _ = store_and_gateway
    gw = Gateway(
        settings=SettingsRepo(tmp_path / "settings.json"),
    )
    counter = itertools.count(100)

    def _open_event(_event_id):
        return EventGateway(
            store, event_root=event_dir, now=_now,
            new_id=lambda: f"app-{next(counter)}")
    monkeypatch.setattr(gw, "open_event", _open_event)
    yield gw


def _ship_one(eg: EventGateway, event_dir: Path, item_id: str) -> Path:
    """Drop a shipped JPEG + matching lineage row + edit_exported
    flag onto ``item_id``. Returns the on-disk path."""
    ship = event_dir / "Exported Media" / "Dia 1"
    ship.mkdir(parents=True, exist_ok=True)
    dest = ship / f"{item_id}.jpg"
    dest.write_bytes(b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=f"Exported Media/Dia 1/{item_id}.jpg",
        phase="edit", source_kind="item",
        source_item_id=item_id, recipe_json='{"look": "natural"}',
        exported_at="t"))
    eg.set_edit_exported(item_id, True)
    return dest


# --------------------------------------------------------------------------- #
# open_for_day(phase="export") chrome contract
# --------------------------------------------------------------------------- #


def test_export_mode_chrome_swaps_labels_and_shows_export_button(
        qapp, app_gateway):
    """Pick all / Skip all relabel to the ship verbs, "Start a new
    pass…" hides, the Export-green primary reveals — and the storage
    phase under the hood is ``"edit"`` (spec/66 §1.1)."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    assert page._export_mode is True
    assert page._phase == "edit"            # shared decision storage
    assert page._identity_phase == "export"
    assert "Export" in page._pick_all_btn.text()
    assert "Drop" in page._skip_all_btn.text()
    # ``isVisibleTo(parent)`` checks the flag set by setVisible()
    # without needing the page to actually be on-screen.
    assert not page._new_pass_btn.isVisibleTo(page)
    assert page._export_btn.isVisibleTo(page)
    page.close_event()


def test_export_two_plus_versions_reshape_into_cluster_compare_orange(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 Slice 5 / Block 1 D2 — when a source item carries 2+
    ``Exported Media/`` lineage rows, the day grid replaces the flat
    cell with a versions cluster cover. Fresh members default
    ``compare``; the cover's state machine paints Compare orange as
    long as any member is undecided."""
    _, eg = store_and_gateway
    # Two third-party returns for the same source item — born compare.
    for rel, ext in (
        ("Exported Media/x1-Lightroom.jpg", "lr"),
        ("Exported Media/x1-Helicon.tif", "hl"),
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-Helicon.tif").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    cluster_id = "cluster:versions:x1"
    assert cluster_id in by_id, list(by_id)
    cover = by_id[cluster_id]
    assert cover.item_kind == "cluster"
    assert cover.cluster_type == "versions"
    assert cover.cluster_count == 2
    assert cover.state == "compare"
    # The original flat x1 cell is replaced — only the cluster remains.
    assert "x1" not in by_id
    page.close_event()


def test_export_versions_cluster_cover_thumb_is_newest_version(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 / Block 1 D5.A — the cluster cover's thumb is the
    newest version's actual file on disk (``versions_for_item`` sorts
    newest-first by exported_at). The sha256 is cleared so the cache
    key falls to path:<rel> instead of mis-serving the source thumb."""
    _, eg = store_and_gateway
    # Two third-party returns, distinct exported_at — Helicon is newer.
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x1-Lightroom.jpg",
        phase="edit", source_kind="item", source_item_id="x1",
        recipe_json=None,
        exported_at="2026-06-19T08:00:00",
        provenance="third_party", intent_state="compare",
    ))
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x1-Helicon.tif",
        phase="edit", source_kind="item", source_item_id="x1",
        recipe_json=None,
        exported_at="2026-06-19T09:30:00",   # newer
        provenance="third_party", intent_state="compare",
    ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-Helicon.tif").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cover = next(
        it for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    expected_newest = event_dir / "Exported Media" / "x1-Helicon.tif"
    assert cover._path == expected_newest
    # Cache key falls to path:<rel> instead of the source's sha256.
    assert cover._sha256 is None
    assert page._thumb_cache_key(cover) == f"path:{expected_newest}"
    page.close_event()


def test_export_versions_sub_grid_p_writes_intent_picked(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Drilling into a versions cluster surfaces every row; pressing P
    on a member writes ``lineage.intent_state='picked'`` via
    set_lineage_intent. The cover's state machine updates on the
    next refresh."""
    _, eg = store_and_gateway
    for rel in (
        "Exported Media/x1-LRC-a.jpg",
        "Exported Media/x1-LRC-b.jpg",
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    for name in ("x1-LRC-a.jpg", "x1-LRC-b.jpg"):
        (event_dir / "Exported Media" / name).write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cover = next(
        it for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    page._open_cluster(cover._cull_cluster)
    # In versions sub-grid: every member starts compare.
    assert all(it.state == "compare" for it in page._items)
    # Press P on the first member.
    page._apply_verb_at_index(0, "pick")
    # The intent_state landed on the lineage row.
    rows = {r.export_relpath: r for r in eg.versions_for_item("x1")}
    picked_relpath = page._items[0].item_id
    assert rows[picked_relpath].intent_state == "picked"
    page.close_event()


def test_export_versions_cover_state_machine(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Verify the Block 1 D3 cover state machine derives from member
    intent_states. The helper is exercised on a freshly-built page."""
    _, eg = store_and_gateway
    states = ["picked", "skipped", "compare"]
    for i, s in enumerate(states):
        rel = f"Exported Media/x1-v{i}.jpg"
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state=s,
        ))
        (event_dir / "Exported Media").mkdir(exist_ok=True)
        (event_dir / "Exported Media" / f"x1-v{i}.jpg").write_bytes(
            b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    # Any compare → cover Compare orange.
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "compare"
    # All picked → cover green.
    for s in eg.versions_for_item("x1"):
        eg.set_lineage_intent(s.export_relpath, "picked")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "picked"
    # All skipped → cover red.
    for s in eg.versions_for_item("x1"):
        eg.set_lineage_intent(s.export_relpath, "skipped")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "skipped"
    # Mixed picked + skipped → cover yellow (mixed).
    rows = eg.versions_for_item("x1")
    eg.set_lineage_intent(rows[0].export_relpath, "picked")
    eg.set_lineage_intent(rows[1].export_relpath, "skipped")
    eg.set_lineage_intent(rows[2].export_relpath, "picked")
    page._refresh_from_gateway()
    cover = next(
        it for it in page._items if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    assert cover.state == "mixed"
    page.close_event()


def test_export_mode_stamps_origin_wordmark_on_single_version_cells(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §2.1 / Block 2 — flat cells with one shipped row get the
    origin wordmark (LRC / Helicon / CO / Mira / ext) so the user sees
    at a glance which editor produced the file. Multi-version items
    become a versions cluster (Slice 5) — at the flat-cell layer
    cell_origin_label returns None for those and no badge is drawn."""
    _, eg = store_and_gateway
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x1-Lightroom-edit.jpg",
        phase="edit", source_kind="item", source_item_id="x1",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party"))
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/x2.jpg",
        phase="edit", source_kind="item", source_item_id="x2",
        recipe_json="{}", exported_at="2026-06-19T08:00:00"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom-edit.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "Dia 1").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "Dia 1" / "x2.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x1"].origin == "LRC"   # filename-inferred from "Lightroom"
    assert by_id["x2"].origin == "Mira"  # provenance default
    # The 0-version keepers carry no badge.
    assert by_id["x3"].origin is None
    assert by_id["x4"].origin is None
    page.close_event()


def test_export_pool_includes_skipped_with_shipped_file_and_flags_it(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §4.2 / Block 7 D1.B & D2.B — the Export grid's pool is
    picked keepers ∪ items with a file in ``Exported Media/``. A photo
    the user skipped in Pick but with a third-party return on disk
    still appears (so they can drop the file or re-Pick), carrying a
    ``skipped_in_pick`` flag for the indicator chip."""
    _, eg = store_and_gateway
    # Item x2 was Pick-picked; flip it to skipped, then drop a ship
    # row to simulate a third-party return for a skipped photo.
    eg.set_phase_state("x2", "pick", STATE_SKIPPED)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x2-LRC.jpg", phase="edit",
        source_kind="item", source_item_id="x2",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x2-LRC.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    # x2 (Pick-skipped + shipped) is in the pool and flagged.
    assert "x2" in by_id
    assert by_id["x2"].skipped_in_pick is True
    # x2 has a shipped row → 1-version default = green.
    assert by_id["x2"].state == STATE_PICKED
    # The other picked keepers (x1/x3/x4) still appear, not flagged,
    # and default red because they have no versions on disk.
    for iid in ("x1", "x3", "x4"):
        assert by_id[iid].skipped_in_pick is False
        assert by_id[iid].state == STATE_SKIPPED
    page.close_event()


def test_export_mode_default_state_is_red_when_no_versions(
        qapp, app_gateway):
    """spec/89 §1.1 / Block 1 D1.C — Export's flat-cell default is
    version-count-driven: a 0-version cell starts **red** (no intent
    to export), a 1-version cell starts green. The fixture's keepers
    carry no lineage rows yet, so every cell reads as ``skipped``."""
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    states = {it.item_id: it.state for it in page._items}
    assert all(s == "skipped" for s in states.values()), states
    page.close_event()


def test_export_mode_mira_only_intent_defaults_to_picked(
        qapp, app_gateway, store_and_gateway):
    """spec/89 §1.1 (Nelson 2026-06-19 lock) — a Mira-edited photo
    with no lineage row counts as ONE ship intent and the flat cell
    defaults to ``picked`` (Will export). Pre-fix, the grid only
    checked ``has_shipped`` (lineage rows), so Mira-only edits painted
    red on the grid even though the Days List bar already counted
    them green — the two surfaces disagreed."""
    _, eg = store_and_gateway
    # x2: a non-baseline Adjustment (Look + filter) — no lineage row.
    eg.save_adjustment(m.Adjustment(
        item_id="x2", look="natural", creative_filter="vivid"))
    # x4: a different shape of Mira intent — crop only.
    eg.save_adjustment(m.Adjustment(
        item_id="x4",
        crop_x=0.05, crop_y=0.05, crop_w=0.9, crop_h=0.9))

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    # Both Mira-edited photos read as Will export by default.
    assert by_id["x2"].state == "picked"
    assert by_id["x4"].state == "picked"
    # Untouched photos (no Adjustment, no lineage) stay Set aside.
    assert by_id["x1"].state == "skipped"
    assert by_id["x3"].state == "skipped"
    page.close_event()


# --------------------------------------------------------------------------- #
# Click semantics — toggle in place, no item_activated emission
# --------------------------------------------------------------------------- #


def test_export_mira_intent_plus_lrc_export_forms_cluster(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 Slice 5 (Nelson 2026-06-19 correction) — a source with
    Mira-edit intent (non-default adjustment) AND one third-party
    return on disk reads as TWO ship intents and joins a versions
    cluster. The user sees both side-by-side without rendering the
    Mira version first."""
    _, eg = store_and_gateway
    # Mira intent on x1 — a brighten Look + crop set off the unedited
    # baseline (EDITED_SQL matches both signals).
    eg.save_adjustment(m.Adjustment(
        item_id="x1", look="brighten",
        crop_x=0.05, crop_y=0.05, crop_w=0.9, crop_h=0.9))
    # One LRC return for the same source.
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x1-LRC.jpg",
        phase="edit", source_kind="item", source_item_id="x1",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party", intent_state="compare"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    by_id = {it.item_id: it for it in page._items}
    cluster_id = "cluster:versions:x1"
    assert cluster_id in by_id, list(by_id)
    cover = by_id[cluster_id]
    assert cover.cluster_type == "versions"
    assert cover.cluster_count == 2   # Mira intent + LRC export
    # Both members in compare → cover Compare orange.
    assert cover.state == "compare"
    # The flat x1 cell is gone.
    assert "x1" not in by_id

    # Drill in and confirm both members surface; verbs route correctly.
    page._open_cluster(cover._cull_cluster)
    by_member = {it.item_id: it for it in page._items}
    assert "mira:x1" in by_member
    assert "Exported Media/x1-LRC.jpg" in by_member
    assert by_member["mira:x1"].origin == "Mira"
    # P on the Mira member writes phase_state(edit, x1).
    idx = next(
        i for i, it in enumerate(page._items)
        if it.item_id == "mira:x1")
    page._apply_verb_at_index(idx, "pick")
    ps = eg.phase_state("x1", "edit")
    assert ps is not None and ps.state == "picked"
    # X on the LRC member writes lineage.intent_state.
    idx = next(
        i for i, it in enumerate(page._items)
        if it.item_id == "Exported Media/x1-LRC.jpg")
    page._apply_verb_at_index(idx, "skip")
    row = next(
        r for r in eg.versions_for_item("x1")
        if r.export_relpath == "Exported Media/x1-LRC.jpg")
    assert row.intent_state == "skipped"
    page.close_event()


def test_export_destructive_watermark_only_lights_when_picked_and_shipped(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §4.2 / Block 7 D3.B Slice 7 — the "Exported" stamp on
    the Export surface becomes a destructive cue: it only paints on
    cells where pressing X would unlink a real file. That means
    state=='picked' AND exported. Anything red has no destructive
    edge (the user has already armed the drop)."""
    _, source_eg = store_and_gateway
    _ship_one(source_eg, event_dir, "x2")

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # x2: shipped + intent_picked (default-green per Block 1 D1.C)
    # → destructive cue lights up.
    idx_x2 = next(i for i, it in enumerate(page._items) if it.item_id == "x2")
    cell_x2 = page._thumb_widgets[idx_x2]
    assert cell_x2._exported is True
    assert cell_x2._state == "picked"
    assert cell_x2._export_destructive_mode is True
    # Now flip x2 to red intent — the destructive cue should drop off
    # on the next repaint cycle.
    page._apply_verb_at_index(idx_x2, "skip")
    # Re-apply isn't visible in the unit test, but the state flipped
    # to skipped and the underlying file got unlinked (the existing
    # X-on-shipped behaviour); the item's exported flag clears too.
    assert page._items[idx_x2].exported is False
    page.close_event()


def test_export_mode_border_click_toggles_in_place(qapp, app_gateway):
    """spec/89 §3.1 / Block 5 D2.A — the border zone keeps the
    locked-grammar ``toggle`` verb on Export-mode flat cells. Center
    click opens the preview viewer instead (covered by a separate
    test); border click flips the intent without drilling in or
    opening a dialog."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    captured: list[str] = []
    page.item_activated.connect(captured.append)
    target = page._items[0]
    assert target.state == STATE_SKIPPED
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_grid_cell_border_clicked(0)
    assert captured == []                   # NO drill-in
    eg = page._eg
    ps = eg.phase_state(target.item_id, "edit")
    assert ps is not None and ps.state == STATE_PICKED
    assert page._items[0].state == STATE_PICKED
    page.close_event()


def test_export_mode_center_click_opens_preview_viewer(qapp, app_gateway):
    """spec/89 §3.1 / Block 5 D1.A — center click on a flat cell opens
    the read-only preview viewer; it does NOT mutate state, does NOT
    drill into a leaf surface. The headless flag keeps the modal
    exec from blocking the test."""
    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    captured: list[str] = []
    page.item_activated.connect(captured.append)
    target = page._items[0]
    prev_state = target.state
    page._on_thumb_clicked(target.item_id, page._thumb_widgets[0])
    # No drill-in, no state change — the click opens a viewer.
    assert captured == []
    assert page._items[0].state == prev_state
    # A dialog was constructed.
    dlg = getattr(page, "_last_preview_dialog", None)
    assert dlg is not None
    assert dlg._items, "preview dialog should carry the neighbour list"
    page.close_event()


def test_preview_dialog_carries_fullres_and_fullscreen_buttons(
        qapp, app_gateway):
    """spec/63 reuse — the preview dialog exposes the same
    "Full Resolution F10" + "Full Screen F11" ghost-button pair the
    Picker uses, wired through the canonical PhotoViewport."""
    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_thumb_clicked(
        page._items[0].item_id, page._thumb_widgets[0])
    dlg = page._last_preview_dialog
    # The two buttons exist and carry the canonical labels.
    assert "Full Resolution" in dlg._fullres_btn.text()
    assert "F10" in dlg._fullres_btn.text()
    assert "Full Screen" in dlg._fullscreen_btn.text()
    assert "F11" in dlg._fullscreen_btn.text()
    # The body is a PhotoViewport — the canonical spec/63 engine.
    from mira.ui.media.photo_viewport import PhotoViewport
    assert isinstance(dlg._viewport, PhotoViewport)
    # Viewport's corner inspect is suppressed — the labelled button
    # covers it (mirroring Picker's choice).
    assert dlg._viewport._corner_inspect_visible is False
    page.close_event()


def test_preview_dialog_f10_button_emits_viewport_truth(
        qapp, app_gateway):
    """The "Full Resolution F10" button fires the viewport's
    truth_requested signal — the same path F10 takes everywhere
    else in the app (PhotoViewport opens the inspection lens)."""
    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_thumb_clicked(
        page._items[0].item_id, page._thumb_widgets[0])
    dlg = page._last_preview_dialog
    truth_fired: list[bool] = []
    dlg._viewport.truth_requested.connect(
        lambda: truth_fired.append(True))
    dlg._fullres_btn.click()
    assert truth_fired == [True]
    page.close_event()


def test_preview_dialog_f11_button_toggles_dialog_fullscreen(
        qapp, app_gateway):
    """The "Full Screen F11" button and the viewport's
    fullscreen_requested signal both toggle the dialog's fullscreen
    state. F11 / F via the dialog's keyPressEvent also route here."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QKeyEvent

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_thumb_clicked(
        page._items[0].item_id, page._thumb_widgets[0])
    dlg = page._last_preview_dialog
    dlg.show()
    assert not dlg.isFullScreen()
    # F11 key → fullscreen on.
    dlg.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_F11,
        Qt.KeyboardModifier.NoModifier))
    assert dlg.isFullScreen()
    # F11 again → back to windowed.
    dlg.keyPressEvent(QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_F11,
        Qt.KeyboardModifier.NoModifier))
    assert not dlg.isFullScreen()
    # The labelled button is a checkable mirror of the dialog state.
    assert dlg._fullscreen_btn.isCheckable()
    dlg.close()
    page.close_event()


def test_preview_dialog_viewport_carries_one_item_per_preview(
        qapp, app_gateway):
    """The viewport receives a ViewportItem per PreviewItem; the
    initial index matches the cell the user clicked on. Stepping via
    the viewport's show_index updates the dialog's chrome too."""
    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Click cell 2 (third item) so the start index isn't 0.
    page._on_thumb_clicked(
        page._items[2].item_id, page._thumb_widgets[2])
    dlg = page._last_preview_dialog
    assert len(dlg._viewport.items()) == len(dlg._items)
    assert dlg._index == 2
    # Step back one → chrome syncs.
    dlg._viewport.show_index(1)
    assert dlg._index == 1
    page.close_event()


def test_preview_dialog_f10_inspects_developed_pixels_for_mira_items(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/89 §3.2 (Nelson 2026-06-19) — F10 on a develop-pipeline
    cell (0-version or virtual Mira member) inspects the
    WOULD-BE-EXPORTED pixels: the host runs
    develop_photo_array at full resolution (no max-edge cap) and
    feeds the developed pixmap to _InspectView. The user sees the
    actual export, not the raw source."""
    _, eg = store_and_gateway
    # x2 has a non-baseline Adjustment → develop_for_preview=True
    # when the host builds the PreviewItem.
    eg.save_adjustment(m.Adjustment(
        item_id="x2", look="natural", creative_filter="vivid"))

    # Track full-develop calls (the new full-res path F10 uses).
    full_develop_calls: list[str] = []
    from mira.ui.exported.preview_dialog import ExportPreviewDialog

    def _fake_full_develop(cls, item):
        full_develop_calls.append(item.item_id)
        # Return a tiny placeholder so _InspectView gets something to
        # open without us decoding a real image.
        from PyQt6.QtGui import QColor, QImage, QPixmap
        img = QImage(16, 12, QImage.Format.Format_RGB888)
        img.fill(QColor(50, 100, 150))
        return QPixmap.fromImage(img)
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap_full",
        classmethod(_fake_full_develop))

    # Stub _InspectView so the test doesn't open a real modal window.
    opened: list[tuple] = []
    class _FakeInspect:
        def __init__(self, base, af_point=None, *, path=None,
                     is_raw=False, with_tools=True, parent=None):
            opened.append((base, path, is_raw))
        def open_windowed(self): pass
        def setFocus(self): pass
        def close(self): pass
    monkeypatch.setattr(
        "mira.ui.media.photo_viewport._InspectView", _FakeInspect)

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    idx = next(
        i for i, it in enumerate(page._items) if it.item_id == "x2")
    page._on_thumb_clicked(
        page._items[idx].item_id, page._thumb_widgets[idx])
    dlg = page._last_preview_dialog
    # The viewport's internal F10 handler is suppressed — the dialog
    # owns the truth-request route.
    assert dlg._viewport._truth_internal is False
    # Fire F10 (via the labelled button or the truth_requested signal).
    dlg._fullres_btn.click()
    # The dialog ran the FULL-resolution develop pipeline (not the
    # bounded one used for the dialog body).
    assert full_develop_calls == ["x2"]
    # And opened the inspection lens with the developed pixmap (NOT
    # the source path's pixels).
    assert len(opened) == 1
    base, path, is_raw = opened[0]
    assert base is not None
    assert is_raw is False        # developed pixmap is never raw
    page.close_event()


def test_preview_dialog_f10_inspects_on_disk_file_for_shipped_items(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/89 §3.2 — for on-disk Mira renders + third-party returns,
    F10 still opens the file directly (the file IS the export, no
    pipeline to run)."""
    _, eg = store_and_gateway
    # Ship x3 as a third-party return.
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x3-LRC.jpg",
        phase="edit", source_kind="item", source_item_id="x3",
        recipe_json=None, exported_at="2026-06-19T08:00:00",
        provenance="third_party", intent_state="picked"))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    # A minimal valid JPEG so load_pixmap returns something non-null.
    from PyQt6.QtGui import QColor, QImage
    img = QImage(8, 8, QImage.Format.Format_RGB888)
    img.fill(QColor(80, 90, 100))
    assert img.save(
        str(event_dir / "Exported Media" / "x3-LRC.jpg"), "JPG", 90)
    eg.set_edit_exported("x3", True)

    full_develop_calls: list[str] = []
    from mira.ui.exported.preview_dialog import ExportPreviewDialog
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap_full",
        classmethod(lambda cls, item: full_develop_calls.append(item.item_id)))

    opened: list[tuple] = []
    class _FakeInspect:
        def __init__(self, base, af_point=None, *, path=None,
                     is_raw=False, with_tools=True, parent=None):
            opened.append((base, path, is_raw))
        def open_windowed(self): pass
        def setFocus(self): pass
        def close(self): pass
    monkeypatch.setattr(
        "mira.ui.media.photo_viewport._InspectView", _FakeInspect)

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    idx = next(
        i for i, it in enumerate(page._items) if it.item_id == "x3")
    page._on_thumb_clicked(
        page._items[idx].item_id, page._thumb_widgets[idx])
    dlg = page._last_preview_dialog
    dlg._fullres_btn.click()
    # No develop pipeline ran — the file IS the export.
    assert full_develop_calls == []
    # The lens opened with the on-disk file path.
    assert len(opened) == 1
    _base, path, _is_raw = opened[0]
    assert path is not None
    assert path.name == "x3-LRC.jpg"
    page.close_event()


def test_preview_dialog_does_not_pre_develop_neighbours(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/89 §11.3 (Nelson 2026-06-19 fix) — opening the dialog
    must NOT run the develop pipeline for every neighbour upfront.
    Pre-fix, _load_viewport called _develop_pixmap N times in
    __init__, blocking the dialog from painting for seconds on a big
    neighbour list. Post-fix, _develop_pixmap is only called for the
    focused item, and even that runs deferred via QTimer.singleShot
    (so the dialog paints the raw source first)."""
    _, eg = store_and_gateway
    # Two Mira-edited photos — both would trigger _develop_pixmap if
    # the dialog pre-developed everything. The fixture's other cells
    # also become develop targets (0 versions, no Mira → still
    # develop_for_preview=False on the host side).
    eg.save_adjustment(m.Adjustment(
        item_id="x2", look="natural", creative_filter="vivid"))
    eg.save_adjustment(m.Adjustment(
        item_id="x4", look="brighten"))

    develop_calls: list[str] = []

    def _fake_develop(cls, item):
        develop_calls.append(item.item_id)
        return None  # falls back to raw file read; we don't care here

    from mira.ui.exported.preview_dialog import ExportPreviewDialog
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap",
        classmethod(_fake_develop))
    # Stub QTimer.singleShot to NOT fire so we can observe the
    # synchronous path alone (lazy = nothing developed yet).
    from PyQt6.QtCore import QTimer
    monkeypatch.setattr(QTimer, "singleShot",
                        staticmethod(lambda ms, fn: None))

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Click x2 (a Mira-edited cell) — that becomes the focused item.
    idx = next(i for i, it in enumerate(page._items)
               if it.item_id == "x2")
    page._on_thumb_clicked(
        page._items[idx].item_id, page._thumb_widgets[idx])
    # No synchronous develop calls — the dialog ctor must NOT pre-
    # render. The deferred QTimer callback was stubbed out so a count
    # of 0 proves lazy-loading is in place.
    assert develop_calls == []
    page.close_event()


def test_preview_staleness_chip_fires_when_recipe_drifts(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 polish — the preview viewer's stale chip lights
    when the on-disk Mira render's ``recipe_json`` no longer matches
    the live Adjustment row.

    Drive the predicate directly with a duck-typed item so the
    Mira-intent-becomes-cluster reshape stays out of the way — the
    predicate routes by item_id, not by surface state."""
    import json as _json
    from types import SimpleNamespace
    from mira.store import models as _m
    from mira.ui.exported.batch import recipe_for_item

    _, eg = store_and_gateway
    # x2 — set the Adjustment first, derive the recipe from it, then
    # record lineage with that exact recipe so the cell starts FRESH.
    eg.store.upsert(_m.Adjustment(item_id="x2", look="natural"))
    fresh_recipe = recipe_for_item(eg, "x2")
    ship_dir = event_dir / "Exported Media" / "Dia 1"
    ship_dir.mkdir(parents=True, exist_ok=True)
    (ship_dir / "x2.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/x2.jpg",
        phase="edit", source_kind="item", source_item_id="x2",
        recipe_json=_json.dumps(fresh_recipe),
        exported_at="t", provenance="mira_render"))

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Fresh — recipe matches shipped row.
    assert page._is_preview_item_stale(
        SimpleNamespace(item_id="x2")) is False
    # Drift the Adjustment off the shipped recipe → stale.
    eg.store.upsert(_m.Adjustment(
        item_id="x2", look="natural",
        creative_filter="bw_high_contrast"))
    assert page._is_preview_item_stale(
        SimpleNamespace(item_id="x2")) is True
    # 0-version source: nothing on disk → fresh (no Mira row to diff).
    assert page._is_preview_item_stale(
        SimpleNamespace(item_id="x1")) is False
    page.close_event()


def test_compare_button_hidden_outside_versions_subgrid(
        qapp, app_gateway):
    """spec/89 §11.3 — the Compare button is sub-grid-only. Hidden
    on the plain day grid (no cluster open)."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    assert not page._compare_btn.isVisibleTo(page)
    page.close_event()


def test_compare_button_visible_inside_versions_subgrid(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 — Compare button reveals when the user drills
    into a versions cluster sub-grid; hides again on close."""
    _, eg = store_and_gateway
    for rel in (
        "Exported Media/x1-Lightroom.jpg",
        "Exported Media/x1-Helicon.tif",
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-Helicon.tif").write_bytes(
        b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    assert not page._compare_btn.isVisibleTo(page)
    cluster = next(
        it._cull_cluster for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    page._open_cluster(cluster)
    assert page._compare_btn.isVisibleTo(page)
    page._close_cluster()
    assert not page._compare_btn.isVisibleTo(page)
    page.close_event()


def test_compare_button_opens_dialog_with_per_version_tiles(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 — clicking Compare while inside the versions
    sub-grid opens a CompareVersionsDialog with one tile per visible
    member (lineage rows + virtual Mira member when present)."""
    _, eg = store_and_gateway
    for rel in (
        "Exported Media/x1-Lightroom.jpg",
        "Exported Media/x1-Helicon.tif",
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-Lightroom.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-Helicon.tif").write_bytes(
        b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page._compare_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cluster = next(
        it._cull_cluster for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    page._open_cluster(cluster)
    page._on_compare_versions()
    dlg = getattr(page, "_last_compare_dialog", None)
    assert dlg is not None
    # Two tiles for the two third-party returns.
    item_ids = [t.item_id() for t in dlg._tiles]
    assert sorted(item_ids) == [
        "Exported Media/x1-Helicon.tif",
        "Exported Media/x1-Lightroom.jpg",
    ]
    page.close_event()


def test_compare_dialog_toggle_routes_through_set_lineage_intent(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 — a tile click inside Compare routes through the
    existing per-version verb path: lineage members write
    ``lineage.intent_state`` via set_lineage_intent."""
    _, eg = store_and_gateway
    for rel in (
        "Exported Media/x1-a.jpg",
        "Exported Media/x1-b.jpg",
    ):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="compare",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-b.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    page = DaysGridPage(app_gateway)
    page._compare_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    cluster = next(
        it._cull_cluster for it in page._items
        if it.item_kind == "cluster"
        and it.item_id == "cluster:versions:x1"
    )
    page._open_cluster(cluster)
    page._on_compare_versions()
    dlg = page._last_compare_dialog
    # Simulate a click on the first tile — toggle from compare → picked.
    target_id = dlg._tiles[0].item_id()
    dlg.intent_toggle_requested.emit(target_id)
    row = next(
        r for r in eg.versions_for_item("x1")
        if r.export_relpath == target_id)
    assert row.intent_state == "picked"
    page.close_event()


def test_preview_staleness_chip_skips_third_party_cells(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 polish — third-party returns never paint the
    stale chip: the file IS the recipe, there's nothing to diff
    against. Even with a drifted Adjustment row, a third-party-only
    history reads as fresh."""
    from types import SimpleNamespace
    from mira.store import models as _m

    _, eg = store_and_gateway
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x3-LRC.jpg",
        phase="edit", source_kind="item", source_item_id="x3",
        recipe_json=None,
        exported_at="2026-06-19T09:00:00",
        provenance="third_party", intent_state="picked",
    ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x3-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.set_edit_exported("x3", True)
    eg.store.upsert(_m.Adjustment(
        item_id="x3", look="natural", creative_filter="film_punch"))

    page = DaysGridPage(app_gateway)
    page._preview_headless = True
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Day-grid path: the source id has no Mira-render row → False
    # even though the third-party row exists and Adjustment drifted.
    assert page._is_preview_item_stale(
        SimpleNamespace(item_id="x3")) is False
    # Versions sub-grid path: the cell IS the third-party lineage row;
    # provenance gate short-circuits before any recipe diff.
    assert page._is_preview_item_stale(
        SimpleNamespace(item_id="Exported Media/x3-LRC.jpg")) is False
    page.close_event()


# --------------------------------------------------------------------------- #
# X-on-shipped → un-export (the spec/68 §3 "no separate delete UI" rule)
# --------------------------------------------------------------------------- #


def test_x_on_shipped_cell_unlinks_file_drops_lineage_clears_flag(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Toggling a SHIPPED cell green→red in Export mode also calls
    ``delete_exported_file``: the on-disk JPEG vanishes, the lineage
    row drops, ``edit_exported`` flips back to False. This is the
    delete-export affordance — no separate verb, the locked X grammar
    carries it (spec/68 §3 second bullet)."""
    _, source_eg = store_and_gateway
    shipped_path = _ship_one(source_eg, event_dir, "x2")
    assert shipped_path.is_file()
    assert source_eg.exported_item_ids() == {"x2"}

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # The cell wears the shipped badge because the indicator wiring
    # already feeds the page (Commit A).
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x2"].exported is True

    # Border click → toggle green→red (spec/89 §3.1 / Block 5 D2.A;
    # center click now opens the preview viewer instead). The handler
    # detects the cell was shipped and calls delete_exported_file.
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x2")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_grid_cell_border_clicked(idx)

    # File gone, lineage row gone, edit_exported cleared.
    assert not shipped_path.is_file()
    assert source_eg.exported_item_ids() == set()
    adj = source_eg.adjustment("x2")
    assert adj is not None and adj.edit_exported is False
    # The cell's badge cleared too.
    assert page._items[idx].exported is False
    page.close_event()


def test_x_on_unshipped_cell_does_not_call_delete_exported_file(
        qapp, app_gateway):
    """Toggling an un-shipped cell green→red is a pure phase_state
    write — no spurious ``delete_exported_file`` call for an item
    that has nothing on disk."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # Spy on the gateway helper.
    eg = page._eg
    called: list[str] = []
    real = eg.delete_exported_file
    eg.delete_exported_file = lambda iid: called.append(iid) or real(iid)
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_grid_cell_border_clicked(0)
    assert called == []                     # no shipped state → no call
    page.close_event()


# --------------------------------------------------------------------------- #
# Pick mode is unchanged — the reroute is additive
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Bulk Drop-all in Export mode — the cascade (the correctness bug
# that landed in 51e57b7 and was caught by the user's eyeball).
# --------------------------------------------------------------------------- #


def test_bulk_drop_all_cascades_un_export_for_shipped_items(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """The bulk Drop-all button (``✗ Drop all``) in Export mode must
    delete the on-disk files for every shipped item it touches AND
    drop their lineage rows. The pre-fix bug: it wrote
    ``phase_state = skipped`` and stopped, leaving the JPEGs on disk
    and the corner badge still painting — the surface lied about
    its state."""
    _, source_eg = store_and_gateway
    shipped_x2 = _ship_one(source_eg, event_dir, "x2")
    shipped_x4 = _ship_one(source_eg, event_dir, "x4")
    assert shipped_x2.is_file() and shipped_x4.is_file()
    assert source_eg.exported_item_ids() == {"x2", "x4"}

    # Auto-confirm the destructive prompt for the test.
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda *args, **kwargs: True)

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_skip_all_clicked()

    # Shipped files are gone, lineage is empty, exported_item_ids
    # returns empty, and the corner badge cleared on every cell.
    assert not shipped_x2.is_file()
    assert not shipped_x4.is_file()
    assert source_eg.exported_item_ids() == set()
    for it in page._items:
        assert it.exported is False
        assert it.state == STATE_SKIPPED
    page.close_event()


def test_bulk_drop_all_confirm_text_names_shipped_count(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """The Drop-all confirm dialog in Export mode names the shipped
    count explicitly so the user knows the on-disk blast before
    confirming. (Without that, the prompt reads identically to a
    Pick-mode bulk Skip — invisible blast.)"""
    _, source_eg = store_and_gateway
    _ship_one(source_eg, event_dir, "x1")
    _ship_one(source_eg, event_dir, "x3")
    captured: list[tuple] = []

    def _capture_confirm(parent, title, body, primary_text=None):
        captured.append((title, body, primary_text))
        return False                        # cancel — keeps the test clean

    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm", _capture_confirm)
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_skip_all_clicked()

    assert len(captured) == 1
    title, body, primary = captured[0]
    assert "Drop all" in title and "4" in title       # 4 items total
    assert "2" in body                                  # 2 are shipped
    assert "Exported Media" in body
    assert "Original Media" in body                     # charter pin
    assert primary == "Drop"
    page.close_event()


def test_bulk_export_all_in_export_mode_uses_ship_verb_in_confirm(
        qapp, app_gateway, monkeypatch):
    """Pick-all in Export mode reads as "Export all" in the
    confirm — the user shouldn't see Pick-mode chrome on the
    Export-mode action."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda parent, title, body, primary_text=None:
            captured.append((title, body, primary_text)) or False)
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_pick_all_clicked()

    assert len(captured) == 1
    title, body, primary = captured[0]
    assert "Export all" in title
    assert "ship" in body.lower()
    assert primary == "Export"
    page.close_event()


# --------------------------------------------------------------------------- #
# Single X-on-shipped — silent, fast, UNDOABLE (spec/63 §4 Ctrl+Z
# + re-press-P quick-recover).
# --------------------------------------------------------------------------- #


def test_ctrl_z_after_x_on_shipped_restores_file_and_lineage(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Ctrl+Z after a silent X-on-shipped puts the on-disk file back,
    re-inserts the lineage row, and re-sets ``edit_exported`` — the
    silent unlink is trivially recoverable."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    _, source_eg = store_and_gateway
    shipped = _ship_one(source_eg, event_dir, "x2")
    original_bytes = shipped.read_bytes()
    assert source_eg.exported_item_ids() == {"x2"}

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")

    # X on the shipped cell → file vanishes.
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x2")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._apply_verb_at_index(idx, "skip")
    assert not shipped.is_file()
    assert source_eg.exported_item_ids() == set()

    # Ctrl+Z → file + lineage + flag come back.
    ev = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier, "z")
    page.keyPressEvent(ev)

    assert shipped.is_file()
    assert shipped.read_bytes() == original_bytes
    assert source_eg.exported_item_ids() == {"x2"}
    by_id = {it.item_id: it for it in page._items}
    assert by_id["x2"].exported is True
    assert by_id["x2"].state == STATE_PICKED
    page.close_event()


def test_repress_p_after_x_on_shipped_restores_file_and_lineage(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Pressing P (or otherwise re-greening the cell) after X-on-
    shipped IS the undo of that un-export — the user doesn't need to
    know about Ctrl+Z to recover one stray X."""
    _, source_eg = store_and_gateway
    shipped = _ship_one(source_eg, event_dir, "x3")
    original_bytes = shipped.read_bytes()

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    idx = next(i for i, it in enumerate(page._items) if it.item_id == "x3")
    page._thumb_widgets[idx].setFocus(Qt.FocusReason.MouseFocusReason)
    page._apply_verb_at_index(idx, "skip")
    assert not shipped.is_file()

    # Re-press P → re-pick should restore the file.
    page._apply_verb_at_index(idx, "pick")
    assert shipped.is_file()
    assert shipped.read_bytes() == original_bytes
    assert source_eg.exported_item_ids() == {"x3"}
    page.close_event()


def test_ctrl_z_with_empty_stack_is_a_no_op(qapp, app_gateway):
    """A Ctrl+Z with nothing to undo doesn't crash and doesn't mutate
    state — the no-op makes the key safe to press idly."""
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    states_before = [it.state for it in page._items]
    ev = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier, "z")
    page.keyPressEvent(ev)
    states_after = [it.state for it in page._items]
    assert states_before == states_after
    page.close_event()


def test_undo_stack_clears_on_close_event(qapp, app_gateway):
    """The captured JPEG bytes don't outlive the event session —
    closing the gateway drops the undo stack so memory doesn't
    leak across day re-opens."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    # The fixture's keepers carry no shipped rows, so cells default red
    # (spec/89 Block 1 D1.C); a "pick" verb flips the first cell green
    # and pushes the undo entry.
    page._apply_verb_at_index(0, "pick")
    assert len(page._undo_stack) == 1
    page.close_event()
    assert page._undo_stack == []


def test_pick_mode_chrome_unchanged(qapp, app_gateway):
    """Pick mode still shows the Pick verbs and emits item_activated on
    photo click — the Export reroute is additive, not a takeover."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="pick")
    assert page._export_mode is False
    assert page._phase == "pick"
    assert page._identity_phase == "pick"
    assert "Pick" in page._pick_all_btn.text()
    assert "Skip" in page._skip_all_btn.text()
    assert page._new_pass_btn.isVisibleTo(page)
    assert not page._export_btn.isVisibleTo(page)
    captured: list[str] = []
    page.item_activated.connect(captured.append)
    page._thumb_widgets[0].setFocus(Qt.FocusReason.MouseFocusReason)
    page._on_thumb_clicked(page._items[0].item_id, page._thumb_widgets[0])
    assert captured == [page._items[0].item_id]    # drill-in fires
    page.close_event()


# --------------------------------------------------------------------------- #
# spec/89 Slice 8 — Export now confirm modal + delete sweep + single-item
# Export-this re-render-ask.
# --------------------------------------------------------------------------- #


def test_export_now_button_label_is_locked_to_export_now(
        qapp, app_gateway):
    """spec/89 §5.1 D1.A — the locked button text is "Export now"
    (the legacy "↑ Export green" wording is gone)."""
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    assert "Export now" in page._export_btn.text()
    assert "green" not in page._export_btn.text().lower()
    page.close_event()


def test_export_now_modal_text_carries_n_and_m(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/89 §5.1 D2.B — the confirm modal's title reads "Render N ·
    Delete M files. Proceed?" with the actual counts the run would
    execute. M counts versions cluster lineage rows with
    intent_state='skipped' whose file is still on disk."""
    _, eg = store_and_gateway
    # Two skipped-intent third-party returns under x2 → cluster, M=2.
    for rel in ("Exported Media/x2-v1.jpg", "Exported Media/x2-v2.jpg"):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x2", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state="skipped",
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x2-v1.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x2-v2.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    # 0-version flat cells default red per spec/89 Block 1 D1.C; press
    # P explicitly on x1 + x3 to mark them green renders. x4 stays red
    # → render target N covers exactly the two we picked.
    eg.set_phase_state("x1", "edit", "picked")
    eg.set_phase_state("x3", "edit", "picked")
    captured: list[tuple] = []

    def _capture_confirm(parent, title, body, primary_text=None):
        captured.append((title, body, primary_text))
        return False                                # cancel — leave files
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm", _capture_confirm)

    # The batch_queue check fires before the modal — provide one so we
    # don't hang on the "Batch queue unavailable" error dialog.
    class _FakeQueue:
        def enqueue(self, *a, **kw):
            pass
    monkeypatch.setattr(
        DaysGridPage, "window",
        lambda self: type("W", (), {"batch_queue": _FakeQueue()})(),
    )

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_export_clicked()

    assert len(captured) == 1
    title, body, primary = captured[0]
    # N = 2 (x1 + x3 picked, render targets); M = 2 (the two skipped
    # versions of x2).
    assert "Render 2" in title
    assert "Delete 2 files" in title
    assert primary == "Run"
    # Body explains the destination directories.
    assert "Exported Media" in body
    assert "Original Media" in body                # charter pin
    # Cancel left both files in place + lineage intact.
    assert (event_dir / "Exported Media" / "x2-v1.jpg").is_file()
    assert (event_dir / "Exported Media" / "x2-v2.jpg").is_file()
    assert len(eg.versions_for_item("x2")) == 2
    page.close_event()


def test_export_now_run_deletes_red_intent_versions(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/89 §5.1 step 2 — Run executes the delete sweep before the
    render submit. After Run, every red-intent version file is gone
    and its lineage row is dropped."""
    _, eg = store_and_gateway
    rel_keep = "Exported Media/x1-keep.jpg"
    rel_drop = "Exported Media/x1-drop.jpg"
    for rel, intent in ((rel_keep, "picked"), (rel_drop, "skipped")):
        eg.record_lineage(m.Lineage(
            export_relpath=rel, phase="edit", source_kind="item",
            source_item_id="x1", recipe_json=None,
            exported_at="2026-06-19T08:00:00",
            provenance="third_party", intent_state=intent,
        ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x1-keep.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (event_dir / "Exported Media" / "x1-drop.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    # Mark x3 green so the render lane fires for at least one cell;
    # otherwise n_render==0 and the submit is skipped by design.
    eg.set_phase_state("x3", "edit", "picked")

    # Auto-accept the modal and short-circuit the render submit (we
    # only care about the delete sweep here). The function-local import
    # of submit_export_batch in DaysGridPage._on_export_clicked re-reads
    # the source module attribute at call time, so patching the source
    # module takes effect.
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda *args, **kwargs: True)
    submitted: list = []
    monkeypatch.setattr(
        "mira.ui.exported.batch.submit_export_batch",
        lambda *a, **kw: submitted.append((a, kw)) or True,
    )

    class _FakeQueue:
        def enqueue(self, *a, **kw):
            pass
    monkeypatch.setattr(
        DaysGridPage, "window",
        lambda self: type("W", (), {"batch_queue": _FakeQueue()})(),
    )

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_export_clicked()

    assert (event_dir / "Exported Media" / "x1-keep.jpg").is_file()
    assert not (event_dir / "Exported Media" / "x1-drop.jpg").is_file()
    rel_paths = {r.export_relpath for r in eg.versions_for_item("x1")}
    assert rel_paths == {rel_keep}             # the skipped row dropped
    # The render lane was still invoked for the other green keeper.
    assert len(submitted) == 1
    page.close_event()


def test_export_now_no_op_when_nothing_to_do(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """Empty plan (no green renders, no red files) shows an info dialog
    and skips the confirm + run entirely.

    spec/118 §3 — to keep the render set empty under the new "re-include
    stale items" rule, each ship's recipe must match the live
    Adjustment so no cell reads stale."""
    # Mark every keeper as already shipped with a recipe matching the
    # live Adjustment (look="original") so nothing reads stale.
    _, eg = store_and_gateway
    for iid in ("x1", "x2", "x3", "x4"):
        ship = event_dir / "Exported Media" / "Dia 1"
        ship.mkdir(parents=True, exist_ok=True)
        dest = ship / f"{iid}.jpg"
        dest.write_bytes(b"\xff\xd8\xff\xd9")
        eg.record_lineage(m.Lineage(
            export_relpath=f"Exported Media/Dia 1/{iid}.jpg",
            phase="edit", source_kind="item",
            source_item_id=iid,
            recipe_json='{"look": "original"}',
            exported_at="t"))
        eg.set_edit_exported(iid, True)
    seen: list = []
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.show_info",
        lambda parent, title, body: seen.append(("info", title, body)),
    )
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda *a, **kw: seen.append(("confirm", a, kw)) or True,
    )

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")
    page._on_export_clicked()

    kinds = [k for k, *_ in seen]
    assert kinds == ["info"]                  # info fired; no confirm
    page.close_event()


def test_export_this_re_render_ask_fires_when_mira_render_exists(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """spec/118 §3 — "Export this" on an item that already has a
    Mira-render version asks the LRC-style three-way Overwrite / Keep
    both / Cancel via :func:`ask_overwrite_or_keep_both` before
    submitting. Cancel skips the submit."""
    _, eg = store_and_gateway
    rel = "Exported Media/Dia 1/x2.jpg"
    eg.record_lineage(m.Lineage(
        export_relpath=rel, phase="edit", source_kind="item",
        source_item_id="x2", recipe_json='{"look": "natural"}',
        exported_at="2026-06-19T08:00:00",
        provenance="mira_render", intent_state="picked",
    ))
    (event_dir / "Exported Media" / "Dia 1").mkdir(parents=True, exist_ok=True)
    (event_dir / "Exported Media" / "Dia 1" / "x2.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.set_edit_exported("x2", True)

    captured: list = []

    def _capture_choice(parent):
        captured.append(parent)
        return None                           # Cancel — skip submit
    monkeypatch.setattr(
        "mira.ui.exported.collision_dialog."
        "ask_overwrite_or_keep_both",
        _capture_choice)
    submitted: list = []
    monkeypatch.setattr(
        "mira.ui.exported.batch.submit_export_batch",
        lambda *a, **kw: submitted.append((a, kw)) or True)

    class _FakeQueue:
        def enqueue(self, *a, **kw):
            pass
    monkeypatch.setattr(
        DaysGridPage, "window",
        lambda self: type("W", (), {"batch_queue": _FakeQueue()})(),
    )

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")

    class _StubDialog:
        accepted = False
        def accept(self):
            self.accepted = True
    dlg = _StubDialog()
    page._on_preview_export_this(dlg, "x2")

    assert len(captured) == 1, (
        "the three-way collision dialog must be the only ask")
    assert submitted == []                    # cancelled before submit
    assert not dlg.accepted                   # dialog stays open on cancel
    page.close_event()


def test_export_this_no_ask_when_no_mira_render(
        qapp, app_gateway, event_dir, store_and_gateway, monkeypatch):
    """Items without a Mira-render version go straight to submit — the
    re-render-ask never fires, even if third-party returns exist
    (those are additive, not replacements)."""
    _, eg = store_and_gateway
    # x3 carries a third-party return only — no Mira render.
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/x3-LRC.jpg", phase="edit",
        source_kind="item", source_item_id="x3", recipe_json=None,
        exported_at="2026-06-19T08:00:00",
        provenance="third_party", intent_state="picked",
    ))
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "x3-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.set_edit_exported("x3", True)

    captured: list = []
    monkeypatch.setattr(
        "mira.ui.pages.days_grid_page.confirm",
        lambda *a, **kw: captured.append((a, kw)) or True)
    submitted: list = []
    monkeypatch.setattr(
        "mira.ui.exported.batch.submit_export_batch",
        lambda *a, **kw: submitted.append((a, kw)) or True)

    class _FakeQueue:
        def enqueue(self, *a, **kw):
            pass
    monkeypatch.setattr(
        DaysGridPage, "window",
        lambda self: type("W", (), {"batch_queue": _FakeQueue()})(),
    )

    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-x", 1, title="Day", date_iso="2026-04-01", phase="export")

    class _StubDialog:
        accepted = False
        def accept(self):
            self.accepted = True
    dlg = _StubDialog()
    page._on_preview_export_this(dlg, "x3")

    assert captured == []                     # no re-render-ask fired
    assert len(submitted) == 1                # the submit happened
    assert dlg.accepted is True               # dialog closed
    # The submitted ExportCell points at x3 with the source path.
    _args, kwargs = submitted[0]
    assert len(kwargs["cells"]) == 1
    assert kwargs["cells"][0].item_id == "x3"
    page.close_event()
