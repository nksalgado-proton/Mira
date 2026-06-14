"""StableMediaStage — vertical container with a fixed-height control zone
sized for the richest case (video).

Picker and Editor are single surfaces that show either a photo or a video.
Video items need an extra transport bar (Picker) or timeline + tools row
(Editor) below the canvas. Without intervention, stepping from a photo to
a video would push the canvas up — disorienting and breaks the steady-stage
feel the redesign mandates (design-system §3, "Stable media stage — REQUIRED").

The fix: the control zone has a fixed ``minimumHeight`` equal to the video
layout's total height. In photo mode, the video-only widgets are hidden but
their height stays reserved (empty padding). Result: the canvas never moves;
the user sees more or fewer controls, the stage stays put.

This module provides the scaffold — surfaces compose it like:

    stage = StableMediaStage(control_zone_height=84)
    stage.setStage(picker_canvas)
    stage.setPhotoControls(QWidget())            # may be empty/spacer
    stage.setVideoControls(transport_bar)        # the richer set
    stage.setMode("photo")                        # default — video bar hides
"""
from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class StableMediaStage(QWidget):
    """Vertical layout: stage on top + fixed-height control zone below.

    The control zone is a QStackedWidget so photo / video controls swap by
    index without the parent layout recomputing heights. Its minimum height
    is locked from the moment the video controls are set, so swapping to
    photo mode never shrinks the zone.
    """

    PHOTO = "photo"
    VIDEO = "video"

    def __init__(
        self,
        *,
        control_zone_height: int = 84,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._control_zone_height = control_zone_height
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self._stage_holder = QWidget(self)
        self._stage_holder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        stage_layout = QVBoxLayout(self._stage_holder)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self._stage: QWidget | None = None
        v.addWidget(self._stage_holder, 1)
        self._controls = QStackedWidget(self)
        self._controls.setFixedHeight(control_zone_height)
        v.addWidget(self._controls)

    def setStage(self, widget: QWidget) -> None:
        """Install the photo/video canvas widget. Replaces any previous."""
        if self._stage is not None:
            self._stage.setParent(None)
            self._stage.deleteLater()
        self._stage = widget
        self._stage_holder.layout().addWidget(widget)

    def setPhotoControls(self, widget: QWidget) -> None:
        """Install the photo-mode control row (often empty/spacer to reserve
        the height in step with the video layout)."""
        widget.setMinimumHeight(self._control_zone_height)
        if self._controls.count() == 0:
            self._controls.addWidget(widget)
        else:
            old = self._controls.widget(0)
            self._controls.removeWidget(old)
            old.deleteLater()
            self._controls.insertWidget(0, widget)

    def setVideoControls(self, widget: QWidget) -> None:
        """Install the video-mode transport + (optional) timeline row.

        The widget's height is taken as the new floor for the control zone —
        if it exceeds the constructor's ``control_zone_height``, the zone
        grows to match, ensuring photo mode still reserves enough room.
        """
        widget.setMinimumHeight(self._control_zone_height)
        if self._controls.count() < 2:
            self._controls.addWidget(widget)
        else:
            old = self._controls.widget(1)
            self._controls.removeWidget(old)
            old.deleteLater()
            self._controls.insertWidget(1, widget)
        # Bump the zone floor if the new widget asks for more room
        if widget.sizeHint().height() > self._control_zone_height:
            self._control_zone_height = widget.sizeHint().height()
            self._controls.setFixedHeight(self._control_zone_height)

    def setMode(self, mode: str) -> None:
        if mode == self.PHOTO:
            self._controls.setCurrentIndex(0)
        elif mode == self.VIDEO:
            self._controls.setCurrentIndex(1)
        else:
            raise ValueError(
                f"mode must be {self.PHOTO!r} or {self.VIDEO!r}, got {mode!r}"
            )

    def controlZoneHeight(self) -> int:
        return self._control_zone_height
