"""The shared ShortcutsDialog: rows render with the right QSS roles,
section dividers span both columns, and the dialog closes cleanly
without blocking the test harness.

Pre-2026-07-01 these tests relied on ``QTimer.singleShot(0, _grab)``
firing INSIDE :meth:`QDialog.exec`. The conftest QDialog.exec stub
(commit ``d06238e``) returns Rejected immediately without spinning
the modal event loop, so the timer never fires. The test now
constructs the dialog through :func:`build_shortcuts_dialog` (the
extracted no-exec variant of :func:`show_shortcuts`) and inspects
its child labels directly — no timer, no exec, no dependency on
event-loop spinning.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QLabel

from mira.ui.base.shortcuts import build_shortcuts_dialog


def test_show_shortcuts_renders_rows_with_qss_roles(qapp):
    """Section rows have an empty key → no ``ShortcutKey`` label for
    them; every other row carries key + action + the shared QSS
    roles."""
    dlg = build_shortcuts_dialog(None, "Test surface", [
        ("", "Decide"),
        ("P", "Pick"),
        ("X", "Skip"),
        ("", "Navigate"),
        ("◀ / ▶", "Previous / next"),
    ])
    try:
        labels = list(dlg.findChildren(QLabel))
        roles = sorted({lb.objectName() for lb in labels
                        if lb.objectName()})
        actions = [lb.text() for lb in labels
                   if lb.objectName() == "ShortcutAction"]
        keys = [lb.text() for lb in labels
                if lb.objectName() == "ShortcutKey"]
        sections = [lb.text() for lb in labels
                    if lb.objectName() == "ShortcutSection"]
        assert keys == ["P", "X", "◀ / ▶"]
        assert actions == ["Pick", "Skip", "Previous / next"]
        assert sections == ["Decide", "Navigate"]
        # The shared roles are all present.
        assert "ShortcutKey" in roles
        assert "ShortcutAction" in roles
        assert "ShortcutSection" in roles
        assert "ShortcutsHeading" in roles
    finally:
        dlg.deleteLater()


def test_show_shortcuts_with_no_section_rows(qapp):
    """Simple flat tables (no section dividers) render as one grid."""
    dlg = build_shortcuts_dialog(None, "Tiny",
                                 [("F1", "Help"), ("Esc", "Back")])
    try:
        assert isinstance(dlg, QDialog)
        keys = [lb.text() for lb in dlg.findChildren(QLabel)
                if lb.objectName() == "ShortcutKey"]
        assert keys == ["F1", "Esc"]
    finally:
        dlg.deleteLater()
