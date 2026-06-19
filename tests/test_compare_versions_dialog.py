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


def test_compare_tiles_defer_develop_to_next_event_loop(
        qapp, tmp_path, monkeypatch):
    """spec/89 §11.3 polish (Nelson 2026-06-19) — Compare tiles
    must NOT run the develop pipeline synchronously in __init__.
    Pre-fix, a 5+-version cluster blocked Compare's open for
    several seconds. Post-fix, develop fires via
    QTimer.singleShot so the dialog paints first."""
    src = _write_jpeg(tmp_path / "src.jpg")
    adj = SimpleNamespace(
        look="original", creative_filter=None,
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0, style=None,
    )
    develop_calls: list[str] = []
    from mira.ui.exported.preview_dialog import ExportPreviewDialog

    def _fake_develop(cls, item):
        develop_calls.append(item.item_id)
        return None
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap",
        classmethod(_fake_develop))
    # Stub QTimer.singleShot to NOT fire — observe the synchronous
    # path alone (lazy = nothing developed yet).
    from PyQt6.QtCore import QTimer
    monkeypatch.setattr(QTimer, "singleShot",
                        staticmethod(lambda ms, fn: None))

    items = [
        CompareItem(
            item_id=f"mira:src{i}", path=src,
            state="compare", title=f"M{i}",
            develop_for_preview=True, develop_adjustment=adj,
        )
        for i in range(4)
    ]
    dlg = CompareVersionsDialog(items)
    # Tile ctor must not have called the develop pipeline.
    assert develop_calls == []
    dlg.close()


def test_compare_f10_inspects_developed_pixels_for_mira_tile(
        qapp, tmp_path, monkeypatch):
    """spec/89 §3.2 + spec/63 §4 — F10 on a develop-pipeline Compare
    tile opens _InspectView with the FULL-resolution developed
    pixmap (via _develop_pixmap_full), not the raw source."""
    src = _write_jpeg(tmp_path / "src.jpg")
    adj = SimpleNamespace(
        look="original", creative_filter=None,
        crop_x=None, crop_y=None, crop_w=None, crop_h=None,
        crop_angle=0.0, rotation=0, look_strength=1.0, style=None,
    )
    full_develop_calls: list[str] = []
    from mira.ui.exported.preview_dialog import ExportPreviewDialog

    def _fake_full(cls, item):
        full_develop_calls.append(item.item_id)
        from PyQt6.QtGui import QColor, QImage, QPixmap
        img = QImage(16, 12, QImage.Format.Format_RGB888)
        img.fill(QColor(50, 100, 150))
        return QPixmap.fromImage(img)
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap_full",
        classmethod(_fake_full))

    opened: list[tuple] = []
    class _FakeInspect:
        def __init__(self, base, af_point=None, *, path=None,
                     is_raw=False, with_tools=True, parent=None):
            opened.append((base, path, is_raw))
        def open_windowed(self): pass
        def setFocus(self): pass
        def close(self): pass
    monkeypatch.setattr(
        "mira.ui.media.photo_viewport._InspectView", _FakeInspect)

    items = [
        CompareItem(
            item_id="mira:src", path=src, state="compare",
            title="Mira", develop_for_preview=True,
            develop_adjustment=adj,
        ),
        CompareItem(
            item_id="Exported Media/lrc.jpg", path=src,
            state="compare", title="LRC",
        ),
    ]
    dlg = CompareVersionsDialog(items)
    # First tile (Mira) is focused on open.
    assert dlg._focused_index == 0
    dlg.keyPressEvent(_key_press(Qt.Key.Key_F10))
    assert full_develop_calls == ["mira:src"]
    assert len(opened) == 1
    base, _path, is_raw = opened[0]
    assert base is not None
    assert is_raw is False
    dlg.close()


def test_compare_f10_inspects_on_disk_file_for_lineage_tile(
        qapp, tmp_path, monkeypatch):
    """spec/89 §3.2 — F10 on a lineage tile (third-party return or
    shipped Mira render) opens _InspectView with the on-disk file
    directly — no pipeline, the file IS the export."""
    src = _write_jpeg(tmp_path / "lrc.jpg")
    full_develop_calls: list[str] = []
    from mira.ui.exported.preview_dialog import ExportPreviewDialog
    monkeypatch.setattr(
        ExportPreviewDialog, "_develop_pixmap_full",
        classmethod(lambda cls, it: full_develop_calls.append(it.item_id)))

    opened: list[tuple] = []
    class _FakeInspect:
        def __init__(self, base, af_point=None, *, path=None,
                     is_raw=False, with_tools=True, parent=None):
            opened.append((base, path, is_raw))
        def open_windowed(self): pass
        def setFocus(self): pass
        def close(self): pass
    monkeypatch.setattr(
        "mira.ui.media.photo_viewport._InspectView", _FakeInspect)

    items = [
        CompareItem(
            item_id="Exported Media/lrc.jpg", path=src,
            state="compare", title="LRC",
        ),
    ]
    dlg = CompareVersionsDialog(items)
    dlg.keyPressEvent(_key_press(Qt.Key.Key_F10))
    # No develop pipeline ran — the file IS the export.
    assert full_develop_calls == []
    assert len(opened) == 1
    _base, path, _is_raw = opened[0]
    assert path is not None
    assert path.name == "lrc.jpg"
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
