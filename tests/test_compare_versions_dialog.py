"""spec/89 §11.3 — CompareVersionsDialog: side-by-side compare for
the versions cluster sub-grid.

Pins the contract:
* One tile per version, each carrying its own intent state border.
* Click a tile's border → emits intent_toggle_requested + moves focus.
* spec/63 locked keymap inside the dialog: P / X / Space act on the
  focused tile; ← → step focus; Esc closes.
* Mouse click syncs focus before firing toggle so the keyboard verb
  the user types next hits the tile they just clicked.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent

from mira.ui.exported.compare_dialog import (
    CompareItem,
    CompareVersionsDialog,
    _CompareTile,
)


def _write_jpeg(path: Path) -> Path:
    img = QImage(64, 48, QImage.Format.Format_RGB888)
    img.fill(QColor(80, 120, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    assert img.save(str(path), "JPG", 90)
    return path


def _build_items(tmp_path: Path, n: int = 3) -> list[CompareItem]:
    items: list[CompareItem] = []
    for i in range(n):
        p = _write_jpeg(tmp_path / f"v{i}.jpg")
        items.append(CompareItem(
            item_id=f"Exported Media/v{i}.jpg",
            path=p,
            state="compare",
            title=f"V{i}",
        ))
    return items


def test_dialog_builds_one_tile_per_item(qapp, tmp_path):
    items = _build_items(tmp_path, 3)
    dlg = CompareVersionsDialog(items)
    assert len(dlg._tiles) == 3
    # First tile starts focused so a keyboard verb works immediately.
    assert dlg._focused_index == 0
    assert dlg._tiles[0]._focused is True
    assert dlg._tiles[1]._focused is False
    dlg.close()


def test_tile_border_click_emits_toggle_and_moves_focus(qapp, tmp_path):
    items = _build_items(tmp_path, 3)
    dlg = CompareVersionsDialog(items)
    toggled: list[str] = []
    dlg.intent_toggle_requested.connect(toggled.append)
    # Click the SECOND tile — should fire toggle for v1 AND set focus.
    dlg._tiles[1]._item   # touch attr so the test reads the wired tile
    dlg._tiles[1].mousePressEvent(_mouse_press_event_on(dlg._tiles[1]))
    assert toggled == ["Exported Media/v1.jpg"]
    assert dlg._focused_index == 1
    assert dlg._tiles[0]._focused is False
    assert dlg._tiles[1]._focused is True
    dlg.close()


def test_arrow_keys_step_focus_without_emitting(qapp, tmp_path):
    items = _build_items(tmp_path, 3)
    dlg = CompareVersionsDialog(items)
    emitted: list[tuple] = []
    dlg.intent_pick_requested.connect(lambda i: emitted.append(("p", i)))
    dlg.intent_skip_requested.connect(lambda i: emitted.append(("x", i)))
    dlg.intent_toggle_requested.connect(lambda i: emitted.append(("t", i)))
    # Right twice → focus on v2.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Right))
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Right))
    assert dlg._focused_index == 2
    # Right again at the edge — no wraparound, stays at the end.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Right))
    assert dlg._focused_index == 2
    # Left once → back to v1.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Left))
    assert dlg._focused_index == 1
    # No verb fired through any of that — focus moves are silent.
    assert emitted == []
    dlg.close()


def test_p_x_space_fire_on_focused_tile(qapp, tmp_path):
    items = _build_items(tmp_path, 3)
    dlg = CompareVersionsDialog(items)
    picked: list[str] = []
    skipped: list[str] = []
    toggled: list[str] = []
    dlg.intent_pick_requested.connect(picked.append)
    dlg.intent_skip_requested.connect(skipped.append)
    dlg.intent_toggle_requested.connect(toggled.append)
    # Focus tile 1.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Right))
    assert dlg._focused_index == 1
    # P → picks v1.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_P))
    assert picked == ["Exported Media/v1.jpg"]
    # X → skips v1.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_X))
    assert skipped == ["Exported Media/v1.jpg"]
    # Space → toggles v1.
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Space))
    assert toggled == ["Exported Media/v1.jpg"]
    dlg.close()


def test_esc_closes_the_dialog(qapp, tmp_path):
    items = _build_items(tmp_path, 2)
    dlg = CompareVersionsDialog(items)
    dlg.show()
    assert dlg.isVisible()
    dlg.keyPressEvent(_key_press(Qt.Key.Key_Escape))
    assert not dlg.isVisible()


def test_set_intent_state_repaints_only_target_tile(qapp, tmp_path):
    items = _build_items(tmp_path, 3)
    dlg = CompareVersionsDialog(items)
    dlg.set_intent_state("Exported Media/v1.jpg", "picked")
    assert dlg._tiles[1]._item.state == "picked"
    # Other tiles unchanged.
    assert dlg._tiles[0]._item.state == "compare"
    assert dlg._tiles[2]._item.state == "compare"
    dlg.close()


def test_compare_item_supports_develop_kwargs(qapp, tmp_path):
    """The dialog accepts CompareItems with develop kwargs and the
    tile loads through the develop pipeline (via ExportPreviewDialog
    ._load_pixmap_for). Smoke check — the pixmap may be None when
    the develop pipeline rejects the synthetic Adjustment, in which
    case the tile shows its "no preview" placeholder."""
    src = _write_jpeg(tmp_path / "src.jpg")
    adj = SimpleNamespace(
        look="original", creative_filter=None,
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0, style=None,
    )
    item = CompareItem(
        item_id="mira:src",
        path=src,
        state="compare",
        title="Mira",
        develop_for_preview=True,
        develop_adjustment=adj,
    )
    dlg = CompareVersionsDialog([item])
    assert len(dlg._tiles) == 1
    # The tile constructed without raising, which proves the develop
    # dispatch path runs end-to-end without import errors.
    dlg.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _key_press(key: Qt.Key) -> QKeyEvent:
    return QKeyEvent(
        QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _mouse_press_event_on(widget) -> "object":
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QMouseEvent
    pos = QPointF(widget.width() / 2.0, widget.height() / 2.0)
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos, pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
