"""The 2026-06-12 Play/Pause sweep (Nelson): every video transport
button across the app wears the canonical TransportButton role + plain
style, and its WIDTH is pinned so the Play↔Pause glyph swap can't
shift the neighbouring buttons on the row.

Three contracts pinned:
  1. Factory: ``transport_button()`` returns a fixed-width ▶ button.
  2. Toggle helper: ``set_transport_playing(btn, True/False)`` swaps
     ▶ ⇄ ⏸ and the button width does NOT change.
  3. Per-surface: each known transport button uses the role and
     keeps its width across the swap.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

from mira.ui.base.surface import (
    set_transport_playing,
    transport_button,
)


# ── factory + helper ─────────────────────────────────────────────────


def test_transport_button_default_shape(qapp):
    """Nelson 2026-06-15 line-icon sweep: the play / pause Unicode glyph
    pair retires. The button is icon-only — GLYPH_PLAY / GLYPH_PAUSE
    from the SVG family, tinted to the active theme's ink. No text."""
    b = transport_button()
    assert b.text() == ""
    assert b.objectName() == "TransportButton"
    # Icon present (it's set in the factory's _refresh_icon).
    assert not b.icon().isNull()
    # Fixed width — the whole point of the factory. The exact value
    # is the factory's choice; just assert it isn't zero / dynamic.
    assert b.minimumWidth() == b.maximumWidth() > 0
    # Plain look — none of the CTA / FeatureToggle roles.
    assert b.objectName() not in {"Primary", "FeatureToggle"}


def test_transport_button_tooltip_threads_through(qapp):
    b = transport_button("Play / pause  (Space)")
    assert b.toolTip() == "Play / pause  (Space)"


def test_set_transport_playing_flips_state_width_unchanged(qapp):
    """``set_transport_playing`` flips the button's playing state; the
    icon swap is the only visible change, the width stays pinned."""
    b = transport_button()
    w_play = b.sizeHint().width()
    assert hasattr(b, "is_playing")
    assert b.is_playing() is False
    set_transport_playing(b, True)
    assert b.is_playing() is True
    assert b.sizeHint().width() == w_play     # FIXED width — no dance
    set_transport_playing(b, False)
    assert b.is_playing() is False
    assert b.sizeHint().width() == w_play
    # Still no text — the icon does the work.
    assert b.text() == ""


# ── per-surface ──────────────────────────────────────────────────────


def _transport_btn(w) -> QPushButton:
    """Return the FIRST TransportButton-role widget under ``w``."""
    btns = [b for b in w.findChildren(QPushButton)
            if b.objectName() == "TransportButton"]
    assert btns, "no TransportButton found"
    return btns[0]


def test_unified_picker_video_transport_is_canonical(qapp):
    """The Primary-role colour that used to paint video Play/Pause as
    a CTA is gone — the role is TransportButton, the width is pinned.
    (Nelson 2026-06-15: spec/70 row 11 folded into 07. PickerPage owns
    the video transport bar on its compact_row reveal — same contract,
    no separate page.)"""
    from mira.ui.pages.video_transport import VideoTransportBar
    bar = VideoTransportBar()
    b = bar.play_btn
    assert b.objectName() == "TransportButton"
    w_before = b.sizeHint().width()
    set_transport_playing(b, True)
    assert b.is_playing() is True
    assert b.sizeHint().width() == w_before
    # The misrouted Primary role is GONE under the bar.
    misrouted = [x for x in bar.findChildren(QPushButton)
                 if x is b and x.objectName() == "Primary"]
    assert misrouted == []


def test_pick_photo_surface_film_transport_is_canonical(qapp):
    """pick_photo_surface's slideshow ▶ used to wear FeatureToggle —
    accent-coloured when checked. Now plain TransportButton; the
    checkable BEHAVIOUR (`isChecked()` drives the play state) is
    preserved."""
    from mira.ui.picked.pick_photo_surface import PickPhotoSurface
    surf = PickPhotoSurface()
    b = surf._film_btn
    assert b.objectName() == "TransportButton"
    assert b.isCheckable() is True           # behaviour preserved
    w_before = b.sizeHint().width()
    set_transport_playing(b, True)
    assert b.sizeHint().width() == w_before
    set_transport_playing(b, False)
    assert b.sizeHint().width() == w_before


def test_quick_sweep_page_transport_is_canonical(qapp):
    from mira.ui.pages.quick_sweep_page import QuickSweepPage
    page = QuickSweepPage()
    b = page._play_btn
    assert b.objectName() == "TransportButton"
    w_before = b.sizeHint().width()
    set_transport_playing(b, True)
    assert b.sizeHint().width() == w_before


# ── regression guard ────────────────────────────────────────────────


def test_no_play_pause_label_swap_left_in_ui_modules():
    """The old ``"▶ Play"`` / ``"⏸ Pause"`` label swap is the exact
    pattern that produced the width-dance. The transport factory
    replaces it. Any surface still calling ``setText(tr("▶ Play"))``
    is a regression — set_transport_playing() is the seam now.

    EXCEPTIONS: the Day-Grid Play button (one-shot, never toggles —
    no dance to fix); the keyboard-shortcuts dialog (the help table
    is allowed to print the glyph names verbatim)."""
    import pathlib
    import mira.ui as ui_pkg
    root = pathlib.Path(ui_pkg.__file__).resolve().parent

    offenders = []
    allowlist = {
        "day_grid_view.py",        # one-shot Play button — no dance
    }
    for path in root.rglob("*.py"):
        if path.name in allowlist:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if ('setText(tr("▶ Play"))' in text or
                'setText(tr("⏸ Pause"))' in text):
            offenders.append(str(path))
    assert offenders == [], (
        "Play/Pause label-swap reintroduced — use "
        "set_transport_playing() from mira.ui.base.surface. "
        f"Offenders: {offenders}")
