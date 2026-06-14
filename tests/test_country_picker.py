"""Tests for ``mira.ui.base.country_picker.CountryPicker``.

Pins the public contract (set_codes / codes round-trip) + the integration
seam ``ClassificationPanel`` relies on (``countries`` extras key maps to a
list of ISO 3166-1 alpha-2 codes, not free text).
"""
from __future__ import annotations

import pytest

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:                                      # pragma: no cover
    QApplication = None

from mira.ui.base.classification_panel import ClassificationPanel
from mira.ui.base.country_picker import (
    CountryPicker,
    display_label_for_code,
    load_countries,
)


@pytest.fixture
def qapp():
    if QApplication is None:
        pytest.skip("PyQt6 not installed")
    app = QApplication.instance() or QApplication([])
    yield app


# ── Data loading ──────────────────────────────────────────────────────────


def test_load_countries_returns_nonempty_iso_pairs():
    countries = load_countries()
    assert len(countries) > 200
    # Every entry: 2-letter alpha code + a non-empty name
    for code, name in countries:
        assert len(code) == 2 and code.isalpha() and code.isupper()
        assert name


def test_display_label_for_known_code():
    label = display_label_for_code("BR")
    assert label.endswith("(BR)")
    assert len(label) > len("(BR)")    # there's a country name in there


def test_display_label_for_unknown_code_falls_back_to_raw():
    # CLDR has sentinel labels for many 2-letter alpha pairs (e.g. ZZ =
    # "Unknown Region"), so those DO render a name. To exercise the fallback,
    # use a code the loader's "2-letter alpha" filter rejects — non-alpha
    # codes are guaranteed to miss the lookup dict.
    assert display_label_for_code("X9") == "X9"


def test_display_label_for_empty_string():
    assert display_label_for_code("") == ""


# ── Widget round-trip ────────────────────────────────────────────────────


def test_picker_set_codes_round_trips(qapp):
    p = CountryPicker()
    p.set_codes(["BR", "PT", "FR"])
    assert p.codes() == ["BR", "PT", "FR"]


def test_picker_normalises_case_and_dedups(qapp):
    p = CountryPicker()
    p.set_codes(["br", "BR", "pt"])
    assert p.codes() == ["BR", "PT"]


def test_picker_preserves_unknown_codes(qapp):
    """Stale / future codes shouldn't silently vanish — they survive a
    round-trip so a misconfigured event row stays intact."""
    p = CountryPicker()
    p.set_codes(["BR", "ZZ"])
    assert "ZZ" in p.codes()


def test_picker_remove_via_chip_click(qapp):
    p = CountryPicker()
    p.set_codes(["BR", "PT"])
    # The first chip is the BR remove-button; clicking it drops BR.
    chip = p._chip_layout.itemAt(0).widget()
    chip.click()
    assert p.codes() == ["PT"]


def test_picker_add_via_combo_activation(qapp):
    p = CountryPicker()
    # Find the combo index for "BR" — every country picker pre-loads the
    # full ISO list so any known code is findable by data.
    idx = p._combo.findData("BR")
    assert idx >= 0
    p._combo.setCurrentIndex(idx)
    p._on_combo_activated(idx)
    assert "BR" in p.codes()


def test_picker_emits_values_changed_on_add_and_remove(qapp):
    p = CountryPicker()
    fired: list[None] = []
    p.values_changed.connect(lambda: fired.append(None))
    idx = p._combo.findData("BR")
    p._on_combo_activated(idx)
    assert fired           # added
    fired.clear()
    chip = p._chip_layout.itemAt(0).widget()
    chip.click()           # removed
    assert fired


def test_picker_does_not_re_add_duplicates(qapp):
    p = CountryPicker()
    p.set_codes(["BR"])
    idx = p._combo.findData("BR")
    p._on_combo_activated(idx)
    assert p.codes() == ["BR"]    # no duplicate


# ── ClassificationPanel integration ──────────────────────────────────────


def test_classification_panel_uses_country_picker_for_countries(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip", extras={"countries": ["BR", "PT"]})
    widget = p._extras_widgets.get("countries")
    assert isinstance(widget, CountryPicker)
    assert widget.codes() == ["BR", "PT"]


def test_classification_panel_round_trips_countries_via_picker(qapp):
    p = ClassificationPanel()
    p.set_values(event_type="trip", extras={"countries": ["BR", "FR"]})
    v = p.values()
    assert v.extras.get("countries") == ["BR", "FR"]


def test_classification_panel_people_stays_freeform_not_country_picker(qapp):
    """``people`` is free-form — names aren't a closed list — so it stays
    as the comma-separated QLineEdit, not a picker."""
    from PyQt6.QtWidgets import QLineEdit
    p = ClassificationPanel()
    p.set_values(event_type="trip", extras={"people": ["Ana", "Carlos"]})
    widget = p._extras_widgets.get("people")
    assert isinstance(widget, QLineEdit)
    assert not isinstance(widget, CountryPicker)
