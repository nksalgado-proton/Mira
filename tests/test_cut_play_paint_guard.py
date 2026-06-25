"""spec/89 §12.6 — :class:`_Scrubber.paintEvent` survives the Qt
zombie case.

A leaked :class:`_Scrubber` (e.g. its owning :class:`CutPlayerDialog`
was never torn down by its test, but Python's GC eventually disposes
of the C++ side later) can have a queued ``QPaintEvent`` land AFTER
its C++ widget was deleted. Pre-guard, ``QPainter(self)`` raised
``RuntimeError: wrapped C/C++ object … has been deleted`` AND blew up
the test run with an access violation. Post-guard, the paint is
swallowed silently.

These tests pin:
  * Live-widget paint still runs (no behaviour change for the common
    case);
  * The Qt-zombie path is no-op: a forcibly-deleted widget's
    ``paintEvent`` doesn't raise.
"""
from __future__ import annotations

import pytest

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QPaintEvent
from PyQt6.QtWidgets import QWidget

from mira.ui.shared.cut_play import _Scrubber


def _paint_event(widget: QWidget) -> QPaintEvent:
    """A QPaintEvent the test can dispatch by hand. The rect is the
    widget's own (any non-empty QRect works — Qt clips internally)."""
    return QPaintEvent(widget.rect())


def test_paint_runs_normally_on_live_widget(qapp):
    """Sanity — paint a live scrubber. No exception, no crash."""
    s = _Scrubber()
    s.resize(200, 28)
    s.set_entries([1000, 2000, 3000], sep_indexes=[1])
    s.set_playhead(1, 0.5)
    s.show()
    qapp.processEvents()
    # Direct call mirrors what Qt's event loop would dispatch.
    s.paintEvent(_paint_event(s))
    s.deleteLater()
    qapp.processEvents()


def test_paint_runs_normally_with_no_entries(qapp):
    """A scrubber that hasn't received ``set_entries`` yet still
    paints the empty unplayed track without raising — the early-return
    branch sits inside the try block, so it must complete cleanly."""
    s = _Scrubber()
    s.resize(200, 28)
    s.show()
    qapp.processEvents()
    s.paintEvent(_paint_event(s))
    s.deleteLater()
    qapp.processEvents()


def test_paint_is_noop_when_widget_cpp_is_deleted(qapp):
    """spec/89 §12.6 — the zombie case. Force the C++ side to be
    deleted via ``sip.delete`` (the deterministic trigger that mirrors
    what happens when a leaked widget's destructor finally runs during
    a later GC sweep). The Python wrapper survives; every Qt call
    against it raises ``RuntimeError: wrapped C/C++ object … has
    been deleted``. The guard must swallow that and the public
    ``paintEvent`` MUST NOT propagate the exception."""
    from PyQt6 import sip
    s = _Scrubber()
    s.set_entries([1000, 2000], sep_indexes=[1])
    sip.delete(s)
    # Sanity: touching the C++ wrapper raises — proves we set up the
    # zombie state correctly.
    with pytest.raises(RuntimeError):
        _ = s.width()
    # Construct a paint event from a live placeholder (the dead
    # wrapper can't make one), then dispatch by hand.
    placeholder = QWidget()
    placeholder.resize(200, 28)
    ev = _paint_event(placeholder)
    # Direct call mirrors what a queued paintEvent would do. No
    # exception escapes.
    s.paintEvent(ev)
    placeholder.deleteLater()
    qapp.processEvents()


def test_paint_inner_call_still_raises_on_zombie(qapp):
    """Defence-in-depth: the underscore-prefixed inner paint method
    is what the public ``paintEvent`` wraps. On a zombie widget that
    inner method MUST raise — otherwise the guard wouldn't be doing
    anything for a live trip through Qt's event loop. Locks in the
    contract so a future refactor that drops the guard fails fast."""
    from PyQt6 import sip
    s = _Scrubber()
    sip.delete(s)
    placeholder = QWidget()
    ev = _paint_event(placeholder)
    with pytest.raises(RuntimeError):
        s._paint(ev)                                              # noqa: SLF001
    placeholder.deleteLater()
    qapp.processEvents()
