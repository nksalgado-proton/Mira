"""spec/127 — Camera Clock Correction dialog layout.

Regression: the "Pick a pair…" button was rendering ~34 px tall (base
``QPushButton`` padding 6/16 + min-height 22) and overflowed the row
next to the H:M:S ``QLineEdit`` and the state ``QComboBox``. The fix
opts the button into the existing ``#PlanBrowseCell`` QSS role, which
slims in-table-cell buttons to ~22 px so they line up with the row's
other widgets.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QPushButton

from core.tz_segments import TzSegment
from mira.store import models as m
from mira.ui.pages.camera_clock_dialog import CameraClockCorrectionDialog


class _StubEg:
    """Minimal EventGateway stand-in: yields one segment with one
    camera so the dialog renders exactly one camera row (enough to
    instantiate the Pick-a-pair button)."""

    def __init__(self):
        self.event_root = None

    def tz_segments(self):
        return [TzSegment(
            trip_tz_seconds=0,
            day_numbers=[1],
            cameras_present=["cam-A"],
        )]

    def camera_tz_corrections(self):
        return []

    def cameras(self):
        return [m.Camera(camera_id="cam-A")]

    def close(self):
        pass


class _StubGw:
    def __init__(self):
        self.opened: list[str] = []

    def open_event(self, event_id):
        self.opened.append(event_id)
        return _StubEg()


def test_pick_pair_button_uses_plan_browse_cell_role(qapp):
    """The button MUST carry the ``PlanBrowseCell`` objectName so the
    in-table-cell QSS rule (compact height) wins over the base
    ``QPushButton`` padding that would otherwise overflow the row."""
    dlg = CameraClockCorrectionDialog(_StubGw(), "evt")
    try:
        # Exactly one row was built (one camera in one segment).
        assert len(dlg._rows) == 1
        rs = next(iter(dlg._rows.values()))
        assert isinstance(rs.pick_pair_btn, QPushButton)
        assert rs.pick_pair_btn.objectName() == "PlanBrowseCell", (
            "spec/127 row layout: the 'Pick a pair…' button must opt "
            "into the #PlanBrowseCell QSS role so it matches the row's "
            "H:M:S entry / state combo height — got objectName="
            f"{rs.pick_pair_btn.objectName()!r}."
        )
    finally:
        dlg.deleteLater()
