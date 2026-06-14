"""``DayBrowseDialog`` — lightweight thumbnail browser for a list of file paths.

A read-only modal that lets the user peek at one day's photos / videos before
deciding to include them in an event. Shared by:

* :class:`~mira.ui.pages.preingest_dialog.PreingestPlanConfirmDialog`
  (Slice C) — browses files from the SOURCE directory (SD card / external
  folder) before they are copied into the event.
* (future) :class:`~mira.ui.pages.manage_days_dialog.ManageDaysDialog`
  — same dialog over items already imported into the event.

Designed to take a plain list of :class:`pathlib.Path` so callers don't have
to construct any domain object. Images use Qt's :class:`QPixmap` loader;
video files render with a film-strip placeholder + filename so the dialog
can still be navigated without ffmpeg-backed thumbnail extraction (which
is heavyweight and out of scope for Slice C).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# Preview cap (Nelson 2026-06-06 eyeball #1): a day's worth of camera shots
# can be hundreds of files; decoding every JPEG to a QPixmap on the UI thread
# blocks the dialog open for seconds. Cap to a generous preview window —
# more than enough to spot a location — and signal the cap in the header.
_BROWSE_PREVIEW_LIMIT = 60
_THUMB_PX = 160
# Video extensions we show with a film-strip placeholder (no decode).
_VIDEO_EXTS = frozenset({
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts",
})


class DayBrowseDialog(QDialog):
    """Modal grid of file thumbnails. ``paths`` are shown in the order given;
    the dialog is closable via ``Close`` or Escape and emits no signals."""

    def __init__(
        self,
        paths: Iterable[Path],
        *,
        title: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or tr("Browse day"))
        self.setModal(True)
        self.resize(900, 640)
        self._all_paths: List[Path] = [Path(p) for p in paths]
        # Cap the preview (Nelson 2026-06-06 eyeball #1) — see _BROWSE_PREVIEW_LIMIT.
        self._paths: List[Path] = self._all_paths[:_BROWSE_PREVIEW_LIMIT]
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        total = len(self._all_paths)
        shown = len(self._paths)
        if total > shown:
            header = QLabel(
                tr("Showing first {shown} of {total} file(s) — preview cap "
                   "keeps the open snappy.")
                .replace("{shown}", str(shown))
                .replace("{total}", str(total))
            )
        else:
            header = QLabel(tr("{n} file(s)").replace("{n}", str(total)))
        header.setObjectName("PageHint")
        layout.addWidget(header)

        if not self._paths:
            empty = QLabel(tr("Nothing to browse — this day has no files on the source."))
            empty.setObjectName("PageHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            layout.addWidget(empty, stretch=1)
        else:
            self._list = QListWidget()
            self._list.setViewMode(QListView.ViewMode.IconMode)
            self._list.setIconSize(QSize(_THUMB_PX, _THUMB_PX))
            self._list.setGridSize(QSize(_THUMB_PX + 24, _THUMB_PX + 40))
            self._list.setResizeMode(QListView.ResizeMode.Adjust)
            self._list.setMovement(QListView.Movement.Static)
            self._list.setWordWrap(True)
            self._list.setUniformItemSizes(True)
            # Busy cursor while decoding thumbnails (
            # [[feedback_busy_cursor_on_lag]] — global UI rule on any lag).
            from PyQt6.QtCore import Qt as _Qt
            from PyQt6.QtGui import QCursor, QGuiApplication
            QGuiApplication.setOverrideCursor(QCursor(_Qt.CursorShape.WaitCursor))
            try:
                for path in self._paths:
                    item = QListWidgetItem(path.name)
                    item.setIcon(_thumb_for(path))
                    item.setToolTip(str(path))
                    self._list.addItem(item)
            finally:
                QGuiApplication.restoreOverrideCursor()
            layout.addWidget(self._list, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


def _thumb_for(path: Path) -> QIcon:
    """Return a thumbnail QIcon for one file.

    Delegates the decode to :func:`mira.ui.media.image_loader.load_pixmap`
    so HEIC / HEIF and RAW (via the camera's embedded preview JPEG) render
    alongside the native Qt formats. Videos and any unreadable file fall
    back to a standard Qt icon so the grid still renders.
    """
    suffix = path.suffix.lower()
    if suffix in _VIDEO_EXTS:
        style = _app_style()
        return (
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            if style else QIcon()
        )
    if path.exists():
        from mira.ui.media.image_loader import load_pixmap
        pix = load_pixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(
                _THUMB_PX, _THUMB_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return QIcon(scaled)
    style = _app_style()
    return (
        style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        if style else QIcon()
    )


def _app_style():
    """QApplication.style() guarded for headless / app-not-built test contexts."""
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        return app.style() if app else None
    except Exception:                                    # noqa: BLE001
        return None
