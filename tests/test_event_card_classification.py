"""Tests for spec/44's classification additions on mira.ui.base.event_card.

The card structurally renders four new things — type badge (with `[type=…]`
property), subtype label (hidden when empty), tag chips (FlowLayout with
"+N more" overflow), and the description tooltip. These tests pin the
visible structure and the QSS roles; the colours themselves live in the
two QSS files (not tested here — that's an eyeball pass).
"""
from __future__ import annotations

from datetime import date

import pytest

try:
    from PyQt6.QtWidgets import QApplication, QLabel
except ImportError:                                      # pragma: no cover
    QApplication = None
    QLabel = None

from mira.ui.base.event_card import EventCard, EventCardData


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def _data(**overrides) -> EventCardData:
    base = dict(
        event_id="e1", name="A trip", start_date=date(2026, 4, 1), end_date=date(2026, 4, 14),
        is_closed=False, total_days=14,
    )
    base.update(overrides)
    return EventCardData(**base)


def _labels(card, role: str) -> list[QLabel]:
    return [
        w for w in card.findChildren(QLabel)
        if w.objectName() == role
    ]


# ── Type badge ─────────────────────────────────────────────────────────────


def test_type_badge_always_present_and_carries_type_property(qapp):
    card = EventCard(_data(event_type="trip"))
    badges = _labels(card, "EventCardTypeBadge")
    assert len(badges) == 1
    assert badges[0].property("type") == "trip"
    assert badges[0].text() == "Trip"


def test_type_badge_unclassified_when_unknown_event_type(qapp):
    """Future / legacy event_type values fall back to the unclassified label so
    the badge always renders something readable."""
    card = EventCard(_data(event_type="future_kind"))
    badges = _labels(card, "EventCardTypeBadge")
    assert badges[0].text() == "Unclassified"
    # The dynamic property still carries the raw value so QSS that cares can
    # branch on it; the label text is what we coerce.
    assert badges[0].property("type") == "future_kind"


def test_type_badge_tooltip_describes_type(qapp):
    card = EventCard(_data(event_type="session"))
    badges = _labels(card, "EventCardTypeBadge")
    assert "Session" in badges[0].toolTip()


# ── Subtype line ───────────────────────────────────────────────────────────


def test_subtype_line_renders_when_set(qapp):
    card = EventCard(_data(event_type="trip", event_subtype="Two weeks"))
    sub = _labels(card, "EventCardSubtype")
    assert len(sub) == 1
    assert sub[0].text() == "Two weeks"


def test_subtype_line_hidden_when_empty(qapp):
    card = EventCard(_data(event_subtype=None))
    assert _labels(card, "EventCardSubtype") == []


# ── Tag chips ──────────────────────────────────────────────────────────────


def test_tag_chips_render_for_short_lists(qapp):
    card = EventCard(_data(tags=["wildlife", "tropical", "rainforest"]))
    chips = _labels(card, "EventCardTagChip")
    assert {c.text() for c in chips} == {"#wildlife", "#tropical", "#rainforest"}
    assert _labels(card, "EventCardTagChipOverflow") == []


def test_tag_chips_overflow_when_too_many(qapp):
    tags = [f"t{i}" for i in range(10)]
    card = EventCard(_data(tags=tags))
    visible = _labels(card, "EventCardTagChip")
    overflow = _labels(card, "EventCardTagChipOverflow")
    assert len(visible) == EventCard._MAX_VISIBLE_TAGS
    assert len(overflow) == 1
    # Overflow chip's tooltip carries the hidden tags
    assert "#t9" in overflow[0].toolTip()


def test_tag_chips_row_absent_when_no_tags(qapp):
    card = EventCard(_data(tags=[]))
    assert _labels(card, "EventCardTagChip") == []
    assert _labels(card, "EventCardTagChipOverflow") == []


# ── Description tooltip ────────────────────────────────────────────────────


def test_description_drives_card_tooltip(qapp):
    card = EventCard(_data(description="Two weeks chasing birds in Costa Rica."))
    assert "Costa Rica" in card.toolTip()


def test_blank_description_falls_back_to_open_hint(qapp):
    card = EventCard(_data(description=""))
    assert card.toolTip() == "Open this event"


def test_long_description_truncates_in_tooltip(qapp):
    long = "A " * 300
    card = EventCard(_data(description=long))
    # Truncation cap is 280 chars + ellipsis
    assert len(card.toolTip()) <= EventCard._TOOLTIP_DESCRIPTION_CAP + 1
    assert card.toolTip().endswith("…")
