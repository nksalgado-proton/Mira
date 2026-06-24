"""spec/118 §2 — the "edited since export" predicate + cell flag.

Pins:

* :func:`mira.ui.exported.staleness.is_cell_stale` flags a flat cell
  whose live Adjustment row has diverged from the on-disk Mira render's
  recorded ``recipe_json`` (the same diff
  :class:`DaysGridPage._is_preview_item_stale` already uses for the
  preview dialog's chip).
* Stale clears once the export refreshes via OVERRIDE (the engine
  upserts ``recipe_json`` to the new live value).
* Third-party returns never read stale (their lineage row has no
  recipe — they ARE the export).
* The DaysGridPage Export-mode grid cells expose ``edited_since_export``
  per cell; the versions-cluster cover sets it iff any member is stale.
* The preview-dialog "Adjustments changed" chip still fires (no
  regression on the existing surface).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage, QPainter

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m
from mira.store.repo import EventStore
from mira.ui.exported.staleness import (
    is_cell_stale,
    is_cluster_cover_stale,
    is_lineage_row_stale,
)
from mira.ui.pages.days_grid_page import DaysGridPage


FIXED_NOW = "2026-06-23T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(160, 100, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 67) % 360, 140, 220))
    p = QPainter(img)
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 88)


def _doc() -> m.EventDocument:
    doc = m.EventDocument(event=m.Event(
        uuid="evt-s", name="Stale fixture",
        created_at=FIXED_NOW, updated_at=FIXED_NOW))
    doc.trip_days = [m.TripDay(day_number=1, date="2026-04-01")]
    doc.cameras = [m.Camera(camera_id="G9")]
    for i in (1, 2, 3):
        doc.items.append(m.Item(
            id=f"s{i}", kind="photo", created_at=FIXED_NOW,
            provenance="captured",
            origin_relpath=f"Original Media/s{i}.jpg",
            sha256=f"{i:064d}", byte_size=1000,
            materialized_at=FIXED_NOW, materialized_phase="ingest",
            camera_id="G9", day_number=1,
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
        ))
        doc.phase_states.append(m.PhaseState(
            item_id=f"s{i}", phase="pick", state="picked"))
    return doc


@pytest.fixture
def event_dir(tmp_path):
    for i in (1, 2, 3):
        _write_jpeg(tmp_path / "Original Media" / f"s{i}.jpg", i)
    return tmp_path


@pytest.fixture
def store_and_gateway(event_dir):
    store = EventStore.create(event_dir / "event.db", event_id="evt-s")
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


def _set_look(eg: EventGateway, item_id: str, look: str) -> None:
    """Write an Adjustment row with the given Look so recipe_for_item
    reads a divergent recipe."""
    adj = eg.adjustment(item_id) or m.Adjustment(item_id=item_id)
    adj.look = look
    eg.save_adjustment(adj)


def _ship_mira(eg: EventGateway, event_dir: Path, item_id: str,
               recipe_dict: dict, *, align_adj: bool = True) -> str:
    """Drop a Mira-render lineage row + matching on-disk file for
    ``item_id`` with ``recipe_dict`` baked into recipe_json. By
    default aligns Adjustment.look to ``recipe_dict["look"]`` so a
    freshly-shipped cell reads as **clean**. Pass ``align_adj=False``
    when you need the Adjustment to stay at its default so EDITED_SQL
    reads False — useful for keeping the grid cell flat (no Mira
    intent → no versions cluster). Returns the export_relpath."""
    rel = f"Exported Media/Dia 1/{item_id}.jpg"
    on_disk = event_dir / rel
    on_disk.parent.mkdir(parents=True, exist_ok=True)
    on_disk.write_bytes(b"\xff\xd8\xff\xd9")
    if align_adj:
        look_in_recipe = recipe_dict.get("look", "natural")
        _set_look(eg, item_id, look_in_recipe)
    eg.record_lineage(m.Lineage(
        export_relpath=rel, phase="edit", source_kind="item",
        source_item_id=item_id,
        recipe_json=json.dumps(recipe_dict),
        exported_at="2026-06-20T10:00:00",
        provenance="mira_render",
        intent_state="picked",
    ))
    eg.set_edit_exported(item_id, True)
    return rel


# --------------------------------------------------------------------- #
# predicate
# --------------------------------------------------------------------- #


def test_predicate_flags_recipe_diverged_item(
        qapp, store_and_gateway, event_dir):
    """is_cell_stale fires once the live recipe drifts from the
    Mira render's recorded recipe_json."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {"look": "natural"})
    # Recipe still matches — clean.
    assert is_cell_stale(eg, "s1") is False
    # Diverge: change the look. Predicate flips.
    _set_look(eg, "s1", "punchy")
    assert is_cell_stale(eg, "s1") is True


