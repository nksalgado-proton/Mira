"""Surface 09 — Share / Cuts.

Share is the permanent **state of a closed event** (spec/66 — *not* a
phase). Reached by opening a closed event (or via the Share menu, which
the menu bar gates to closed events only). This module hosts:

* ``ShareCutsPage`` — the page MainWindow mounts. It is a list ↔ detail
  ↔ session chassis with the spec/61 dialog handlers folded in (New
  Cut, Adjust, Rename, Delete, Open detail, Play, Export).
* ``_CutsListView`` — the redesigned visual layer: the spec/71 share
  identity header (closed-card pink, NOT a phase colour), the accent-
  framed ``#exported`` pool card, and the user-Cut rows (cover · meta ·
  Open primary · Adjust ghost · kebab for Rename/Delete).

spec/65 §3.9 governs the list fidelity (pool card, cut rows, kebab for
rare actions). spec/66/68 govern the closed-event gating in MainWindow.
The deep Cut-session program (pool algebra, Picker-on-a-Cut, separators,
audio, play/export) is the spec/61 program and is **not built here** —
this module wires the existing legacy dialogs / session pages
(unchanged) into the redesigned shell so the surface looks and routes
correctly while the deep work remains tracked under spec/61.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import audio_library, cut_names
from mira.gateway import Gateway
from mira.shared.cut_session import CutSession
from mira.ui.design import (
    Card,
    danger_ghost_button,
    ghost_button,
    primary_button,
    tag,
)
from mira.ui.i18n import tr
from mira.ui.shared.cut_detail_page import CutDetailPage
from mira.ui.shared.cut_session_page import CutSessionPage
# spec/65 §3.13 / §5.1: the New Cut dialog routes through an adapter that
# wraps the redesigned page. The legacy dialog at
# ``mira.ui.shared.new_cut_dialog`` stays around for its tests and
# provides the ``CutDraft`` dataclass the adapter still returns, so the
# call sites here (``NewCutDialog(...).exec()`` + ``.draft()``) are the
# same as the prior chassis used.
from mira.ui.shared.new_cut_dialog_adapter import NewCutDialog

log = logging.getLogger(__name__)


# ── snapshots (the list-view feed) ───────────────────────────────────


@dataclass
class CutSnapshot:
    """One user Cut row's data."""

    cut_id: str
    name: str
    item_count: int = 0
    duration_seconds: int = 0
    description: str = ""
    exported_date: str = ""


@dataclass
class PoolSnapshot:
    """The built-in #exported pool — the universe every Cut starts from."""

    exported_count: int = 0
    sub_line: str = ""


def _fmt_duration(s: int) -> str:
    m, sec = divmod(max(0, int(s)), 60)
    return f"{m}:{sec:02d}"


# ── rename dialog (carried over from the legacy chassis) ─────────────


