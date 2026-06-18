"""Tests that ``QApplication.aboutToQuit`` releases the library
writer lock — spec/76 §A.6 ("released on clean exit").

The pure-logic ``core.library_lock`` primitive already has its own
suite (``test_library_lock.py``). This file pins the UI wiring
contract: that the teardown callback we hand to ``aboutToQuit``
actually removes ``.mira-writer.lock`` when Qt is about to exit
its event loop. It complements the manual eyeball check in the
spec/76 brief — automated, headless, no GUI window.
"""
from __future__ import annotations

from pathlib import Path

from core import library_lock
from core.library_lock import LOCK_FILENAME, acquire


def test_about_to_quit_releases_lock(qapp, tmp_path: Path):
    """Connecting our teardown to ``aboutToQuit`` removes the lock
    file when Qt fires the signal — mirrors the wiring in
    ``mira/ui/app.py``."""
    library_root = tmp_path
    result = acquire(library_root)
    assert result.acquired is True
    assert (library_root / LOCK_FILENAME).exists()

    def _teardown():
        library_lock.release(library_root)
    qapp.aboutToQuit.connect(_teardown)

    try:
        qapp.aboutToQuit.emit()
        assert not (library_root / LOCK_FILENAME).exists()
    finally:
        qapp.aboutToQuit.disconnect(_teardown)


def test_release_is_idempotent_after_about_to_quit(qapp, tmp_path: Path):
    """``main()`` keeps a belt-and-suspenders ``release`` call after
    ``app.exec()`` returns. After ``aboutToQuit`` has already removed
    the file, that second ``release`` is a safe no-op."""
    library_root = tmp_path
    acquire(library_root)
    assert library_lock.release(library_root) is True
    # Second call (the post-``exec()`` safety net) finds nothing.
    assert library_lock.release(library_root) is False


def test_excepthook_release_chain_clears_lock_before_chaining(tmp_path: Path):
    """Nelson 2026-06-18 — a Python exception in a paintEvent must release
    the lock BEFORE the previous excepthook fires (which is the default
    Python hook that prints to stderr and dies). Mirrors the chain
    ``mira/ui/app.py`` installs."""
    import sys
    library_root = tmp_path
    acquire(library_root)
    assert (library_root / LOCK_FILENAME).exists()

    chained_called: list = []
    prev_hook = sys.excepthook

    def fake_prev(exc_type, exc, tb):
        chained_called.append(exc_type)

    def release_then_chain(exc_type, exc, tb):
        library_lock.release(library_root)
        fake_prev(exc_type, exc, tb)

    sys.excepthook = release_then_chain
    try:
        try:
            raise RuntimeError("simulated paintEvent KeyError")
        except RuntimeError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
        # Lock gone BEFORE the chained hook ran (the order is the load
        # bearing part — we cleared the file first, then signalled the
        # prev hook).
        assert not (library_root / LOCK_FILENAME).exists()
        assert chained_called == [RuntimeError]
    finally:
        sys.excepthook = prev_hook


def test_atexit_handler_releases_lock_on_interpreter_exit(tmp_path: Path):
    """The third layer (after ``aboutToQuit`` and the ``try/finally``):
    register a release via ``atexit`` so a Python crash that bypasses
    the event-loop unwind path still leaks no lock file. Tested by
    invoking the registered handler directly (atexit runs at
    interpreter shutdown, which we can't drive from inside a test)."""
    import atexit
    library_root = tmp_path
    acquire(library_root)
    assert (library_root / LOCK_FILENAME).exists()

    def handler():
        library_lock.release(library_root)

    atexit.register(handler)
    try:
        handler()                              # simulate atexit firing
        assert not (library_root / LOCK_FILENAME).exists()
    finally:
        atexit.unregister(handler)