def test_predicate_clears_after_override_re_export(
        qapp, store_and_gateway, event_dir):
    """spec/118 §3 — after an OVERRIDE refresh, the lineage row's
    recipe_json upserts to the new value and the cell goes clean."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {"look": "natural"})
    _set_look(eg, "s1", "punchy")
    assert is_cell_stale(eg, "s1") is True
    # Simulate the OVERRIDE: re-record_lineage at the same
    # export_relpath with the new recipe (upsert via the same PK).
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/s1.jpg",
        phase="edit", source_kind="item", source_item_id="s1",
        recipe_json=json.dumps({"look": "punchy"}),
        exported_at="2026-06-23T15:00:00",
        provenance="mira_render",
        intent_state="picked",
    ))
    assert is_cell_stale(eg, "s1") is False


def test_predicate_never_flags_third_party_return(
        qapp, store_and_gateway, event_dir):
    """Third-party returns carry no recipe (recipe_json is NULL) — the
    file IS the export. Stale must never fire on them, regardless of
    the live Adjustment row."""
    _, eg = store_and_gateway
    rel = "Exported Media/s1-Lightroom.jpg"
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "s1-Lightroom.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel, phase="edit", source_kind="item",
        source_item_id="s1", recipe_json=None,
        exported_at="2026-06-23T10:00:00",
        provenance="third_party",
        intent_state="picked",
    ))
    # Even with an aggressive live edit, stay clean.
    _set_look(eg, "s1", "punchy")
    assert is_cell_stale(eg, "s1") is False


def test_predicate_zero_versions_is_not_stale(
        qapp, store_and_gateway, event_dir):
    """A picked-but-never-exported item has no Mira render to compare
    against. Stale = False; the item just hasn't shipped yet."""
    _, eg = store_and_gateway
    _set_look(eg, "s1", "punchy")
    assert is_cell_stale(eg, "s1") is False


def test_predicate_virtual_mira_member_is_never_stale(
        qapp, store_and_gateway):
    """Virtual Mira cluster members carry the live edit by definition
    — there's no on-disk Mira render yet to compare against."""
    _, eg = store_and_gateway
    _set_look(eg, "s1", "punchy")
    assert is_cell_stale(eg, "mira:s1") is False


