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
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import audio_library, cut_names
from mira.gateway import Gateway
from mira.shared.cut_session import CutSession
from mira.ui.design import (
    Card,
    ghost_button,
    primary_button,
    tag,
)
from mira.ui.i18n import tr
from mira.ui.shared.cut_detail_page import CutDetailPage
from mira.ui.shared.cut_session_page import CutSessionPage
# spec/65 §3.13 / §5.1: the New Cut dialog routes through an adapter
# that wraps the redesigned page. ``CutDraft`` lives in
# :mod:`mira.shared.cut_draft` (the dialog→session handoff shape); the
# call sites here (``NewCutDialog(...).exec()`` + ``.draft()``) are
# the same as the prior chassis used.
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


@dataclass
class DCSnapshot:
    """One Dynamic Collection row (spec/81 §2): a live recipe — formula +
    filters — that resolves to a member-file set on demand. The DC list
    surfaces these as reusable operands; pinning one (or composing on top
    of it) produces a Cut."""

    dc_id: str
    name: str             # the DC's tag (without the '#' prefix; UI prepends)
    expr_summary: str = ""    # e.g. "+#exported -#drafts"
    live_count: int = 0       # how many files this DC resolves to right now
    filters_summary: str = "" # e.g. "macro · photo"


def _fmt_duration(s: int) -> str:
    m, sec = divmod(max(0, int(s)), 60)
    return f"{m}:{sec:02d}"


def _format_dc_expr(expr) -> str:
    """A one-line, human-readable summary of a DC formula (spec/81 §2):
    operator + operand chips, e.g. ``+#exported -#drafts``. The base
    token ``"exported"`` and any DC/Cut operand surface as ``#tag``.
    Display operators are ``+`` / ``-`` / ``∩`` (∩ is the user-facing
    glyph for the resolver's ASCII ``&``)."""
    bits: list[str] = []
    op_glyph = {"+": "+", "-": "-", "&": "∩"}
    for pair in expr or ():
        try:
            op, operand = pair[0], pair[1]
        except (IndexError, TypeError):
            continue
        if isinstance(operand, str):
            tag = operand
        elif isinstance(operand, dict):
            tag = str(operand.get("tag") or "")
        else:
            continue
        if not tag:
            continue
        bits.append(f"{op_glyph.get(op, op)}#{tag}")
    return " ".join(bits)


def _format_dc_filters(filters) -> str:
    """A one-line summary of a DC's filter map: styles + media type."""
    if not isinstance(filters, dict):
        return ""
    bits: list[str] = []
    styles = filters.get("styles") or []
    if styles:
        bits.append(" + ".join(str(s) for s in styles))
    media = filters.get("media_type")
    if media and media != "both":
        bits.append(str(media))
    return " · ".join(bits)


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


# ── export-target dialog (spec/81 §5 — "defaulted, not frozen") ─────


