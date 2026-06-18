"""``RecognitionDialog`` — spec/88 propose-and-confirm sync pair surface.

Replaces the front end of the "I don't know my camera's TZ" flow. Instead
of asking the user to *construct* a sync pair, the dialog shows the
strongest candidate cluster computed by :func:`core.clock_recognition`
as a row of ``[camera | phone]`` thumbnail cards and asks "do you
recognize any of these as the same moment?". One click confirms; "None
of these / show another" walks the next cluster; the last cluster falls
back to the manual :class:`SyncPairPickerDialog`.

After the user confirms a card, a *preview-before-apply* panel reads the
shift's full impact (``"Shifting 214 photos by +1h; 6 move to a
different day"``) and asks Apply / Cancel — the rail the bad
hand-constructed correction lacked (Nelson 2026-06-18). The impact
itself is computed by the caller (Slice 3 wires it to the live event);
the dialog only orchestrates the preview surface so it can be unit-tested
in isolation.

Cards intentionally **do not label the implied offset** (spec/88 §2
ranking: "biases the eye"). The user recognizes the *moment*, not the
math.

Qt-only — no engine state, no file I/O outside ``load_pixmap``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, List, Optional, Sequence

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from core.clock_calibration import CalibrationPair
from core.clock_recognition import CandidateCluster, CandidatePair
from core.discrete_tz import format_offset
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


# Card visuals — Nelson 2026-06-18 redesign: each card is one ROW with
# camera on the left and phone on the right; cards stack vertically so
# the user reads top-to-bottom comparing scenes pair by pair.
_THUMB_SIZE = QSize(280, 210)
_CARD_GAP = 12

# How many pair rows are visible without "show another" — three per
# spec/88 feedback (Nelson 2026-06-18): bigger thumbs, easier scene
# recognition than the prior 6-cards horizontal strip.
_DEFAULT_CARDS_VISIBLE = 3


@dataclass(frozen=True)
class ApplyImpact:
    """What the recognition flow tells the user before they apply.

    ``photo_count``     — how many CAMERA photos this correction touches.
    ``shift``           — the constant offset that will be applied to each.
    ``day_moves``       — how many photos cross a day boundary as a result
                          (so the user expects to see them move in the plan).
    """
    photo_count: int
    shift: timedelta
    day_moves: int


# A callable the dialog invokes after the user confirms a card — returns
# the full impact so the preview can show "Shifting N photos by ±X".
# Slice 3 supplies a real implementation; Slice-2 tests pass a stub.
ImpactCallback = Callable[[CandidatePair], ApplyImpact]


def _format_shift(td: timedelta) -> str:
    """``+1h``, ``-1h 30min``, ``+45min``, ``0`` — for the preview line."""
    total = int(round(td.total_seconds()))
    if total == 0:
        return "0"
    sign = "+" if total > 0 else "-"
    secs = abs(total)
    h, rem = divmod(secs, 3600)
    m = rem // 60
    parts: list[str] = []
    if h:
        parts.append(tr("{h}h").replace("{h}", str(h)))
    if m:
        parts.append(tr("{m}min").replace("{m}", str(m)))
    if not parts:
        parts.append(tr("{s}s").replace("{s}", str(secs)))
    return sign + " " + " ".join(parts)


class _PairCard(QFrame):
    """One pair as a single ROW: camera thumbnail on the left, phone
    thumbnail on the right, filenames underneath. Clicking anywhere in
    the row confirms the pair.

    No timestamps (spec/88 §2 ranking — "do not label each card with the
    offset it implies; that biases the eye"). Even raw EXIF clock-times
    can bias: within a cluster every pair has the same raw delta, and
    a non-zero delta makes truly-simultaneous-but-clock-shifted scenes
    *look* mismatched. Strip the clock; force scene recognition.
    """

    pair_clicked = pyqtSignal(object)  # CandidatePair

    def __init__(self, pair: CandidatePair, parent: QWidget | None = None):
        super().__init__(parent)
        self._pair = pair
        self.setObjectName("RecognitionPairCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(16)

        cam_side = self._make_side(pair.camera_item, tr("Camera"))
        phone_side = self._make_side(pair.phone_item, tr("Phone"))
        outer.addWidget(cam_side, stretch=1)
        outer.addWidget(phone_side, stretch=1)

    def pair(self) -> CandidatePair:
        return self._pair

    def _make_side(self, item, role_label: str) -> QWidget:
        """Build one half of the row: small role caption, thumbnail,
        filename. No timestamp."""
        side = QWidget()
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(4)

        role = QLabel(role_label)
        role.setObjectName("PageHint")
        role.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(role)

        col.addWidget(self._make_thumb(item.path),
                      alignment=Qt.AlignmentFlag.AlignCenter)

        name = getattr(item, "path", None)
        caption = QLabel(name.name if name is not None else "")
        caption.setObjectName("PageHint")
        caption.setWordWrap(True)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(caption)

        return side

    def _make_thumb(self, path) -> QLabel:
        thumb = QLabel()
        thumb.setFixedSize(_THUMB_SIZE)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setObjectName("RecognitionThumb")
        # Defer the actual decode until shown — load_pixmap respects EXIF
        # orientation. Falls back to a textual placeholder (video / missing).
        try:
            from mira.ui.media.image_loader import load_pixmap
            pm = load_pixmap(path, target_size=_THUMB_SIZE)
        except Exception:  # noqa: BLE001
            pm = QPixmap()
        if pm.isNull():
            thumb.setText(tr("(no preview)"))
        else:
            scaled = pm.scaled(
                _THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(scaled)
        return thumb

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.pair_clicked.emit(self._pair)
            event.accept()
            return
        super().mousePressEvent(event)


class RecognitionDialog(QDialog):
    """Propose-and-confirm sync-pair surface (spec/88).

    Two pages stacked:

    * **Picker page** — header headline + horizontal card row + the
      "None of these" / "Use manual pair…" / Cancel buttons.
    * **Preview page** — "Shifting N photos by ±X; M move to a different
      day." + Apply / Back.

    The dialog accepts when the user clicks Apply on the preview;
    :meth:`selected_pair` then returns the confirmed
    :class:`CalibrationPair`. If the user instead clicks the manual
    fallback, the dialog accepts with :meth:`fallback_to_manual` ``True``
    and ``selected_pair() is None`` so the caller knows to open
    :class:`SyncPairPickerDialog` in its place.
    """

    def __init__(
        self,
        *,
        camera_id: str,
        reference_id: str,
        clusters: Sequence[CandidateCluster],
        impact_for: ImpactCallback,
        cards_visible: int = 6,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Confirm camera timezone"))
        self.setModal(True)
        self.resize(820, 880)
        self._camera_id = camera_id
        self._reference_id = reference_id
        self._clusters: List[CandidateCluster] = list(clusters)
        self._impact_for = impact_for
        # Default-3 matches the Nelson 2026-06-18 layout — 3 stacked pair
        # rows, scene-by-scene. Callers can still override.
        self._cards_visible = (
            cards_visible if cards_visible != 6 else _DEFAULT_CARDS_VISIBLE
        )
        self._cluster_index = 0
        self._confirmed_pair: Optional[CandidatePair] = None
        self._confirmed_impact: Optional[ApplyImpact] = None
        self._fallback_to_manual = False
        self._build_ui()
        self._render_current_cluster()

    # ── Build ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        self._stack = QStackedLayout()
        self._stack.addWidget(self._build_picker_page())
        self._stack.addWidget(self._build_preview_page())
        # QStackedLayout isn't a QWidget; wrap in a holder.
        holder = QWidget()
        holder.setLayout(self._stack)
        root.addWidget(holder, stretch=1)

    def _build_picker_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._headline = QLabel("")
        self._headline.setObjectName("PageHeading")
        self._headline.setWordWrap(True)
        layout.addWidget(self._headline)

        hint = QLabel(tr(
            "Each row is one candidate pair: camera on the left, phone on "
            "the right. Click the row you recognize as the same moment "
            "(same scene, same people). If none looks right, show the "
            "next set — or fall back to picking a pair by hand."
        ))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Vertical stack of pair rows — three at a time per spec/88
        # 2026-06-18 redesign. No horizontal scroll; "Show another" cycles
        # clusters and re-populates this column.
        self._card_row = QWidget()
        self._card_row_layout = QVBoxLayout(self._card_row)
        self._card_row_layout.setContentsMargins(0, 0, 0, 0)
        self._card_row_layout.setSpacing(_CARD_GAP)
        layout.addWidget(self._card_row, stretch=1)

        # Bottom row of buttons.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._another_btn = QPushButton(
            tr("None of these — show another"))
        self._another_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._another_btn.clicked.connect(self._on_show_another)
        btn_row.addWidget(self._another_btn)
        btn_row.addStretch(1)
        self._manual_btn = QPushButton(tr("Use manual pair…"))
        self._manual_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._manual_btn.clicked.connect(self._on_use_manual)
        btn_row.addWidget(self._manual_btn)
        self._cancel_btn = QPushButton(tr("Cancel"))
        self._cancel_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)
        layout.addLayout(btn_row)

        return page

    def _build_preview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title = QLabel(tr("Preview — ready to apply"))
        title.setObjectName("PageHeading")
        layout.addWidget(title)

        self._preview_body = QLabel("")
        self._preview_body.setObjectName("PageHint")
        self._preview_body.setWordWrap(True)
        self._preview_body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._preview_body)

        layout.addStretch(1)

        self._preview_buttons = QDialogButtonBox(parent=page)
        self._apply_btn = QPushButton(tr("Apply"))
        self._apply_btn.setDefault(True)
        self._apply_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply_btn.clicked.connect(self.accept)
        self._preview_buttons.addButton(
            self._apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self._back_btn = QPushButton(tr("Back"))
        self._back_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._back_btn.clicked.connect(self._on_back_to_picker)
        self._preview_buttons.addButton(
            self._back_btn, QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(self._preview_buttons)

        return page

    # ── Cluster rendering ───────────────────────────────────────────

    def _render_current_cluster(self) -> None:
        # Clear existing cards.
        while self._card_row_layout.count():
            child = self._card_row_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        if not self._clusters:
            # No clusters at all → empty state; the picker page still
            # offers the manual fallback path.
            self._headline.setText(tr(
                "<b>Couldn't find any plausible matches.</b><br>"
                "Use the manual pair picker instead, or Cancel."
            ))
            self._another_btn.setVisible(False)
            return

        cluster = self._clusters[self._cluster_index]
        self._headline.setText(self._headline_for(cluster))

        for pair in cluster.pairs[: self._cards_visible]:
            card = _PairCard(pair, parent=self._card_row)
            card.pair_clicked.connect(self._on_card_clicked)
            self._card_row_layout.addWidget(card)
        self._card_row_layout.addStretch(1)

        # Hide "Show another" if this is the last cluster — but always keep
        # the manual fallback visible so the user has an exit at every step.
        is_last = self._cluster_index >= len(self._clusters) - 1
        self._another_btn.setVisible(not is_last)

    def _headline_for(self, cluster: CandidateCluster) -> str:
        kappa = cluster.snapped_kappa_minutes
        if kappa == 0:
            base = tr(
                "It looks like the <b>{cam}</b> clock is set to <b>UTC</b> — "
                "matching your phone. One click below confirms."
            ).replace("{cam}", self._camera_id)
        else:
            base = tr(
                "It looks like the <b>{cam}</b> clock is set to <b>{tz}</b>. "
                "Do you recognize any of these as the same moment?"
            ).replace("{cam}", self._camera_id).replace(
                "{tz}", format_offset(kappa))
        progress = ""
        if len(self._clusters) > 1:
            progress = tr(
                "<br>Showing option {n} of {total}."
            ).replace("{n}", str(self._cluster_index + 1)).replace(
                "{total}", str(len(self._clusters)))
        return base + progress

    # ── Picker handlers ─────────────────────────────────────────────

    def _on_show_another(self) -> None:
        if self._cluster_index >= len(self._clusters) - 1:
            return
        self._cluster_index += 1
        self._render_current_cluster()

    def _on_use_manual(self) -> None:
        """User opted out of recognition → caller falls back to the
        legacy hand-picked-pair UI. We accept (so the modal closes
        cleanly) and the caller checks :meth:`fallback_to_manual`."""
        self._fallback_to_manual = True
        self._confirmed_pair = None
        self._confirmed_impact = None
        self.accept()

    def _on_card_clicked(self, pair: CandidatePair) -> None:
        """User recognized a moment → swing to the preview page with the
        impact line ready."""
        impact = self._impact_for(pair)
        self._confirmed_pair = pair
        self._confirmed_impact = impact
        self._preview_body.setText(self._preview_text(impact))
        self._stack.setCurrentIndex(1)

    def _on_back_to_picker(self) -> None:
        self._confirmed_pair = None
        self._confirmed_impact = None
        self._stack.setCurrentIndex(0)

    def _preview_text(self, impact: ApplyImpact) -> str:
        photo_line = tr(
            "Shifting <b>{n}</b> {cam} photo(s) by <b>{shift}</b>."
        ).replace("{n}", str(impact.photo_count)).replace(
            "{cam}", self._camera_id).replace(
            "{shift}", _format_shift(impact.shift))
        if impact.day_moves:
            day_line = tr(
                "<br><b>{m}</b> will move to a different day."
            ).replace("{m}", str(impact.day_moves))
        else:
            day_line = tr(
                "<br>No photos cross a day boundary."
            )
        return photo_line + day_line

    # ── Public read API ─────────────────────────────────────────────

    @property
    def fallback_to_manual(self) -> bool:
        """True when the user chose to fall back to the legacy hand-picked
        sync-pair picker. Caller opens :class:`SyncPairPickerDialog` in
        place of this dialog's confirmed pair."""
        return self._fallback_to_manual

    def confirmed_candidate(self) -> Optional[CandidatePair]:
        """The :class:`CandidatePair` the user recognized + applied. Only
        non-``None`` after :meth:`exec` returns ``Accepted`` AND
        :pyattr:`fallback_to_manual` is False."""
        if (self.result() != QDialog.DialogCode.Accepted
                or self._fallback_to_manual):
            return None
        return self._confirmed_pair

    def selected_pair(self) -> Optional[CalibrationPair]:
        """The :class:`CalibrationPair` the engine consumes. Convenience
        wrapper around :meth:`confirmed_candidate` →
        ``to_calibration_pair``."""
        cand = self.confirmed_candidate()
        if cand is None:
            return None
        return cand.to_calibration_pair()

    def confirmed_impact(self) -> Optional[ApplyImpact]:
        """The impact the preview surfaced — for callers wanting to log
        what the user saw before they clicked Apply."""
        if (self.result() != QDialog.DialogCode.Accepted
                or self._fallback_to_manual):
            return None
        return self._confirmed_impact
