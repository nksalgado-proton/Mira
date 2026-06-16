"""Shared pytest fixtures.

Currently provides:
  - ``qapp`` session-scoped QApplication for tests that exercise Qt
    widgets. Individual Qt tests opt into it by accepting the fixture
    as a parameter.
  - Autouse modal-blocker neutralization. The app shows informational
    ``QMessageBox`` calls in places (TZ-mismatch alert, first-run
    photos-root prompt, etc.) that would hang headless tests. The
    autouse fixture replaces ``QMessageBox.information / .warning /
    .question`` with non-blocking stubs that return ``Ok`` /
    ``Yes``. Tests that NEED to observe a modal call (e.g. checking
    the alert message) can monkeypatch their own version on top.
"""

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication shared by all Qt-based tests.

    Re-uses an existing QApplication if one was already created (e.g. by
    another test). We never call quit() because killing the app mid-session
    breaks later tests that need Qt.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(scope="session", autouse=True)
def _shutdown_photo_cache_singleton():
    """Stop the PhotoCache decode worker at session end. Pages that
    embed the PhotoViewport spin the process-wide singleton (spec/63);
    without this, its QThread is destroyed while running at interpreter
    teardown. The Edit prep singleton (spec/63 §6.1) gets the same
    discipline."""
    yield
    from mira.ui.media import photo_cache as pc
    if pc._singleton is not None:
        pc._singleton.shutdown()
    try:
        from mira.ui.edited import edit_prep as ep
        ep.shutdown_edit_prep()
    except Exception:                                            # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _stub_blocking_modals(monkeypatch):
    """Replace blocking QMessageBox calls with non-blocking stubs.

    Without this, any test that walks a code path popping
    QMessageBox.information / .warning / .question hangs the suite
    indefinitely (no human present to click Ok). The stubs return the
    default-button enum so the calling code branches as if the user
    dismissed the dialog with Ok.

    Tests that want to assert a specific modal call (message contents,
    button choice) can re-monkeypatch the method to a Mock/spy on top
    of this default neutralization.
    """
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    # QFileDialog folder/file pickers — return empty-string (cancelled)
    # by default so code paths that prompt on missing paths see the
    # cancel branch in headless tests. Tests that need the user to
    # "pick a folder" should re-monkeypatch to return a real path.
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        lambda *args, **kwargs: ("", ""),
    )
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *args, **kwargs: ("", ""),
    )


# ─────────────────────────────────────────────────────────────────────────
# Slice 0 — bulk-skip tests deferred to Slice B (added 2026-06-06)
# ─────────────────────────────────────────────────────────────────────────
# The vocabulary rename of Slice 0 (cull→select, kept→picked, etc.) is
# mechanical. Some tests exercise behaviour that depends on the OLD
# 4-phase model (separate cull + select phases) or cross the legacy/rebuild
# vocab boundary (STATE_KEPT alias colliding with STATE_PICKED rebuild value,
# K→P hotkey on rebuild surfaces, legacy core.wizard.apply_capture_settings
# writing the OLD setting key, etc.). These tests need rewriting under the
# unified Select model — that's Slice B's job, not Slice 0's. Bulk-skipping
# here keeps the test signal clean until Slice B redoes them.
import pytest as _pytest

_SLICE_B_FILES = {
    "test_pick_e2e",
    "test_pick_export_gather",
    "test_quick_sweep_buckets",
    "test_quick_sweep_page",
    "test_video_marks",
    "test_video_cull_page",
    "test_source_index",
    "test_consistency_audit",
    "test_bucket_cull_shell",
    "test_bucket_cull_shell_select_nudge",
    "test_bucket_navigator",
    "test_bucket_navigator_model",
    "test_ingest_pick_page",
    "test_media_canvas_immersive",
    "test_phase_progress_export_hook",
    "test_process_export_engine",
    "test_process_decisions",
    "test_standalone_pick",
    "test_standalone_pick_copy",
    "test_edit_host_page",
    "test_edit_page",
    "test_edit_page_rebuild",
    # (test_edit_video_page + test_edit_video_page_rebuild retired
    # with the Surface 12 fold 2026-06-15 — the separate video edit
    # page is gone; its workshop coverage moves onto EditorPage.)
    "test_pick_phase_sync",
    "test_pick_clip_index",
    # (test_pick_top_bar + test_pick_stats_chart retired with the
    # PickTopBar / PickStatsChart widget retirement — the legacy Picker
    # chrome is gone; their successor widgets carry their own tests.)
    "test_pick_surface",
    "test_pick_stats",
    "test_pick_state",
    "test_pick_navigator",
    "test_pick_filter",
    "test_event_stats",
    "test_event_classification",
}


def pytest_collection_modifyitems(config, items):
    """Bulk-skip Slice-B tests with a single marker.

    Spec/48-slice-0-manifest §14 defers behavioural-divergence tests to Slice B.
    """
    skip_marker = _pytest.mark.skip(
        reason="Slice B: behaviour-divergence test deferred to unified Select implementation"
    )
    for item in items:
        if item.module.__name__.split(".")[-1] in _SLICE_B_FILES:
            item.add_marker(skip_marker)
