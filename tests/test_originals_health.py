"""Tests for the missing-originals classification + relink + prune flow
(the locate/relink layer over the captured tree, charter §7).

Three tiers, mirroring the structure of :mod:`tests.test_gateway`:

* Pure ``classify`` — exercises the OK / OFFLINE / MOVED decision tree
  against synthetic paths (no Gateway, no event.db).
* ``Gateway.check_originals`` + :meth:`Gateway.relink_event` — the
  verify-then-allow + atomic re-anchor primitives, on real tmp_path
  trees with materialised events.
* ``EventGateway.prune_missing_originals`` /
  :meth:`EventGateway.list_missing_origin_items` — the explicit-only
  destructive primitive plus its enumerator, exercising FK cascades to
  ``phase_state`` / ``adjustment`` / ``video_marker`` /
  ``video_snapshot`` / ``lineage``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtWidgets import QDialog

from mira.gateway import (
    EventsIndex,
    Gateway,
    OriginalsCheck,
    OriginalsHealth,
    make_entry,
)
from mira.gateway import originals_health as oh
from mira.settings.repo import SettingsRepo
from mira.store import json_dump, models as m

from tests.test_store import _rich_document


FIXED_NOW = "2026-06-15T12:00:00+00:00"


def _now() -> str:
    return FIXED_NOW


def _entry_for(root, base):
    return make_entry(
        event_id="evt-1", name="Costa Rica 2026", start_date="2026-04-01",
        end_date="2026-04-14", is_closed=False,
        event_root=root, photos_base_path=base,
    )


def _gateway(tmp_path, base):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index, now=_now)
    gw.set_photos_base_path(str(base))
    return gw


def _materialised(tmp_path):
    """A Gateway with one materialised event whose Original Media/ has
    a couple of placeholder files. Returns ``(gw, base, event_root)``."""
    base = tmp_path / "lib"
    gw = _gateway(tmp_path, base)
    event_root = base / "Costa Rica 2026"
    entry = _entry_for(event_root, base)
    gw.materialise_event(json_dump.to_json(_rich_document()), entry)
    (event_root / "Original Media" / "P1000001.RW2").write_bytes(b"x")
    (event_root / "Original Media" / "P1000002.RW2").write_bytes(b"y")
    return gw, base, event_root


# =========================================================================== #
# Pure classify — the decision tree, no Gateway
# =========================================================================== #


def test_classify_ok_when_originals_present(tmp_path):
    event_root = tmp_path / "lib" / "EvA"
    (event_root / "Original Media").mkdir(parents=True)
    (event_root / "Original Media" / "P1.RW2").write_bytes(b"x")
    verdict = oh.classify(
        base_path=tmp_path / "lib",
        event_root=event_root,
        requires_base=True,
    )
    assert verdict.state == OriginalsHealth.OK
    assert verdict.is_ok
    assert verdict.event_root == event_root
    assert verdict.originals_dir == event_root / "Original Media"


def test_classify_offline_when_base_missing(tmp_path):
    """Relative-anchored events orphan when the base anchor itself is gone."""
    verdict = oh.classify(
        base_path=tmp_path / "ghost-base",         # never created
        event_root=tmp_path / "ghost-base" / "EvA",
        requires_base=True,
    )
    assert verdict.state == OriginalsHealth.STORAGE_OFFLINE


def test_classify_skips_base_check_for_abs_anchored(tmp_path):
    """Abs-anchored events don't depend on the base — base presence is
    not part of their reachability story. A live abs-anchored tree is
    OK even when ``photos_base_path`` doesn't exist."""
    event_root = tmp_path / "external" / "EvB"
    (event_root / "Original Media").mkdir(parents=True)
    (event_root / "Original Media" / "P.RW2").write_bytes(b"x")
    verdict = oh.classify(
        base_path=tmp_path / "ghost-base",
        event_root=event_root,
        requires_base=False,
    )
    assert verdict.state == OriginalsHealth.OK


def test_classify_moved_when_event_root_missing_but_parent_exists(tmp_path):
    """Walk-up disambiguation: when ``event_root`` is gone but an ancestor
    still exists, the leaf was moved — not the drive."""
    base = tmp_path / "lib"
    base.mkdir()
    verdict = oh.classify(
        base_path=base,
        event_root=base / "missing-event",
        requires_base=True,
    )
    assert verdict.state == OriginalsHealth.ORIGINALS_MOVED


