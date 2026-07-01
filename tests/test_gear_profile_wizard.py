"""spec/85 — :class:`GearProfileWizard` tests.

Drives the wizard against a hand-seeded ``mira.db`` + a real
:class:`LibraryGateway` so the read + write paths exercise the slice-2
gear_profile table verbatim. The wiring test confirms the cross-event DC
list dialog's "Manage my gear…" button opens the wizard.
"""
from __future__ import annotations

import pytest

from mira.gateway.library_gateway import LibraryGateway
from mira.ui.pages.gear_profile_wizard import (
    GEAR_PRE_TICK_THRESHOLD,
    GearProfileWizard,
    WIZARD_GENRES,
    _GearRow,
)
from mira.user_store import models as um
from mira.user_store.repo import UserStore


NOW = "2026-06-17T00:00:00+00:00"


def _open_lg(tmp_path):
    store = UserStore.create(
        tmp_path / "mira.db", app_version="test", created_at=NOW)
    return LibraryGateway(store, now=lambda: NOW), store


def _seed_inventory(store: UserStore, *, cameras=(), lenses=()):
    """Seed cameras and lenses via global_items rows so available_cameras /
    available_lenses see them."""
    i = 0
    for key, count in cameras:
        for _ in range(count):
            store.upsert(um.GlobalItem(
                event_uuid="E", item_id=f"c{i}", synced_at=NOW,
                camera_id=key))
            i += 1
    for key, count in lenses:
        for _ in range(count):
            store.upsert(um.GlobalItem(
                event_uuid="E", item_id=f"l{i}", synced_at=NOW,
                lens_model=key))
            i += 1


# --------------------------------------------------------------------------- #
# Populate — inventory + pre-fill
# --------------------------------------------------------------------------- #


def test_wizard_pre_ticks_high_count_gear(qapp, tmp_path):
    """spec/85 §3 — rows with count ≥ GEAR_PRE_TICK_THRESHOLD open ticked;
    low-count gear opens unticked."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(
        store,
        cameras=[("Pana+G9M2", GEAR_PRE_TICK_THRESHOLD + 5),
                 ("Sony A7", 2)],
        lenses=[("LEICA 45mm", GEAR_PRE_TICK_THRESHOLD), ("Borrowed", 1)],
    )
    w = GearProfileWizard(lg)
    cameras = {r.key: r.is_active() for r in w.camera_rows()}
    lenses = {r.key: r.is_active() for r in w.lens_rows()}
    assert cameras["Pana+G9M2"] is True
    assert cameras["Sony A7"] is False
    assert lenses["LEICA 45mm"] is True       # exactly at the threshold
    assert lenses["Borrowed"] is False
    store.close()


def test_wizard_existing_profile_overrides_pre_tick(qapp, tmp_path):
    """If the user previously declared a camera inactive, re-opening the
    wizard preserves that — the photo-count default never silently
    re-ticks gear the user already curated."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(store, cameras=[("Pana+G9M2", 99)])
    # Existing row: inactive even though count is high.
    lg.set_gear_active("camera", "Pana+G9M2", False)
    w = GearProfileWizard(lg)
    assert w.camera_rows()[0].is_active() is False
    store.close()


