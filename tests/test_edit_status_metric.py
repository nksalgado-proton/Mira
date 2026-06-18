"""The Edit metric + edit decoration (Nelson 2026-06-18).

Baseline is **Original only** — a photo is unedited solely at the Original
look with no filter and no crop. Natural is a deliberate Look ("the default
one"). ``edit_reasons`` reports every dimension off baseline, in order
(look, filter, crop); the boolean Edit metric is ``bool(edit_reasons)``.

Tiers:
* ``core.edit_status`` — the pure predicate / reasons + their SQL twin.
* gateway ``edited_count`` + ``phase_day_progress['edit']`` — edited ÷ picked.
* Days-Grid — green/amber edit border + the Look/Filter/Crop reason pill.
* Days-Lists Edit row — As shot (green) + Edited (amber), summing to 100%.
* editor default — new adjustments start at Original (no look applied).
"""
from __future__ import annotations

import pytest

from core.edit_status import edit_reasons, is_adjustment_edited
from mira.store import models as m

# reuse the gateway fixture (materialised rich event)
from tests.test_gateway import event_gw  # noqa: F401


def _adj(**kw) -> m.Adjustment:
    return m.Adjustment(item_id="x", **kw)


# --------------------------------------------------------------------------- #
# Pure predicate / reasons — Original-only baseline
# --------------------------------------------------------------------------- #


def test_model_default_look_is_original():
    # The editor now starts new photos at Original (no look applied).
    assert m.Adjustment(item_id="x").look == "original"


def test_original_is_the_only_unedited_look():
    assert edit_reasons(_adj(look="original")) == ()
    assert edit_reasons(_adj()) == ()                 # default == original
    assert is_adjustment_edited(_adj(look="original")) is False


def test_none_adjustment_is_unedited():
    assert edit_reasons(None) == ()


def test_natural_counts_as_a_look_edit():
    assert edit_reasons(_adj(look="natural")) == ("look",)
    assert edit_reasons(_adj(look="brighter")) == ("look",)


def test_filter_and_crop_need_original_look_to_read_alone():
    assert edit_reasons(_adj(look="original", creative_filter="bw")) == ("filter",)
    assert edit_reasons(_adj(look="original", crop_w=0.5)) == ("crop",)
    assert edit_reasons(_adj(look="original", aspect_label="4:3")) == ("crop",)
    assert edit_reasons(_adj(look="original", aspect_label="Original")) == ()


def test_reasons_are_independent_and_ordered():
    r = edit_reasons(_adj(look="deeper", creative_filter="bw", crop_w=0.5))
    assert r == ("look", "filter", "crop")
    # Natural look + crop → both, look first.
    assert edit_reasons(_adj(look="natural", crop_w=0.5)) == ("look", "crop")


# --------------------------------------------------------------------------- #
# Gateway SQL twin
# --------------------------------------------------------------------------- #


def test_edited_count_counts_off_baseline_rows(event_gw):
    # Fixture i-photo is look="brighter" + a crop → edited.
    assert event_gw.edited_count() == 1
    # An Original-baseline row does NOT inflate the count.
    event_gw.save_adjustment(m.Adjustment(item_id="i-stk", look="original"))
    assert len(event_gw.adjustments()) == 2
    assert event_gw.edited_count() == 1
    # But Natural IS a Look edit — flipping i-stk to natural counts it.
    event_gw.save_adjustment(m.Adjustment(item_id="i-stk", look="natural"))
    assert event_gw.edited_count() == 2


def test_phase_day_progress_edit_bucket_is_edited_over_picked(event_gw):
    edit = event_gw.phase_day_progress()["edit"]
    assert edit[1]["decided"] == 1     # edited numerator (i-photo)
    assert edit[1]["total"] == 1       # picked denominator
    event_gw.save_adjustment(m.Adjustment(item_id="i-stk", look="original"))
    assert event_gw.phase_day_progress()["edit"][1]["decided"] == 1


# --------------------------------------------------------------------------- #
# Qt surfaces (run under verify.bat on Windows)
# --------------------------------------------------------------------------- #


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_thumb_edit_border_and_reason_pill(qapp):
    from mira.ui.design.thumbs import Thumb
    edited = Thumb(edit_reasons=("look", "crop"), border_token="amber")
    assert edited._edit_reasons == ("look", "crop")
    assert edited._border_token == "amber"
    unedited = Thumb(edit_reasons=(), border_token="green")
    assert unedited._edit_reasons == ()
    assert unedited._border_token == "green"


def test_edit_row_as_shot_plus_edited_is_100(qapp):
    from mira.ui.design import StageProgress
    from mira.ui.pages.days_lists_page import DayRow, DaySnapshot
    snap = DaySnapshot(
        day_number=3, title="Cloud forest", date_iso="2026-04-03",
        picked=10, skipped=2, edited=4, items=20,
    )
    row = DayRow(snap, phase="edit")
    bars = row.findChildren(StageProgress)
    as_shot, edited = bars
    assert as_shot.value() == 60 and as_shot._color_token == "green"
    assert edited.value() == 40 and edited._color_token == "amber"
    assert as_shot.value() + edited.value() == 100
