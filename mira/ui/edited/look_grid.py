"""LookGridDialog — the spec/54 §4.2 "grid moment".

A 2×2 grid of THIS photo rendered under every Look — Original /
Natural / Brighter / Deeper — every tile a clickable choice (Original
included: "leave it as shot" is a first-class pick, spec/54 §3.2).
The user enters, clicks the winner, leaves.

Renders happen on the surface's downsampled preview array via the
same engine the live canvas uses (``look_params_from_natural`` +
``apply_params``), so the tiles ARE the four outcomes, not
approximations. Four LUT-based renders of a ≤1280 px preview take
tens of milliseconds — the dialog opens instantly, no async needed.

Keyboard: 1–4 pick a tile directly; Esc cancels.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor, QIcon, QImage, QKeyEvent, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.photo_auto import available_looks, look_params_from_natural
from core.photo_render import Params, apply_params
from mira.ui.i18n import tr

# Tile width inside the grid. Two tiles + margins ≈ 1010 px dialog —
# comfortable on the 1366×768 floor the UI standards target.
TILE_WIDTH = 480


def look_display_name(key: str) -> str:
    """User-visible Look names (spec/54 §2, locked 2026-06-10).
    Internal keys are stable; display text goes through tr()."""
    return {
        "original": tr("Original"),
        "natural": tr("Natural"),
        "brighter": tr("Brighter"),
        "deeper": tr("Deeper"),
    }.get(key, key)


def filter_display_name(key: str) -> str:
    """User-visible creative-filter names (spec/55). PROVISIONAL —
    the naming session (EN + pt-BR) is the last vocabulary lock of
    the redesign; until then these are the working keys, title-cased
    through tr()."""
    return {
        "vivid": tr("Vivid"),
        "bw": tr("B&W"),
        "sepia": tr("Sepia"),
        "faded": tr("Faded"),
        "golden": tr("Golden"),
        "cinema": tr("Cinema"),
        "bleach": tr("Bleach"),
        "dramatic": tr("Dramatic"),
        "crisp": tr("Crisp"),
    }.get(key, key)


def _tile_pixmap(arr: np.ndarray, width: int = TILE_WIDTH) -> QPixmap:
    h, w = arr.shape[:2]
    if w > width:
        new_h = max(1, int(round(h * width / w)))
        arr = np.asarray(Image.fromarray(arr).resize(
            (width, new_h), Image.Resampling.LANCZOS))
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    qimg = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    # Copy — the numpy buffer is function-local.
    return QPixmap.fromImage(qimg.copy())


class LookGridDialog(QDialog):
    """2×2 chooser over the loaded photo. Use :meth:`choose`."""

    def __init__(
        self,
        preview: np.ndarray,
        natural: Params,
        current_look: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("LookGridDialog")
        self.setWindowTitle(tr("Choose a Look"))
        self.setModal(True)
        self._chosen: Optional[str] = None
        self._keys = list(available_looks())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)
        hint = QLabel(tr(
            "The same photo under each Look — click the one you like. "
            "(1–4 pick directly, Esc keeps the current choice.)"))
        hint.setObjectName("LookGridHint")
        outer.addWidget(hint)

        grid = QGridLayout()
        grid.setSpacing(10)
        outer.addLayout(grid)

        for i, key in enumerate(self._keys):
            params = look_params_from_natural(natural, key)
            rendered = preview if params.is_identity \
                else apply_params(preview, params)
            btn = QPushButton()
            btn.setObjectName("LookGridTile")
            btn.setCheckable(True)
            btn.setChecked(key == current_look)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            pix = _tile_pixmap(rendered)
            btn.setIcon(QIcon(pix))
            btn.setIconSize(pix.size())
            btn.setToolTip(tr(
                "Apply the {name} look to this photo.")
                .replace("{name}", look_display_name(key)))
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            caption = QLabel(
                f"{i + 1} — {look_display_name(key)}")
            caption.setObjectName("LookGridCaption")
            caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(btn)
            cell.addWidget(caption)
            grid.addLayout(cell, i // 2, i % 2)

    # ── Interaction ──────────────────────────────────────────────

    def _pick(self, key: str) -> None:
        self._chosen = key
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_4:
            idx = key - Qt.Key.Key_1
            if idx < len(self._keys):
                self._pick(self._keys[idx])
                return
        super().keyPressEvent(event)

    # ── Entry point ──────────────────────────────────────────────

    @staticmethod
    def choose(
        preview: np.ndarray,
        natural: Params,
        current_look: str,
        parent: Optional[QWidget] = None,
    ) -> Optional[str]:
        """Open the grid moment; return the picked Look key, or
        ``None`` if the user backed out (Esc / close)."""
        dlg = LookGridDialog(preview, natural, current_look, parent)
        dlg.exec()
        return dlg._chosen