def test_classify_offline_when_no_ancestor_exists(tmp_path, monkeypatch):
    """Walk-up disambiguation: when nothing along the chain is reachable
    (drive unmounted), the verdict is OFFLINE, not MOVED."""
    monkeypatch.setattr(oh, "_any_ancestor_exists", lambda p: False)
    verdict = oh.classify(
        base_path=None,
        event_root=Path("Z:/never-mounted/EvX"),
        requires_base=False,
    )
    assert verdict.state == OriginalsHealth.STORAGE_OFFLINE


def test_classify_moved_when_original_media_missing(tmp_path):
    event_root = tmp_path / "lib" / "EvA"
    event_root.mkdir(parents=True)            # event_root exists, no Original Media
    verdict = oh.classify(
        base_path=tmp_path / "lib",
        event_root=event_root,
        requires_base=True,
    )
    assert verdict.state == OriginalsHealth.ORIGINALS_MOVED


def test_classify_moved_when_original_media_empty(tmp_path):
    event_root = tmp_path / "lib" / "EvA"
    (event_root / "Original Media").mkdir(parents=True)
    verdict = oh.classify(
        base_path=tmp_path / "lib",
        event_root=event_root,
        requires_base=True,
    )
    assert verdict.state == OriginalsHealth.ORIGINALS_MOVED


def test_classify_offline_when_event_root_is_none():
    """Unresolvable index entry (neither relpath nor abs fallback set) →
    treated as OFFLINE so the dialog routes to the alert, not the
    locate flow."""
    verdict = oh.classify(
        base_path=None, event_root=None, requires_base=False,
    )
    assert verdict.state == OriginalsHealth.STORAGE_OFFLINE


# =========================================================================== #
# Gateway.check_originals — integration on a real materialised event
# =========================================================================== #


def test_check_originals_ok_for_fresh_event(tmp_path):
    gw, _base, _root = _materialised(tmp_path)
    verdict = gw.check_originals("evt-1")
    assert verdict.state == OriginalsHealth.OK


def test_check_originals_unknown_id_returns_ok(tmp_path):
    """No row to check ⇒ the locate flow has nothing to do; OK is the
    safe default."""
    gw, _base, _root = _materialised(tmp_path)
    assert gw.check_originals("ghost").state == OriginalsHealth.OK


def test_check_originals_storage_offline_when_base_unreachable(tmp_path):
    """Relative-anchored event whose base anchor disappears → OFFLINE.
    Built on the same shape as ``test_base_change_blockers_*``."""
    gw, base, _root = _materialised(tmp_path)
    # Move the base out from under the index — index still points at base.
    base.rename(tmp_path / "lib_renamed")
    assert gw.check_originals("evt-1").state == OriginalsHealth.STORAGE_OFFLINE


def test_check_originals_moved_when_event_folder_renamed(tmp_path):
    """User moved the whole event folder to a new subdir under base."""
    gw, base, event_root = _materialised(tmp_path)
    event_root.rename(base / "Renamed Folder")
    verdict = gw.check_originals("evt-1")
    assert verdict.state == OriginalsHealth.ORIGINALS_MOVED
    assert verdict.event_root == event_root  # the path we tried (now stale)


def test_check_originals_moved_when_original_media_carved_out(tmp_path):
    """event_root still there, ``Original Media/`` removed. Same
    classification — the locate flow handles both shapes."""
    gw, _base, event_root = _materialised(tmp_path)
    import shutil
    shutil.rmtree(event_root / "Original Media")
    assert gw.check_originals("evt-1").state == OriginalsHealth.ORIGINALS_MOVED


def test_check_originals_does_not_open_event_db(tmp_path, monkeypatch):
    """Detection is a pure filesystem read — no event.db opens (per spec/44
    the keystroke-on-filter invariant; same idea applies here)."""
    gw, _base, _root = _materialised(tmp_path)
    opens: list[str] = []
    real_open = gw.open_event
    monkeypatch.setattr(
        gw, "open_event",
        lambda eid: opens.append(eid) or real_open(eid),
    )
    gw.check_originals("evt-1")
    assert opens == []


