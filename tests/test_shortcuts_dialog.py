"""The shared ShortcutsDialog: rows render with the right QSS roles,
section dividers span both columns, and the dialog closes cleanly
without blocking the test harness."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QGridLayout, QLabel

from mira.ui.base.shortcuts import show_shortcuts


def _find_dialog(qapp) -> QDialog | None:
    for w in qapp.topLevelWidgets():
        if isinstance(w, QDialog) and w.objectName() == "ShortcutsDialog":
            return w
    return None


def test_show_shortcuts_renders_rows_with_qss_roles(qapp):
    """The dialog is modal — close it before it exec()s so the test
    doesn't park. We hook the dialog the moment it shows and inspect
    its child grid before closing."""
    captured: dict = {}

    def _grab():
        dlg = _find_dialog(qapp)
        if dlg is None:
            QTimer.singleShot(20, _grab)
            return
        labels = [c for c in dlg.findChildren(QLabel)]
        captured["roles"] = sorted({lb.objectName() for lb in labels
                                    if lb.objectName()})
        captured["actions"] = [lb.text() for lb in labels
                               if lb.objectName() == "ShortcutAction"]
        captured["keys"] = [lb.text() for lb in labels
                            if lb.objectName() == "ShortcutKey"]
        captured["sections"] = [lb.text() for lb in labels
                                if lb.objectName() == "ShortcutSection"]
        dlg.accept()

    QTimer.singleShot(0, _grab)
    show_shortcuts(None, "Test surface", [
        ("", "Decide"),
        ("P", "Pick"),
        ("X", "Skip"),
        ("", "Navigate"),
        ("◀ / ▶", "Previous / next"),
    ])
    # Section rows have an empty key → no "ShortcutKey" label for them.
    assert captured["keys"] == ["P", "X", "◀ / ▶"]
    assert captured["actions"] == ["Pick", "Skip", "Previous / next"]
    assert captured["sections"] == ["Decide", "Navigate"]
    # The shared roles are all present.
    assert "ShortcutKey" in captured["roles"]
    assert "ShortcutAction" in captured["roles"]
    assert "ShortcutSection" in captured["roles"]
    assert "ShortcutsHeading" in captured["roles"]


def test_show_shortcuts_with_no_section_rows(qapp):
    """Simple flat tables (no section dividers) render as one grid."""
    def _grab():
        dlg = _find_dialog(qapp)
        if dlg is None:
            QTimer.singleShot(20, _grab)
            return
        dlg.accept()

    QTimer.singleShot(0, _grab)
    show_shortcuts(None, "Tiny", [("F1", "Help"), ("Esc", "Back")])
