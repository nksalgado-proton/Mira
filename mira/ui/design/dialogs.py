"""Reusable dialog templates — message & progress.

Two families:

    MessageDialog.info / success / warning / error / confirm  — modal
        confirmations and alerts. Body = intent-colored icon tile +
        title + 1-2 sentence message; footer = ghost/primary (or danger
        for destructive confirms).

    ProgressDialog                                            — long-
        running job feedback. Body = icon/spinner + title + current-
        item line + StageProgress bar + meta row; footer = Cancel
        (and optional final actions on completion).

Convenience module-level helpers:

    show_info / show_success / show_warning / show_error / confirm /
    confirm_destructive

These all return the dialog (or for confirm helpers, a bool) so callers
can wire them up in a single line.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design.buttons import (
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.design.progress import StageProgress


_INTENT = {
    "info":    {"color": "#5b8def", "glyph": "i"},
    "success": {"color": "#34d399", "glyph": "✓"},
    "warning": {"color": "#fbbf24", "glyph": "▲"},
    "error":   {"color": "#ef4444", "glyph": "✕"},
    "confirm": {"color": "#7c6cff", "glyph": "?"},
    "destructive": {"color": "#ef4444", "glyph": "🗑"},
}


def _icon_tile(intent: str) -> QLabel:
    cfg = _INTENT.get(intent, _INTENT["info"])
    tile = QLabel(cfg["glyph"])
    tile.setFixedSize(46, 46)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet(
        f"background: rgba(124,108,255,0.10); color: {cfg['color']};"
        f" border: 1px solid {cfg['color']}; border-radius: 14px;"
        f" font-size: 20px; font-weight: 800;"
    )
    return tile


def _divider() -> QFrame:
    d = QFrame()
    d.setStyleSheet("background: #262b38; max-height: 1px; min-height: 1px;")
    return d


def _make_danger_button(text: str) -> QPushButton:
    """Solid-red 'destructive primary' button — only used by the
    destructive-confirm template per design-system."""
    btn = QPushButton(text)
    btn.setObjectName("DangerPrimary")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton#DangerPrimary {"
        " background: #ef4444; color: #ffffff; border: none;"
        " border-radius: 11px; padding: 9px 18px; font-weight: 600;"
        "}"
        "QPushButton#DangerPrimary:hover { background: #d83838; }"
    )
    return btn


# ── MessageDialog ────────────────────────────────────────────────────


class MessageDialog(QDialog):
    """One-layout message dialog.

    Use the classmethods (``info`` / ``success`` / ``warning`` / ``error``
    / ``confirm`` / ``destructive``) instead of direct construction —
    they configure the right intent + button layout.
    """

    def __init__(
        self,
        *,
        intent: str,
        title: str,
        message: str,
        primary_text: str,
        secondary_text: Optional[str] = None,
        ghost_text: Optional[str] = None,
        primary_is_danger: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.resize(480, 260)
        self._result_kind = "cancel"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Body
        body = QWidget()
        b = QHBoxLayout(body)
        b.setContentsMargins(22, 22, 22, 22)
        b.setSpacing(16)
        b.addWidget(_icon_tile(intent), 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        text_col.addWidget(t)
        msg = QLabel(message)
        msg.setObjectName("Sub")
        msg.setWordWrap(True)
        msg.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        text_col.addWidget(msg)
        b.addLayout(text_col, 1)
        outer.addWidget(body, 1)
        outer.addWidget(_divider())

        # Footer
        footer_host = QWidget()
        footer = QHBoxLayout(footer_host)
        footer.setContentsMargins(22, 14, 22, 14)
        footer.setSpacing(8)
        # Optional ghost ('Cancel' for confirms, 'Discard' for warnings, etc.)
        if ghost_text:
            ghost = ghost_button(ghost_text)
            ghost.clicked.connect(self._on_cancel)
            footer.addWidget(ghost)
        # Optional secondary (e.g. 'Open folder' / 'View log')
        if secondary_text:
            sec = ghost_button(secondary_text)
            sec.clicked.connect(self._on_secondary)
            footer.addWidget(sec)
        footer.addStretch()
        primary = (
            _make_danger_button(primary_text)
            if primary_is_danger
            else primary_button(primary_text)
        )
        primary.clicked.connect(self._on_primary)
        primary.setDefault(True)
        primary.setAutoDefault(True)
        footer.addWidget(primary)
        outer.addWidget(footer_host)

    # ── result API ────────────────────────────────────────────────────

    def result_kind(self) -> str:
        """'primary' | 'secondary' | 'cancel'"""
        return self._result_kind

    def _on_primary(self) -> None:
        self._result_kind = "primary"
        self.accept()

    def _on_secondary(self) -> None:
        self._result_kind = "secondary"
        self.accept()

    def _on_cancel(self) -> None:
        self._result_kind = "cancel"
        self.reject()

    # ── factories ─────────────────────────────────────────────────────

    @classmethod
    def info(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        primary_text: str = "Got it",
    ) -> "MessageDialog":
        return cls(
            intent="info", title=title, message=message,
            primary_text=primary_text, parent=parent,
        )

    @classmethod
    def success(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        secondary_text: Optional[str] = None,
        primary_text: str = "Done",
    ) -> "MessageDialog":
        return cls(
            intent="success", title=title, message=message,
            primary_text=primary_text, secondary_text=secondary_text,
            parent=parent,
        )

    @classmethod
    def warning(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        primary_text: str = "Keep editing",
        ghost_text: str = "Discard",
    ) -> "MessageDialog":
        return cls(
            intent="warning", title=title, message=message,
            primary_text=primary_text, ghost_text=ghost_text, parent=parent,
        )

    @classmethod
    def error(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        secondary_text: Optional[str] = None,
        primary_text: str = "OK",
    ) -> "MessageDialog":
        return cls(
            intent="error", title=title, message=message,
            primary_text=primary_text, secondary_text=secondary_text,
            parent=parent,
        )

    @classmethod
    def confirm(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        primary_text: str = "Continue",
        ghost_text: str = "Cancel",
    ) -> "MessageDialog":
        return cls(
            intent="confirm", title=title, message=message,
            primary_text=primary_text, ghost_text=ghost_text, parent=parent,
        )

    @classmethod
    def destructive(
        cls, title: str, message: str, *,
        parent: Optional[QWidget] = None,
        primary_text: str = "Delete",
        ghost_text: str = "Cancel",
    ) -> "MessageDialog":
        return cls(
            intent="destructive", title=title, message=message,
            primary_text=primary_text, ghost_text=ghost_text,
            primary_is_danger=True, parent=parent,
        )


# ── shortcut helpers ─────────────────────────────────────────────────


def show_info(parent: QWidget | None, title: str, message: str) -> None:
    MessageDialog.info(title, message, parent=parent).exec()


def show_success(
    parent: QWidget | None, title: str, message: str,
    *, secondary_text: str | None = None,
) -> str:
    dlg = MessageDialog.success(
        title, message, parent=parent, secondary_text=secondary_text,
    )
    dlg.exec()
    return dlg.result_kind()


def show_error(parent: QWidget | None, title: str, message: str) -> None:
    MessageDialog.error(title, message, parent=parent).exec()


def confirm(
    parent: QWidget | None, title: str, message: str,
    *, primary_text: str = "Continue",
) -> bool:
    dlg = MessageDialog.confirm(
        title, message, parent=parent, primary_text=primary_text,
    )
    return (dlg.exec() == QDialog.DialogCode.Accepted
            and dlg.result_kind() == "primary")


def confirm_destructive(
    parent: QWidget | None, title: str, message: str,
    *, primary_text: str = "Delete",
) -> bool:
    dlg = MessageDialog.destructive(
        title, message, parent=parent, primary_text=primary_text,
    )
    return (dlg.exec() == QDialog.DialogCode.Accepted
            and dlg.result_kind() == "primary")


# ── ProgressDialog ───────────────────────────────────────────────────


class ProgressDialog(QDialog):
    """Long-running job feedback.

    Modes:
      - determinate:  setValue(0..100) on the bar; setCurrentItem(text).
      - indeterminate: setIndeterminate(True) animates a marquee bar.
      - multi-step:   pass steps=['Resolve…', 'Decode…', ...] in the
                      constructor; setStep(i) advances the step list.
      - complete:     setComplete(message, action_label=...) flips the
                      dialog to a success state with the action button.
    """

    cancel_requested = Callable[[], None]

    def __init__(
        self,
        *,
        title: str,
        steps: list[str] | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.resize(540, 280 if not steps else 360)
        self._steps = list(steps or [])
        self._step_idx = -1
        self._cancelled = False
        self._build_ui(title)
        # Indeterminate marquee — animate by pulsing the StageProgress
        # value when in indeterminate mode.
        self._marquee_timer = QTimer(self)
        self._marquee_timer.timeout.connect(self._tick_marquee)
        self._marquee_dir = 1

    def _build_ui(self, title: str) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QWidget()
        b = QVBoxLayout(body)
        b.setContentsMargins(22, 22, 22, 22)
        b.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        self._icon = _icon_tile("info")
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        head_col = QVBoxLayout()
        head_col.setSpacing(4)
        self._title = QLabel(title)
        self._title.setObjectName("CardTitle")
        head_col.addWidget(self._title)
        self._current_item = QLabel("")
        self._current_item.setObjectName("Sub")
        self._current_item.setWordWrap(True)
        head_col.addWidget(self._current_item)
        head.addLayout(head_col, 1)
        b.addLayout(head)

        self._bar = StageProgress()
        self._bar.setMinimumHeight(14)
        b.addWidget(self._bar)

        self._meta = QLabel("Working…")
        self._meta.setObjectName("Faint")
        b.addWidget(self._meta)

        if self._steps:
            self._steps_host = QVBoxLayout()
            self._steps_host.setSpacing(4)
            self._step_labels: list[QLabel] = []
            for s in self._steps:
                lbl = QLabel(f"○  {s}")
                lbl.setObjectName("Sub")
                self._step_labels.append(lbl)
                self._steps_host.addWidget(lbl)
            b.addLayout(self._steps_host)

        outer.addWidget(body, 1)
        outer.addWidget(_divider())

        # Footer
        footer_host = QWidget()
        footer = QHBoxLayout(footer_host)
        footer.setContentsMargins(22, 14, 22, 14)
        footer.setSpacing(8)
        footer.addStretch()
        self._action_btn = primary_button("Open folder")
        self._action_btn.hide()
        footer.addWidget(self._action_btn)
        self._cancel_btn = ghost_button("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        footer.addWidget(self._cancel_btn)
        outer.addWidget(footer_host)

    # ── public API ────────────────────────────────────────────────────

    def setCurrentItem(self, text: str) -> None:
        self._current_item.setText(text)

    def setValue(self, pct: int) -> None:
        """Determinate update. ``pct`` is 0..100."""
        self._bar.setValue(int(pct))
        self._bar.setState("prog" if pct < 100 else "done")
        if pct >= 100:
            self._meta.setText("100%")
        else:
            self._meta.setText(f"{int(pct)}%")

    def setMeta(self, text: str) -> None:
        self._meta.setText(text)

    def setIndeterminate(self, on: bool) -> None:
        if on:
            self._marquee_timer.start(60)
        else:
            self._marquee_timer.stop()

    def setStep(self, i: int) -> None:
        """Multi-step mode: advance the step list. Previous step gets
        a green check, current step gets an accent dot, pending stays
        empty."""
        if not self._steps:
            return
        self._step_idx = i
        for j, lbl in enumerate(self._step_labels):
            if j < i:
                lbl.setText(f"✓  {self._steps[j]}")
                lbl.setStyleSheet("color: #34d399;")
            elif j == i:
                lbl.setText(f"●  {self._steps[j]}  · now")
                lbl.setStyleSheet("color: #7c6cff; font-weight: 700;")
            else:
                lbl.setText(f"○  {self._steps[j]}")
                lbl.setStyleSheet("")

    def setComplete(
        self,
        title: str,
        *,
        message: str = "",
        action_label: str | None = "Open folder",
        on_action: Optional[Callable[[], None]] = None,
    ) -> None:
        """Flip the dialog to a completion state."""
        self._title.setText(title)
        self._current_item.setText(message)
        # Swap intent icon to success
        cfg = _INTENT["success"]
        self._icon.setText(cfg["glyph"])
        self._icon.setStyleSheet(
            f"background: rgba(52,211,153,0.10); color: {cfg['color']};"
            f" border: 1px solid {cfg['color']}; border-radius: 14px;"
            f" font-size: 20px; font-weight: 800;"
        )
        self._bar.setValue(100)
        self._bar.setState("done")
        self._meta.setText("Complete")
        self._marquee_timer.stop()
        if action_label:
            self._action_btn.setText(action_label)
            if on_action:
                # disconnect any previous binding to avoid double-fires
                try:
                    self._action_btn.clicked.disconnect()
                except TypeError:
                    pass
                self._action_btn.clicked.connect(on_action)
            self._action_btn.show()
        self._cancel_btn.setText("Close")

    def was_cancelled(self) -> bool:
        return self._cancelled

    # ── internals ─────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        self._cancelled = True
        self.reject()

    def _tick_marquee(self) -> None:
        v = self._bar.value() + (8 * self._marquee_dir)
        if v >= 100:
            v = 100
            self._marquee_dir = -1
        elif v <= 0:
            v = 0
            self._marquee_dir = 1
        self._bar.setValue(v)
        self._bar.setState(None)
