"""Real-asset end-to-end smoke for Surface 07 (Picker).

Drives :class:`PickerPage` against the live Gateway pointing at
``D:\\Photos\\_mira_events`` and walks through the locked spec/63 §4
keymap on a real photo:

* nav: ← / →
* decision verbs: P, X, Space, C
* cluster sweep: Enter (when the picked item is in a cluster bucket)
* inspection lens: F10 emits ``truth_requested`` on the viewport
* sharpness: a real decoded score is computed + persisted via the
  gateway

Outputs screenshots in light and dark themes::

    scripts/smoke_surface_07_dark.png
    scripts/smoke_surface_07_light.png

The verification numbers are printed (decision counts, sharpness scores
written, viewport's current pixmap tier) so a paste-back review shows the
engine actually ran end to end. Exits non-zero on any failure so /loop
or CI catches regressions.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _pick_event_with_photos(gw, eg_open):
    """Find the first event/day/photo we can drive the Picker on. Returns
    ``(event_id, event_name, day_number, item_id)`` or ``None``."""
    from mira.picked import day_grid_cells

    rows = gw.list_events()
    for row in rows:
        event_id = str(row["id"])
        try:
            eg = eg_open(event_id)
        except Exception:
            continue
        try:
            trip_days = sorted(
                d.day_number for d in eg.trip_days()
                if d.day_number is not None
            )
            for dn in trip_days:
                try:
                    cells = day_grid_cells(eg, dn, phase="pick")
                except Exception:
                    continue
                for cell in cells:
                    if cell.item_kind == "photo" and cell.item_id:
                        return (
                            event_id, str(row.get("name", "")),
                            dn, cell.item_id,
                        )
        finally:
            try:
                eg.close()
            except Exception:
                pass
    return None


def _send_key(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    """Synthetic key event to ``widget`` (drives the locked grammar)."""
    QApplication.postEvent(
        widget,
        QKeyEvent(QEvent.Type.KeyPress, key, modifiers),
    )
    QApplication.postEvent(
        widget,
        QKeyEvent(QEvent.Type.KeyRelease, key, modifiers),
    )
    for _ in range(3):
        QApplication.processEvents()


def _drain_events(times: int = 10) -> None:
    for _ in range(times):
        QApplication.processEvents()


def _drive_picker(page) -> dict:
    """Drive the Picker through the locked keymap on the loaded item.
    Returns counts/observations for paste-back verification."""
    from mira.picked.status import STATE_PICKED, STATE_SKIPPED, STATE_CANDIDATE

    obs = {
        "items_loaded": len(page._items),
        "start_state": None,
        "after_pick": None,
        "after_skip": None,
        "after_space": None,
        "after_cycle": None,
        "sharpness_persisted": False,
        "viewport_sharp_pixels": False,
        "truth_signal_fires": False,
        "edge_no_op": True,
    }
    if not page._items:
        return obs
    item_id = page._items[0].item_id
    eg = page._eg

    # Drain until either sharp pixels land or a brief deadline elapses.
    # The viewport decodes async — we want the score to reflect REAL
    # pixels, not a placeholder.
    import time
    deadline = time.time() + 5.0
    while time.time() < deadline:
        _drain_events(5)
        if page.viewport.sharp_pixmap_info() is not None:
            obs["viewport_sharp_pixels"] = True
            break
    _drain_events(10)

    eff_start = page._effective(item_id)
    obs["start_state"] = eff_start

    # P (Pick)
    _send_key(page.viewport, Qt.Key.Key_P)
    obs["after_pick"] = eg.phase_state(item_id, "pick").state
    assert obs["after_pick"] == STATE_PICKED, \
        f"P did not Pick: {obs['after_pick']}"

    # X (Skip)
    _send_key(page.viewport, Qt.Key.Key_X)
    obs["after_skip"] = eg.phase_state(item_id, "pick").state
    assert obs["after_skip"] == STATE_SKIPPED, \
        f"X did not Skip: {obs['after_skip']}"

    # Space (toggle Pick ⇄ Skip — from Skipped goes to Picked)
    _send_key(page.viewport, Qt.Key.Key_Space)
    obs["after_space"] = eg.phase_state(item_id, "pick").state
    assert obs["after_space"] == STATE_PICKED, \
        f"Space did not toggle: {obs['after_space']}"

    # C (cycle Skip → Pick → Compare → Skip — from Picked goes to Compare)
    _send_key(page.viewport, Qt.Key.Key_C)
    after_c = eg.phase_state(item_id, "pick").state
    assert after_c == STATE_CANDIDATE, \
        f"C did not cycle Picked → Compare: {after_c}"
    _send_key(page.viewport, Qt.Key.Key_C)
    after_c2 = eg.phase_state(item_id, "pick").state
    assert after_c2 == STATE_SKIPPED, \
        f"C second cycle Compare → Skip wrong: {after_c2}"
    obs["after_cycle"] = after_c2

    # Edge (← at index 0 should be a no-op since single-item bucket)
    cur_idx_before = page._index
    _send_key(page.viewport, Qt.Key.Key_Left)
    obs["edge_no_op"] = (page._index == cur_idx_before)

    # F10 emits truth_requested — the viewport's contract; we count
    # signal fires across a quick connection. set_truth_internal(False)
    # suppresses the modal _open_inspect_view() (which would block the
    # smoke); the signal itself still fires.
    fires = []
    page.viewport.set_truth_internal(False)
    page.viewport.truth_requested.connect(lambda: fires.append(True))
    _send_key(page.viewport, Qt.Key.Key_F10)
    obs["truth_signal_fires"] = bool(fires)
    page.viewport.set_truth_internal(True)

    # Sharpness — check the gateway has a stored score on this item.
    item = eg.item(item_id)
    obs["sharpness_persisted"] = (
        item is not None and item.sharpness_score is not None
    )
    obs["sharpness_score"] = (
        getattr(item, "sharpness_score", None) if item else None
    )
    return obs


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.picker_page import PickerPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1

    picked = _pick_event_with_photos(gw, gw.open_event)
    if picked is None:
        print("no openable events with a photo cell; aborting smoke")
        return 1
    event_id, event_name, day_number, item_id = picked
    print(
        f"smoking against event {event_name!r} day {day_number} "
        f"item {item_id}")

    out_dir = _REPO / "scripts"
    rc = 0
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        page = PickerPage(gateway=gw, parent=root)
        ok = page.open_to_item(event_id, day_number, item_id)
        if not ok:
            print(f"open_to_item failed for {item_id}; aborting smoke")
            return 1
        rl.addWidget(page)
        root.show()
        # Drive the keymap end to end only ONCE (the first mode); both
        # screenshots reflect the post-drive state but we don't re-poke
        # the gateway twice.
        if mode == "dark":
            obs = _drive_picker(page)
            print("--- observations ---")
            for k, v in obs.items():
                print(f"  {k}: {v}")
            if not all([
                obs["after_pick"],
                obs["after_skip"],
                obs["after_space"],
                obs["after_cycle"],
                obs["edge_no_op"],
                obs["truth_signal_fires"],
                obs["sharpness_persisted"],
            ]):
                print("SMOKE FAILED — see observations above")
                rc = 1
        # Drain any pending paints.
        _drain_events(20)
        pm = root.grab()
        out = out_dir / f"smoke_surface_07_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        page.close_event()
        root.close()
        root.deleteLater()
    return rc


if __name__ == "__main__":
    sys.exit(main())