def test_predicate_resolved_params_in_shipped_recipe_dont_force_stale(
        qapp, store_and_gateway, event_dir):
    """The lineage writer adds ``resolved_params`` + ``tone_scaling``
    to the shipped recipe snapshot for archival; the live recipe never
    emits them. A clean re-read must not flag stale just because the
    archival snapshot is richer."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {
        "look": "natural",
        "resolved_params": {"exposure": 0.0},
        "tone_scaling": {"highlights": 1.0},
    })
    assert is_cell_stale(eg, "s1") is False


def test_predicate_falls_back_to_false_on_missing_gateway():
    """Defensive: ``None`` gateway / no event_root — quietly return
    False so callers can splat without a guard."""
    assert is_cell_stale(None, "s1") is False


# --------------------------------------------------------------------- #
# cluster cover
# --------------------------------------------------------------------- #


def test_cluster_cover_stale_when_any_member_stale(
        qapp, store_and_gateway, event_dir):
    """spec/118 §2 — cluster cover reads stale iff ≥1 Mira-render
    member is stale. Third-party members alone never flip the cover."""
    _, eg = store_and_gateway
    # Mira render — clean.
    _ship_mira(eg, event_dir, "s1", {"look": "natural"})
    assert is_cluster_cover_stale(eg, "s1") is False
    # Add a third-party return — still clean.
    rel_ext = "Exported Media/s1-LRC.jpg"
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "s1-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel_ext, phase="edit", source_kind="item",
        source_item_id="s1", recipe_json=None,
        exported_at="2026-06-20T10:30:00",
        provenance="third_party",
        intent_state="picked",
    ))
    assert is_cluster_cover_stale(eg, "s1") is False
    # Now diverge the live recipe — the Mira member goes stale → cover stale.
    _set_look(eg, "s1", "punchy")
    assert is_cluster_cover_stale(eg, "s1") is True


def test_lineage_row_helper_third_party_passes_through(
        qapp, store_and_gateway, event_dir):
    """is_lineage_row_stale stays False on a third-party row even if
    the live recipe is wildly different — the row has no recipe to
    diff against."""
    _, eg = store_and_gateway
    rel = "Exported Media/s2-LRC.jpg"
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "s2-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel, phase="edit", source_kind="item",
        source_item_id="s2", recipe_json=None,
        provenance="third_party",
        intent_state="picked",
    ))
    _set_look(eg, "s2", "punchy")
    row = eg.versions_for_item("s2")[0]
    assert is_lineage_row_stale(eg, row) is False


# --------------------------------------------------------------------- #
# grid cell + cluster cover expose the flag
# --------------------------------------------------------------------- #


def test_export_grid_cell_flags_edited_since_export(
        qapp, app_gateway, event_dir, store_and_gateway):
    """The Export grid cell carries ``edited_since_export=True`` when
    the source item's recipe has drifted from its on-disk Mira render.

    Setup uses ``align_adj=False`` so the Adjustment row stays at
    default ("original" via :func:`set_edit_exported`) → SQL EDITED
    False → no Mira intent → cell stays flat. With the shipped recipe
    baked at "vivid" and the live recipe at "original", s1 is stale.
    s2 ships with the matching "original" → clean."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {"look": "vivid"}, align_adj=False)
    _ship_mira(eg, event_dir, "s2", {"look": "original"}, align_adj=False)
    page = DaysGridPage(app_gateway)
    assert page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    by_id = {it.item_id: it for it in page._items}
    assert by_id["s1"].edited_since_export is True
    assert by_id["s2"].edited_since_export is False
    page.close_event()


def test_export_grid_cell_clean_when_no_mira_render(
        qapp, app_gateway, event_dir, store_and_gateway):
    """A picked-but-never-exported cell shows no stale flag even
    when the live Adjustment is non-default."""
    _, eg = store_and_gateway
    _set_look(eg, "s1", "punchy")
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    cell = next(it for it in page._items if it.item_id == "s1")
    assert cell.edited_since_export is False
    page.close_event()


