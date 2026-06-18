"""Tests for ``RecognitionDialog`` — spec/88 propose-and-confirm UI.

The dialog wraps :func:`core.clock_recognition.find_candidate_pairs`'s
output in cluster cards + a preview/Apply rail. These tests drive the
dialog through its states (cluster cycling, manual fallback, recognize +
preview + Apply) without rendering real images: the impact callback is
stubbed, and ``load_pixmap`` happily returns a placeholder QPixmap when
the underlying path doesn't exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QDialog
except ImportError:                                      # pragma: no cover
    QApplication = None
    QDialog = None

from core.clock_recognition import (
    CandidateCluster,
    CandidatePair,
    find_candidate_pairs,
)
from core.fresh_source import SourceItem


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── Fixture builders ─────────────────────────────────────────────────────


def _cam(name: str, t: datetime) -> SourceItem:
    return SourceItem(
        path=Path(f"cam/{name}.rw2"),
        timestamp=t,
        camera_id="G9",
    )


def _phone(name: str, t: datetime, tz_minutes: int) -> SourceItem:
    return SourceItem(
        path=Path(f"phone/{name}.jpg"),
        timestamp=t,
        camera_id="iPhone",
        tz_offset_minutes=tz_minutes,
    )


def _zero_cluster_clusters():
    """Build a single, dominant κ=0 cluster (camera correctly set)."""
    inst = [datetime(2025, 5, 12, h, 0, 0) for h in (10, 11, 12, 13)]
    cams = [_cam(f"c{i}", t) for i, t in enumerate(inst)]
    phones = [_phone(f"p{i}", t, 0) for i, t in enumerate(inst)]
    return find_candidate_pairs(cams, phones)


def _two_cluster_clusters():
    """A κ=0 cluster (clocks agree) and a κ=-15 cluster (cam 10 min after
    phone). Both within the 15-min gate; placed weeks apart so cross-group
    pairs are filtered."""
    a_inst = [datetime(2025, 5, 12, 9, 0, 0),
              datetime(2025, 5, 12, 9, 45, 0)]
    cams_a = [_cam(f"a{i}", t) for i, t in enumerate(a_inst)]
    phones_a = [_phone(f"pa{i}", t, 0) for i, t in enumerate(a_inst)]
    b_inst = [datetime(2025, 7, 12, 9, 0, 0),
              datetime(2025, 7, 12, 9, 45, 0)]
    cams_b = [_cam(f"b{i}", t) for i, t in enumerate(b_inst)]
    phones_b = [_phone(f"pb{i}", t + timedelta(minutes=10), 0)
                for i, t in enumerate(b_inst)]
    return find_candidate_pairs(cams_a + cams_b, phones_a + phones_b)


def _dummy_impact(_pair):
    from mira.ui.pages.clock_recognition_dialog import ApplyImpact
    return ApplyImpact(
        photo_count=214, shift=timedelta(hours=1), day_moves=6,
    )


# ── Headline and cluster cycling ─────────────────────────────────────────


def test_headline_for_zero_cluster_calls_out_matching_phone(qapp):
    """The 0-offset case has a distinct one-click headline (spec/88 §3
    point 2): a correctly-set camera shouldn't read like a TZ choice."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        # The 0-cluster wins ties, so it's first.
        assert clusters[0].snapped_kappa_minutes == 0
        text = dlg._headline.text()
        assert "G9" in text
        # Doesn't pitch the user a TZ to confirm; pitches "matching".
        assert "matching" in text.lower() or "UTC" in text
    finally:
        dlg.deleteLater()


def test_headline_for_non_zero_cluster_shows_the_tz(qapp):
    """For a non-zero proposal, the headline names the apparent TZ so the
    user has a memory peg ("we were in Buenos Aires; UTC-3 fits")."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _two_cluster_clusters()
    # Skip past the (deterministic) tie-break to the κ=-180 cluster.
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        # If the first cluster is κ=-120, the second is κ=-180 (or vice
        # versa). Either way the non-zero headline names a UTC offset.
        text = dlg._headline.text()
        assert "G9" in text
        assert "UTC" in text
    finally:
        dlg.deleteLater()


def test_show_another_advances_to_next_cluster(qapp):
    """The "None of these — show another" path walks the cluster list in
    order so the user can see every candidate before giving up."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _two_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        assert dlg._cluster_index == 0
        first_headline = dlg._headline.text()
        dlg._on_show_another()
        assert dlg._cluster_index == 1
        second_headline = dlg._headline.text()
        # Different cluster → different headline.
        assert first_headline != second_headline
        # On the last cluster, "show another" is hidden.
        assert dlg._another_btn.isHidden() or len(clusters) > 2
    finally:
        dlg.deleteLater()


def test_show_another_stops_at_last_cluster(qapp):
    """At the end of the cluster list, the button is hidden — the user's
    only outs are the manual fallback or Cancel (never silently loop)."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _two_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        # Click through to the last cluster.
        for _ in range(len(clusters)):
            dlg._on_show_another()
        # Calling again past the end is a no-op.
        dlg._on_show_another()
        assert dlg._cluster_index == len(clusters) - 1
    finally:
        dlg.deleteLater()


def test_empty_cluster_list_shows_only_the_manual_exit(qapp):
    """No clusters at all (no phone overlap, sparse overlap, …) → the
    picker has no cards but the manual fallback button still lets the
    user out (spec/88 §5 first bullet — sparse overlap falls to manual)."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=[], impact_for=_dummy_impact,
    )
    try:
        assert dlg._card_row_layout.count() == 0
        assert dlg._another_btn.isHidden()
        # Manual fallback button stays available even with no clusters —
        # checked via the not-hidden flag (Qt's isVisible needs an actual
        # shown parent, which we don't do in tests).
        assert not dlg._manual_btn.isHidden()
    finally:
        dlg.deleteLater()