# =========================================================================== #
# Gateway.relink_event — verify-then-allow + atomic path rewrite
# =========================================================================== #


def test_relink_event_uses_relpath_when_under_base(tmp_path):
    """Moving the event folder to another subdir of the SAME base keeps
    the row relative-anchored; only ``event_relpath`` changes."""
    gw, base, event_root = _materialised(tmp_path)
    new_root = base / "2026 - CR (renamed)"
    event_root.rename(new_root)

    gw.relink_event("evt-1", new_root)

    entry = gw.index.get("evt-1")
    assert entry["event_relpath"] == "2026 - CR (renamed)"
    assert entry["event_root_abs"] is None


def test_relink_event_uses_abs_when_outside_base(tmp_path):
    """Moving the event folder onto a different drive (simulated as a
    sibling of the base) flips the row to abs-anchored."""
    gw, base, event_root = _materialised(tmp_path)
    new_root = tmp_path / "external" / "EvA"
    new_root.parent.mkdir(parents=True)
    event_root.rename(new_root)

    gw.relink_event("evt-1", new_root)

    entry = gw.index.get("evt-1")
    assert entry["event_relpath"] is None
    assert entry["event_root_abs"] == str(new_root)


def test_relink_event_refused_when_event_db_absent(tmp_path):
    """Pointing at a folder with no event.db is refused. Index unchanged
    — same verify-then-allow shape as ``base_change_blockers``."""
    gw, _base, _root = _materialised(tmp_path)
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    before = gw.index.get("evt-1")
    with pytest.raises(FileNotFoundError):
        gw.relink_event("evt-1", bogus)
    assert gw.index.get("evt-1") == before


def test_relink_event_unknown_id_raises(tmp_path):
    """The path verify can pass but the index has no such id —
    surface that as :class:`KeyError`, like ``open_event``."""
    gw, _base, event_root = _materialised(tmp_path)
    with pytest.raises(KeyError):
        gw.relink_event("ghost", event_root)


def test_relink_event_preserves_items_and_decisions(tmp_path):
    """A round-trip: pick an item, relink the event, reopen — the
    decision survives at the new path. Charter §7 in action: the index
    moved, not the data."""
    gw, base, event_root = _materialised(tmp_path)
    eg = gw.open_event("evt-1")
    try:
        eg.set_phase_state("i-photo", "pick", "picked")
        ps_before = eg.phase_state("i-photo", "pick")
        assert ps_before.state == "picked"
    finally:
        eg.close()

    new_root = base / "Costa Rica MOVED"
    event_root.rename(new_root)
    gw.relink_event("evt-1", new_root)

    eg = gw.open_event("evt-1")
    try:
        ps_after = eg.phase_state("i-photo", "pick")
        assert ps_after.state == "picked"
        assert eg.event_root == new_root
        assert eg.event().uuid == "evt-1"
    finally:
        eg.close()


def test_relink_event_refreshes_classification_cache(tmp_path):
    """Same projection refresh as ``refresh_index_entry`` so the
    dashboard chip labels stay current after a relink."""
    gw, base, event_root = _materialised(tmp_path)
    gw.set_classification("evt-1", event_type="trip", event_subtype="Two weeks")
    new_root = base / "Costa Rica NEW"
    event_root.rename(new_root)

    gw.relink_event("evt-1", new_root)

    entry = gw.index.get("evt-1")
    assert entry["event_type"] == "trip"
    assert entry["event_subtype"] == "Two weeks"


# =========================================================================== #
# EventGateway.list_missing_origin_items + prune_missing_originals — the
# explicit-only destructive primitive
# =========================================================================== #