def test_versions_cluster_cover_flags_stale_when_any_member_stale(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Cluster cover carries the stale flag whenever any Mira-render
    member would itself flag stale."""
    _, eg = store_and_gateway
    # Two Mira renders for s1 → forms a versions cluster.
    _ship_mira(eg, event_dir, "s1", {"look": "natural"}, align_adj=False)
    eg.record_lineage(m.Lineage(
        export_relpath="Exported Media/Dia 1/s1 (2).jpg",
        phase="edit", source_kind="item", source_item_id="s1",
        recipe_json=json.dumps({"look": "natural"}),
        exported_at="2026-06-21T10:00:00",
        provenance="mira_render",
        intent_state="picked",
    ))
    (event_dir / "Exported Media" / "Dia 1" / "s1 (2).jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    # Drift the live recipe — the newest Mira render is now stale,
    # so the cluster cover flips. _set_look also creates a Mira
    # intent → 2 lineage + 1 mira = 3 intents → cluster definitely
    # forms.
    _set_look(eg, "s1", "punchy")
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    cover = next(
        it for it in page._items
        if it.item_id == "cluster:versions:s1"
    )
    assert cover.edited_since_export is True
    page.close_event()


def test_versions_cluster_subgrid_members_carry_stale_flag(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Drilling in: each Mira-render member exposes its own stale
    flag; third-party members never do."""
    _, eg = store_and_gateway
    # One Mira render + one third-party return → 2-intent cluster.
    _ship_mira(eg, event_dir, "s1", {"look": "natural"}, align_adj=False)
    rel_ext = "Exported Media/s1-LRC.jpg"
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "s1-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel_ext, phase="edit", source_kind="item",
        source_item_id="s1", recipe_json=None,
        exported_at="2026-06-20T10:30:00",
        provenance="third_party",
        intent_state="picked",
    ))
    _set_look(eg, "s1", "punchy")
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    cover = next(
        it for it in page._items
        if it.item_id == "cluster:versions:s1"
    )
    page._open_cluster(cover._cull_cluster)
    by_id = {it.item_id: it for it in page._items}
    mira_member_id = "Exported Media/Dia 1/s1.jpg"
    third_party_member_id = rel_ext
    assert by_id[mira_member_id].edited_since_export is True
    assert by_id[third_party_member_id].edited_since_export is False
    page.close_event()


def test_preview_dialog_staleness_chip_still_fires_for_subgrid_member(
        qapp, app_gateway, event_dir, store_and_gateway):
    """Sub-grid item_id is a lineage relpath, not a source id. The
    preview chip path still flips correctly for stale Mira-render
    members."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {"look": "natural"}, align_adj=False)
    rel_ext = "Exported Media/s1-LRC.jpg"
    (event_dir / "Exported Media").mkdir(exist_ok=True)
    (event_dir / "Exported Media" / "s1-LRC.jpg").write_bytes(
        b"\xff\xd8\xff\xd9")
    eg.record_lineage(m.Lineage(
        export_relpath=rel_ext, phase="edit", source_kind="item",
        source_item_id="s1", recipe_json=None,
        provenance="third_party", intent_state="picked",
    ))
    _set_look(eg, "s1", "punchy")
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    cover = next(
        it for it in page._items
        if it.item_id == "cluster:versions:s1"
    )
    page._open_cluster(cover._cull_cluster)
    by_id = {it.item_id: it for it in page._items}
    mira_member = by_id["Exported Media/Dia 1/s1.jpg"]
    third_party_member = by_id[rel_ext]
    assert page._is_preview_item_stale(mira_member) is True
    assert page._is_preview_item_stale(third_party_member) is False
    page.close_event()


# --------------------------------------------------------------------- #
# Preview dialog "Adjustments changed" chip regression
# --------------------------------------------------------------------- #


def test_preview_dialog_staleness_chip_still_fires(
        qapp, app_gateway, event_dir, store_and_gateway):
    """spec/89 §11.3 polish — the preview viewer's "Adjustments changed
    — Export to refresh" chip rides the same predicate that now feeds
    the grid badge. Regress for a stale flat cell."""
    _, eg = store_and_gateway
    _ship_mira(eg, event_dir, "s1", {"look": "vivid"}, align_adj=False)
    # Flat stale cell — Adjustment.look stays at default ("original"),
    # live recipe = {"look": "natural"} ≠ shipped {"look": "vivid"}.
    page = DaysGridPage(app_gateway)
    page.open_for_day(
        "evt-s", 1, title="Day", date_iso="2026-04-01",
        phase="export")
    cell = next(it for it in page._items if it.item_id == "s1")
    assert page._is_preview_item_stale(cell) is True
    page.close_event()
