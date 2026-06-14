"""BucketNavigator — phase-agnostic Day → Bucket picker (spec/06, spec/11 §6 step 3).

Moved from ``mira/ui/culler/bucket_navigator.py`` to ``mira/ui/base/`` so every
phase (Cull, Select, Process …) can import it without a cross-consumer dependency.

**What changed from the legacy navigator (spec/06 redesign):**

* :class:`BucketNavigatorConfig` — per-consumer configuration: heading template,
  return-button label, :class:`StatusLabels` overrides, and a list of
  :class:`BatchOpDef` objects that declare batch operations (scope + target state).
* Batch-op buttons appear in the ``title_actions`` slot of each
  :class:`~mira.ui.base.info_card_row.InfoCardRow`.  Clicking one shows a standard
  confirm dialog, then emits :attr:`BucketNavigator.batch_op_requested` — the owning page
  executes against the gateway + calls a refresh method.  The navigator itself stays
  gateway-free.
* Tooltips on every day/bucket card row (Level 1).
* ``StatusBreakdown`` uses the per-consumer :class:`~mira.ui.base.status_breakdown.
  StatusLabels` from the config (Level 2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QKeyEvent
from mira.ui.base.surface import back_button
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mira.picked import (
    BADGE_BROWSED,
    BADGE_DONE,
    BADGE_IN_PROGRESS,
    BucketStatus,
    CullBucket,
    PickDay,
)
from mira.ui.base.info_card_row import (
    VARIANT_BUCKET,
    VARIANT_DAY,
    InfoCardRow,
)
from mira.ui.base.status_breakdown import StatusBreakdown, StatusLabels
from mira.ui.i18n import tr

# Bucket kind → a friendly, translated label for the row title.
_KIND_LABEL = {
    "focus_bracket": "Focus bracket",
    "exposure_bracket": "Exposure bracket",
    "burst": "Burst",
    "moment": "Moment",
    "individual": "Individuals",
    "video": "Video",
    "video_moment": "Video moment",
}

# Human-readable phase-state names used in the confirm dialog.
_STATE_DISPLAY = {
    "picked": "Kept",
    "skipped": "Discarded",
    "candidate": "Compare",
    "untouched": "Untouched",
}


# ── Public dataclasses ────────────────────────────────────────────────────────


@dataclass
class BatchOpDef:
    """Declares one batch operation offered to the user at the navigator level.

    ``label``   — button text shown on the card row (e.g. "✓ Keep all").
    ``state``   — the :mod:`mira.picked` phase-state value to apply
                  (``"picked"`` | ``"skipped"``).
    ``scope``   — ``"bucket"`` (button on bucket rows) or ``"day"`` (button on day rows).
    ``tooltip`` — hover hint on the button.
    """

    label: str
    state: str
    scope: str = "bucket"
    tooltip: str = ""


@dataclass
class BucketNavigatorConfig:
    """Per-consumer configuration for :class:`BucketNavigator`.

    All text fields pass through :func:`~mira.ui.i18n.tr` at render time.

    ``day_list_heading_template``
        Heading shown above the day list.  ``{n}`` is replaced by the day count.
    ``return_button_label``
        Label on the "return to event" button shown on the day list.
    ``return_button_tooltip``
        Tooltip on the same button.
    ``status_labels``
        Row labels for the :class:`~mira.ui.base.status_breakdown.StatusBreakdown`
        widget.  Defaults to Cull vocabulary (Kept / Compare / Discarded / Untouched).
    ``batch_ops``
        Ordered list of :class:`BatchOpDef` objects.  Each one becomes a compact button
        in the ``title_actions`` slot of the matching card row.
    """

    day_list_heading_template: str = "{n} day(s) — pick where to select"
    return_button_label: str = "Back"
    return_button_tooltip: str = "Leave the culler and return to the event."
    status_labels: StatusLabels = field(default_factory=StatusLabels)
    batch_ops: List[BatchOpDef] = field(default_factory=list)
    #: When True, clicking a day card emits :attr:`BucketNavigator.day_activated`
    #: instead of rendering the legacy bucket-list view (spec/32 Day Grid mode —
    #: the host opens the flat day surface). When False (default), the legacy
    #: behaviour is preserved for any consumer that still wants the bucket list.
    day_grid_mode: bool = False
    #: When True, a "Start a new pass…" button appears next to the return
    #: button in the days panel.  Clicking it emits
    #: :attr:`BucketNavigator.clear_marks_requested` so the host can confirm
    #: with the user and then call ``gateway.clear_visited_for_phase(phase)``
    #: to wipe every ✓ tick at this phase (spec/32 §2.10).  Decisions are
    #: not touched.  Cull + Process opt in (Nelson 2026-06-09); Select keeps
    #: it off unless the user asks.
    show_clear_marks_button: bool = False
    #: Label for the "Start a new pass…" button.  Default reads as user
    #: intent ("start a new pass") rather than mechanism ("clear marks").
    clear_marks_button_label: str = "Start a new pass…"
    clear_marks_button_tooltip: str = (
        "Clear every ✓ tick from this phase and re-open it with a clean "
        "slate. Decisions are preserved — only the 'I've already looked at "
        "this' marks are reset."
    )
    #: When True, an "Export all days" button appears on the days bar.
    #: Clicking it emits :attr:`BucketNavigator.export_all_requested` so
    #: the host can run the event-scope export. Edit opts in
    #: (Nelson 2026-06-07).
    show_export_all_button: bool = False
    export_all_button_label: str = "📤 Export all days"
    export_all_button_tooltip: str = (
        "Export every day's processed photos at once."
    )
    #: Nelson 2026-06-07 — "Pick all days" / "Skip all days" event-scope
    #: batch ops on the days bar. Off by default; Pick opts in. Hosts
    #: connect ``pick_all_days_requested`` / ``skip_all_days_requested``
    #: to a handler that walks every captured item in the event.
    show_pick_all_button: bool = False
    pick_all_button_label: str = "✓ Pick all days"
    pick_all_button_tooltip: str = (
        "Mark every item across every day as Picked."
    )
    show_skip_all_button: bool = False
    skip_all_button_label: str = "✗ Skip all days"
    skip_all_button_tooltip: str = (
        "Mark every item across every day as Skipped."
    )


# Pick default config — was CULL_CONFIG; the Pick host (PickPage) uses
# this. Verbs renamed Keep/Discard → Pick/Skip per the spec/48 rename
# pass (Nelson 2026-06-07).
CULL_CONFIG = BucketNavigatorConfig(
    day_list_heading_template="{n} day(s) — pick where to start",
    return_button_label="Back",
    return_button_tooltip="Leave the picker and return to the event.",
    status_labels=StatusLabels(show_candidate=False, merge_untouched=True),
    show_clear_marks_button=True,
    batch_ops=[
        BatchOpDef(
            label="✓ Pick all",
            state="picked",
            scope="bucket",
            tooltip="Mark every item in this cluster as Picked without opening it.",
        ),
        BatchOpDef(
            label="✗ Skip all",
            state="skipped",
            scope="bucket",
            tooltip="Mark every item in this cluster as Skipped without opening it.",
        ),
        BatchOpDef(
            label="✓ Pick all",
            state="picked",
            scope="day",
            tooltip="Mark every item in this day as Picked.",
        ),
        BatchOpDef(
            label="✗ Skip all",
            state="skipped",
            scope="day",
            tooltip="Mark every item in this day as Skipped.",
        ),
    ],
)

# Select config — pool = Cull-Kept items only; cross-camera; no Compare state shown
# in headings (kept/discarded/untouched vocabulary only).
SELECT_CONFIG = BucketNavigatorConfig(
    day_list_heading_template="{n} day(s) — pick where to select",
    return_button_label="Back",
    return_button_tooltip="Leave the selector and return to the event.",
    status_labels=StatusLabels(show_candidate=False, merge_untouched=True),
    batch_ops=[
        BatchOpDef(
            label="✓ Select all",
            state="picked",
            scope="bucket",
            tooltip="Mark every item in this bucket as Selected without opening it.",
        ),
        BatchOpDef(
            label="✗ Discard all",
            state="skipped",
            scope="bucket",
            tooltip="Mark every item in this bucket as Discarded without opening it.",
        ),
        BatchOpDef(
            label="✓ Select all",
            state="picked",
            scope="day",
            tooltip="Mark every item in this day as Selected.",
        ),
        BatchOpDef(
            label="✗ Discard all",
            state="skipped",
            scope="day",
            tooltip="Mark every item in this day as Discarded.",
        ),
    ],
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _badge_hint(badge: str) -> str:
    if badge == BADGE_DONE:
        return tr("Done")
    if badge == BADGE_IN_PROGRESS:
        return tr("In progress")
    if badge == BADGE_BROWSED:
        return tr("Browsed")
    return ""


def _kind_label(kind: str) -> str:
    return tr(_KIND_LABEL.get(kind, kind.replace("_", " ").title()))


def _populate(breakdown: StatusBreakdown, status: BucketStatus) -> None:
    breakdown.populate(
        kept=status.kept, candidate=status.candidate,
        discarded=status.discarded, untouched=status.untouched,
    )


# ── Widget ────────────────────────────────────────────────────────────────────


class BucketNavigator(QWidget):
    """Phase-agnostic Day → Bucket picker, configured via :class:`BucketNavigatorConfig`.

    The navigator is **gateway-free**: it is handed a precomputed ``list[PickDay]`` and
    simply renders it.  Batch operations emit :attr:`batch_op_requested` and are executed
    by the owning page (which holds the gateway).
    """

    bucket_activated = pyqtSignal(object)   # CullBucket
    back_requested = pyqtSignal()           # leave the phase (from the Day list)
    # Emitted instead of rendering the bucket-list view when ``config.day_grid_mode`` is
    # True (spec/32 Day Grid mode — host opens the flat day surface). Carries the clicked
    # day's ``day_number`` (or ``None`` for the undated bucket).
    day_activated = pyqtSignal(object)      # day_number: int | None
    # (BatchOpDef, list[str] item_ids) — emitted after the user confirms; the owning page
    # executes against the gateway, then calls set_days() / show_buckets_for() to refresh.
    batch_op_requested = pyqtSignal(object, list)
    # spec/32 §2.10 — "Start a new pass…" button.  Emitted when the user
    # clicks; the host shows a confirmation dialog and then calls
    # ``gateway.clear_visited_for_phase(phase)``.  Visibility of the button
    # is gated by ``BucketNavigatorConfig.show_clear_marks_button``.
    clear_marks_requested = pyqtSignal()
    # Nelson 2026-06-07 — "Export all days" batch action on the Edit
    # navigator. Visibility gated by
    # ``BucketNavigatorConfig.show_export_all_button``. Host runs the
    # event-scope export when this fires.
    export_all_requested = pyqtSignal()
    # Nelson 2026-06-07 — event-scope Pick all / Skip all on the Pick
    # navigator. Visibility gated by the matching ``show_*_button``
    # config flag. Host walks every captured item in the event.
    pick_all_days_requested = pyqtSignal()
    skip_all_days_requested = pyqtSignal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        config: Optional[BucketNavigatorConfig] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BucketNavigator")
        self._cfg = config or BucketNavigatorConfig()
        self._days: List[PickDay] = []
        self._day: Optional[PickDay] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._heading = QLabel("")
        self._heading.setObjectName("SelectBucketInfo")
        self._heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self._heading)

        # Sticky Back (Bucket view) / Return (Day view) bars, outside the scroll area.
        self._back_bar = QWidget()
        _bb = QHBoxLayout(self._back_bar)
        _bb.setContentsMargins(12, 8, 12, 0)
        self._back_btn = back_button()
        self._back_btn.setToolTip(tr("Back to the day list."))
        self._back_btn.clicked.connect(self._show_days)
        _bb.addWidget(self._back_btn)
        _bb.addStretch(1)
        self._back_bar.setVisible(False)
        outer.addWidget(self._back_bar)

        self._day_bar = QWidget()
        _db = QHBoxLayout(self._day_bar)
        _db.setContentsMargins(12, 8, 12, 0)
        # The leave-the-phase button. The audit's earlier #DangerButton
        # role was dropped 2026-06-12 (Nelson): non-house colour on a
        # navigation control was the exact felt-wrong he called out.
        # Plain Back, the standard button look — the tooltip carries
        # the "leave the picker / selector" context.
        self._quit_btn = back_button(tr(self._cfg.return_button_label))
        self._quit_btn.setToolTip(tr(self._cfg.return_button_tooltip))
        self._quit_btn.clicked.connect(self.back_requested.emit)
        _db.addWidget(self._quit_btn)
        _db.addStretch(1)
        # spec/32 §2.10 — "Start a new pass…" (only for phases that opt in
        # via ``BucketNavigatorConfig.show_clear_marks_button``).  Sits on
        # the right of the days-bar so the user reaches it after Return.
        self._clear_marks_btn = QPushButton(
            tr(self._cfg.clear_marks_button_label))
        self._clear_marks_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._clear_marks_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._clear_marks_btn.setToolTip(
            tr(self._cfg.clear_marks_button_tooltip))
        self._clear_marks_btn.clicked.connect(self.clear_marks_requested.emit)
        self._clear_marks_btn.setVisible(
            bool(self._cfg.show_clear_marks_button))
        _db.addWidget(self._clear_marks_btn)
        # Nelson 2026-06-07 — event-scope "Pick all days" / "Skip all
        # days" batch ops on the Pick navigator days bar. Off by default;
        # phases opt in via the config.
        self._pick_all_days_btn = QPushButton(
            tr(self._cfg.pick_all_button_label))
        self._pick_all_days_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._pick_all_days_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._pick_all_days_btn.setToolTip(
            tr(self._cfg.pick_all_button_tooltip))
        self._pick_all_days_btn.clicked.connect(
            self.pick_all_days_requested.emit)
        self._pick_all_days_btn.setVisible(
            bool(self._cfg.show_pick_all_button))
        _db.addWidget(self._pick_all_days_btn)
        self._skip_all_days_btn = QPushButton(
            tr(self._cfg.skip_all_button_label))
        self._skip_all_days_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._skip_all_days_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._skip_all_days_btn.setToolTip(
            tr(self._cfg.skip_all_button_tooltip))
        self._skip_all_days_btn.clicked.connect(
            self.skip_all_days_requested.emit)
        self._skip_all_days_btn.setVisible(
            bool(self._cfg.show_skip_all_button))
        _db.addWidget(self._skip_all_days_btn)
        # Nelson 2026-06-07 — "Export all days" (only for phases that opt in
        # via ``BucketNavigatorConfig.show_export_all_button``). Sits next
        # to the new-pass button on the right of the days bar; one click
        # exports the entire event's processed photos via the host's
        # existing event-scope export path.
        self._export_all_btn = QPushButton(
            tr(self._cfg.export_all_button_label))
        self._export_all_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._export_all_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._export_all_btn.setToolTip(
            tr(self._cfg.export_all_button_tooltip))
        self._export_all_btn.clicked.connect(self.export_all_requested.emit)
        self._export_all_btn.setVisible(
            bool(self._cfg.show_export_all_button))
        _db.addWidget(self._export_all_btn)
        self._day_bar.setVisible(False)
        outer.addWidget(self._day_bar)

        self._stack = QStackedWidget()
        self._day_host = QWidget()
        self._day_box = QVBoxLayout(self._day_host)
        self._day_box.setContentsMargins(12, 8, 12, 12)
        self._day_box.setSpacing(8)
        self._bucket_host = QWidget()
        self._bucket_box = QVBoxLayout(self._bucket_host)
        self._bucket_box.setContentsMargins(12, 8, 12, 12)
        self._bucket_box.setSpacing(8)
        self._bucket_scroll = self._scroller(self._bucket_host)
        self._stack.addWidget(self._scroller(self._day_host))  # 0 — days
        self._stack.addWidget(self._bucket_scroll)             # 1 — buckets
        outer.addWidget(self._stack, stretch=1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    @staticmethod
    def _scroller(host: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setObjectName("NavScroll")
        sa.setWidget(host)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.Shape.NoFrame)
        sa.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return sa

    # ── public API ─────────────────────────────────────────────────────────────

    def set_untouched_merge_target(self, state: str) -> None:
        """Fold the Untouched count into ``'picked'`` or ``'skipped'`` so the simplified
        breakdown matches the configured per-phase default (Nelson 2026-06-03). The page
        calls this on open with its phase default; only meaningful when the config's labels
        already have ``merge_untouched=True``."""
        self._cfg.status_labels.merge_untouched_into = state

    def set_days(self, days: List[PickDay]) -> None:
        """Render a freshly-built day list (the navigator's entry point)."""
        self._days = list(days)
        self._show_days()

    def refresh(self, days: List[PickDay]) -> None:
        """Re-render with rebuilt days, staying on the current view if possible."""
        self._days = list(days)
        if self._stack.currentIndex() == 1 and self._day is not None:
            match = next(
                (d for d in self._days if d.day_number == self._day.day_number), None
            )
            if match is not None:
                self._show_buckets(match)
                return
        self._show_days()

    def show_buckets_for(self, days: List[PickDay], day_number) -> None:
        """Re-render and land on a specific day's bucket list.

        Falls back to the day list if that day is gone.
        """
        self._days = list(days)
        match = next((d for d in self._days if d.day_number == day_number), None)
        if match is not None:
            self._show_buckets(match)
        else:
            self._show_days()

    # ── views ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _clear(box: QVBoxLayout) -> None:
        while box.count():
            it = box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)

    def _show_days(self) -> None:
        self._day = None
        self._clear(self._day_box)
        heading = (
            tr(self._cfg.day_list_heading_template)
            .replace("{n}", str(len(self._days)))
        )
        self._heading.setText(heading)
        for day in self._days:
            self._day_box.addWidget(self._build_day_card(day))
        self._day_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._back_bar.setVisible(False)
        self._day_bar.setVisible(True)
        self._stack.setCurrentIndex(0)

    def _show_buckets(self, day: PickDay) -> None:
        self._day = day
        self._clear(self._bucket_box)
        self._heading.setText(
            tr("{label} — {n} bucket(s)")
            .replace("{label}", day.label)
            .replace("{n}", str(len(day.buckets)))
        )
        for idx, bucket in enumerate(day.buckets, 1):
            self._bucket_box.addWidget(self._build_bucket_card(idx, bucket))
        self._bucket_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._back_bar.setVisible(True)
        self._day_bar.setVisible(False)
        self._stack.setCurrentIndex(1)
        # Always start at the first bucket (issue 4 — Nelson 2026-06-02).
        self._bucket_scroll.verticalScrollBar().setValue(0)

    # ── card builders ──────────────────────────────────────────────────────────

    def _build_day_card(self, day: PickDay) -> InfoCardRow:
        title = day.label
        hint = _badge_hint(day.status.badge)
        if hint:
            title = f"{title} — {hint}"
        breakdown = StatusBreakdown(labels=self._cfg.status_labels)
        _populate(breakdown, day.status)
        photos = sum(b.count for b in day.buckets)
        metadata = [
            tr("Buckets: {n}").replace("{n}", str(len(day.buckets))),
            tr("Items: {n}").replace("{n}", str(photos)),
        ]
        day_ops = [op for op in self._cfg.batch_ops if op.scope == "day"]
        title_actions = [self._build_batch_btn(op, lambda d=day: self._day_item_ids(d))
                         for op in day_ops]
        card = InfoCardRow(
            title=title, content_widget=breakdown, metadata_lines=metadata,
            title_actions=title_actions,
            right_column_width=180, variant=VARIANT_DAY,
        )
        card.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        card.setToolTip(
            tr("{label} · {n} bucket(s) · {p} item(s)")
            .replace("{label}", day.label)
            .replace("{n}", str(len(day.buckets)))
            .replace("{p}", str(photos))
        )
        if self._cfg.day_grid_mode:
            # spec/32: host opens the flat Day Grid surface; the legacy bucket-list view
            # is bypassed entirely. Signal carries the durable day_number.
            card.clicked.connect(
                lambda d=day: self.day_activated.emit(d.day_number))
        else:
            card.clicked.connect(lambda d=day: self._show_buckets(d))
        return card

    def _build_bucket_card(self, idx: int, bucket: CullBucket) -> InfoCardRow:
        title = (
            tr("#{i} · {kind} · {n}")
            .replace("{i}", str(idx))
            .replace("{kind}", _kind_label(bucket.kind))
            .replace("{n}", str(bucket.count))
        )
        hint = _badge_hint(bucket.status.badge)
        if hint:
            title = f"{title} — {hint}"
        breakdown = StatusBreakdown(labels=self._cfg.status_labels)
        _populate(breakdown, bucket.status)
        metadata = []
        if bucket.camera:
            metadata.append(tr("Camera: {c}").replace("{c}", bucket.camera))
        bucket_ops = [op for op in self._cfg.batch_ops if op.scope == "bucket"]
        title_actions = [
            self._build_batch_btn(op, lambda b=bucket: self._bucket_item_ids(b))
            for op in bucket_ops
        ]
        card = InfoCardRow(
            title=title, content_widget=breakdown, metadata_lines=metadata,
            title_actions=title_actions,
            right_column_width=180, variant=VARIANT_BUCKET,
        )
        card.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        kind_str = _kind_label(bucket.kind)
        card.setToolTip(
            tr("{kind} bucket · {n} item(s){cam}")
            .replace("{kind}", kind_str)
            .replace("{n}", str(bucket.count))
            .replace("{cam}", f" · {bucket.camera}" if bucket.camera else "")
        )
        card.clicked.connect(lambda b=bucket: self.bucket_activated.emit(b))
        return card

    # ── batch ops ──────────────────────────────────────────────────────────────

    def _build_batch_btn(self, op: BatchOpDef, item_ids_fn: Callable) -> QPushButton:
        btn = QPushButton(tr(op.label))
        btn.setObjectName("BatchOpButton")
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        if op.tooltip:
            btn.setToolTip(tr(op.tooltip))
        btn.clicked.connect(
            lambda _checked=False, o=op, fn=item_ids_fn: self._trigger_batch(o, fn())
        )
        return btn

    def _trigger_batch(self, op: BatchOpDef, item_ids: list) -> None:
        if not item_ids:
            return
        n = len(item_ids)
        state_label = tr(_STATE_DISPLAY.get(op.state, op.state))
        msg = (
            tr("Mark {n} item(s) as {state}?")
            .replace("{n}", str(n))
            .replace("{state}", state_label)
        )
        result = QMessageBox.question(
            self,
            tr("Confirm"),
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self.batch_op_requested.emit(op, item_ids)

    @staticmethod
    def _bucket_item_ids(bucket: CullBucket) -> List[str]:
        return [item.item_id for item in bucket.items]

    @staticmethod
    def _day_item_ids(day: PickDay) -> List[str]:
        return [item.item_id for b in day.buckets for item in b.items]

    # ── keyboard ───────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self._stack.currentIndex() == 1:
                self._show_days()
            else:
                self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