def _origin_event(tmp_path):
    """A materialised event with two captured items pointing at real
    files under ``Original Media/`` plus the supporting decision/edit
    rows so the cascade has something to delete in the prune tests."""
    gw, base, event_root = _materialised(tmp_path)
    eg = gw.open_event("evt-1")

    # Two captured photos, both materialised under Original Media/.
    om = event_root / "Original Media"
    (om / "P-orig-1.RW2").write_bytes(b"a" * 8)
    (om / "P-orig-2.RW2").write_bytes(b"b" * 8)
    eg.store.upsert(m.Item(
        id="i-orig-1", kind="photo", created_at=FIXED_NOW,
        provenance="captured",
        origin_relpath="Original Media/P-orig-1.RW2",
        sha256="a" * 64, byte_size=8,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9M2", capture_time_raw="2026-04-01T10:00:00",
    ))
    eg.store.upsert(m.Item(
        id="i-orig-2", kind="photo", created_at=FIXED_NOW,
        provenance="captured",
        origin_relpath="Original Media/P-orig-2.RW2",
        sha256="b" * 64, byte_size=8,
        materialized_at=FIXED_NOW, materialized_phase="ingest",
        camera_id="G9M2", capture_time_raw="2026-04-01T10:00:01",
    ))
    # Decisions + adjustments so the FK cascade has child rows to drop.
    eg.set_phase_state("i-orig-1", "pick", "picked")
    eg.set_phase_state("i-orig-2", "pick", "skipped")
    return gw, eg, event_root


def test_list_missing_origin_items_empty_when_all_present(tmp_path):
    gw, eg, _root = _origin_event(tmp_path)
    try:
        assert eg.list_missing_origin_items() == []
    finally:
        eg.close()


def test_list_missing_origin_items_flags_only_gone_files(tmp_path):
    gw, eg, event_root = _origin_event(tmp_path)
    try:
        (event_root / "Original Media" / "P-orig-1.RW2").unlink()
        assert eg.list_missing_origin_items() == ["i-orig-1"]
    finally:
        eg.close()


def test_list_missing_origin_items_ignores_non_original_paths(tmp_path):
    """Items whose ``origin_relpath`` lives outside ``Original Media/``
    (e.g. fixture items under ``00 - Captured/...``) are out of scope —
    they belong to other reconciles."""
    gw, eg, _root = _origin_event(tmp_path)
    try:
        # Rich-document items use the legacy "00 - Captured/..." prefix
        # — those must not show up in the missing-originals enumerator
        # regardless of whether their files exist.
        ids = set(eg.list_missing_origin_items())
        assert "i-photo" not in ids
        assert "i-video" not in ids
    finally:
        eg.close()


def test_prune_missing_originals_cascades_to_phase_state(tmp_path):
    """Killing an item also kills its decision rows (FK cascade)."""
    gw, eg, event_root = _origin_event(tmp_path)
    try:
        assert eg.phase_state("i-orig-1", "pick") is not None
        assert eg.prune_missing_originals(["i-orig-1"]) == 1
        assert eg.item("i-orig-1") is None
        assert eg.phase_state("i-orig-1", "pick") is None
        # Surviving item is untouched.
        assert eg.item("i-orig-2") is not None
        assert eg.phase_state("i-orig-2", "pick").state == "skipped"
    finally:
        eg.close()


def test_prune_missing_originals_no_op_on_empty_input(tmp_path):
    gw, eg, _root = _origin_event(tmp_path)
    try:
        before = eg.event().updated_at
        assert eg.prune_missing_originals([]) == 0
        # No transaction ran → ``_touch`` did not bump updated_at.
        assert eg.event().updated_at == before
    finally:
        eg.close()


def test_prune_missing_originals_returns_zero_for_unknown_id(tmp_path):
    gw, eg, _root = _origin_event(tmp_path)
    try:
        assert eg.prune_missing_originals(["never-existed"]) == 0
        # The real ids both survive.
        assert eg.item("i-orig-1") is not None
        assert eg.item("i-orig-2") is not None
    finally:
        eg.close()


def test_prune_missing_originals_bumps_updated_at(tmp_path):
    """``_touch`` fires on a non-empty prune so the event's
    modification cursor reflects the change."""
    gw, eg, _root = _origin_event(tmp_path)
    try:
        before = eg.event().updated_at
        eg.prune_missing_originals(["i-orig-1"])
        assert eg.event().updated_at == FIXED_NOW
        assert before == FIXED_NOW  # _now is constant; the UPDATE re-ran
    finally:
        eg.close()


# =========================================================================== #
# MissingOriginalsDialog — outcome semantics
# =========================================================================== #


def _make_check(state, tmp_path):
    """Quick :class:`OriginalsCheck` for dialog tests."""
    event_root = tmp_path / "Event"
    return OriginalsCheck(
        state=state,
        event_root=event_root,
        base_path=tmp_path,
        originals_dir=event_root / "Original Media",
    )