def test_wizard_existing_profile_loads_genres(qapp, tmp_path):
    """spec/85 §3 — existing preferred_genres pre-populate the row's genre
    picker so the user reviews instead of redeclaring."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(store, lenses=[("LEICA 45mm", 50)])
    lg.set_gear_genres("lens", "LEICA 45mm", ["macro", "portrait"])
    w = GearProfileWizard(lg)
    row = w.lens_rows()[0]
    assert set(row.selected_genres()) == {"macro", "portrait"}
    store.close()


def test_wizard_loading_label_swaps_out_after_populate(qapp, tmp_path):
    lg, store = _open_lg(tmp_path)
    _seed_inventory(store, cameras=[("Pana+G9M2", 1)])
    w = GearProfileWizard(lg)
    # The loading label is hidden after populate completes; the body scroll
    # area is no longer hidden.
    assert w._gathering_label.isHidden()
    assert not w._scroll.isHidden()
    store.close()


def test_wizard_with_empty_inventory_shows_placeholders(qapp, tmp_path):
    """Fresh install with no items yet still opens cleanly — the user
    sees a hint per section instead of an empty list."""
    lg, store = _open_lg(tmp_path)
    w = GearProfileWizard(lg)
    assert w.camera_rows() == []
    assert w.lens_rows() == []
    store.close()


# --------------------------------------------------------------------------- #
# Commit
# --------------------------------------------------------------------------- #


def test_wizard_save_writes_active_and_genres(qapp, tmp_path):
    """Save persists every row through set_gear_active + set_gear_genres."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(
        store,
        cameras=[("Pana+G9M2", 50)],
        lenses=[("LEICA 45mm", 50)],
    )
    w = GearProfileWizard(lg)
    # Mutate state.
    cam_row = w.camera_rows()[0]
    cam_row.set_active(True)
    cam_row.set_genres(["wildlife"])
    lens_row = w.lens_rows()[0]
    lens_row.set_active(False)
    lens_row.set_genres(["macro", "portrait"])
    fired = []
    w.saved.connect(lambda: fired.append(1))
    w._on_save()
    # Persisted state — read back through the repo.
    cam_db = lg.gear_profile_for("camera", "Pana+G9M2")
    lens_db = lg.gear_profile_for("lens", "LEICA 45mm")
    assert cam_db.is_active is True
    assert set(LibraryGateway.gear_preferred_genres(cam_db)) == {"wildlife"}
    assert lens_db.is_active is False
    assert set(LibraryGateway.gear_preferred_genres(lens_db)) == \
        {"macro", "portrait"}
    assert fired == [1]
    store.close()


def test_wizard_cancel_does_not_write(qapp, tmp_path):
    """Cancel discards every mutation — the user can back out without
    accidentally re-tagging gear they meant to leave alone."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(store, cameras=[("Pana+G9M2", 50)])
    w = GearProfileWizard(lg)
    w.camera_rows()[0].set_active(True)
    w.camera_rows()[0].set_genres(["wildlife"])
    w.reject()
    # Nothing landed.
    assert lg.gear_profile_for("camera", "Pana+G9M2") is None
    store.close()


# --------------------------------------------------------------------------- #
# Genre picker covers the wizard genre set
# --------------------------------------------------------------------------- #


def test_genre_picker_offers_all_wizard_genres(qapp, tmp_path):
    """spec/85 §3 — the wizard's genre set matches the first-run wizard
    so users see the same labels in both surfaces."""
    lg, store = _open_lg(tmp_path)
    _seed_inventory(store, lenses=[("LEICA 45mm", 1)])
    w = GearProfileWizard(lg)
    picker = w.lens_rows()[0]._genre_picker
    labels = [cb.text() for cb in picker._boxes]
    assert tuple(labels) == WIZARD_GENRES
    store.close()


# --------------------------------------------------------------------------- #
# Wiring — "Manage my gear…" button on the DC list dialog
# --------------------------------------------------------------------------- #


@pytest.mark.skip(
    reason="spec/162 Round 2b — CrossEventDcsDialog + its Manage-Gear "
           "button retired with the Save/Load Collection surface")
def test_dc_list_dialog_has_manage_gear_button(qapp, tmp_path):
    from mira.ui.pages.cross_event_dcs_dialog import CrossEventDcsDialog
    lg, store = _open_lg(tmp_path)
    d = CrossEventDcsDialog(lg)
    assert d._manage_gear_btn.text().startswith("Manage")
    store.close()


@pytest.mark.skip(
    reason="spec/162 Round 2b — CrossEventDcsDialog + its Manage-Gear "
           "button retired with the Save/Load Collection surface")
def test_dc_list_dialog_manage_gear_opens_wizard(qapp, tmp_path, monkeypatch):
    """Clicking "Manage my gear…" instantiates the wizard. We monkeypatch
    out exec so the test stays headless."""
    from mira.ui.pages import cross_event_dcs_dialog as mod
    lg, store = _open_lg(tmp_path)
    d = mod.CrossEventDcsDialog(lg)

    instantiated = []

    class _StubWizard:
        def __init__(self, library_gateway, *, parent=None):
            instantiated.append(library_gateway)

        def exec(self):
            return 0

    monkeypatch.setattr(mod, "GearProfileWizard", _StubWizard)
    d._manage_gear_btn.click()
    assert instantiated == [lg]
    store.close()
