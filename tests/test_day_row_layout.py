"""spec/129 — Days list row layout: keep the capture-distribution
spark fully visible; compress the status bars instead.

Before spec/129 every bar row had ``lab.setFixedWidth(60)`` +
``StageProgress`` + ``count_label.setFixedWidth(96)`` ≈ 156px of
unshrinkable width. The center column couldn't yield, so when the
dialog narrowed, the spark on the right clipped instead of the bars.

After: the StageProgress track carries a small minimumWidth (24) and
the labels/count drop to setMinimumWidth(0) + setMaximumWidth(60/96)
+ Preferred policy. The bar row's minimum drops well below the spark's
fixed 168px — the spark stays fully visible at every reasonable width.
"""
from __future__ import annotations

import pytest

from mira.ui.design import StageProgress
from mira.ui.pages.days_lists_page import DayRow, DaySnapshot


def _snap(**over):
    base = dict(
        day_number=3, title="Cloud forest", date_iso="2026-04-03",
        picked=10, skipped=2, edited=4, items=20,
        buckets=4,
    )
    base.update(over)
    return DaySnapshot(**base)


# ── StageProgress: track is the compressible element ───────────────────


def test_stage_progress_has_small_min_width_and_can_shrink(qapp):
    """The track itself can compress to the spec/129 24px floor (still
    visible, never zero). Its size policy is Expanding so the layout
    grows it to absorb leftover space at wide widths."""
    from PyQt6.QtWidgets import QSizePolicy

    bar = StageProgress()
    assert bar.minimumWidth() == 24
    # Horizontal policy must still be Expanding so the track absorbs
    # leftover space when the row is wide (the compression path only
    # bites under pressure).
    assert bar.sizePolicy().horizontalPolicy() == \
        QSizePolicy.Policy.Expanding


def test_day_row_bars_cap_max_width(qapp):
    """Nelson 2026 follow-up to spec/129: at wide layouts the bars
    must not span the entire center column — they ate space the
    right-column spark needs to breathe. Each bar in the row caps
    at a sensible visible width so the bars look proportionate while
    the spark stays anchored on the right."""
    row = DayRow(_snap(), phase="pick")
    try:
        bars = row.findChildren(StageProgress)
        assert bars, "expected at least one StageProgress in the row"
        for bar in bars:
            assert 0 < bar.maximumWidth() <= 260, (
                "DayRow bars must cap their maximum width so wide "
                "layouts don't make the bars overrun the right-column "
                f"spark; got maximumWidth={bar.maximumWidth()}")
    finally:
        row.deleteLater()


def test_day_row_title_block_can_shrink_below_text_width(qapp):
    """A long title + date + location must not raise the row's
    minimum width above the viewport — without this, the row
    overflows the QScrollArea (horizontal scroll is OFF) and the
    spark on the right gets clipped off-screen. Title + sub use
    the Ignored horizontal policy so the text bypasses QLabel's
    text-width minimumSizeHint."""
    from PyQt6.QtWidgets import QLabel, QSizePolicy

    snap = _snap(
        title="An extremely long day title that would otherwise pin "
              "the row's minimum width past the viewport",
        date_iso="2026-04-03",
    )
    snap.location = "Some very long location string that adds further pressure"
    row = DayRow(snap, phase="pick")
    try:
        title = next(
            (w for w in row.findChildren(QLabel)
             if w.objectName() == "DayRowTitle"),
            None,
        )
        assert title is not None, "expected a DayRowTitle label"
        assert title.sizePolicy().horizontalPolicy() == \
            QSizePolicy.Policy.Ignored, (
                "DayRowTitle must use Ignored horizontal policy so a "
                "long title doesn't push the row's minimum past the "
                "viewport and clip the right-column spark."
            )
    finally:
        row.deleteLater()


# ── DayRow: bar row's minimum drops below the spark's fixed width ─────


