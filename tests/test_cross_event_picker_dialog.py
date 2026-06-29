"""Cross-event Picker (grid weed-out / pick-in commit path).

The Picker is now a :class:`ThumbGrid` (green/red state borders + click-to-
toggle), reusing the event-Cut grid widget. Tests read the grid's item
``state`` + ``payload`` and drive the toggle / batch / commit handlers.
"""
from __future__ import annotations

from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
)
from mira.shared.cut_draft import PIN_PICK_IN, PIN_WEED_OUT
from mira.ui.pages.cross_event_picker_dialog import CrossEventPickerDialog


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


def _states(d) -> list:
    return [it.state for it in d._grid._items]


def _payloads(d) -> list:
    return [it.payload for it in d._grid._items]


# --------------------------------------------------------------------------- #
# Construction — grid cells rendered, default ledger applied
# --------------------------------------------------------------------------- #


def test_renders_one_cell_per_candidate(qapp):
    """One grid item per file in the session, keyed by the packed key."""
    sess = _make_session(n=4)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert len(d._grid._items) == 4
    assert set(_payloads(d)) == {f"A::i{i}" for i in range(2)} | {
        f"B::i{i}" for i in range(2, 4)}
    d.deleteLater()


def test_weed_out_starts_all_picked_green(qapp):
    """Weed-out: session starts all-picked; every cell paints the green
    (picked) state border."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert _states(d) == ["picked", "picked", "picked"]
    d.deleteLater()


def test_pick_in_starts_all_skipped_red(qapp):
    """Pick-in: session starts all-skipped; every cell paints the red
    (skipped) state border."""
    sess = _make_session(pin_mode=PIN_PICK_IN, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert _states(d) == ["skipped", "skipped", "skipped"]
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Ledger — clicking a cell toggles the session + repaints the border
# --------------------------------------------------------------------------- #


def test_cell_click_toggles_session_and_border(qapp):
    """Activating a cell flips session.is_picked AND the cell's state."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=2)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    key = sess.files[0].key
    assert sess.is_picked(key)
    d._on_cell_activated(0)
    assert not sess.is_picked(key)
    assert d._grid._items[0].state == "skipped"
    # Toggle back.
    d._on_cell_activated(0)
    assert sess.is_picked(key)
    assert d._grid._items[0].state == "picked"
    d.deleteLater()


def test_picked_count_in_footer_updates_live(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert "3/3 picked" in d._budget_label.text()
    d._on_cell_activated(0)
    assert "2/3 picked" in d._budget_label.text()
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Batch — Pick all / Skip all
# --------------------------------------------------------------------------- #


def test_pick_all_and_skip_all(qapp):
    """The batch buttons flip every candidate at once."""
    sess = _make_session(pin_mode=PIN_PICK_IN, n=3)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert sess.picked_count() == 0
    d._set_all(True)
    assert sess.picked_count() == 3
    assert _states(d) == ["picked", "picked", "picked"]
    d._set_all(False)
    assert sess.picked_count() == 0
    assert _states(d) == ["skipped", "skipped", "skipped"]
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Thumbnails — the host's resolver feeds each cell's pixmap
# --------------------------------------------------------------------------- #


def test_thumb_resolver_feeds_pixmaps(qapp):
    """When a resolver is supplied, each item carries its resolved pixmap;
    a resolver that raises degrades to no pixmap (never blocks the grid)."""
    from PyQt6.QtGui import QPixmap
    sess = _make_session(n=2)
    seen: list = []

    def _resolver(sf):
        seen.append(sf.key)
        pm = QPixmap(8, 8)
        return pm

    d = CrossEventPickerDialog(
        sess, commit_callback=lambda s: None, thumb_resolver=_resolver)
    assert set(seen) == {sess.files[0].key, sess.files[1].key}
    assert all(it.pixmap is not None for it in d._grid._items)
    d.deleteLater()


def test_thumb_resolver_failure_degrades_to_placeholder(qapp):
    sess = _make_session(n=1)

    def _angry(_sf):
        raise RuntimeError("decode failed")

    d = CrossEventPickerDialog(
        sess, commit_callback=lambda s: None, thumb_resolver=_angry)
    assert d._grid._items[0].pixmap is None        # placeholder, not a crash
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Budget zones (spec/81 §4)
# --------------------------------------------------------------------------- #


def test_budget_zone_green_under_target(qapp):
    sess = _make_session(pin_mode=PIN_PICK_IN, n=4, target_s=120, max_s=180)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert d._budget_label.property("zone") == "green"
    d.deleteLater()


def test_budget_zone_amber_between_target_and_max(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=4, target_s=12, max_s=36)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert d._budget_label.property("zone") == "amber"
    d.deleteLater()


def test_budget_zone_red_over_max(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=4, target_s=6, max_s=12)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert d._budget_label.property("zone") == "red"
    d.deleteLater()


def test_budget_zone_none_when_no_limits(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=2, target_s=None, max_s=None)
    d = CrossEventPickerDialog(sess, commit_callback=lambda s: None)
    assert d._budget_label.property("zone") == "none"
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Commit / cancel
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


def test_commit_failure_surfaces_friendly_warning(qapp, monkeypatch):
    """A 'taken' ValueError surfaces the human-readable message, dialog
    stays open (committed not emitted)."""
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=1)

    def _angry(_s):
        raise ValueError("taken")

    warned: list = []
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **kw: warned.append(a[2]) or QMessageBox.StandardButton.Ok)
    d = CrossEventPickerDialog(sess, commit_callback=_angry)
    fired: list = []
    d.committed.connect(lambda s: fired.append(s))
    d._on_commit()
    assert any("already exists" in str(m) for m in warned)
    assert fired == []
    d.deleteLater()


def test_cancel_does_not_commit(qapp):
    sess = _make_session(pin_mode=PIN_WEED_OUT, n=1)
    received: list = []
    d = CrossEventPickerDialog(
        sess, commit_callback=lambda s: received.append(s))
    d.reject()
    assert received == []
    d.deleteLater()


# --------------------------------------------------------------------------- #
# Tooltip — grab member surfaces the 'grab' hint
# --------------------------------------------------------------------------- #


def test_grab_member_tooltip(qapp):
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
    assert "grab" in d._grid._items[0].tooltip
    d.deleteLater()
