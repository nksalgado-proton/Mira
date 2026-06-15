"""Export-phase round trip — the engine + lineage contract (spec/66
§1.1 + spec/68 §3).

History: this file used to pin the standalone ``ExportPage`` (the
flat-grid MVP at ``mira/ui/exported/export_page.py``). That surface
retired with the spec/68 §3 reroute (Export now rides the same Phases
→ Days Lists → Days Grid spine as Pick/Edit, using
:class:`~mira.ui.pages.days_grid_page.DaysGridPage` in
``phase="export"`` mode and the lifted batch submitter
:func:`mira.ui.exported.batch.submit_export_batch`).

What survives here is the end-to-end engine contract — the one pin
that guards the Inseto na Varanda silent-fail (commit ``4017cd8``):
items_with_sources → ``run_manifest_inline`` → ``build_batch_result``
→ the commit closure → ``record_edit_export_lineage`` →
``EventGateway.exported_item_ids()`` returns the source ids. Any
regression that breaks the manifest → JPEG → lineage path lights up
here, irrespective of which surface drove the batch.

The Days Grid Export-mode UX (toggle in place, X-on-shipped cleanup,
"Export green" trigger) is pinned by ``tests/test_days_grid_page.py``
(added with the reroute).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.store import models as m

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
    ``skipped`` photos that Pick discarded."""
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


# --------------------------------------------------------------------------- #
# The Export round trip — engine + lineage. Drives the inline render
# path (no subprocess) so the test doesn't depend on worker spawn in
# the sandbox.
# --------------------------------------------------------------------------- #


def test_inline_export_round_trip_yields_exported_item_ids(qapp, tmp_path):
    """End-to-end through the inline render path:

    items_with_sources  →  ``run_manifest_inline``  →  ``build_batch_result``
    →  the commit closure  →  ``record_edit_export_lineage``  →
    ``EventGateway.exported_item_ids()`` returns the source ids.

    Guards against the Inseto na Varanda silent-fail (2026-06-15,
    commit ``4017cd8``): five Export runs reported finished, zero
    lineage rows, zero ``Exported Media/`` JPEGs. The commit closure
    short-circuited on empty ``ok_unit_ids`` and the gap was silent —
    the queue line still said *finished*."""
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
        # The fixture items use ``origin_relpath = f"d/{iid}.jpg"`` — write
        # real JPEGs at those paths so the render can read them.
        event_root = base / "Test"
        for iid in ("p1", "p2", "p3"):
            src = event_root / "d" / f"{iid}.jpg"
            src.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (16, 12), (100, 100, 100)).save(
                str(src), "JPEG", quality=92)

        # Manifest mirroring what the new
        # mira.ui.exported.batch.submit_export_batch builds.
        dest_dir = exported_media_dir(event_root) / "Dia 1"
        units = tuple(
            PhotoUnit(
                unit_id=iid,
                source=str(event_root / "d" / f"{iid}.jpg"),
                dest_dir=str(dest_dir),
                # PhotoUnit.file_type holds the Enum *value*; the
                # name-cased ``"JPEG"`` was the Inseto silent-fail.
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

        # The commit half of submit_export_batch — set the flag, write
        # the rows.
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
        except Exception:                                            # noqa: BLE001
            pass