@pytest.mark.parametrize(
    "phase", ["pick", "edit", "export"],
    ids=["pick-variant", "edit-variant", "export-variant"],
)
def test_bar_row_min_width_below_spark_for_every_variant(qapp, phase):
    """spec/129 acceptance — at narrow widths the center column must
    yield ahead of the spark. The bar-row contribution to center's
    minimum must drop well below the spark's fixed 168px so the row's
    overall minimum doesn't push the spark off-screen.

    Applies to every DayRow variant (Pick / Edit / Export — spec §3
    last bullet)."""
    row = DayRow(_snap(), phase=phase)
    try:
        spark_w = row._meta_wrap.width()
        # meta_wrap is setFixedWidth(168) — confirm it's locked.
        assert spark_w == 168, (
            "meta_wrap must stay fixed at 168px so the spark never "
            f"clips (got {spark_w})")

        # Inspect every bar row inside center: each must have a
        # minimum width well below the spark.
        bar_rows = []
        center = row._center_layout
        for i in range(center.count()):
            item = center.itemAt(i)
            sub = item.layout()
            if sub is None:
                continue
            # The bar rows are the ones containing a StageProgress;
            # the top row (title + buttons) has no track.
            has_track = False
            for j in range(sub.count()):
                w = sub.itemAt(j).widget()
                if isinstance(w, StageProgress):
                    has_track = True
                    break
            if has_track:
                bar_rows.append(sub)

        assert bar_rows, "expected at least one bar row in center"
        for br in bar_rows:
            br_min = br.minimumSize().width()
            assert br_min < spark_w, (
                f"bar-row minimum {br_min}px should be well under the "
                f"spark's {spark_w}px so the spark never clips")
            # The new floor is roughly StageProgress.minimumWidth(24)
            # + spacing — a hard upper bound here pins the "well
            # below" expectation against regressions that re-pin the
            # label/count.
            assert br_min < 80, (
                f"bar-row minimum {br_min}px is suspiciously high — "
                "did setFixedWidth creep back into the bar row?")
    finally:
        row.deleteLater()


def test_bar_row_label_and_count_are_no_longer_fixed_width(qapp):
    """Pin the actual widget contract — labels and count cells must
    have minimumWidth=0 (so they can give up width) and a finite
    maximumWidth equal to the old visual budget (so wide layout is
    unchanged)."""
    from PyQt6.QtWidgets import QLabel

    row = DayRow(_snap(), phase="pick")
    try:
        labels = [w for w in row.findChildren(QLabel)
                  if w.objectName() == "DayRowBarLabel"]
        assert labels, "expected at least one DayRowBarLabel"
        for lab in labels:
            assert lab.minimumWidth() == 0, (
                f"DayRowBarLabel '{lab.text()}' must shrink (min=0); "
                f"got {lab.minimumWidth()}")
            assert lab.maximumWidth() == 60, (
                f"DayRowBarLabel '{lab.text()}' must cap at 60 so "
                f"wide layout is unchanged; got {lab.maximumWidth()}")
    finally:
        row.deleteLater()


# ── Wide layout unchanged ──────────────────────────────────────────────


def test_wide_layout_preserves_meta_wrap_width(qapp):
    """At wide widths, meta_wrap still reports its 168px width — the
    fix changed only the compressible side, not the spark side."""
    row = DayRow(_snap(), phase="pick")
    try:
        row.resize(1200, row.sizeHint().height())
        row.adjustSize()
        assert row._meta_wrap.width() == 168
    finally:
        row.deleteLater()


# ── Regress the existing DayRow build for each variant ─────────────────


@pytest.mark.parametrize(
    "phase, expected_bars",
    [("pick", 2), ("edit", 2), ("export", 3)],
    ids=["pick-2-bars", "edit-2-bars", "export-3-bars"],
)
def test_day_row_variants_still_build_with_expected_bar_count(
    qapp, phase, expected_bars,
):
    """Regress the DayRow build for each phase variant — spec/129 is
    layout-only, the bar count + tokens per variant must not move."""
    row = DayRow(_snap(), phase=phase)
    try:
        bars = row.findChildren(StageProgress)
        assert len(bars) == expected_bars
    finally:
        row.deleteLater()
