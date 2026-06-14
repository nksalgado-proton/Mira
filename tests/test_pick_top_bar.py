"""Tests for ``mira.ui.picked.pick_top_bar.PickTopBar``.

The unified Pick top line — pure chrome. The bar's minimal contract
after the 2026-06-13 classification strip (e214d21) + the locked
``Pick / Skip`` vocab is:

* six elements — Back · type chip · info label · Pick All · Skip All · Help;
* four signals — back / keep_all / discard_all / help — fire on click;
* two setters — ``set_bucket_type`` / ``set_info`` — drive the labels;
* every button is ``NoFocus`` (the page owns the keyboard).

Retirement guards: the bar lost ``style_label`` / ``style_button``
(``GenreReadout`` / ``ReclassifyButton`` roles) and ``export_button``
(``Primary`` role) when classification UI moved to Edit-only; the
matching signals (``style_change_requested`` / ``export_requested``)
and setters (``set_style_text`` / ``set_style_visible`` /
``set_export_enabled``) retired with them. The Help button no longer
wears the misrouted ``ReclassifyButton`` role (see
``test_help_sweep.py`` for the parallel guard).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from mira.ui.picked.pick_top_bar import PickTopBar


def _bar() -> PickTopBar:
    return PickTopBar()


def test_elements_and_qss_roles(qapp):
    b = _bar()
    try:
        assert b.back_button.text() == "Back"
        assert b.back_button.objectName() == "BackButton"

        assert b.type_label.objectName() == "PositionLabel"
        assert b.info_label.objectName() == "SelectBucketInfo"

        assert b.keep_all_button.text() == "Pick All"
        assert b.skip_all_button.text() == "Skip All"

        assert b.help_button.text() == "?"
        assert b.help_button.objectName() == "HelpButton"
    finally:
        b.deleteLater()


def test_retired_attributes_are_gone(qapp):
    """Classification UI strip (e214d21) — these attrs must not return."""
    b = _bar()
    try:
        for attr in (
            "style_label", "style_button", "export_button",
            "style_change_requested", "export_requested",
            "set_style_text", "set_style_visible", "set_export_enabled",
        ):
            assert not hasattr(b, attr), (
                f"PickTopBar should not expose retired attr {attr!r}")
    finally:
        b.deleteLater()


def test_retired_object_names_absent_in_tree(qapp):
    """No widget in the bar tree should carry the retired QSS roles."""
    from PyQt6.QtWidgets import QWidget
    b = _bar()
    try:
        retired = {"GenreReadout", "ReclassifyButton"}
        offenders = [
            w.objectName() for w in b.findChildren(QWidget)
            if w.objectName() in retired
        ]
        assert offenders == [], (
            f"Retired QSS roles still wired: {offenders}")
    finally:
        b.deleteLater()


def test_every_signal_fires_on_click(qapp):
    b = _bar()
    try:
        seen: list[str] = []
        b.back_requested.connect(lambda: seen.append("back"))
        b.keep_all_requested.connect(lambda: seen.append("keep"))
        b.discard_all_requested.connect(lambda: seen.append("discard"))
        b.help_requested.connect(lambda: seen.append("help"))
        b.back_button.click()
        b.keep_all_button.click()
        b.skip_all_button.click()
        b.help_button.click()
        assert seen == ["back", "keep", "discard", "help"]
    finally:
        b.deleteLater()


def test_content_setters_drive_labels(qapp):
    b = _bar()
    try:
        b.set_bucket_type("Focus bracket")
        assert b.type_label.text() == "Focus bracket"
        assert not b.type_label.isHidden()

        b.set_bucket_type("")
        assert b.type_label.isHidden()

        b.set_info("clip.mp4 · 2/9")
        assert b.info_label.text() == "clip.mp4 · 2/9"
    finally:
        b.deleteLater()


def test_page_owns_keyboard_no_button_takes_focus(qapp):
    b = _bar()
    try:
        for w in (
            b.back_button, b.keep_all_button,
            b.skip_all_button, b.help_button,
        ):
            assert w.focusPolicy() == Qt.FocusPolicy.NoFocus
    finally:
        b.deleteLater()
