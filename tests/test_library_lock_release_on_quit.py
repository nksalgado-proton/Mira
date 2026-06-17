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
