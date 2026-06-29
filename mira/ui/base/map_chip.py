"""Inline chip widget for the day-row and event-header map slot (spec/155).

Two states driven by the ``attached`` dynamic property:

* **empty** — dashed-border chip with a ``+`` icon and ``Map`` label.
* **attached** — solid-border chip with a small thumbnail of the
  attached map on the left and ``Map`` label on the right.

Style lives in QSS (the canonical role is ``#MapChip``); a dynamic
property ``attached="true|false"`` lets the redesign stylesheet branch
the two states with ``polish/unpolish``. Inline `setStyleSheet` is
deliberately avoided (CLAUDE.md QSS rule + spec/92).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)


def tr(s: str) -> str:
    return QApplication.translate("MapChip", s)


_THUMB_SIZE = QSize(36, 22)


class MapChip(QPushButton):
    """Clickable chip carrying the map state for one slot.

    The chip *signals* (``clicked``) — it does NOT own the dialog or
    talk to the gateway. The page wires ``clicked`` to whatever opens
    :class:`mira.ui.base.map_attach_dialog.MapAttachDialog`. After the
    dialog emits ``mapChanged`` the page calls :meth:`set_map_path`
    with the new state and the chip re-paints in place.
    """

    def __init__(
        self,
        *,
        event_root: Path,
        parent=None,
        empty_label: Optional[str] = None,
        attached_label: Optional[str] = None,
        tooltip_empty: Optional[str] = None,
        tooltip_attached: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self._event_root = Path(event_root)
        self._empty_label = empty_label or tr("Map")
        self._attached_label = attached_label or tr("Map")
        self._tooltip_empty = tooltip_empty or tr("Attach a map for this day.")
        self._tooltip_attached = tooltip_attached or tr(
            "Replace or remove the attached map.")
        self.setObjectName("MapChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # Single inner row: thumb (optional) + text label.
        self._thumb = QLabel(self)
        self._thumb.setObjectName("MapChipThumb")
        self._thumb.setFixedSize(_THUMB_SIZE)
        self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text = QLabel(self._empty_label, self)
        self._text.setObjectName("MapChipText")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 8, 2)
        layout.setSpacing(6)
        layout.addWidget(self._thumb)
        layout.addWidget(self._text)
        self._map_rel: Optional[str] = None
        self._apply_state()

    # ── public API ────────────────────────────────────────────────

    def set_map_path(self, rel: Optional[str]) -> None:
        """Set the chip's slot path (or None for empty).

        ``rel`` is relative to ``event_root`` (e.g. ``Maps/day-02.jpg``).
        Triggers a re-render.
        """
        if rel == self._map_rel:
            return
        self._map_rel = rel
        self._apply_state()

    def map_path(self) -> Optional[str]:
        return self._map_rel

    # ── internals ─────────────────────────────────────────────────

    def _apply_state(self) -> None:
        attached = self._map_rel is not None
        self.setProperty("attached", "true" if attached else "false")
        if attached:
            pix = self._load_thumb_pixmap(self._map_rel)  # type: ignore[arg-type]
            if not pix.isNull():
                self._thumb.setPixmap(pix.scaled(
                    _THUMB_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ))
                self._thumb.setText("")
            else:
                # Fallback when the file is missing or unreadable —
                # still signal "attached" so the user can re-pick.
                self._thumb.setPixmap(QPixmap())
                self._thumb.setText("?")
            self._text.setText(self._attached_label)
            self.setToolTip(self._tooltip_attached)
        else:
            self._thumb.setPixmap(QPixmap())
            self._thumb.setText("+")
            self._text.setText(self._empty_label)
            self.setToolTip(self._tooltip_empty)
        # Re-polish so the QSS dynamic-property selector picks up the new state.
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        style.unpolish(self._thumb)
        style.polish(self._thumb)

    def _load_thumb_pixmap(self, rel: str) -> QPixmap:
        """Resolve the chip's thumbnail source.

        For JPEG / PNG maps the source IS the image. For MP4 maps the
        source is the pre-extracted first-frame sidecar that
        ``EventGateway.attach_*_map`` wrote alongside the video — this
        keeps chip paints cheap (no ffmpeg invocation per repaint).
        """
        from core.path_builder import (
            MAP_VIDEO_THUMB_SUFFIX,
            is_video_map_path,
        )
        abs_path = self._event_root / rel
        if is_video_map_path(rel):
            sidecar = abs_path.with_suffix(
                abs_path.suffix + MAP_VIDEO_THUMB_SUFFIX)
            return QPixmap(str(sidecar))
        return QPixmap(str(abs_path))
