"""Cross-event Picker (weed-out / pick-in commit path)."""
from __future__ import annotations

import pytest

from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
)
from mira.shared.cut_draft import PIN_PICK_IN, PIN_WEED_OUT
from mira.ui.pages.cross_event_picker_dialog import (
    CrossEventPickerDialog,
    _CandidateRow,
)


def _make_session(*, pin_mode=PIN_WEED_OUT, n=3,
                  target_s=None, max_s=None) -> CrossEventCutSession:
    files = []
    for i in range(n):
        files.append(CrossEventSessionFile(
            event_uuid="A" if i < 2 else "B",
            item_id=f"i{i}",
            export_relpath=f"Exported Media/p{i}.jpg",
            kind="photo",
            capture_time=f"2026-04-0{i+1}T10:00:00",
            duration_ms=0,
            day_bucket=f"A::2026-04-0{i+1}" if i < 2 else f"B::2026-04-0{i+1}",
        ))
    return CrossEventCutSession(
        name="cross_test",
        expr=(("+", "exported"),),
        filters={},
        pin_mode=pin_mode,
        target_s=target_s, max_s=max_s, photo_s=6.0,
        music_category=None,
        files=tuple(files),
        anchor_event_id="A",
        separators_on=False,
    )


# --------------------------------------------------------------------------- #
# Construction — cells rendered, default ledger applied
# --------------------------------------------------------------------------- #


def test_renders_one_row_per_candidate(qapp):
    """One :class:`_CandidateRow` per file in the session."""
    sess = _make_session(n=4)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    rows = d._rows
    assert len(rows) == 4
    assert {r.key for r in rows} == {f"A::i{i}" for i in range(2)} | {
        f"B::i{i}" for i in range(2, 4)}
    d.deleteLater()


def test_weed_out_starts_all_picked_in_ui(qapp):
    """Weed-out: session starts all-picked; the cells' checkboxes are on."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    for row in d._rows:
        assert row._check.isChecked()
    d.deleteLater()


def test_pick_in_starts_all_skipped_in_ui(qapp):
    """Pick-in: session starts all-skipped; the cells' checkboxes are off."""
    sess = _make_session(pin_mode=PIN_PICK_IN, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    for row in d._rows:
        assert not row._check.isChecked()
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Ledger — toggling a row updates the session
# --------------------------------------------------------------------------- #


def test_toggle_row_updates_session_ledger(qapp):
    """Flipping a row's checkbox flips the session.is_picked state."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=2)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    target_row = d._rows[0]
    assert sess.is_picked(target_row.key)
    target_row._check.setChecked(False)
    assert not sess.is_picked(target_row.key)
    d.deleteLater()


def test_picked_count_in_footer_updates_live(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    # All picked initially.
    assert "3/3 picked" in d._budget_label.text()
    # Skip one.
    d._rows[0]._check.setChecked(False)
    assert "2/3 picked" in d._budget_label.text()
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Budget zones (spec/81 §4)
# --------------------------------------------------------------------------- #


def test_budget_zone_green_under_target(qapp):
    """Picked length under target → zone green."""
    sess = _make_session(pin_mode=PIN_PICK_IN, n=4,
                         target_s=120, max_s=180)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    # 0 picked → 0s → green.
    assert d._budget_label.property("zone") == "green"
    d.deleteLater()


def test_budget_zone_amber_between_target_and_max(qapp):
    """Picked length between target and max → amber."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=4,
                         target_s=12, max_s=36)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    # 4 picked × 6s = 24s; between target (12) and max (36) → amber.
    assert d._budget_label.property("zone") == "amber"
    d.deleteLater()


def test_budget_zone_red_over_max(qapp):
    """Picked length over max → red."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=4,
                         target_s=6, max_s=12)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    # 4 picked × 6s = 24s; over max (12) → red.
    assert d._budget_label.property("zone") == "red"
    d.deleteLater()


def test_budget_zone_none_when_no_limits(qapp):
    """No target + no max → zone none."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=2,
                         target_s=None, max_s=None)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert d._budget_label.property("zone") == "none"
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Commit — fires the callback + emits committed
# --------------------------------------------------------------------------- #


def test_commit_invokes_callback_with_session(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=2)
    received: list = []
    d = CrossEventPickerDialog(
        sess, commit_callback=lambda s: received.append(s))
    fired: list = []
    d.committed.connect(lambda s: fired.append(s))
    d._on_commit()
    assert received == [sess]
    assert fired == [sess]
    d.deleteLater()


def test_commit_failure_surfaces_warning(qapp, monkeypatch):
    """Callback raise → QMessageBox.warning, dialog stays open."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=1)

    def _angry(_s):
        raise RuntimeError("disk full")

    warned: list = []
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: warned.append(a[2]) or QMessageBox.StandardButton.Ok)
    d = CrossEventPickerDialog(sess, commit_callback=_angry)
    fired: list = []
    d.committed.connect(lambda s: fired.append(s))
    d._on_commit()
    assert any("disk full" in str(m) for m in warned)
    assert fired == []                                    # not emitted on error
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Cancel — no commit, no signal
# --------------------------------------------------------------------------- #


def test_cancel_does_not_commit(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=1)
    received: list = []
    d = CrossEventPickerDialog(
        sess, commit_callback=lambda s: received.append(s))
    d.reject()
    assert received == []
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Row metadata
# --------------------------------------------------------------------------- #


def test_row_renders_grab_member_label(qapp):
    """Grab-kind rows surface the 'grab' label so the user knows they're
    pulling the original."""
    sess = CrossEventCutSession(
        name="x", expr=(("+", "collected"),), filters={},
        pin_mode=PIN_WEED_OUT, target_s=None, max_s=None, photo_s=6.0,
        music_category=None,
        files=(CrossEventSessionFile(
            event_uuid="A", item_id="a1",
            origin_relpath="Original Media/raw.raw",
            member_kind="grab", kind="photo",
        ),),
        anchor_event_id="A", separators_on=False,
    )
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    # The row's meta line contains "grab".
    meta = d._rows[0]._meta_line(sess.files[0], "Original Media/raw.raw")
    assert "grab" in meta
    d.deleteLater()