class _ExportTargetDialog(QDialog):
    """Pick where this Cut's folder gets written.

    Spec/81 §5: the export target is **defaulted, not frozen** — the Cut
    stores no path, the default is ``<event_root>/Cuts/<tag>/``, and the
    user can override it per export. This is the user-visible surface of
    that: a read-only default line, an editable target, a Browse… button.
    OK lands on the visible value; Cancel skips the export."""

    def __init__(
        self,
        *,
        default_path: Path,
        tag_display: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Export Cut"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._default_path = Path(default_path)

        box = QVBoxLayout(self)
        # Heading + hint line — same form-grammar as _RenameCutDialog.
        heading = QLabel(tr("Export {tag} to…").replace(
            "{tag}", tag_display))
        heading.setObjectName("DialogHeading")
        box.addWidget(heading)
        hint = QLabel(tr(
            "Where the files, separators, and audio playlist will land. "
            "The default is the event's Cuts folder; change it freely."))
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        box.addWidget(hint)

        group = QGroupBox(tr("Target folder"))
        group.setObjectName("FormFieldGroup")
        gbox = QVBoxLayout(group)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._edit = QLineEdit(str(self._default_path))
        self._edit.setToolTip(tr(
            "The folder will be created if it doesn't exist."))
        self._edit.textChanged.connect(self._refresh)
        row.addWidget(self._edit, 1)
        browse = ghost_button("Browse…")
        browse.setToolTip(tr("Pick another folder."))
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        gbox.addLayout(row)
        # Tiny status line: shows whether the path resolves to a creatable
        # location. We don't pre-create — that's the export's job.
        self._status = QLabel("")
        self._status.setObjectName("PageHint")
        gbox.addWidget(self._status)
        box.addWidget(group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Export"))
            self._ok.setToolTip(tr("Start the export."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Don't export."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._refresh()

    def _on_browse(self) -> None:
        # Open the system picker rooted at the deepest existing parent of
        # the current value (the default `<event_root>/Cuts/<tag>/` may not
        # exist yet on the first export — Qt would refuse to open there).
        from PyQt6.QtWidgets import QFileDialog
        start = self._edit.text().strip() or str(self._default_path)
        cur = Path(start)
        while cur != cur.parent and not cur.exists():
            cur = cur.parent
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Choose export folder"), str(cur),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self._edit.setText(chosen)

    def _refresh(self) -> None:
        text = self._edit.text().strip()
        ok = bool(text)
        if not text:
            self._status.setText(tr("type a folder path"))
        else:
            p = Path(text)
            # Path is valid if some ancestor exists (the export will mkdir
            # the rest). Drives that don't exist are rejected.
            anc = p
            while anc != anc.parent and not anc.exists():
                anc = anc.parent
            if anc.exists():
                self._status.setText(tr(
                    "will write to {path}").replace("{path}", text))
            else:
                self._status.setText(tr(
                    "no part of {path} exists yet").replace("{path}", text))
                ok = False
        if self._ok is not None:
            self._ok.setEnabled(ok)

    def target(self) -> Path:
        return Path(self._edit.text().strip())


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
    """Accent-framed card for the #exported base DC (spec/81 §2 — the
    universe every Cut starts from). Visual chrome mirrors the
    CrossEventCutsBand pattern. Class kept named ``_PoolCard`` to
    avoid touching the ``#CrossEventBand`` QSS rule it rides on."""

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
        # Spec/81 §2: #exported is the event's base DC — the chip says so.
        title_row.addWidget(tag("Base DC"))
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
        # Open the #exported drill-down (spec/61 §1.4 cascade-aware Delete).
        btn = ghost_button("Open")
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


# ── DC row (the Dynamic Collections tab content) ─────────────────────


class DCRow(Card):
    """One Dynamic Collection row (spec/81 §2): a recipe + filters that
    resolves to a member-file set on demand. A DC is NOT playable /
    exportable on its own (spec/81 §2) — the only action available is
    "Pin → New Cut" which opens the New Cut dialog with this DC
    pre-selected as the source. Delete drops the DC; pinned Cuts survive
    (ON DELETE SET NULL, the freeze invariant)."""

    ROW_HEIGHT = 92

    pin_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        snapshot: "DCSnapshot",
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

        info = QVBoxLayout()
        info.setSpacing(4)
        name_label = QLabel(f"#{snapshot.name}" if snapshot.name else "")
        name_label.setObjectName("CardTitle")
        info.addWidget(name_label)
        meta_bits: list[str] = [
            f"<b>{snapshot.live_count}</b> files",
        ]
        if snapshot.expr_summary:
            meta_bits.append(snapshot.expr_summary)
        if snapshot.filters_summary:
            meta_bits.append(snapshot.filters_summary)
        meta = QLabel(" · ".join(meta_bits))
        meta.setObjectName("Sub")
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setWordWrap(True)
        info.addWidget(meta)
        info.addStretch()
        info_wrap = QWidget()
        info_wrap.setLayout(info)
        row.addWidget(info_wrap, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        pin_btn = primary_button("Pin → New Cut")
        pin_btn.setToolTip(tr(
            "Open the New Cut dialog with this Dynamic Collection "
            "pre-loaded as the source."))
        pin_btn.clicked.connect(
            lambda: self.pin_requested.emit(snapshot.dc_id))
        actions.addWidget(pin_btn)
        kebab = QToolButton()
        kebab.setObjectName("KebabBtn")
        kebab.setText("⋮")
        kebab.setToolTip(tr("More actions"))
        kebab.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        kebab.setArrowType(Qt.ArrowType.NoArrow)
        menu = QMenu(kebab)
        delete_action = menu.addAction(tr("Delete"))
        delete_action.triggered.connect(
            lambda: self.delete_requested.emit(snapshot.dc_id))
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
    dc_pin_requested = pyqtSignal(str)
    dc_delete_requested = pyqtSignal(str)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._pool = PoolSnapshot()
        self._cuts: list[CutSnapshot] = []
        self._dcs: list[DCSnapshot] = []
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

        # Pool card slot (the #exported card is the universe for BOTH
        # Cuts and DCs — spec/81 §2 — so it sits above the tabs, not
        # inside one of them).
        self._pool_slot = QVBoxLayout()
        self._pool_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._pool_slot)

        # Tabs: Cuts (the frozen, playable/exportable artifacts) and DCs
        # (the live recipes, spec/81 §2). The two-noun model surfaces here.
        self._tabs = QTabWidget()
        self._tabs.setObjectName("ShareTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._build_cuts_tab(), tr("Cuts"))
        self._tabs.addTab(self._build_dcs_tab(), tr("Dynamic Collections"))
        outer.addWidget(self._tabs, 1)

    def _build_cuts_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)

        self._section_label = QLabel("Cuts · 0")
        self._section_label.setObjectName("Micro")
        v.addWidget(self._section_label)

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
        v.addWidget(self._scroll, 1)
        return host

    def _build_dcs_tab(self) -> QWidget:
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 12, 0, 0)
        v.setSpacing(12)

        self._dc_section_label = QLabel("Dynamic Collections · 0")
        self._dc_section_label.setObjectName("Micro")
        v.addWidget(self._dc_section_label)

        self._dc_empty_hint = QLabel(tr(
            "Dynamic Collections are reusable recipes — set algebra over "
            "the exported universe (and other DCs / Cuts), with optional "
            "filters. Compose one in the New Cut dialog and Save as DC… "
            "to see it here."))
        self._dc_empty_hint.setObjectName("PageHint")
        self._dc_empty_hint.setWordWrap(True)
        v.addWidget(self._dc_empty_hint)

        self._dc_scroll = QScrollArea()
        self._dc_scroll.setWidgetResizable(True)
        self._dc_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._dc_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        dc_inner = QWidget()
        self._dcs_layout = QVBoxLayout(dc_inner)
        self._dcs_layout.setContentsMargins(0, 0, 0, 0)
        self._dcs_layout.setSpacing(12)
        self._dcs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._dc_scroll.setWidget(dc_inner)
        self._dc_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        v.addWidget(self._dc_scroll, 1)
        return host

    # ── data API ────────────────────────────────────────────────────────

    def setForPreview(
        self,
        pool: PoolSnapshot,
        cuts: list[CutSnapshot],
        dcs: list["DCSnapshot"] = (),
    ) -> None:
        self._pool = pool
        self._cuts = list(cuts)
        self._dcs = list(dcs or ())
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

        # DCs tab — same clear-then-rebuild pattern.
        self._dc_section_label.setText(
            f"Dynamic Collections · {len(self._dcs)}")
        self._dc_empty_hint.setVisible(not self._dcs)
        while self._dcs_layout.count():
            it = self._dcs_layout.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        for snap in self._dcs:
            row = DCRow(snap)
            row.pin_requested.connect(self.dc_pin_requested.emit)
            row.delete_requested.connect(self.dc_delete_requested.emit)
            self._dcs_layout.addWidget(row)
        self._dcs_layout.addStretch(1)


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
        self.list_page.pool_open_requested.connect(self._on_open_pool)
        self.list_page.adjust_requested.connect(self._on_adjust_cut)
        self.list_page.rename_requested.connect(self._on_rename_cut)
        self.list_page.delete_requested.connect(self._on_delete_cut)
        self.list_page.dc_pin_requested.connect(self._on_pin_dc)
        self.list_page.dc_delete_requested.connect(self._on_delete_dc)
        self._stack.addWidget(self.list_page)
        self.detail_page = CutDetailPage(show_export=True, show_play=True)
        self.detail_page.back_requested.connect(self._on_detail_back)
        self.detail_page.adjust_requested.connect(self._on_adjust_cut)
        self.detail_page.export_requested.connect(self._on_export_cut)
        self.detail_page.play_requested.connect(self._on_play_cut)
        self._stack.addWidget(self.detail_page)
        # The #exported pool detail — flat grid of every shipped file
        # with multi-select + cascade-aware Delete (spec/61 §1.4 + the
        # Nelson 2026-06-15 task: explicit deletion of exported media).
        from mira.ui.shared.dc_detail_page import DCDetailPage
        self.pool_page = DCDetailPage()
        self.pool_page.back_requested.connect(self._on_pool_back)
        self.pool_page.files_deleted.connect(self._on_pool_files_deleted)
        self._stack.addWidget(self.pool_page)
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
        dcs = self._build_dc_snapshots()
        self.list_page.setForPreview(pool, cuts, dcs)

    def _build_dc_snapshots(self) -> list[DCSnapshot]:
        """Read DCs (spec/81 §2) for the DCs tab: tag, live resolution
        count, and one-line summaries of the formula + filters. The live
        count comes from the gateway's resolver (the DC is a recipe;
        membership is computed on demand)."""
        out: list[DCSnapshot] = []
        eg = self._eg
        if eg is None:
            return out
        for dc in eg.dynamic_collections():
            expr = eg.dc_expr(dc)
            filters = eg.dc_filters(dc)
            try:
                live = eg.dc_probe(expr, filters)
            except Exception:  # noqa: BLE001 — a malformed DC counts as 0
                live = 0
            out.append(DCSnapshot(
                dc_id=dc.id,
                name=dc.tag or "",
                expr_summary=_format_dc_expr(expr),
                live_count=int(live or 0),
                filters_summary=_format_dc_filters(filters),
            ))
        return out

    # ── New Cut → session ────────────────────────────────────────────

    def _dialog_kwargs(self) -> dict:
        eg = self._eg
        cut_counts = []
        for cut in eg.cuts():
            totals = eg.cut_show_totals(cut.id)
            cut_counts.append((cut.tag, totals.photo_count + totals.video_count))
        # Spec/81 §2 — the New Cut dialog's add row offers DCs as
        # operands alongside Cuts, so a DC can be composed out of other
        # DCs (``all-time-best = best-macro + best-wildlife``). Each DC
        # carries its live resolution count (recipe → set, evaluated on
        # demand) so the chip count is honest. A malformed DC is read as
        # zero (matches the DCs tab's resilience).
        dc_rows: list[tuple[str, str, int]] = []
        for dc in eg.dynamic_collections():
            try:
                live = eg.dc_probe(eg.dc_expr(dc), eg.dc_filters(dc))
            except Exception:                              # noqa: BLE001
                live = 0
            dc_rows.append((dc.id, dc.tag, int(live or 0)))
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
            existing_dcs=dc_rows,
            exported_count=len(eg.exported_files()),
            style_options=eg.cut_style_options(),
            music_categories=categories,
            music_hint=music_hint,
            pool_probe=lambda expr: len(eg.resolve_dc(expr)),
            totals_probe=lambda expr, styles, tf: eg.dc_show_totals(
                expr, filters={"styles": list(styles), "media_type": tf}),
            event_label=eg.event().name,
            separators_on=self._separators_on(),
            templates=self._templates(),
            template_saver=self._save_template,
            dc_saver=self._save_dc,
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

    def _save_dc(self, name: str, info: dict) -> None:
        """Save the dialog's current source as a Dynamic Collection
        (spec/81 §2). Translates the dialog's ``cut_info()`` payload (the
        same shape the adapter consumes for template saves) into a
        :meth:`EventGateway.create_dc` call. Raises ``ValueError`` with
        a ``check_tag`` code ('empty' / 'reserved' / 'taken') on bad
        names so the dialog can surface a user-friendly message; the
        ``cycle`` code surfaces from the gateway's cycle guard. The page
        refreshes on success so the DC tab shows the new DC."""
        eg = self._eg
        if eg is None:
            return
        # Spec/81 §2 — prefer the typed-ref ``pool_expr`` the dialog
        # ships (kind + id + tag per operand). Falls back to the legacy
        # signed-mult dict translation only when ``pool_expr`` is
        # absent (older dialog tests / pre-spec/81 callers).
        from mira.ui.shared.new_cut_dialog_adapter import _expr_from_counts
        pool_expr = info.get("pool_expr")
        if pool_expr:
            expr = [list(p) for p in pool_expr]
        else:
            expr = [list(t) for t in _expr_from_counts(info.get("pool", {}))]
        styles = list(info.get("styles") or [])
        media_type = "both"
        if bool(info.get("include_photos", True)) and not bool(
                info.get("include_videos", True)):
            media_type = "photo"
        elif bool(info.get("include_videos", True)) and not bool(
                info.get("include_photos", True)):
            media_type = "video"
        eg.create_dc(
            name,
            expr=expr,
            styles=styles,
            media_type=media_type,
        )
        # Refresh the page so the DC tab shows the new DC.
        self.refresh()

    def _save_template(self, name: str, draft) -> None:
        """Persist the dialog's recipe to the user-level template store.

        spec/81 reshape: :class:`CutDraft` now carries ``expr`` / ``styles`` /
        ``media_type`` / ``pin_mode``. The user-store column names stay
        legacy (``pool_expr_json`` / ``style_filter_json`` / ``type_filter`` /
        ``default_state``) — that table is the user-level template schema,
        not a CutDraft field. We derive the legacy ``default_state`` from
        the new ``pin_mode`` (keep-all + weed-out start all-in → ``picked``;
        pick-in starts all-out → ``skipped``). The JSON inside
        ``pool_expr_json`` is the new operand encoding (bare ``"exported"``
        or typed ``{"kind":"cut|dc",...}`` ref) — readers handle either."""
        us = getattr(self.gateway, "user_store", None)
        if us is None:
            return
        import json
        import uuid
        from datetime import datetime, timezone
        from mira.shared.cut_draft import PIN_KEEP_ALL, PIN_WEED_OUT
        from mira.user_store import models as um
        default_state = ("picked" if draft.pin_mode in (PIN_KEEP_ALL, PIN_WEED_OUT)
                         else "skipped")
        us.upsert(um.CutTemplate(
            id=uuid.uuid4().hex,
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            pool_expr_json=json.dumps([list(t) for t in draft.expr]),
            style_filter_json=json.dumps(list(draft.styles)),
            type_filter=draft.media_type,
            default_state=default_state,
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
        every setting editable — name, source, filters, times, music,
        cards — then Start enters the session seeded from membership,
        where the picks themselves change. Save Cut commits both.

        Spec/81 reshape: a Cut no longer carries ``pool_expr_json`` /
        ``style_filter_json`` / ``type_filter`` (those columns dropped in
        migration v6→v7). The frozen formula lives on the Cut as
        ``expr_snapshot_json``; the filters live on the source DC
        (``filters_json``). The prefill keys keep their legacy names since
        the dialog adapter reads them under those names — the *content*
        switches to the new shape."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        import json
        from types import SimpleNamespace
        # Filters live on the DC now; for a Cut with no live DC (orphaned
        # via DC delete, or ad-hoc), fall back to empty filters.
        styles_json = '[]'
        type_filter = "both"
        if cut.source_dc_id:
            dc = eg.dynamic_collection(cut.source_dc_id)
            if dc is not None:
                filters = eg.dc_filters(dc)
                styles_json = json.dumps(list(filters.get("styles") or []))
                type_filter = filters.get("media_type", "both") or "both"
        prefill = SimpleNamespace(
            name=cut.tag,
            pool_expr_json=cut.expr_snapshot_json,
            style_filter_json=styles_json,
            type_filter=type_filter,
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
        self._return_to_list()

    def _on_session_done_nothing(self) -> None:
        self._return_to_list()

    def _return_to_list(self) -> None:
        """Return to the Cuts list after a session ends.

        Order matters (KI-1, Nelson 2026-06-16): make the list page
        current FIRST, then tear the session down. The reverse order
        leaves a frame where ``QStackedWidget`` has to auto-pick a new
        current widget while focus is still parked on a button inside
        the now-removed session page, and the Qt input subsystem ends
        up with focus pointing at a widget that's queued for deletion
        — clicks on the list-page Back button visually register but
        don't deliver. Refreshing the data + handing focus explicitly
        to the list-page Back button gives the input system a clean
        target before the deleteLater fires."""
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)
        # Force focus off any session-page child (which is about to be
        # deleted) and onto a live widget on the list page. The back
        # button is the natural default since the user just finished
        # a session.
        try:
            self.list_page._back.setFocus()                # noqa: SLF001
        except Exception:                                  # noqa: BLE001
            pass
        self._teardown_session()

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

    def _on_open_pool(self) -> None:
        """Open the #exported pool detail (spec/61 §1.4 — the
        cascade-aware Delete surface). Re-resolves the live ledger so
        a re-entry after a delete reflects the current ship set."""
        if self._eg is None:
            return
        self.pool_page.open_pool(self._eg)
        self._stack.setCurrentWidget(self.pool_page)

    def _on_pool_back(self) -> None:
        self.pool_page.close_event()
        self.refresh()
        self._stack.setCurrentWidget(self.list_page)

    def _on_pool_files_deleted(self, _relpaths) -> None:
        """A pool delete completed. The List view's pool card sub-line
        + every Cut's item count read from
        ``exported_files()`` / ``cut_show_totals()`` — refresh them so
        the user sees the cascade reflected on Back without an extra
        click. If a Cut session is open, ``_session_page`` lives in
        the stack and its ledger is keyed by export_relpath; reloading
        on its next entry picks up the cascade."""
        try:
            self.refresh()
        except Exception:                                          # noqa: BLE001
            log.exception(
                "ShareCutsPage: refresh after pool delete failed")

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
        # Spec/81 §3.1 — live overlays in Play. When the Cut has any
        # overlay field selected, the dialog draws ``when / where / how¹
        # / how²`` over each frame; the resolver is the same gateway
        # join the embedded export uses. In-app Play always draws live
        # regardless of the Cut's ``overlay_mode`` (embedded vs burn_in
        # only matters at export — the rehearsal previews the hand-off).
        overlay_fields = eg.cut_overlay_fields(cut)
        provenance_resolver = eg.frame_provenance if overlay_fields else None
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
            overlay_fields=overlay_fields,
            provenance_resolver=provenance_resolver,
            parent=self,
        )
        dlg.setWindowTitle(
            cut_names.display_tag(cut.tag) + " — " + tr("rehearsal"))
        dlg.start()
        dlg.exec()

    def _pick_export_target(self, cut) -> Optional[Path]:
        """Spec/81 §5 seam — prompt for the export target.

        Defaulted to ``<event_root>/Cuts/<tag>/`` (the same shape
        :func:`cut_export.default_target` produces, but we recompute here
        so the picker can show it even when the export module hasn't been
        imported yet at the call-site). Returns the picked path on Accept;
        ``None`` on Cancel — caller bails. A test seam (``_exec_target_dialog``)
        lets tests stub the dialog without exec()-ing the real one."""
        from mira.shared.cut_export import default_target
        eg = self._eg
        if eg is None:
            return None
        default = default_target(Path(eg.event_root), cut.tag)
        return self._exec_target_dialog(default, cut)

    def _exec_target_dialog(self, default: Path, cut) -> Optional[Path]:
        """The modal seam (mirrors ``_exec_edit_dialog``). Tests stub
        this; the app runs the real dialog."""
        dlg = _ExportTargetDialog(
            default_path=default,
            tag_display=cut_names.display_tag(cut.tag),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.target()

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
        cursor through the work, honest summary after.

        Spec/81 §5: the export target is the user's call — defaulted to
        ``<event_root>/Cuts/<tag>/`` (the Cut never stores a path) but
        editable per export. The picker dialog runs BEFORE any work
        starts; Cancel skips the export entirely."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        from PyQt6.QtGui import QGuiApplication
        from mira.shared.cut_export import default_target, export_cut
        target = self._pick_export_target(cut)
        if target is None:
            return
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
        # Spec/81 §3.1 — overlays. When the Cut has fields selected, feed
        # the export a provenance resolver so embedded mode writes IPTC
        # *where* tags (technical EXIF rides the file already). The
        # gateway owns the join. ``target`` is the user-picked folder
        # (spec/81 §5 — defaulted, not frozen).
        overlay_fields = eg.cut_overlay_fields(cut)
        provenance_resolver = eg.frame_provenance if overlay_fields else None
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = export_cut(
                eg, cut,
                event_root=Path(eg.event_root),
                target=target,
                separators_on=seps,
                separator_writer=self._separator_writer(cut) if seps else None,
                opener_writer=opener_writer,
                audio_root=getattr(
                    self._settings(), "audio_library_path", "") or None,
                provenance_resolver=provenance_resolver,
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

    # ── DC actions (spec/81 §2 — DC is a live recipe, not playable) ──

    def _on_pin_dc(self, dc_id: str) -> None:
        """Pin → New Cut (spec/81 §4): open the New Cut dialog with this
        DC's formula + filters pre-loaded as the source. The user picks a
        pin mode (keep-all / weed-out / pick-in) and lands in the pin
        session — the only path from a DC to a playable / exportable
        artifact."""
        eg = self._eg
        if eg is None:
            return
        dc = eg.dynamic_collection(dc_id)
        if dc is None:
            return
        import json
        from types import SimpleNamespace
        filters = eg.dc_filters(dc)
        prefill = SimpleNamespace(
            name=dc.tag,
            pool_expr_json=dc.expr_json,
            style_filter_json=json.dumps(list(filters.get("styles") or [])),
            type_filter=filters.get("media_type", "both") or "both",
            default_state="picked",
            target_s=None, max_s=None,
            photo_s=6.0,
            music_category=None,
            card_style="black",
        )
        kwargs = self._dialog_kwargs()
        draft = self._exec_edit_dialog(prefill, kwargs)
        if draft is None:
            return
        # Carry the DC link through so the resulting Cut's
        # source_dc_id points at it (the freeze invariant — spec/81 §5).
        from dataclasses import replace as _replace
        draft = _replace(draft, source_dc_id=dc_id)
        session = CutSession.from_draft(
            eg, draft, separators_on=self._separators_on())
        self._start_session(session)

    def _on_delete_dc(self, dc_id: str) -> None:
        eg = self._eg
        dc = eg.dynamic_collection(dc_id) if eg else None
        if dc is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Delete Dynamic Collection"))
        box.setText(tr(
            "Delete {tag}? The recipe goes; any Cuts pinned from it "
            "survive (their frozen membership is untouched)."
        ).replace("{tag}", cut_names.display_tag(dc.tag)))
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        eg.delete_dc(dc_id)
        self.refresh()
