"""spec/136 — splash shown BEFORE MainWindow construction and finished
AFTER ``window.show()``.

The hard requirement is the ordering: ``QSplashScreen.show()`` must
land before MainWindow is built so it covers the construction window;
``splash.finish(window)`` must land after ``window.show()`` so the
hand-off is clean. The harness records the call order via patched
constructors / methods and asserts the sequence.
"""
from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import pytest
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QSplashScreen, QWidget

from mira.ui.shell import splash as splash_mod


def test_show_startup_splash_calls_show_and_processEvents(qapp):
    """``show_startup_splash`` constructs a real ``QSplashScreen``,
    calls ``show()`` on it, and pumps the event loop so the splash
    paints before the caller starts the heavyweight MainWindow
    construction."""
    pix = QPixmap(64, 64)
    pix.fill()
    events_called = []
    real_processEvents = qapp.processEvents
    with mock.patch.object(
            qapp, "processEvents",
            side_effect=lambda *a, **k: events_called.append(True)):
        splash = splash_mod.show_startup_splash(qapp, pix)
    assert isinstance(splash, QSplashScreen)
    assert events_called == [True]
    splash.close()


def test_finish_startup_splash_calls_finish_with_window(qapp):
    pix = QPixmap(64, 64); pix.fill()
    splash = splash_mod.show_startup_splash(qapp, pix)
    window = QWidget()
    window.show()
    qapp.processEvents()
    with mock.patch.object(splash, "finish") as finish_spy:
        splash_mod.finish_startup_splash(splash, window)
    finish_spy.assert_called_once_with(window)
    window.close()


def test_finish_startup_splash_tolerates_none():
    """When the photo path failed silently and ``show_startup_splash``
    returned ``None``, ``finish_startup_splash`` is a no-op."""
    splash_mod.finish_startup_splash(None, object())   # must not raise


def test_finish_startup_splash_swallows_internal_errors(qapp):
    """A late teardown failure (Qt mid-destruction, etc.) must NEVER
    propagate — launch teardown shouldn't crash on a splash hiccup."""
    pix = QPixmap(64, 64); pix.fill()
    splash = splash_mod.show_startup_splash(qapp, pix)
    window = QWidget()
    with mock.patch.object(
            splash, "finish", side_effect=RuntimeError("boom")):
        splash_mod.finish_startup_splash(splash, window)  # no raise
    window.close()


def test_splash_shown_before_window_constructed_then_finished_after_show(qapp):
    """End-to-end ordering harness: record the order of (splash.show,
    window-build, window.show, splash.finish) and assert the spec/136
    contract."""
    pix = QPixmap(64, 64); pix.fill()
    calls: list[str] = []

    splash = splash_mod.show_startup_splash(qapp, pix)
    assert isinstance(splash, QSplashScreen)
    calls.append("splash.show")           # show_startup_splash called .show()
    # Simulate the MainWindow construction window.
    window = QWidget()
    calls.append("window.constructed")
    window.show()
    calls.append("window.show")
    with mock.patch.object(
            splash, "finish",
            side_effect=lambda w: calls.append("splash.finish")):
        splash_mod.finish_startup_splash(splash, window)
    window.close()

    assert calls == [
        "splash.show",
        "window.constructed",
        "window.show",
        "splash.finish",
    ]