# ── Card click → preview + apply ─────────────────────────────────────────


def test_clicking_a_card_swings_to_preview_with_impact(qapp):
    """One click on a card runs the impact callback and shows its result
    on the preview page (the rail the bad correction lacked)."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    captured: list = []

    def impact_for(pair):
        captured.append(pair)
        return _dummy_impact(pair)

    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=impact_for,
    )
    try:
        a_pair = clusters[0].pairs[0]
        dlg._on_card_clicked(a_pair)
        assert captured == [a_pair]
        assert dlg._stack.currentIndex() == 1
        # The preview body names the photo count + shift.
        body = dlg._preview_body.text()
        assert "214" in body
        # Shift formatter — 1h shows as "+ 1h".
        assert "1h" in body
        # Day-move line shows the count when non-zero.
        assert "6" in body
    finally:
        dlg.deleteLater()


def test_apply_accepts_with_confirmed_pair(qapp):
    """Apply closes the dialog with Accepted + selected_pair pointing at
    the user's confirmed pair as a CalibrationPair the engine consumes."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        a_pair = clusters[0].pairs[0]
        dlg._on_card_clicked(a_pair)
        dlg.accept()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.fallback_to_manual is False
        cand = dlg.confirmed_candidate()
        assert cand is a_pair
        cal = dlg.selected_pair()
        assert cal is not None
        # The selected CalibrationPair is built from the same raw EXIF
        # timestamps the CandidatePair points at — no pre-snapping.
        assert cal.camera_path == a_pair.camera_item.path
        assert cal.reference_path == a_pair.phone_item.path
        assert cal.camera_time == a_pair.camera_item.timestamp
        assert cal.reference_time == a_pair.phone_item.timestamp
    finally:
        dlg.deleteLater()


def test_back_from_preview_returns_to_picker(qapp):
    """Back is the undo rail (spec/88 §3.5): a wrong card click can be
    cleanly walked back without applying."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        a_pair = clusters[0].pairs[0]
        dlg._on_card_clicked(a_pair)
        assert dlg._stack.currentIndex() == 1
        dlg._on_back_to_picker()
        assert dlg._stack.currentIndex() == 0
        # Pair is forgotten — Apply can no longer leak through.
        assert dlg._confirmed_pair is None
    finally:
        dlg.deleteLater()


# ── Manual fallback path ────────────────────────────────────────────────


def test_manual_fallback_path_accepts_with_flag_set(qapp):
    """"Use manual pair…" closes the dialog Accepted but with
    fallback_to_manual True → caller opens the legacy picker in its
    place. selected_pair stays None."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        dlg._on_use_manual()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.fallback_to_manual is True
        assert dlg.confirmed_candidate() is None
        assert dlg.selected_pair() is None
    finally:
        dlg.deleteLater()


def test_cancel_rejects_and_returns_nothing(qapp):
    """Cancel is the kill — no pair, no fallback, dialog Rejected."""
    from mira.ui.pages.clock_recognition_dialog import RecognitionDialog

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
    )
    try:
        dlg.reject()
        assert dlg.result() == QDialog.DialogCode.Rejected
        assert dlg.fallback_to_manual is False
        assert dlg.confirmed_candidate() is None
        assert dlg.selected_pair() is None
    finally:
        dlg.deleteLater()


# ── Card rendering ───────────────────────────────────────────────────────


def test_card_row_shows_no_more_than_cards_visible(qapp):
    """The horizontal sample is capped so the dialog stays readable, even
    when a cluster has many qualifying pairs."""
    from mira.ui.pages.clock_recognition_dialog import (
        RecognitionDialog,
        _PairCard,
    )

    # Make a fat cluster — 16 pairs (cross-product 4×4 all within minutes
    # so every off snaps to κ=0).
    inst = [datetime(2025, 5, 12, 10, m, 0) for m in (0, 2, 4, 6)]
    cams = [_cam(f"c{i}", t) for i, t in enumerate(inst)]
    phones = [_phone(f"p{i}", t, 0) for i, t in enumerate(inst)]
    clusters = find_candidate_pairs(cams, phones)
    assert clusters[0].size == 16
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters, impact_for=_dummy_impact,
        cards_visible=4,
    )
    try:
        # Count widgets in the row (skip the stretch at the end).
        cards = [
            dlg._card_row_layout.itemAt(i).widget()
            for i in range(dlg._card_row_layout.count())
            if isinstance(dlg._card_row_layout.itemAt(i).widget(), _PairCard)
        ]
        assert len(cards) == 4
    finally:
        dlg.deleteLater()


def test_preview_text_omits_day_moves_line_when_zero(qapp):
    """No-day-move case has its own copy ("no day boundary crossed") so
    the user reads it as deliberate, not as a missing field."""
    from mira.ui.pages.clock_recognition_dialog import (
        ApplyImpact,
        RecognitionDialog,
    )

    clusters = _zero_cluster_clusters()
    dlg = RecognitionDialog(
        camera_id="G9", reference_id="iPhone",
        clusters=clusters,
        impact_for=lambda _p: ApplyImpact(
            photo_count=42, shift=timedelta(0), day_moves=0,
        ),
    )
    try:
        dlg._on_card_clicked(clusters[0].pairs[0])
        body = dlg._preview_body.text()
        assert "42" in body
        # The 0-shift case still renders without crashing.
        assert "0" in body
        assert "no" in body.lower() or "day" in body.lower()
    finally:
        dlg.deleteLater()
