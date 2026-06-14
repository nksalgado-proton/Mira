"""The 2026-06-12 Back-button sweep (Nelson): every surface's
quit-and-return button reads "Back" in the standard button style.

The factory contract (label + role + plain style) is covered by
test_base_surface.TestCanonicalAffordances. This file pins the
SWEEP — each surface that has a quit-and-return button uses the
factory, not a hand-built QPushButton with a glyph or colour role.

Build each surface in isolation (we don't need it wired to data;
the button is constructed in __init__). If a future call site
reintroduces a raw `QPushButton("← Back")` or a `#DangerButton`
role on a navigation control, this fails."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from mira.ui.base.day_grid_view import DayGridView
from mira.ui.picked.pick_top_bar import PickTopBar


def _all_pushbuttons(w: QWidget) -> list[QPushButton]:
    return w.findChildren(QPushButton)


def _back_buttons(w: QWidget) -> list[QPushButton]:
    """Every BackButton-role widget under ``w``."""
    return [b for b in _all_pushbuttons(w) if b.objectName() == "BackButton"]


def test_pick_top_bar_back_is_plain_back(qapp):
    bar = PickTopBar()
    assert bar.back_button.text() == "Back"
    assert bar.back_button.objectName() == "BackButton"


def test_day_grid_back_uses_plain_label(qapp):
    view = DayGridView()
    # The DayGridBackButton object name is kept for the surface QSS
    # override slot; the label is the uniform plain "Back".
    btns = [b for b in _all_pushbuttons(view)
            if b.objectName() == "DayGridBackButton"]
    assert len(btns) == 1
    assert btns[0].text() == "Back"


def test_bucket_navigator_quit_button_has_no_danger_role(qapp):
    """Nelson's exact ask — the bucket navigator's leave-the-phase
    button used to wear ``#DangerButton`` (red outline). That non-house
    colour on a navigation control is dropped; the button is the
    plain BackButton role now."""
    from mira.ui.base.bucket_navigator import BucketNavigator
    nav = BucketNavigator()
    # No widget anywhere under the navigator should still carry the
    # DangerButton role.
    danger = [b for b in _all_pushbuttons(nav)
              if b.objectName() == "DangerButton"]
    assert danger == [], (
        "DangerButton role survived on the navigator — Nelson's "
        "non-house-colour ask reverts.")
    # The leave button reads "Back" (the config default).
    assert nav._quit_btn.text() == "Back"
    assert nav._quit_btn.objectName() == "BackButton"


def test_no_raw_back_glyph_in_navigation_modules(qapp):
    """The factory is the single source of truth for the Back label
    — no surface should still ship "← Back" / "⟵ Back" / "Back to
    days" in a hand-built QPushButton. Catches regressions where a
    contributor reintroduces a raw QPushButton + glyph."""
    import mira.ui as ui_pkg

    forbidden = ("← Back", "⟵ Back", "← Library", "Back to days",
                 "← Quit Comparison")
    # Walk the UI source tree for hand-built back buttons. Each match
    # is a place that should use back_button() instead — the sweep
    # converted every known instance; this guards the contract.
    import pathlib
    root = pathlib.Path(ui_pkg.__file__).resolve().parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("__init__.py",):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in forbidden:
            if f'QPushButton(tr("{needle}"))' in text or \
                    f'QPushButton("{needle}")' in text:
                offenders.append((path, needle))
    assert offenders == [], (
        "Raw QPushButton back-glyph reintroduced — use "
        "back_button() from mira.ui.base.surface instead. "
        f"Offenders: {offenders}")