class _RenameCutDialog(QDialog):
    """Rename with the same live transform preview as creation —
    titled group + input inside (the form grammar)."""

    def __init__(self, current_tag: str, taken: List[str],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Rename Cut"))
        self.setModal(True)
        self.setMinimumWidth(420)
        self._taken = [t for t in taken if t != current_tag]
        box = QVBoxLayout(self)
        group = QGroupBox(tr("New name"))
        group.setObjectName("FormFieldGroup")
        gbox = QVBoxLayout(group)
        self._edit = QLineEdit(current_tag)
        self._edit.setToolTip(tr(
            "Type any name; the tag below is what gets stored. Already-"
            "exported folders keep their old name (snapshots)."))
        self._edit.textChanged.connect(self._refresh)
        gbox.addWidget(self._edit)
        self._preview = QLabel("")
        self._preview.setObjectName("PageHint")
        gbox.addWidget(self._preview)
        box.addWidget(group)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Rename"))
            self._ok.setToolTip(tr("Apply the new name."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Keep the current name."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        slug = cut_names.slugify(self._edit.text())
        err = cut_names.check_tag(slug, self._taken)
        if err == "empty":
            self._preview.setText(tr("type a name to see its tag"))
        elif err == "reserved":
            self._preview.setText(tr("tag: {tag} — reserved built-in name")
                                  .replace("{tag}", cut_names.display_tag(slug)))
        elif err == "taken":
            self._preview.setText(tr("tag: {tag} — already taken in this event")
                                  .replace("{tag}", cut_names.display_tag(slug)))
        else:
            self._preview.setText(tr("tag: {tag} — available")
                                  .replace("{tag}", cut_names.display_tag(slug)))
        if self._ok is not None:
            self._ok.setEnabled(err is None)

    def new_name(self) -> str:
        return self._edit.text().strip()


# ── share-state identity header (spec/71) ────────────────────────────


class _ShareIdentityHeader(QWidget):
    """The Share *state* identity strip — spec/71's closed-card treatment.

    Composition matches :class:`SurfaceIdentityHeader` (rail + name badge
    + purpose line + reminder) for visual coherence across decision
    surfaces, but the colour token is **pink** (the closed-event
    semantic, same as :class:`ChipClosed`) — *not* one of the four
    phase identity colours. Spec/71 is explicit: Share is **not** a
    phase, so it sits outside the four-phase palette and reads as a
    closed-event marker instead.

    Reuses the existing ``#SurfaceHeaderRail`` / ``#SurfaceHeaderBadge``
    QSS roles with ``phase="share"`` so the QSS catalog stays the one
    seam (rules added to ``assets/themes/redesign.qss``).
    """

    def __init__(
        self,
        name: str,
        purpose: str,
        reminder: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SurfaceHeader")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 1. Pink rail (closed-card semantic).
        rail = QFrame()
        rail.setObjectName("SurfaceHeaderRail")
        rail.setProperty("phase", "share")
        rail.setFixedHeight(3)
        outer.addWidget(rail)

        # 2. Title row: badge + purpose.
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(14)

        badge = QLabel(name.upper())
        badge.setObjectName("SurfaceHeaderBadge")
        badge.setProperty("phase", "share")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = QFont(badge.font())
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        badge.setFont(f)
        title_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        purpose_lbl = QLabel(purpose)
        purpose_lbl.setObjectName("SurfaceHeaderPurpose")
        purpose_lbl.setWordWrap(True)
        title_row.addWidget(
            purpose_lbl, 1, Qt.AlignmentFlag.AlignVCenter)

        outer.addLayout(title_row)

        # 3. Optional reminder (no §5a state legend — Cuts are post-pick).
        if reminder:
            rem = QLabel(reminder)
            rem.setObjectName("SurfaceHeaderReminder")
            rem.setWordWrap(True)
            outer.addWidget(rem)


# ── pool card ─────────────────────────────────────────────────────────


class _PoolCard(QFrame):
    """Accent-framed pool card mirroring the CrossEventCutsBand chrome."""

    open_requested = pyqtSignal()

    def __init__(self, pool: PoolSnapshot) -> None:
        super().__init__()
        self.setObjectName("CrossEventBand")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)
        # Globe icon tile (Unicode placeholder — the custom glyph swap
        # lands with the spec/69 icon sweep, same pass that fixes the
        # eye/tick/split-chip Unicode across the whole catalog).
        tile = QLabel("🌐")
        tile.setFixedSize(50, 50)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            "background: #211f3a; color: #7c6cff;"
            " border: 1px solid #7c6cff; border-radius: 14px;"
            " font-size: 22px;"
        )
        h.addWidget(tile)
        # Label block
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        t = QLabel("#exported")
        t.setObjectName("CardTitle")
        title_row.addWidget(t)
        title_row.addWidget(tag("Pool"))
        title_row.addStretch()
        block.addLayout(title_row)
        sub_text = pool.sub_line or (
            f"{pool.exported_count} exported files — the universe"
            f" every cut starts from."
        )
        sub = QLabel(sub_text)
        sub.setObjectName("Sub")
        block.addWidget(sub)
        h.addLayout(block, 1)
        # Open pool ghost button
        btn = ghost_button("Open pool")
        btn.clicked.connect(self.open_requested.emit)
        h.addWidget(btn)


# ── cut row (with kebab for rare actions, spec/65 §3.9) ──────────────


class CutRow(Card):
    """One user Cut card row — Open primary + Adjust ghost + kebab (⋮)
    menu carrying the rare actions (Rename / Delete), per spec/65 §3.9
    ("4 ghost buttons reads as crowded; mockup uses a kebab menu with
    the rare actions hidden").

    Fixed height (Nelson 2026-06-15): the row's vertical size is pinned
    so the list scrolls when it overflows; without it the rows balloon
    to fill the available height and there is no scrolling. The earlier
    cover-thumb slot was empty (``cover_pixmap`` never wired) and has
    been removed — spec/61 §3 lists the row fields as tag · item count
    · duration · music category · exported status only.
    """

    ROW_HEIGHT = 92

    open_requested = pyqtSignal(str)
    adjust_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        snapshot: CutSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        self.setFixedHeight(self.ROW_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.layout().setContentsMargins(18, 18, 18, 18)
        self.layout().setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(14)

        # Info block
        info = QVBoxLayout()
        info.setSpacing(4)
        name_label = QLabel(snapshot.name)
        name_label.setObjectName("CardTitle")
        info.addWidget(name_label)
        meta_bits = [
            f"<b>{snapshot.item_count}</b> items",
            f"<b>{_fmt_duration(snapshot.duration_seconds)}</b>",
        ]
        if snapshot.description:
            meta_bits.append(snapshot.description)
        if snapshot.exported_date:
            meta_bits.append(f"exported <b>{snapshot.exported_date}</b>")
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Sub")
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setWordWrap(True)
        info.addWidget(meta)
        info.addStretch()
        info_wrap = QWidget()
        info_wrap.setLayout(info)
        row.addWidget(info_wrap, 1)

        # Action cluster — Open primary + Adjust ghost + ⋮ kebab.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        open_btn = primary_button("Open")
        open_btn.clicked.connect(
            lambda: self.open_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(open_btn)
        adjust_btn = ghost_button("Adjust")
        adjust_btn.clicked.connect(
            lambda: self.adjust_requested.emit(snapshot.cut_id)
        )
        actions.addWidget(adjust_btn)
        # Kebab — rare actions (Rename / Delete) tucked behind ⋮ so the
        # row's primary verbs read cleanly (spec/65 §3.9).
        kebab = QToolButton()
        kebab.setObjectName("CutRowKebab")
        kebab.setText("⋮")
        kebab.setCursor(Qt.CursorShape.PointingHandCursor)
        kebab.setToolTip(tr("More actions"))
        kebab.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        kebab.setArrowType(Qt.ArrowType.NoArrow)
        menu = QMenu(kebab)
        rename_action = menu.addAction(tr("Rename…"))
        rename_action.triggered.connect(
            lambda: self.rename_requested.emit(snapshot.cut_id)
        )
        delete_action = menu.addAction(tr("Delete"))
        delete_action.triggered.connect(
            lambda: self.delete_requested.emit(snapshot.cut_id)
        )
        kebab.setMenu(menu)
        actions.addWidget(kebab)
        actions_wrap = QWidget()
        actions_wrap.setLayout(actions)
        row.addWidget(actions_wrap)

        self.layout().addLayout(row)


# ── list view ─────────────────────────────────────────────────────────


class _CutsListView(QWidget):
    """The redesigned cuts-list visual.

    The page's *content* layer — Share identity header (spec/71), the
    ``#exported`` pool card (spec/65 §3.9), and the user-Cut rows.
    Internal child of :class:`ShareCutsPage`; emits action signals the
    chassis wires to the dialog/session handlers.
    """

    back_requested = pyqtSignal()
    new_cut_requested = pyqtSignal()
    pool_open_requested = pyqtSignal()
    open_requested = pyqtSignal(str)
    adjust_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._pool = PoolSnapshot()
        self._cuts: list[CutSnapshot] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(18)

        # spec/71 — Share identity header (closed-card treatment, NOT a
        # phase colour). The Back ghost button lives inside the title
        # row of the header so the strip reads as one composition.
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        new_btn = primary_button("+ New Cut")
        new_btn.clicked.connect(self.new_cut_requested.emit)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
        header_row.addWidget(self._back, 0, Qt.AlignmentFlag.AlignTop)
        identity = _ShareIdentityHeader(
            name=tr("Share"),
            purpose=tr(
                "Assemble Cuts from the exported finals for hand-off."),
            reminder=tr(
                "Closed event · Cuts are built from the #exported pool."
            ),
        )
        header_row.addWidget(identity, 1)
        header_row.addWidget(new_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header_row)

        # Pool card slot
        self._pool_slot = QVBoxLayout()
        self._pool_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._pool_slot)

        # Section label
        self._section_label = QLabel("Cuts · 0")
        self._section_label.setObjectName("Micro")
        outer.addWidget(self._section_label)

        # Cuts list scroll
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        self._cuts_layout = QVBoxLayout(inner)
        self._cuts_layout.setContentsMargins(0, 0, 0, 0)
        self._cuts_layout.setSpacing(12)
        self._cuts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(inner)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

    # ── data API ────────────────────────────────────────────────────────

    def setForPreview(
        self,
        pool: PoolSnapshot,
        cuts: list[CutSnapshot],
    ) -> None:
        self._pool = pool
        self._cuts = list(cuts)
        self._render()

    def _render(self) -> None:
        # Rebuild pool card
        while self._pool_slot.count():
            it = self._pool_slot.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        pool_card = _PoolCard(self._pool)
        pool_card.open_requested.connect(self.pool_open_requested.emit)
        self._pool_slot.addWidget(pool_card)

        # Section label
        self._section_label.setText(f"Cuts · {len(self._cuts)}")

        # Cuts list — clear EVERY item (rows AND the trailing stretch
        # left by the previous render). Without this the stretch
        # accumulates one per refresh; AlignTop alone doesn't stop
        # Preferred-policy children from filling the scroll viewport,
        # so the trailing stretch is what keeps the rows at the top.
        while self._cuts_layout.count():
            it = self._cuts_layout.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        for snap in self._cuts:
            row = CutRow(snap)
            row.open_requested.connect(self.open_requested.emit)
            row.adjust_requested.connect(self.adjust_requested.emit)
            row.rename_requested.connect(self.rename_requested.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            self._cuts_layout.addWidget(row)
        self._cuts_layout.addStretch(1)


# ── chassis — the page MainWindow mounts ──────────────────────────────


class ShareCutsPage(QWidget):
    """Surface 09 — the Share state of a closed event (spec/66/68).

    The host MainWindow mounts at ``_CURATE_PAGE_KEY``. Three doors lead
    here: the **Share menu** (gated to closed events only via
    ``_SURFACE_CLOSED_EVENT``), the **closed-tile body click** on the
    events list (``_open_event_cuts_list``), and **menu → New Cut…**
    (which lands here and immediately opens the New Cut dialog).

    Internally a list ↔ detail ↔ session stack:

    * :class:`_CutsListView` — the redesigned landing visual.
    * :class:`CutDetailPage` — the flat-grid Cut detail (legacy, kept
      until the spec/61 program rebuilds it).
    * :class:`CutSessionPage` — the New Cut / Adjust picking session
      (legacy, ditto).

    Lifecycle mirrors the other phase hosts (``open_event(event_id) ->
    bool`` + ``closed`` signal) so MainWindow's routing pattern stays
    one shape across surfaces.
    """

    closed = pyqtSignal()

    def __init__(
        self,
        gateway: Gateway,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ShareCutsPage")
        self.gateway = gateway
        self._eg = None
        self._event_id: Optional[str] = None
        self._session_page: Optional[CutSessionPage] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        # The redesigned list view (Share identity header + #exported pool
        # card + Cut rows with the spec/65 §3.9 kebab on rare actions).
        self.list_page = _CutsListView()
        self.list_page.back_requested.connect(self._on_back)
        self.list_page.new_cut_requested.connect(self._on_new_cut)
        self.list_page.open_requested.connect(self._on_open_cut)
        self.list_page.adjust_requested.connect(self._on_adjust_cut)
        self.list_page.rename_requested.connect(self._on_rename_cut)
        self.list_page.delete_requested.connect(self._on_delete_cut)
        self._stack.addWidget(self.list_page)
        self.detail_page = CutDetailPage(show_export=True, show_play=True)
        self.detail_page.back_requested.connect(self._on_detail_back)
        self.detail_page.adjust_requested.connect(self._on_adjust_cut)
        self.detail_page.export_requested.connect(self._on_export_cut)
        self.detail_page.play_requested.connect(self._on_play_cut)
        self._stack.addWidget(self.detail_page)
        outer.addWidget(self._stack)

    # ── lifecycle (the PickPage / EditHostPage contract) ─────────────

    def open_event(self, event_id: str) -> bool:
        self._close_gateway()
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:  # noqa: BLE001
            log.exception("could not open event %s for share", event_id)
            QMessageBox.warning(
                self, tr("Share"),
                tr("This event could not be opened for Share."))
            return False
        self._event_id = event_id
        # Self-heal lost-commit Exports — backfill lineage rows for any
        # JPEGs that landed under ``Exported Media/`` but never got a
        # row written (the silent empty-ok_unit_ids fail in
        # ``ExportPage._submit_batch.commit``). #exported reads from
        # those rows, so the pool card + Cut rows reflect every shipped
        # file on the next entry. No-op when nothing is orphaned.
        try:
            n = self._eg.rescan_exported_media()
            if n:
                log.info(
                    "ShareCutsPage.open_event: backfilled %d Exported "
                    "Media lineage row(s) on entry", n)
        except Exception:  # noqa: BLE001
            log.exception(
                "ShareCutsPage: rescan_exported_media failed on entry")
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)
        return True

    def _close_gateway(self) -> None:
        self._teardown_session()
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:  # noqa: BLE001
                pass
            self._eg = None

    def _on_back(self) -> None:
        self._close_gateway()
        self.closed.emit()

    def _settings(self):
        """The LOADED Settings object. ``gateway.settings`` is the REPO
        (Nelson eyeball 2026-06-12 — attribute reads on the repo silently
        returned defaults, which killed the audio path). Loading fresh
        each read also picks up Settings-dialog changes live."""
        s = self.gateway.settings
        return s.load() if hasattr(s, "load") else s

    def _separators_on(self) -> bool:
        return bool(getattr(self._settings(), "use_separators", True))

    # ── the list ─────────────────────────────────────────────────────

    def refresh(self) -> None:
        if self._eg is None:
            return
        exported_count = len(self._eg.exported_files())
        pool = PoolSnapshot(
            exported_count=exported_count,
            sub_line=(
                f"{exported_count} exported file"
                + ("" if exported_count == 1 else "s")
                + " — the universe every cut starts from."
            ),
        )
        cuts: list[CutSnapshot] = []
        for cut in self._eg.cuts():
            totals = self._eg.cut_show_totals(cut.id)
            if not self._separators_on():
                from dataclasses import replace as _replace
                totals = _replace(totals, separator_count=0)
            count = totals.photo_count + totals.video_count
            seconds = int(totals.seconds(cut.photo_s) or 0)
            cuts.append(CutSnapshot(
                cut_id=cut.id,
                name=cut.tag or "",
                item_count=count,
                duration_seconds=seconds,
                description=(cut.music_category or ""),
                exported_date=str(cut.last_exported_at or "")[:10],
            ))
        self.list_page.setForPreview(pool, cuts)

    # ── New Cut → session ────────────────────────────────────────────

    def _dialog_kwargs(self) -> dict:
        eg = self._eg
        cut_counts = []
        for cut in eg.cuts():
            totals = eg.cut_show_totals(cut.id)
            cut_counts.append((cut.tag, totals.photo_count + totals.video_count))
        audio_path = getattr(self._settings(), "audio_library_path", "")
        categories = audio_library.list_moods(audio_path)
        if categories:
            music_hint = None                     # the dialog's default
        elif audio_path:
            music_hint = tr(
                "No category folders found in {path} — create subfolders "
                "(e.g. happy, calm) with your music inside.").replace(
                "{path}", str(audio_path))
        else:
            music_hint = tr(
                "Set the audio library folder in Settings to enable music.")
        return dict(
            existing_cuts=cut_counts,
            exported_count=len(eg.exported_files()),
            style_options=eg.cut_style_options(),
            music_categories=categories,
            music_hint=music_hint,
            pool_probe=lambda expr: len(eg.resolve_pool(expr)),
            totals_probe=lambda expr, styles, tf: eg.pool_show_totals(
                expr, style_filter=styles, type_filter=tf),
            event_label=eg.event().name,
            separators_on=self._separators_on(),
            templates=self._templates(),
            template_saver=self._save_template,
        )

    def _templates(self) -> list:
        """Saved recipes from the user-level store (spec/61 §2 + slice 10),
        exposed as flat recipe objects (card_style lifted out of extras).
        Graceful absence when the host gateway carries no user store."""
        us = getattr(self.gateway, "user_store", None)
        if us is None:
            return []
        try:
            import json
            from types import SimpleNamespace
            from mira.user_store import models as um
            out = []
            for t in us.all(um.CutTemplate):
                try:
                    card = json.loads(t.extras_json).get("card_style", "black")
                except (ValueError, TypeError):
                    card = "black"
                out.append(SimpleNamespace(
                    name=t.name,
                    pool_expr_json=t.pool_expr_json,
                    style_filter_json=t.style_filter_json,
                    type_filter=t.type_filter,
                    default_state=t.default_state,
                    target_s=t.target_s, max_s=t.max_s,
                    photo_s=t.photo_s,
                    music_category=t.music_category,
                    card_style=card,
                ))
            return out
        except Exception:  # noqa: BLE001 — templates are a convenience
            log.exception("could not load cut templates")
            return []

    def _save_template(self, name: str, draft) -> None:
        us = getattr(self.gateway, "user_store", None)
        if us is None:
            return
        import json
        import uuid
        from datetime import datetime, timezone
        from mira.user_store import models as um
        us.upsert(um.CutTemplate(
            id=uuid.uuid4().hex,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            pool_expr_json=json.dumps([list(t) for t in draft.pool_expr]),
            style_filter_json=json.dumps(list(draft.style_filter)),
            type_filter=draft.type_filter,
            default_state=draft.default_state,
            target_s=draft.target_s,
            max_s=draft.max_s,
            photo_s=draft.photo_s,
            music_category=draft.music_category,
            extras_json=json.dumps(
                {"card_style": getattr(draft, "card_style", "black")}),
        ))

    def start_new_cut(self) -> None:
        """Public entry — the Share menu's "New Cut…" lands here after
        the shell opened the event."""
        self._on_new_cut()

    def _on_new_cut(self) -> None:
        if self._eg is None:
            return
        if not self._eg.exported_files():
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("New Cut"))
            box.setText(tr(
                "Nothing has been exported in this event yet — Cuts are "
                "built from exported finals. Export some photos in Edit "
                "first."))
            box.exec()
            return
        dlg = NewCutDialog(parent=self, **self._dialog_kwargs())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        session = CutSession.from_draft(
            self._eg, dlg.draft(), separators_on=self._separators_on())
        self._start_session(session)

    def _on_adjust_cut(self, cut_id: str) -> None:
        """Adjust = back through the DIALOG first (Nelson 2026-06-12):
        every setting editable — name, pool, filters, times, music,
        cards — then Start enters the session seeded from membership,
        where the picks themselves change. Save Cut commits both."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        from types import SimpleNamespace
        prefill = SimpleNamespace(
            name=cut.tag,
            pool_expr_json=cut.pool_expr_json,
            style_filter_json=cut.style_filter_json,
            type_filter=cut.type_filter,
            default_state=cut.default_state,
            target_s=cut.target_s, max_s=cut.max_s,
            photo_s=cut.photo_s,
            music_category=cut.music_category,
            card_style=eg.cut_card_style(cut),
        )
        kwargs = self._dialog_kwargs()
        kwargs["existing_cuts"] = [
            (tag, n) for tag, n in kwargs["existing_cuts"] if tag != cut.tag]
        draft = self._exec_edit_dialog(prefill, kwargs)
        if draft is None:
            return
        session = CutSession.for_cut_with_draft(
            eg, cut, draft, separators_on=self._separators_on())
        self._start_session(session)

    def _exec_edit_dialog(self, prefill, kwargs):
        """The modal seam — tests stub this; the app runs the dialog.
        (A test once exec()'d the real dialog and parked a window on
        Nelson's desktop for 24 minutes. Never again.)"""
        dlg = NewCutDialog(
            parent=self, prefill=prefill,
            heading_text=tr("Edit Cut"), **kwargs)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.draft()

    def _start_session(self, session: CutSession) -> None:
        self._teardown_session()
        page = CutSessionPage(
            self._eg, session, event_root=self._eg.event_root)
        page.finished.connect(self._on_session_done)
        page.cancelled.connect(self._on_session_done_nothing)
        self._session_page = page
        self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)

    def _teardown_session(self) -> None:
        if self._session_page is not None:
            self._stack.removeWidget(self._session_page)
            self._session_page.deleteLater()
            self._session_page = None

    def _on_session_done(self, _cut) -> None:
        self._teardown_session()
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)

    def _on_session_done_nothing(self) -> None:
        self._teardown_session()
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)

    # ── row actions ──────────────────────────────────────────────────

    def _on_open_cut(self, cut_id: str) -> None:
        cut = self._eg.cut(cut_id) if self._eg else None
        if cut is None:
            return
        self.detail_page.show_cut(
            self._eg, cut,
            separators_on=self._separators_on(),
            aspect=getattr(self._settings(), "separator_aspect", "16:9"))
        self._stack.setCurrentWidget(self.detail_page)

    def _on_detail_back(self) -> None:
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)

    def _on_play_cut(self, cut_id: str) -> None:
        """Play all (spec/61 §5.4): the full-screen rehearsal — photos
        timed, clips true-length, separators in, music underneath."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        from mira.shared.cut_session import show_entries
        from mira.ui.shared.cut_play import CutPlayerDialog
        entries = show_entries(eg, cut, separators_on=self._separators_on())
        if not entries:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Play"))
            box.setText(tr("This Cut has no members yet — Adjust it and "
                           "pick some files first."))
            box.exec()
            return
        music = []
        if cut.music_category:
            root = getattr(self._settings(), "audio_library_path", "")
            tracks = [
                t for t in audio_library.scan_library(Path(root))
                if t.kind is audio_library.AudioKind.MUSIC
                and t.mood == cut.music_category
            ] if root else []
            totals = eg.cut_show_totals(cut.id)
            if not self._separators_on():
                from dataclasses import replace as _replace
                totals = _replace(totals, separator_count=0)
            music = audio_library.build_playlist(
                tracks, totals.seconds(cut.photo_s))
        aspect = getattr(self._settings(), "separator_aspect", "16:9")
        card_style = eg.cut_card_style(cut)
        opener_image = None
        if self._separators_on():
            from mira.ui.shared.separator_card import (
                cut_opener_lines, render_cut_opener_image,
            )
            totals = eg.cut_show_totals(cut.id)
            opener_image = render_cut_opener_image(
                tag_text=cut_names.display_tag(cut.tag),
                lines=cut_opener_lines(cut, totals, cut.photo_s),
                aspect=aspect, height=1080,
                card_style=card_style, seed_key=cut.id)
        dlg = CutPlayerDialog(
            entries,
            event_root=Path(eg.event_root),
            photo_s=cut.photo_s,
            day_meta={d.day_number: d for d in eg.trip_days()},
            aspect=aspect,
            music_tracks=music,
            opener_image=opener_image,
            card_style=card_style,
            seed_prefix=cut.id,
            parent=self,
        )
        dlg.setWindowTitle(
            cut_names.display_tag(cut.tag) + " — " + tr("rehearsal"))
        dlg.start()
        dlg.exec()

    def _separator_writer(self, cut):
        """The export's separator renderer — the UI layer owns pixels
        (QImage), the export module owns files and order."""
        from mira.ui.shared.separator_card import render_separator_image
        eg = self._eg
        day_meta = {d.day_number: d for d in eg.trip_days()}
        aspect = getattr(self._settings(), "separator_aspect", "16:9")
        card_style = eg.cut_card_style(cut)

        def write(target: Path, day) -> None:
            meta = day_meta.get(day)
            img = render_separator_image(
                day_number=day,
                date=getattr(meta, "date", None),
                location=getattr(meta, "location", None),
                description=getattr(meta, "description", "") or "",
                aspect=aspect, height=1080,
                card_style=card_style, seed_key=f"{cut.id}:{day}")
            if not img.save(str(target), "JPG", 92):
                raise OSError(f"could not write {target}")
        return write

    def _on_export_cut(self, cut_id: str) -> None:
        """Export all (spec/61 §5.2): links + separators + audio, wait
        cursor through the work, honest summary after."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        from PyQt6.QtGui import QGuiApplication
        from mira.shared.cut_export import export_cut
        seps = self._separators_on()
        opener_writer = None
        if seps:
            from mira.ui.shared.separator_card import (
                cut_opener_lines, render_cut_opener_image,
            )
            aspect = getattr(self._settings(), "separator_aspect", "16:9")
            totals = eg.cut_show_totals(cut.id)
            lines = cut_opener_lines(cut, totals, cut.photo_s)
            tag_text = cut_names.display_tag(cut.tag)
            card_style = eg.cut_card_style(cut)
            cut_id_seed = cut.id

            def opener_writer(target: Path) -> None:  # noqa: F811
                img = render_cut_opener_image(
                    tag_text=tag_text, lines=lines,
                    aspect=aspect, height=1080,
                    card_style=card_style, seed_key=cut_id_seed)
                if not img.save(str(target), "JPG", 92):
                    raise OSError(f"could not write {target}")
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = export_cut(
                eg, cut,
                event_root=Path(eg.event_root),
                separators_on=seps,
                separator_writer=self._separator_writer(cut) if seps else None,
                opener_writer=opener_writer,
                audio_root=getattr(
                    self._settings(), "audio_library_path", "") or None,
            )
        except Exception:  # noqa: BLE001 — disk-level surprises surface honestly
            log.exception("export failed for cut %s", cut_id)
            QGuiApplication.restoreOverrideCursor()
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Export Cut"))
            box.setText(tr("The export failed — see the log for details. "
                           "Nothing in your library was touched."))
            box.exec()
            return
        QGuiApplication.restoreOverrideCursor()

        lines = [tr("Exported to {folder}").replace(
            "{folder}", str(result.folder))]
        bits = [tr("{n} files linked").replace("{n}", str(result.linked))]
        if result.copied:
            bits.append(tr("{n} copied").replace("{n}", str(result.copied)))
        if result.separators:
            bits.append(tr("{n} separator slides").replace(
                "{n}", str(result.separators)))
        if result.audio_files:
            bits.append(tr("{n} songs").replace("{n}", str(result.audio_files)))
        lines.append(" · ".join(bits))
        if result.missing:
            lines.append(tr(
                "{n} member file(s) were missing on disk and were "
                "skipped.").replace("{n}", str(len(result.missing))))
        if result.audio_short:
            lines.append(tr(
                "The '{cat}' music folder is shorter than the show — add "
                "more songs or pick another folder.").replace(
                "{cat}", str(cut.music_category)))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Export Cut"))
        box.setText("\n".join(lines))
        box.exec()
        self.refresh()

    def _on_rename_cut(self, cut_id: str) -> None:
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        dlg = _RenameCutDialog(
            cut.tag, [c.tag for c in eg.cuts()], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            eg.rename_cut(cut_id, dlg.new_name())
        except (ValueError, KeyError) as exc:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.NoIcon)
            box.setWindowTitle(tr("Rename Cut"))
            box.setText(tr("Could not rename: {why}").replace(
                "{why}", str(exc)))
            box.exec()
        self.refresh()

    def _on_delete_cut(self, cut_id: str) -> None:
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Delete Cut"))
        box.setText(tr(
            "Delete {tag}? The definition and its membership go; your "
            "files and any already-exported folders stay untouched."
        ).replace("{tag}", cut_names.display_tag(cut.tag)))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        eg.delete_cut(cut_id)
        self.refresh()
