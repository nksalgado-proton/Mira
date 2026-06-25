"""spec/147 §2 — the toolbar's "Export now · N" / "Delete now · M"
button faces and hints carry the LIVE count, update on intent
change, and disable at 0.

Two surfaces, four buttons:

* Days Grid (per-day) — ``_export_btn`` + ``_delete_now_btn``.
* Days List (all-days) — ``_export_now_btn`` + ``_delete_now_btn``.

The Days List variant exposes a public setter
(:meth:`DaysListsPage.set_export_now_counts`) so the host can push
the live N + M whenever an intent change makes them stale. The
Days Grid variant computes the counts itself in
:meth:`DaysGridPage._refresh_export_button_counts`.

Pinned contracts:

* Each button face reads "Export now · N" / "Delete now · M" when
  the count is > 0.
* Tooltips read the hint sentences from the spec.
* Each button disables at 0 and the tooltip swaps to the canonical
  "Nothing marked …" string.
* Setting the count again updates the face + hint live (no full
  re-render needed).
"""
from __future__ import annotations

import pytest

from mira.ui.pages.days_lists_page import DaysListsPage


# --------------------------------------------------------------------- #
# DaysListsPage.set_export_now_counts
# --------------------------------------------------------------------- #


@pytest.fixture
def days_list(qapp):
    page = DaysListsPage(gateway=None)
    page.set_phase_identity("export")
    yield page
    page.deleteLater()


def test_days_list_export_now_face_carries_n(days_list):
    """spec/147 §2 — when N > 0 the face reads "Export now · N"
    and the tooltip reads the canonical render hint."""
    days_list.set_export_now_counts(7, 0)
    assert days_list._export_now_btn.text() == "↑ Export now · 7"
    assert "7" in days_list._export_now_btn.toolTip()
    assert "Will export" in days_list._export_now_btn.toolTip()
    assert days_list._export_now_btn.isEnabled()


def test_days_list_delete_now_face_carries_m(days_list):
    """spec/147 §2 — when M > 0 the face reads "Delete now · M"
    and the tooltip reads the canonical delete hint."""
    days_list.set_export_now_counts(0, 3)
    assert days_list._delete_now_btn.text() == "✗ Delete now · 3"
    assert "3" in days_list._delete_now_btn.toolTip()
    assert "Set aside" in days_list._delete_now_btn.toolTip()
    assert days_list._delete_now_btn.isEnabled()


def test_days_list_zero_state_disables_both_buttons(days_list):
    """spec/147 §2 — zero state. Each button face drops the count
    suffix, the tooltip swaps to "Nothing marked …", and the
    button disables. The user can't fire a no-op verb."""
    days_list.set_export_now_counts(0, 0)
    assert days_list._export_now_btn.text() == "↑ Export now"
    assert days_list._delete_now_btn.text() == "✗ Delete now"
    assert days_list._export_now_btn.toolTip() == "Nothing marked Will export."
    assert days_list._delete_now_btn.toolTip() == "Nothing marked Set aside."
    assert days_list._export_now_btn.isEnabled() is False
    assert days_list._delete_now_btn.isEnabled() is False


def test_days_list_counts_update_live_on_intent_change(days_list):
    """spec/147 §2 — the host pushes a new (N, M) tuple whenever
    intents change; the button faces + hints must reflect the new
    counts immediately."""
    days_list.set_export_now_counts(5, 2)
    assert days_list._export_now_btn.text() == "↑ Export now · 5"
    assert days_list._delete_now_btn.text() == "✗ Delete now · 2"

    # User marks two more cells Will export.
    days_list.set_export_now_counts(7, 2)
    assert days_list._export_now_btn.text() == "↑ Export now · 7"
    assert days_list._delete_now_btn.text() == "✗ Delete now · 2"

    # User clears all Set-aside intents.
    days_list.set_export_now_counts(7, 0)
    assert days_list._delete_now_btn.text() == "✗ Delete now"
    assert days_list._delete_now_btn.isEnabled() is False


def test_days_list_delete_now_signal_exists(days_list):
    """spec/147 §2 — Delete now's host-facing signal must exist
    so the host can wire the verb to its handler. The signal pairs
    with the existing ``export_now_requested`` for parity."""
    # Both signals are defined on the class.
    assert hasattr(type(days_list), "export_now_requested")
    assert hasattr(type(days_list), "delete_now_requested")
    # And the button click emits the signal — a basic sanity check.
    fired: list = []
    days_list.delete_now_requested.connect(lambda: fired.append(True))
    days_list.set_export_now_counts(0, 1)
    days_list._delete_now_btn.click()
    assert fired == [True]


# --------------------------------------------------------------------- #
# DaysListsPage labels — renamed bulk verbs
# --------------------------------------------------------------------- #


def test_days_list_bulk_verbs_renamed_in_export_identity(days_list):
    """spec/147 §2 — under the Export identity, the bulk verbs
    read the renamed intent-only labels."""
    assert "Mark all to export" in days_list._pick_all_days_btn.text()
    assert "Set all aside" in days_list._skip_all_days_btn.text()
    # And the bulk Set aside tooltip warns about file survival —
    # the user steered toward Delete now · M for the actual blast.
    assert "Set aside" in days_list._skip_all_days_btn.toolTip()
    assert "Delete now" in days_list._skip_all_days_btn.toolTip()


# --------------------------------------------------------------------- #
# DaysListsPage — both buttons only visible in Export identity
# --------------------------------------------------------------------- #


def test_days_list_buttons_only_visible_in_export_identity(days_list):
    """The Pick / Edit identities don't expose Export now or
    Delete now. Flip identity and verify both buttons hide."""
    assert days_list._export_now_btn.isVisibleTo(days_list)
    assert days_list._delete_now_btn.isVisibleTo(days_list)
    days_list.set_phase_identity("pick")
    assert not days_list._export_now_btn.isVisibleTo(days_list)
    assert not days_list._delete_now_btn.isVisibleTo(days_list)