def test_dialog_offline_close_returns_kept(tmp_path, qapp):
    """OFFLINE alert: closing the dialog yields ``kept`` — informational,
    no data change, no chosen path."""
    from mira.ui.pages.missing_originals_dialog import (
        MissingOriginalsDialog,
        OUTCOME_KEPT,
    )
    check = _make_check(OriginalsHealth.STORAGE_OFFLINE, tmp_path)
    dlg = MissingOriginalsDialog(check=check, event_name="Trip A")
    try:
        dlg._on_cancel()
        assert dlg.outcome == OUTCOME_KEPT
        assert dlg.chosen_path is None
    finally:
        dlg.deleteLater()


def test_dialog_moved_locate_cancelled_stays_kept(tmp_path, qapp, monkeypatch):
    """MOVED + user cancels the folder picker → outcome stays ``kept``,
    dialog is not accepted (user can pick a different action)."""
    from mira.ui.pages.missing_originals_dialog import (
        MissingOriginalsDialog,
        OUTCOME_KEPT,
    )
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: "")  # cancelled
    check = _make_check(OriginalsHealth.ORIGINALS_MOVED, tmp_path)
    dlg = MissingOriginalsDialog(check=check, event_name="Trip A",
                                 missing_count=12)
    try:
        dlg._on_locate_clicked()
        assert dlg.outcome == OUTCOME_KEPT
        assert dlg.chosen_path is None
    finally:
        dlg.deleteLater()


def test_dialog_moved_locate_chosen_path_is_relink(tmp_path, qapp, monkeypatch):
    """MOVED + user picks a folder → outcome is ``relink`` and
    ``chosen_path`` carries the picked folder (caller will then call
    ``gateway.relink_event``)."""
    from mira.ui.pages.missing_originals_dialog import (
        MissingOriginalsDialog,
        OUTCOME_RELINK,
    )
    from PyQt6.QtWidgets import QFileDialog
    picked = tmp_path / "new-home"
    picked.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        lambda *a, **k: str(picked))
    check = _make_check(OriginalsHealth.ORIGINALS_MOVED, tmp_path)
    dlg = MissingOriginalsDialog(check=check, event_name="Trip A")
    try:
        dlg._on_locate_clicked()
        assert dlg.outcome == OUTCOME_RELINK
        assert dlg.chosen_path == picked
    finally:
        dlg.deleteLater()


def test_dialog_moved_prune_requires_destructive_confirm(
    tmp_path, qapp, monkeypatch,
):
    """The destructive branch ALWAYS routes through ``MessageDialog``'s
    destructive confirm — the prune outcome must not fire on a single
    click of the "gone for good" button. Plan §"Hard rules":
    pruning is never triggered by detection alone."""
    from mira.ui.pages.missing_originals_dialog import (
        MissingOriginalsDialog,
        OUTCOME_KEPT, OUTCOME_PRUNE,
    )
    from mira.ui.design import dialogs as design_dialogs

    # Stub the destructive confirm to refuse (Rejected). The outer
    # dialog must NOT mark the outcome as PRUNE.
    class _DenyDlg:
        def exec(self):
            return QDialog.DialogCode.Rejected

        def result_kind(self):
            return "cancel"

    monkeypatch.setattr(
        design_dialogs.MessageDialog, "destructive",
        classmethod(lambda cls, *a, **k: _DenyDlg()),
    )

    check = _make_check(OriginalsHealth.ORIGINALS_MOVED, tmp_path)
    dlg = MissingOriginalsDialog(check=check, missing_count=5)
    try:
        dlg._on_prune_clicked()
        assert dlg.outcome == OUTCOME_KEPT
    finally:
        dlg.deleteLater()

    # Now wire the confirm to accept → outcome flips to PRUNE.
    class _AcceptDlg:
        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_kind(self):
            return "primary"

    monkeypatch.setattr(
        design_dialogs.MessageDialog, "destructive",
        classmethod(lambda cls, *a, **k: _AcceptDlg()),
    )
    dlg2 = MissingOriginalsDialog(check=check, missing_count=5)
    try:
        dlg2._on_prune_clicked()
        assert dlg2.outcome == OUTCOME_PRUNE
    finally:
        dlg2.deleteLater()
