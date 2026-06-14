"""Tests for ``mira.ui.base.classification_panel.ClassificationPanel``.

Pin the contract three downstream surfaces depend on (NewEventPage, the
pre-ingest dialog in Slice C, the Edit-info dialog in Slice D): set_values
round-trips through values(), Subtype presets re-seed on Type change,
extras rebuild per type, and the description cap is enforced at read time.
"""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                      # pragma: no cover
    QApplication = None

from mira import event_classification as ec
from mira.ui.base.classification_panel import (
    DESCRIPTION_MAX,
    ClassificationPanel,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


def test_default_values_are_unclassified_blank(qapp):
    p = ClassificationPanel()
    v = p.values()
    assert v.event_type == ec.EVENT_TYPE_UNCLASSIFIED
    assert v.event_subtype is None
    assert v.description == ""
    assert v.tags == []
    assert v.extras == {}


def test_set_values_round_trips_simple_fields(qapp):
    p = ClassificationPanel()
    p.set_values(
        event_type="trip", event_subtype="Two weeks",
        description="Birds + rainforest.",
        tags=["wildlife", "tropical"],
    )
    v = p.values()
    assert v.event_type == "trip"
    assert v.event_subtype == "Two weeks"
    assert v.description == "Birds + rainforest."
    assert v.tags == ["wildlife", "tropical"]


def test_set_values_round_trips_extras_per_type(qapp):
    p = ClassificationPanel()
    p.set_values(
        event_type="trip",
        extras={"countries": ["CR", "PT"], "duration_label": "two_weeks",
                "people": ["Ana", "Carlos"]},
    )
    v = p.values()
    assert v.extras.get("countries") == ["CR", "PT"]
    assert v.extras.get("duration_label") == "two_weeks"
    assert v.extras.get("people") == ["Ana", "Carlos"]


def test_unknown_type_normalises_to_unclassified(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="happening")     # the user's working name, renamed
    assert p.values().event_type == ec.EVENT_TYPE_UNCLASSIFIED


def test_subtype_presets_seed_on_type_change(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="session")
    # The subtype combo carries Session's presets after type=session
    items = [p._subtype.itemText(i) for i in range(p._subtype.count())]
    for preset in ec.subtype_presets_for("session"):
        assert preset in items


def test_extras_rebuild_when_type_flips(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip", extras={"countries": ["CR"]})
    # Type=trip exposes countries
    assert "countries" in p._extras_widgets
    # Programmatic radio click for the on_type_changed flow
    p._type_buttons["occasion"].setChecked(True)
    p._on_type_changed()
    assert "countries" not in p._extras_widgets
    # Occasion-specific keys appear
    assert "host" in p._extras_widgets


def test_extras_filter_to_current_type_at_read_time(qapp):
    """Even if a stale widget is still wired up, values() should only emit
    extras keys that match the current type (defensive — keeps the gateway's
    set_classification call clean)."""
    p = ClassificationPanel()
    p.set_values(event_type="trip", extras={"countries": ["CR"]})
    # User flips to Project — countries shouldn't leak through
    p._type_buttons["project"].setChecked(True)
    p._on_type_changed()
    v = p.values()
    assert "countries" not in v.extras


def test_description_truncates_at_cap(qapp):
    p = ClassificationPanel()
    long = "A " * (DESCRIPTION_MAX + 50)
    p.set_values(description=long)
    v = p.values()
    assert len(v.description) <= DESCRIPTION_MAX


def test_tags_parse_strips_blanks_and_whitespace(qapp):
    p = ClassificationPanel()
    p._tags.setText("  wildlife ,, tropical ,  ")
    v = p.values()
    assert v.tags == ["wildlife", "tropical"]


def test_values_changed_fires_on_user_edit_not_on_set_values(qapp):
    p = ClassificationPanel()
    fired: list[None] = []
    p.values_changed.connect(lambda: fired.append(None))
    # Programmatic set_values: no signals during the populate
    p.set_values(event_type="trip", description="something")
    assert fired == []
    # Real user edit: signal fires
    p._description.setPlainText("user-edited")
    assert fired


def test_set_values_with_custom_subtype_keeps_text(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="project", event_subtype="my custom subtype")
    assert p.values().event_subtype == "my custom subtype"


def test_empty_subtype_returns_none(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip", event_subtype=None)
    assert p.values().event_subtype is None


# ── Tag suggestion chips ───────────────────────────────────────────────────


def _chip_texts(panel):
    layout = panel._tag_suggestions_layout
    return [layout.itemAt(i).widget().text() for i in range(layout.count())]


def test_tag_suggestion_chips_seed_per_type(qapp):
    """Each type carries its own curated example chips so the user learns
    what kind of tags fit the context."""
    p = ClassificationPanel()
    p.set_values(event_type="trip")
    texts = _chip_texts(p)
    # Chips render with a "+ " prefix; check the raw tags appear in the row.
    joined = " ".join(texts)
    for sample in ("wildlife", "landscape", "sunset"):
        assert sample in joined


def test_tag_suggestion_chips_change_with_type(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip")
    trip_chips = " ".join(_chip_texts(p))
    p._type_buttons["session"].setChecked(True)
    p._on_type_changed()
    session_chips = " ".join(_chip_texts(p))
    assert trip_chips != session_chips
    # Session-specific examples present
    for sample in ("golden-hour", "macro", "b&w"):
        assert sample in session_chips


def test_clicking_a_chip_appends_the_tag(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip", tags=["existing"])
    layout = p._tag_suggestions_layout
    first = layout.itemAt(0).widget()
    first.click()
    v = p.values()
    assert "existing" in v.tags
    assert len(v.tags) == 2     # one chip added


def test_clicking_a_chip_twice_does_not_duplicate(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip")
    chip = p._tag_suggestions_layout.itemAt(0).widget()
    chip.click()
    chip.click()
    chip.click()
    v = p.values()
    assert len(v.tags) == 1
