"""Bracket Help panel (spec/110).

When the user opens a focus or exposure bracket cluster in the Picker
and presses the inviting Help button, this panel surfaces spec/108's
round-trip contract **inline with this bracket's concrete stem and
paths** — so the user doesn't have to translate a generic doc to their
file. Two paths, kind-aware:

* **Focus** brackets — external-only (Mira has no built-in focus
  stacker, spec/108 §4). Tells the user where to drop the merged file
  and *exactly* what filename prefix the spec/57 §3.2 matcher will
  accept. Actions: Copy name prefix · Open ``Picked Media/`` (reveal
  in Explorer) · Full guide (spec/108 doc) · "then run Scan for returns
  in Edit."
* **Exposure** brackets — same external path PLUS the in-app Mertens
  lane (spec/109). The Merge-in-Mira button is present + enabled when
  the host wires a callback; absent that wiring (legacy callers, the
  pre-spec/109 disabled-with-tooltip mode) the button stays disabled
  with a "lands in Edit" note.

No inline styling — visual treatment rides QSS roles. Strings via
:func:`tr`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
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

from core.bracket_help import BracketHelpContext
from mira.ui.i18n import tr


class BracketHelpPanel(QDialog):
    """Compact kind-aware help dialog for one bracket cluster.

    ``ctx`` is the resolved :class:`BracketHelpContext`. ``on_merge``
    (exposure-only) is the spec/109 in-app-merge callback the host
    binds to its batch-queue entry point; ``None`` leaves the merge
    button disabled with a "lands in Edit" tooltip. ``on_open_folder``
    reveals ``Picked Media/`` in the OS file browser when set; ``None``
    leaves the button disabled. ``on_full_guide`` opens the spec/108
    round-trip contract doc; ``None`` leaves it disabled (host wires
    the actual doc opener).
    """

    def __init__(
        self,
        ctx: BracketHelpContext,
        *,
        on_merge: Optional[Callable[[], None]] = None,
        on_open_folder: Optional[Callable[[], None]] = None,
        on_full_guide: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._on_merge = on_merge
        self._on_open_folder = on_open_folder
        self._on_full_guide = on_full_guide
        self.setObjectName("BracketHelpPanel")
        self.setWindowTitle(self._window_title())
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_header())
        body = self._build_body()
        body.setWordWrap(True)
        body.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(body)
        layout.addWidget(self._build_prefix_row())
        layout.addWidget(self._build_actions_row())

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    # ── tests + host access ───────────────────────────────────────

    def name_prefix(self) -> str:
        """The link-stem prefix the user copies to paste into their
        external tool's "Save as" field (spec/110 §3 focus action).
        Tests assert this starts with a real bracket-member stem."""
        return self._ctx.name_prefix

    @property
    def merge_button(self) -> Optional[QPushButton]:
        """The Merge-in-Mira (spec/109) action button — present iff
        ``ctx.kind == 'exposure_bracket'``, otherwise ``None``. Tests
        assert the exposure panel exposes this; focus does not."""
        return getattr(self, "_merge_btn", None)

    @property
    def copy_prefix_button(self) -> QPushButton:
        return self._copy_btn

    @property
    def open_folder_button(self) -> QPushButton:
        return self._open_folder_btn

    @property
    def full_guide_button(self) -> QPushButton:
        return self._full_guide_btn

    # ── builders ──────────────────────────────────────────────────

    def _window_title(self) -> str:
        if self._ctx.kind == "exposure_bracket":
            return tr("Exposure bracket — how to merge")
        return tr("Focus bracket — how to stack")

    def _build_header(self) -> QLabel:
        if self._ctx.kind == "exposure_bracket":
            text = tr("Exposure bracket · {n} frames").replace(
                "{n}", str(self._ctx.member_count))
        else:
            text = tr("Focus bracket · {n} frames").replace(
                "{n}", str(self._ctx.member_count))
        lbl = QLabel(text)
        lbl.setObjectName("BracketHelpHeader")
        return lbl

    def _build_body(self) -> QLabel:
        if self._ctx.kind == "exposure_bracket":
            text = tr(
                "Two paths for getting a single tonemapped image out of "
                "this bracket:\n\n"
                "• Merge in Mira — Mira fuses the frames with Mertens "
                "exposure fusion, badges the result as a Mira-produced "
                "stack output, and drops it into the day beside its "
                "siblings.\n\n"
                "• Process externally — use your HDR tool of choice; "
                "Mira adopts the result back as the bracket's master "
                "(badged ext) via the round-trip contract below.")
        else:
            text = tr(
                "Mira doesn't merge focus stacks. Use the external tool "
                "of your choice (Helicon, Zerene, …) and return the "
                "result by the spec/108 round-trip contract: save the "
                "stacked file into the Picked Media/ folder (the root, "
                "not a subfolder) with a filename that starts with the "
                "prefix below.\n\n"
                "Then run Scan for returns in Edit — Mira adopts the "
                "file as this bracket's master and badges it ext.")
        lbl = QLabel(text)
        lbl.setObjectName("BracketHelpBody")
        return lbl

    def _build_prefix_row(self) -> QFrame:
        """The concrete drop instructions for THIS bracket — Picked
        Media/ path + the anchor member's link stem. The prefix is
        selectable for the user who'd rather copy it manually than
        click Copy."""
        frame = QFrame(self)
        frame.setObjectName("BracketHelpPrefix")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)

        v.addWidget(QLabel(tr("Save into:")))
        path_lbl = QLabel(str(self._ctx.picked_media_dir))
        path_lbl.setObjectName("BracketHelpPath")
        path_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        path_lbl.setWordWrap(True)
        v.addWidget(path_lbl)

        v.addWidget(QLabel(tr("Filename starts with:")))
        prefix_lbl = QLabel(self._ctx.name_prefix)
        prefix_lbl.setObjectName("BracketHelpPrefixStem")
        prefix_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(prefix_lbl)
        return frame

    def _build_actions_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._copy_btn = QPushButton(tr("Copy name prefix"))
        self._copy_btn.clicked.connect(self._copy_prefix)
        layout.addWidget(self._copy_btn)

        self._open_folder_btn = QPushButton(tr("Open Picked Media/"))
        self._open_folder_btn.setEnabled(self._on_open_folder is not None)
        if self._on_open_folder is not None:
            self._open_folder_btn.clicked.connect(self._on_open_folder)
        layout.addWidget(self._open_folder_btn)

        self._full_guide_btn = QPushButton(tr("Full guide"))
        self._full_guide_btn.setEnabled(self._on_full_guide is not None)
        if self._on_full_guide is not None:
            self._full_guide_btn.clicked.connect(self._on_full_guide)
        layout.addWidget(self._full_guide_btn)

        if self._ctx.kind == "exposure_bracket":
            # spec/109 §5 — the in-app Mertens lane. Present on every
            # exposure-bracket panel; the host wires the callback when
            # it can dispatch (currently from the Edit-entry returns
            # box; the Picker hook ships disabled-with-tooltip per the
            # spec/110 §6 fallback).
            self._merge_btn = QPushButton(tr("Merge in Mira (Mertens)"))
            self._merge_btn.setObjectName("Primary")
            if self._on_merge is None:
                self._merge_btn.setEnabled(False)
                self._merge_btn.setToolTip(tr(
                    "Lands in Edit — open Edit and use the Merge "
                    "exposure brackets in Mira action there."))
            else:
                self._merge_btn.clicked.connect(self._invoke_merge)
            layout.addWidget(self._merge_btn)

        layout.addStretch(1)
        return row

    # ── action handlers ───────────────────────────────────────────

    def _copy_prefix(self) -> None:
        """Copy the link-stem prefix to the system clipboard."""
        clip = QGuiApplication.clipboard()
        if clip is not None:
            clip.setText(self._ctx.name_prefix)

    def _invoke_merge(self) -> None:
        """Fire the host's merge callback and close — the host owns the
        batch-queue side effect; the panel just brokers the click."""
        if self._on_merge is not None:
            self._on_merge()
        self.accept()


__all__ = ["BracketHelpPanel"]
