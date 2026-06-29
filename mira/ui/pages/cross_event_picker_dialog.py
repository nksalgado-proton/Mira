"""The cross-event Picker (spec/81 §4 / spec/61 §2 — weed-out / pick-in).

The cross-event sibling of :mod:`mira.ui.shared.cut_session_page`. Drives a
:class:`CrossEventCutSession` in weed-out (start all-in, skip rejects) or
pick-in (start all-out, pick keepers) mode. Each candidate is a packed
key — a (event_uuid, item_id) pair the resolver returned. The user
flips Pick/Skip per candidate; the budget zone updates live; commit
freezes the membership into the anchor event's ``event.db``.

The Picker doesn't open the anchor gateway itself — the host owns that
lifecycle and supplies a ``commit_callback(session)`` so the page stays
gateway-agnostic. Tests use a stub callback.
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core import cut_budget, cut_names
from mira.shared.cross_event_cut_session import (
    CrossEventCutSession,
    CrossEventSessionFile,
)
from mira.shared.cut_draft import PIN_PICK_IN, PIN_WEED_OUT
from mira.ui.design import ghost_button, primary_button
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# One row per candidate
# --------------------------------------------------------------------------- #


class _CandidateRow(QFrame):
    """Per-candidate cell: Pick/Skip checkbox + identity + capture facts.

    Signals:
        toggled(str, bool)   packed key + new picked state
    """

    toggled = pyqtSignal(str, bool)

    def __init__(self, sess_file: CrossEventSessionFile,
                 picked: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._file = sess_file
        self.setObjectName("CrossEventPickerRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self._check = QCheckBox()
        self._check.setChecked(picked)
        self._check.toggled.connect(
            lambda v: self.toggled.emit(self._file.key, v))
        layout.addWidget(self._check)

        idline = QVBoxLayout()
        idline.setSpacing(2)
        ident = QLabel(f"{sess_file.event_uuid}::{sess_file.item_id}")
        ident.setObjectName("CrossEventPickerIdent")
        idline.addWidget(ident)
        relpath_text = (sess_file.export_relpath or sess_file.origin_relpath
                        or "")
        meta = QLabel(self._meta_line(sess_file, relpath_text))
        meta.setObjectName("CrossEventPickerMeta")
        meta.setWordWrap(True)
        idline.addWidget(meta)
        layout.addLayout(idline, 1)

    @staticmethod
    def _meta_line(f: CrossEventSessionFile, relpath_text: str) -> str:
        bits: List[str] = []
        if f.member_kind == "grab":
            bits.append(tr("grab"))
        else:
            bits.append(tr("export"))
        if f.kind == "video":
            secs = f.duration_ms / 1000.0
            bits.append(tr("video {s:.1f}s").format(s=secs))
        if f.capture_time:
            bits.append(f.capture_time[:10])
        if relpath_text:
            bits.append(relpath_text)
        return " · ".join(bits)

    @property
    def key(self) -> str:
        return self._file.key

    def set_checked(self, on: bool) -> None:
        self._check.blockSignals(True)
        self._check.setChecked(on)
        self._check.blockSignals(False)


# --------------------------------------------------------------------------- #
# The dialog
# --------------------------------------------------------------------------- #


class CrossEventPickerDialog(QDialog):
    """Drive a :class:`CrossEventCutSession` to commit.

    The constructor takes the session + a ``commit_callback`` that the host
    wires to open the anchor event gateway and call ``session.commit(eg)``.
    The Picker only knows about the session.
    """

    #: Fires after a successful commit with the same session. Host uses it
    #: to refresh upstream lists.
    committed = pyqtSignal(CrossEventCutSession)

    def __init__(
        self,
        session: CrossEventCutSession,
        *,
        commit_callback: Callable[[CrossEventCutSession], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._commit_callback = commit_callback
        self.setWindowTitle(tr("Pin cross-event Cut — {name}").format(
            name=cut_names.display_tag(cut_names.slugify(session.name or ""))))
        self.setMinimumSize(720, 560)
        self.setObjectName("CrossEventPickerDialog")
        self._rows: List[_CandidateRow] = []
        self._build_layout()
        self._refresh_budget()

    # ----- layout --------------------------------------------------------- #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_header())

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget(scroll)
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        for sf in self._session.files:
            row = _CandidateRow(
                sf, picked=self._session.is_picked(sf.key))
            row.toggled.connect(self._on_row_toggled)
            body.addWidget(row)
            self._rows.append(row)
        body.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        # Footer: budget line + actions.
        root.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventPickerHeader")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)
        mode_text = {
            PIN_WEED_OUT: tr("Weed out — start all picked, skip rejects"),
            PIN_PICK_IN: tr("Pick in — start all skipped, pick keepers"),
        }.get(self._session.pin_mode, self._session.pin_mode)
        title = QLabel(
            tr("{n} candidates · {mode}").format(
                n=len(self._session.files), mode=mode_text))
        title.setObjectName("CrossEventPickerTitle")
        f = title.font(); f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)
        anchor = self._session.anchor_event_id or "—"
        sub = QLabel(tr("Anchor event: {anchor}").format(anchor=anchor))
        sub.setObjectName("CrossEventPickerSub")
        layout.addWidget(sub)
        return box

    def _build_footer(self) -> QWidget:
        box = QFrame()
        box.setObjectName("CrossEventPickerFooter")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self._budget_label = QLabel("")
        self._budget_label.setObjectName("CrossEventPickerBudget")
        layout.addWidget(self._budget_label, 1)

        self._cancel = ghost_button(tr("Cancel"))
        self._cancel.clicked.connect(self.reject)
        layout.addWidget(self._cancel)

        self._commit = primary_button(tr("Pin Cut"))
        self._commit.clicked.connect(self._on_commit)
        layout.addWidget(self._commit)
        return box

    # ----- callbacks ----------------------------------------------------- #

    def _on_row_toggled(self, key: str, picked: bool) -> None:
        self._session.set_state(key, picked)
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        totals = self._session.totals()
        seconds = totals.seconds(self._session.photo_s)
        zone = cut_budget.zone(seconds,
                               self._session.target_s,
                               self._session.max_s)
        picked = self._session.picked_count()
        total = len(self._session.files)
        mins = seconds / 60.0
        zone_text = {
            cut_budget.ZONE_GREEN: tr("green"),
            cut_budget.ZONE_AMBER: tr("amber"),
            cut_budget.ZONE_RED: tr("red"),
            cut_budget.ZONE_NONE: tr("no limit"),
        }.get(zone, zone)
        self._budget_label.setText(
            tr("{picked}/{total} picked · {mins:.1f} min · {zone}").format(
                picked=picked, total=total, mins=mins, zone=zone_text))
        # Reflect zone on the label's object name so QSS can style it.
        self._budget_label.setProperty("zone", zone)
        self._budget_label.style().unpolish(self._budget_label)
        self._budget_label.style().polish(self._budget_label)

    def _on_commit(self) -> None:
        try:
            self._commit_callback(self._session)
        except Exception as exc:                            # noqa: BLE001
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, tr("Commit failed"), self._commit_error_text(exc))
            return
        self.committed.emit(self._session)
        self.accept()

    @staticmethod
    def _commit_error_text(exc: Exception) -> str:
        """Map the gateway's tag validation codes to a human-readable
        message; anything else surfaces verbatim. ``'taken'`` is the common
        one — the Cut name collides with an existing Cut OR Collection (one
        global tag namespace)."""
        code = str(exc)
        if code == "taken":
            return tr(
                "A Cut or Collection with that name already exists. "
                "Choose a different name.")
        if code == "reserved":
            return tr("That name is reserved. Choose a different name.")
        if code == "empty":
            return tr("Enter a name for the Cut.")
        return tr("Could not pin Cut: {err}").format(err=code)


__all__ = ["CrossEventPickerDialog"]
