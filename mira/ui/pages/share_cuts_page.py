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
    QCheckBox,
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
# spec/90 §7 Phase 5: the New Cut dialog is the two-faced
# :class:`NewRecipeDialog` widget — the Cut-face configuration (no Scope,
# no hardware filters, event-scope inventory). The handoff value is a
# :class:`CutDraft` the dialog builds via the spec/90 Phase 3 adapter
# (:func:`recipe_to_cut_draft`); the page connects to
# :attr:`NewRecipeDialog.start_requested` to receive it.
from mira.shared.recipe_store import RecipeStore
from mira.ui.pages.new_recipe_dialog import (
    FLAVOUR_CUT,
    INVENTORY_EVENT,
    NewRecipeContext,
    NewRecipeDialog,
    OperandOption,
)

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


def _expr_from_pool_dict(pool: dict) -> list:
    """Translate the legacy ``{"#name": signed_mult}`` shape (the
    ``cut_info()['pool']`` field the old dialog emitted) into the
    spec/81 DC expression shape — ordered ``[[op, operand], ...]`` with
    the base ``"exported"`` bare and any user tag typed as
    ``{"kind":"cut",...}``. Kept on the page so callers of
    :meth:`_save_dc` that still pass a ``pool`` dict (existing tests
    + back-compat) keep working after the new_cut_dialog_adapter
    retired."""
    out: list = []
    for prefixed, mult in (pool or {}).items():
        try:
            mult = int(mult)
        except (TypeError, ValueError):
            continue
        if mult == 0:
            continue
        tag = str(prefixed).lstrip("#")
        op = "+" if mult > 0 else "-"
        operand: object = tag if tag == "exported" else {
            "kind": "cut", "id": None, "tag": tag}
        for _ in range(abs(mult)):
            out.append([op, operand])
    return out


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


@dataclass(frozen=True)
class ExportChoices:
    """spec/105 §6 — the user's choices captured by
    :class:`_ExportTargetDialog`: the target folder + the two flags
    routed into :func:`mira.shared.cut_export.export_cut`. Held as
    a small frozen record so tests can stub :meth:`_exec_target_dialog`
    cleanly (mirroring the Optional[Path] seam it replaces)."""
    target: Path
    include_originals: bool = False
    copy_mode: bool = False


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
        event_root: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Export Cut"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._default_path = Path(default_path)
        # spec/105 §6 — the cross-volume notice compares the typed target
        # to the event's media volume. ``None`` (cross-event export with
        # no single event root) suppresses the notice; the cross-event
        # export already explains its multi-source nature in the
        # summary.
        self._event_root = Path(event_root) if event_root else None

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
        # spec/105 §6 — cross-volume notice. Hidden until the target
        # crosses to a different volume than the event's media (or a
        # cross-event Cut, where members span volumes by nature).
        self._volume_notice = QLabel("")
        self._volume_notice.setObjectName("PageHint")
        self._volume_notice.setWordWrap(True)
        self._volume_notice.setVisible(False)
        gbox.addWidget(self._volume_notice)
        box.addWidget(group)

        # spec/105 §3+§5 — the two export options.
        options = QGroupBox(tr("Options"))
        options.setObjectName("FormFieldGroup")
        obox = QVBoxLayout(options)
        self._originals_chk = QCheckBox(
            tr("Also export the original files"))
        self._originals_chk.setToolTip(tr(
            "Place each member's source original under a "
            "'Original Media/' subfolder inside the Cut folder."))
        obox.addWidget(self._originals_chk)
        self._copy_mode_chk = QCheckBox(
            tr("Make independent copies instead of links"))
        self._copy_mode_chk.setToolTip(tr(
            "By default each show file is a hardlink to the event's "
            "bytes (instant, zero disk). Tick this to write fresh "
            "copies you can move or archive without the event."))
        obox.addWidget(self._copy_mode_chk)
        box.addWidget(options)

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
        # spec/105 §6 — cross-volume notice. Compare the typed target
        # against the event's media volume; show the "will be copied"
        # warning when they differ so the user isn't surprised by a
        # slow/space-heavy export.
        if ok and self._event_root is not None and text:
            from mira.shared.cut_export import _same_volume
            if not _same_volume(Path(text), self._event_root):
                self._volume_notice.setText(tr(
                    "These will be copied, not linked, because the "
                    "target is on a different drive than this event's "
                    "media."))
                self._volume_notice.setVisible(True)
            else:
                self._volume_notice.setVisible(False)
        else:
            self._volume_notice.setVisible(False)
        if self._ok is not None:
            self._ok.setEnabled(ok)

    def target(self) -> Path:
        return Path(self._edit.text().strip())

    def include_originals(self) -> bool:
        """spec/105 §3 — whether to copy each member's source
        original into ``<dest>/Original Media/``."""
        return self._originals_chk.isChecked()

    def copy_mode(self) -> bool:
        """spec/105 §5 — whether to force independent copies for
        media, originals AND audio (default = hardlink with cross-
        volume copy fallback)."""
        return self._copy_mode_chk.isChecked()


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
        tile.setObjectName("IconTile")
        tile.setProperty("tone", "accent")
        tile.setProperty("bordered", "true")  # accent-outlined holder (redesign.qss)
        _tile_font = tile.font()
        _tile_font.setPixelSize(22)
        tile.setFont(_tile_font)
        h.addWidget(tile)
        # Label block
        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        t = QLabel("#exported")
        t.setObjectName("CardTitle")
        title_row.addWidget(t)
        # Spec/81 §2: #exported is the event's base Collection — the
        # chip says so (spec/93 vocab — "Collection" everywhere user-
        # facing; the model is still :class:`DynamicCollection` in code).
        title_row.addWidget(tag("Base Collection"))
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

    Row height bumped 92 → 104 (Nelson 2026-06-20): the CardTitle's
    descenders (p / g / j / q / y) were clipped on a 2-line meta
    because the VBox compressed the title to fit the wrapped Sub label
    inside the 56-px content area. The 12-px bump + CardTitle's new
    4-px descender padding (assets/themes/redesign.qss) give the
    layout enough breathing room.
    """

    ROW_HEIGHT = 104

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
        # Nelson 2026-06-20 eyeball: per-row #Card border + shadow stacked
        # under the tab-content #Card2 border read as too many borders in
        # dark mode. Re-role to #ShareListRow + drop the inherited
        # shadow so each tile separates by bg tone + spacing alone; the
        # outer Card2 carries the only visible boundary.
        self.setObjectName("ShareListRow")
        self.setGraphicsEffect(None)
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
    (ON DELETE SET NULL, the freeze invariant).

    Height matches :class:`CutRow.ROW_HEIGHT` — same title-descender
    fix applies (Nelson 2026-06-20, see CutRow docstring)."""

    ROW_HEIGHT = 104

    pin_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        snapshot: "DCSnapshot",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        # Same border / shadow treatment as :class:`CutRow` — borderless
        # inside the tab-content #Card2 (Nelson 2026-06-20, see CutRow).
        self.setObjectName("ShareListRow")
        self.setGraphicsEffect(None)
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
            "Open the New Cut dialog with this Collection pre-loaded "
            "as the source."))
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
        # Flush full-width rail at the very top + content below, exactly like
        # the other surfaces (Days Grid / Editor / Picker). Share reads as a
        # closed-event STATE, not a phase, so the rail is PINK (phase="share").
        # Back moved to the shared title bar; the old badge/purpose identity
        # block is dropped for consistency (Nelson 2026-06-21).
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setProperty("phase", "share")
        self._rail.setFixedHeight(2)
        root.addWidget(self._rail)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 18, 32, 24)
        outer.setSpacing(18)
        root.addWidget(content, 1)

        # Header row — the primary action only (Back is in the title bar now).
        new_btn = primary_button("+ New Cut")
        new_btn.clicked.connect(self.new_cut_requested.emit)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(12)
        header_row.addStretch(1)
        header_row.addWidget(new_btn, 0, Qt.AlignmentFlag.AlignVCenter)
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
        self._tabs.addTab(self._build_dcs_tab(), tr("Collections"))
        outer.addWidget(self._tabs, 1)

    def _build_cuts_tab(self) -> QWidget:
        # Nelson 2026-06-20 final: wrapper box around tab content with
        # the SAME accent/purple border the #exported pool card uses —
        # not the gray Card2 hairline. Distinct object name so the
        # styling can be accent-tinted without touching the rest of
        # Card2's usages across the app.
        host = QFrame()
        host.setObjectName("ShareTabPane")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(host)
        v.setContentsMargins(16, 14, 16, 14)
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
        # Transparent viewport + inner widget so the Card2 wash shows
        # through. CRITICAL: do NOT use setStyleSheet on the scroll
        # area — it cascades to every descendant (including the
        # row's QPushButton fills) and flattens them. autoFillBackground
        # is the safe per-widget switch.
        self._scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        inner.setAutoFillBackground(False)
        self._cuts_layout = QVBoxLayout(inner)
        self._cuts_layout.setContentsMargins(0, 0, 0, 0)
        # Zero spacing: rows butt-up; the hairline on QFrame#ShareListRow
        # is the only divider (Nelson 2026-06-20 final).
        self._cuts_layout.setSpacing(0)
        self._cuts_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(inner)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        v.addWidget(self._scroll, 1)
        return host

    def _build_dcs_tab(self) -> QWidget:
        # Same #ShareTabPane wrapper as :meth:`_build_cuts_tab`
        # (accent-bordered).
        host = QFrame()
        host.setObjectName("ShareTabPane")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(host)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)

        self._dc_section_label = QLabel("Collections · 0")
        self._dc_section_label.setObjectName("Micro")
        v.addWidget(self._dc_section_label)

        self._dc_empty_hint = QLabel(tr(
            "Collections are reusable recipes — set algebra over the "
            "exported universe (and other Collections / Cuts), with "
            "optional filters. Compose one in the New Cut dialog and "
            "Save as Collection… to see it here."))
        self._dc_empty_hint.setObjectName("PageHint")
        self._dc_empty_hint.setWordWrap(True)
        v.addWidget(self._dc_empty_hint)

        self._dc_scroll = QScrollArea()
        self._dc_scroll.setWidgetResizable(True)
        self._dc_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._dc_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Same per-widget approach as the Cuts scroll (no setStyleSheet
        # cascade — see _build_cuts_tab).
        self._dc_scroll.viewport().setAutoFillBackground(False)
        dc_inner = QWidget()
        dc_inner.setAutoFillBackground(False)
        self._dcs_layout = QVBoxLayout(dc_inner)
        self._dcs_layout.setContentsMargins(0, 0, 0, 0)
        # Match the cuts list — zero spacing + hairline divider.
        self._dcs_layout.setSpacing(0)
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
            f"Collections · {len(self._dcs)}")
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
        # Back lives in the shared title bar; on_titlebar_back routes it to the
        # current sub-page (list → close event, detail/pool → back to list).
        self.uses_titlebar_back = True
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
        self.detail_page.publish_requested.connect(self._on_publish_cut)
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

    def on_titlebar_back(self) -> None:
        """Shared title-bar Back → the CURRENT sub-page's back action: the
        list closes the event (``_on_back``), detail / pool return to the list
        (``_on_detail_back`` / ``_on_pool_back``). Mirrors Days Grid's
        cluster-aware title-bar Back."""
        cur = self._stack.currentWidget()
        sig = getattr(cur, "back_requested", None)
        if sig is not None:
            sig.emit()

    def show_help(self) -> None:
        """Shared title-bar Help / F1 → the current sub-page's help if it has
        one (the Cut detail / session grids), else the global shortcuts list."""
        cur = self._stack.currentWidget()
        fn = getattr(cur, "show_help", None)
        if callable(fn):
            fn()
            return
        from mira.ui.base.shortcuts import show_global_shortcuts
        show_global_shortcuts(self)

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

    _EXPORTED_TAG = "exported"

    def _dialog_kwargs(self) -> dict:
        """Bundle the per-event facts the :class:`NewRecipeDialog` needs.
        Returns a dict the page passes both ways: tests inspect the
        kwargs to confirm the gateway feeds are wired, and the dialog
        ctor reads the context + probes off it. Kept stable across
        spec/90 Phase 4e so the test suite keeps reading
        ``existing_cuts`` / ``existing_dcs`` / ``exported_count`` /
        ``pool_probe`` / ``totals_probe`` / ``event_label`` /
        ``music_hint`` / ``music_categories`` / ``style_options``."""
        eg = self._eg
        cut_counts = []
        for cut in eg.cuts():
            totals = eg.cut_show_totals(cut.id)
            cut_counts.append((cut.tag, totals.photo_count + totals.video_count))
        # Spec/81 §2 — DCs appear in the dialog's operand picker alongside
        # base + Cuts. Each DC carries its live resolution count (recipe →
        # set, evaluated on demand). A malformed DC reads as zero — the
        # picker still lists it so the user can rewire / delete.
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
        )

    def _build_recipe_context(
        self, kwargs: dict, *, prefill: Optional[object] = None,
    ) -> NewRecipeContext:
        """Translate :meth:`_dialog_kwargs` + an optional ``prefill``
        (Edit-mode :class:`SimpleNamespace`) into a
        :class:`NewRecipeContext`. The operand inventory is base +
        DCs + Cuts in declared order; collision suffixes ``— DC`` /
        ``— Cut`` keep them distinguishable per spec/81 §2."""
        available_pools: list[OperandOption] = [
            OperandOption(
                name=f"#{self._EXPORTED_TAG}",
                count=int(kwargs.get("exported_count") or 0),
                kind="base", tag=self._EXPORTED_TAG),
        ]
        dc_rows = list(kwargs.get("existing_dcs") or ())
        cut_rows = list(kwargs.get("existing_cuts") or ())
        dc_tags = {tag for _id, tag, _n in dc_rows}
        cut_tags = {tag for tag, _n in cut_rows}
        collisions = dc_tags & cut_tags
        for dc_id, tag, n in dc_rows:
            suffix = " — Collection" if tag in collisions else ""
            available_pools.append(OperandOption(
                name=f"#{tag}{suffix}", count=int(n or 0),
                kind="dc", id=dc_id or None, tag=tag))
        for tag, n in cut_rows:
            suffix = " — Cut" if tag in collisions else ""
            available_pools.append(OperandOption(
                name=f"#{tag}{suffix}", count=int(n or 0),
                kind="cut", tag=tag))

        ctx = NewRecipeContext(
            event_name=kwargs.get("event_label") or "",
            available_pools=available_pools,
            available_styles=list(kwargs.get("style_options") or []),
            # spec/106 — thread the music inventory + empty-state hint
            # the dialog needs to populate the soundtrack combo.
            music_categories=list(kwargs.get("music_categories") or []),
            music_hint=kwargs.get("music_hint") or None,
        )
        # Default selection — start the Source from #exported so the
        # user composes from there (matches the legacy New Cut default).
        ctx.selected_source = [(
            "or",
            OperandOption(
                name=f"#{self._EXPORTED_TAG}",
                count=int(kwargs.get("exported_count") or 0),
                kind="base", tag=self._EXPORTED_TAG),
        )]

        if prefill is not None:
            self._apply_recipe_prefill(ctx, prefill, kwargs)
            # Adjust flow → permissive Start gate (spec/90 §5.1 / Phase
            # 4e edit note): the user may be clearing the budget on a
            # Cut whose source resolves to an empty pool today, and
            # that should still save.
            ctx.is_editing = True
        return ctx

    def _apply_recipe_prefill(
        self, ctx: NewRecipeContext, prefill: object, kwargs: dict,
    ) -> None:
        """Edit-mode prefill: read a :class:`SimpleNamespace` carrying the
        legacy Cut fields (``pool_expr_json`` / ``style_filter_json`` /
        ``type_filter`` / ``default_state`` / ``target_s`` / ``max_s`` /
        ``photo_s`` / ``card_style``) and seed the equivalent dialog
        state. Anything the dialog can't honour (legacy keep-all /
        weed-out / pick-in pin-mode hint) maps onto the §1.5 sugar
        equivalent: no rules + Otherwise = pick/skip."""
        import json as _json
        name = getattr(prefill, "name", "") or ""
        if name:
            ctx.name = name

        # Source from the cut's expr_snapshot_json.
        pool_json = getattr(prefill, "pool_expr_json", None)
        if pool_json:
            try:
                expr = _json.loads(pool_json) or []
            except (TypeError, ValueError):
                expr = []
            inventory = {p.tag or p.name.lstrip("#"): p
                         for p in ctx.available_pools}
            selected: list[tuple[str, OperandOption]] = []
            op_to_join = {"+": "or", "-": "but not in", "&": "and"}
            for index, pair in enumerate(expr):
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    continue
                op, operand = pair[0], pair[1]
                if isinstance(operand, str):
                    tag = operand
                elif isinstance(operand, Mapping if False else dict):
                    tag = operand.get("tag") or ""
                else:
                    tag = ""
                if not tag:
                    continue
                hit = inventory.get(tag)
                if hit is None:
                    # Operand no longer in the inventory — synthesise a
                    # placeholder chip so the prefill is honest.
                    kind = (operand.get("kind") if isinstance(operand, dict)
                            else "base")
                    op_id = (operand.get("id") if isinstance(operand, dict)
                             else None)
                    hit = OperandOption(
                        name=f"#{tag}", kind=kind or "base",
                        tag=tag, id=op_id)
                join = "or" if index == 0 else op_to_join.get(op, "or")
                selected.append((join, hit))
            if selected:
                ctx.selected_source = selected

        # Filters.
        style_json = getattr(prefill, "style_filter_json", None)
        if style_json:
            try:
                ctx.selected_styles = list(_json.loads(style_json) or [])
            except (TypeError, ValueError):
                pass
        type_filter = getattr(prefill, "type_filter", "both") or "both"
        ctx.include_photos = type_filter in ("both", "photo")
        ctx.include_videos = type_filter in ("both", "video")

        # Otherwise verdict — spec/90 §1.5 sugar from the legacy default_state.
        state = getattr(prefill, "default_state", "skipped") or "skipped"
        ctx.otherwise = "pick" if state == "picked" else "skip"

        # Runtime presentation. spec/90 §5.1: ``has_budget`` derives from
        # whether the existing Cut carries a real bound — a Cut saved
        # with target_s=max_s=None re-opens the dialog with the
        # checkbox unchecked + spinners greyed.
        target_s = getattr(prefill, "target_s", None)
        max_s = getattr(prefill, "max_s", None)
        if isinstance(target_s, (int, float)):
            ctx.target_minutes = max(1, int(round(float(target_s) / 60)))
        if isinstance(max_s, (int, float)):
            ctx.max_minutes = max(1, int(round(float(max_s) / 60)))
        photo_s = getattr(prefill, "photo_s", None)
        if isinstance(photo_s, (int, float)):
            ctx.per_photo_seconds = max(0.1, float(photo_s))
        ctx.has_budget = (
            isinstance(target_s, (int, float))
            or isinstance(max_s, (int, float))
        )
        # spec/106 — pre-select the cut's current music category in the
        # restored combo. ``None``/``""`` means the cut had no
        # soundtrack; the combo defaults to its "No music" entry.
        music_category = getattr(prefill, "music_category", None)
        if music_category:
            ctx.music_category = str(music_category)

    def _recipe_store(self) -> Optional[RecipeStore]:
        """Construct a :class:`RecipeStore` over the app's user_store, or
        ``None`` when the host gateway carries no user store (smokes /
        unit tests without persistence).

        spec/94 Phase 1b — prefer the Gateway factory so the store is
        wired to the JSON-tree library. Falls back to the bare-SQL
        constructor when the umbrella gateway doesn't expose the
        factory (older test harnesses)."""
        gw = self.gateway
        if gw is None:
            return None
        factory = getattr(gw, "recipe_store", None)
        if callable(factory):
            try:
                return factory()
            except Exception:                          # noqa: BLE001
                log.exception("recipe_store factory failed")
                return None
        us = getattr(gw, "user_store", None)
        if us is None:
            return None
        try:
            return RecipeStore(us)
        except Exception:                              # noqa: BLE001
            log.exception("could not open recipe store")
            return None

    def _make_new_recipe_dialog(
        self,
        kwargs: dict,
        *,
        prefill: Optional[object] = None,
        heading_text: Optional[str] = None,
    ) -> NewRecipeDialog:
        """Construct the :class:`NewRecipeDialog` from page kwargs +
        prefill. Wires every probe + the recipe store; sets the window
        title when a heading override is given (Edit Cut)."""
        eg = self._eg
        ctx = self._build_recipe_context(kwargs, prefill=prefill)
        # Cut face = flavour='cut' + no scope + no hardware + event inventory
        # per spec/90 §2.1. ``recipe_probe`` returns a RecipeResolution so the
        # metrics + Start gate use the resolver's pool/seed map directly.
        dlg = NewRecipeDialog(
            flavour=FLAVOUR_CUT,
            show_scope=False,
            show_hardware=False,
            inventory_scope=INVENTORY_EVENT,
            ctx=ctx,
            pool_probe=kwargs.get("pool_probe"),
            totals_probe=kwargs.get("totals_probe"),
            recipe_probe=(lambda comp: eg.resolve_recipe(comp))
                          if eg is not None else None,
            recipe_store=self._recipe_store(),
            dc_creator=self._make_dc_creator(),
            dc_loader=self._make_dc_loader(),
            classify_placement=self._make_placement_classifier(),
            event_name_for_id=self._make_event_name_lookup(),
            recipes_tree_provider=self._make_recipes_tree_provider(),
            recipe_resolver_by_ref=self._make_recipe_resolver_by_ref(),
            parent=self,
        )
        if heading_text:
            dlg.setWindowTitle(heading_text)
        return dlg

    def _make_dc_loader(self):
        """Build the :meth:`NewRecipeDialog.dc_loader` closure for the
        Cut-face dialog (spec/90 §5). Resolves an
        :class:`OperandOption` to ``(expr, filters)`` so Load DC can
        replace the dialog's Source + Filters with the saved DC's
        contents.

        Returns ``None`` when no per-event gateway is open."""
        eg = self._eg
        if eg is None:
            return None

        def dc_loader(operand: OperandOption) -> tuple[list, dict]:
            dc = None
            if operand.id:
                dc = eg.dynamic_collection(operand.id)
            if dc is None and operand.tag:
                dc = eg.dc_by_tag(operand.tag)
            if dc is None:
                return ([], {})
            return (list(eg.dc_expr(dc)), dict(eg.dc_filters(dc)))

        return dc_loader

    def _make_dc_creator(self):
        """Build the :meth:`NewRecipeDialog.dc_creator` closure for the
        Cut-face dialog (spec/90 §5). Translates the dialog's
        ``filters_payload()`` dict back into the gateway's
        ``styles`` / ``media_type`` parameters and refreshes the page
        so the new DC lands in the DCs tab after the sub-dialog closes.

        Returns ``None`` when no per-event gateway is open — the dialog
        keeps the Save as DC button visible-but-inert in that case
        (smokes / unit tests without persistence)."""
        eg = self._eg
        if eg is None:
            return None
        page = self

        def dc_creator(name: str, expr: list, filters: dict) -> OperandOption:
            styles = list((filters or {}).get("styles") or [])
            media_type = (filters or {}).get("media_type") or "both"
            dc = eg.create_dc(
                name, expr=expr, styles=styles, media_type=media_type)
            try:
                live = eg.dc_probe(eg.dc_expr(dc), eg.dc_filters(dc))
            except Exception:                              # noqa: BLE001
                live = 0
            page.refresh()
            return OperandOption(
                name=f"#{dc.tag}",
                count=int(live or 0),
                kind="dc",
                tag=dc.tag,
                id=dc.id,
            )

        return dc_creator

    # ── spec/94 Phase 1b — placement classifier + event-name lookup ──

    def _make_placement_classifier(self):
        """Build the closure NewRecipeDialog calls on every probe to
        drive the binding badge + the spec/93 §5 placement rule.

        Walks the composition's operand closure via the gateway:
        :class:`EventGateway` resolves event-scope DC compositions for
        nested recursion; the Cut store gives us each Cut's owning
        event when it's single-event. Cross-event Cuts return ``None``
        for ``cut_event_by_ref`` so they don't introduce a binding.
        """
        eg = self._eg
        umbrella = self.gateway

        def _dc_composition_by_ref(operand):
            dc_id = operand.get("id") if isinstance(operand, dict) else None
            tag = operand.get("tag") if isinstance(operand, dict) else None
            # Event-scope DC (the one this dialog typically pins).
            if eg is not None:
                hit = (eg.dynamic_collection(dc_id) if dc_id else None) \
                    or (eg.dc_by_tag(tag) if tag else None)
                if hit is not None:
                    return {
                        "source": eg.dc_expr(hit),
                        "filters": eg.dc_filters(hit),
                    }
            # Global / cross-event DC — resolved via the file-tree
            # library_gateway when the umbrella exposes the factory.
            if umbrella is not None and hasattr(umbrella, "library_gateway"):
                try:
                    lg = umbrella.library_gateway()
                    sf = (lg.dynamic_collection(dc_id) if dc_id else None) \
                        or (lg.dc_by_tag(tag) if tag else None)
                    if sf is not None:
                        return {
                            "source": lg.dc_expr(sf),
                            "filters": lg.dc_filters(sf),
                        }
                except Exception:                       # noqa: BLE001
                    pass
            return None

        def _cut_event_by_ref(operand):
            cut_id = operand.get("id") if isinstance(operand, dict) else None
            tag = operand.get("tag") if isinstance(operand, dict) else None
            if eg is None:
                return None
            try:
                cut = (eg.cut(cut_id) if cut_id else None) \
                    or (eg.cut_by_tag(tag) if tag else None)
            except Exception:                           # noqa: BLE001
                return None
            if cut is None:
                return None
            # A cross-event Cut (no source_dc_kind / 'user' kind) doesn't
            # introduce a single-event binding (spec/93 §5).
            kind = getattr(cut, "source_dc_kind", None)
            if kind == "user":
                return None
            return getattr(eg, "event_id", "") or ""

        def _classify(composition):
            from core.placement_classifier import (
                OperandClosureContext,
                classify_placement,
            )
            return classify_placement(
                composition,
                OperandClosureContext(
                    dc_composition_by_ref=_dc_composition_by_ref,
                    cut_event_by_ref=_cut_event_by_ref,
                ),
            )

        return _classify

    def _make_recipes_tree_provider(self):
        """Return the callable that hands NewRecipeDialog the file
        library's TreeNode (spec/93 §4 / §9). Mounting the
        :class:`CascadingTreeMenu` against this provider replaces the
        flat ``_LoadRecipeDialog`` for users with a Gateway-backed
        library."""
        umbrella = self.gateway
        if umbrella is None or not hasattr(umbrella, "recipes_gateway"):
            return None
        eg = self._eg
        event_id = getattr(eg, "event_id", "") if eg is not None else ""

        def _provider():
            try:
                return umbrella.recipes_gateway.tree_for_event(event_id)
            except Exception:                           # noqa: BLE001
                return None

        return _provider

    def _make_recipe_resolver_by_ref(self):
        """Resolve a :class:`DefinitionRef` chosen via the cascading
        menu back to a Recipe-shaped object the dialog can apply."""
        umbrella = self.gateway
        if umbrella is None or not hasattr(umbrella, "recipes_gateway"):
            return None
        eg = self._eg
        event_id = getattr(eg, "event_id", "") if eg is not None else ""

        def _resolve(ref):
            try:
                resolution = umbrella.recipes_gateway.resolve(
                    ref, event_id=event_id)
            except Exception:                           # noqa: BLE001
                return None
            if resolution is None:
                return None
            # Project to a um.Recipe-shaped dataclass so the dialog's
            # existing :meth:`_apply_recipe` flow takes it as-is.
            from mira.shared.recipe_store import RecipeStore
            from core.definition_files import DefinitionFile
            df = DefinitionFile(
                id=resolution.id,
                name=resolution.name,
                kind="recipe",
                payload=dict(resolution.composition or {}),
            )
            return RecipeStore._df_to_recipe(df)

        return _resolve

    def _make_event_name_lookup(self):
        """Resolve an ``event_id`` to its human-readable name via the
        umbrella gateway's index. Returns ``""`` when the event isn't
        in the index (the binding badge falls back to an id stub)."""
        umbrella = self.gateway
        if umbrella is None:
            return None

        def _lookup(event_id):
            try:
                entry = umbrella.index.get(event_id)
            except Exception:                           # noqa: BLE001
                return ""
            if not entry:
                return ""
            return entry.get("name") or ""

        return _lookup

    def _save_dc(self, name: str, info: dict) -> None:
        """Save the dialog's current source as a Dynamic Collection
        (spec/81 §2). Accepts the legacy ``cut_info``-shaped dict so the
        existing tests (and the older "Save as DC…" footer flow) keep
        working — Phase 4e doesn't surface this in the new dialog yet
        (spec/90 §3.4's Save-as-DC seam in the picker is still a
        placeholder), but the host hook is the same.

        Raises ``ValueError`` with a ``check_tag`` code ('empty' /
        'reserved' / 'taken') on bad names so a caller can surface a
        user-friendly message; the ``cycle`` code surfaces from the
        gateway's cycle guard. The page refreshes on success so the DC
        tab shows the new DC."""
        eg = self._eg
        if eg is None:
            return
        # Prefer the typed-ref pool_expr the dialog ships; fall back to
        # the legacy signed-mult dict translation when only ``pool`` is
        # present (older callers / direct tests).
        pool_expr = info.get("pool_expr")
        if pool_expr:
            expr = [list(p) for p in pool_expr]
        else:
            expr = _expr_from_pool_dict(info.get("pool", {}))
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
        self.refresh()

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
        kwargs = self._dialog_kwargs()
        draft = self._exec_edit_dialog(prefill=None, kwargs=kwargs)
        if draft is None:
            return
        session = CutSession.from_draft(
            self._eg, draft, separators_on=self._separators_on())
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
        the recipe-context adapter reads them under those names — the
        *content* switches to the new shape."""
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
        Nelson's desktop for 24 minutes. Never again.)

        Spec/90 Phase 4e: opens :class:`NewRecipeDialog` and returns the
        :class:`CutDraft` the dialog's ``start_requested`` signal emits.
        Returns ``None`` if the user cancelled."""
        heading = tr("Edit Cut") if prefill is not None else None
        dlg = self._make_new_recipe_dialog(
            kwargs, prefill=prefill, heading_text=heading)
        drafts: list = []
        dlg.start_requested.connect(drafts.append)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return drafts[0] if drafts else None

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
        # spec/111 — the slideshow canvas aspect lives on the Cut. The
        # detail page renders separator / opener cards at the Cut's
        # aspect so the rehearsal and the export match (no more 16:9
        # cards in a 4:3 show).
        from core.cut_aspect import normalise as _normalise_aspect
        self.detail_page.show_cut(
            self._eg, cut,
            separators_on=self._separators_on(),
            aspect=_normalise_aspect(getattr(cut, "aspect", "16:9")))
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
        # spec/111 — the Cut carries the slideshow canvas aspect; the
        # Play preview matches the export's pixel dimensions so the
        # rehearsal isn't a different shape from the handoff.
        from core.cut_aspect import aspect_dimensions, normalise
        aspect = normalise(getattr(cut, "aspect", "16:9"))
        _, canvas_h = aspect_dimensions(aspect)
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
                aspect=aspect, height=canvas_h,
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

    def _pick_export_target(self, cut):
        """Spec/81 §5 + spec/105 §2 — prompt for the export target +
        options. Returns an :class:`ExportChoices` on Accept; ``None``
        on Cancel — caller bails. A test seam
        (:meth:`_exec_target_dialog`) lets tests stub the dialog
        without exec()-ing the real one.

        The default target is the volume-aware
        :func:`resolve_event_cut_target` value: when the event is on
        the same volume as ``library_root``, the Cut home is
        ``<library_root>/Cuts/<event>/<cut>/`` (one discoverable
        location, links work); when the event lives on a different
        volume (the ``event_root_abs`` escape hatch), the home is
        ``<event_root>/Cuts/<cut>/`` (the event's own volume, links
        still work). A non-blank ``cuts_export_root`` setting
        overrides both."""
        from mira.shared.cut_export import resolve_event_cut_target
        from mira.paths import library_root as _library_root_from_paths
        eg = self._eg
        if eg is None:
            return None
        try:
            event_name = eg.event().name or ""
        except Exception:                                          # noqa: BLE001
            event_name = ""
        s = self._settings()
        library_root = _library_root_from_paths()
        cuts_export_root = (
            getattr(s, "cuts_export_root", "") or "") if s else ""
        default = resolve_event_cut_target(
            event_root=Path(eg.event_root),
            event_name=event_name,
            cut_tag=cut.tag,
            library_root=library_root,
            cuts_export_root=cuts_export_root or None,
        )
        return self._exec_target_dialog(default, cut)

    def _exec_target_dialog(self, default: Path, cut):
        """The modal seam (mirrors ``_exec_edit_dialog``). Tests stub
        this; the app runs the real dialog. Returns an
        :class:`ExportChoices` on Accept, ``None`` on Cancel."""
        eg = self._eg
        event_root = Path(eg.event_root) if eg is not None else None
        dlg = _ExportTargetDialog(
            default_path=default,
            tag_display=cut_names.display_tag(cut.tag),
            event_root=event_root,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return ExportChoices(
            target=dlg.target(),
            include_originals=dlg.include_originals(),
            copy_mode=dlg.copy_mode(),
        )

    def _separator_writer(self, cut):
        """The export's separator renderer — the UI layer owns pixels
        (QImage), the export module owns files and order.

        spec/111 — the canvas aspect lives on the Cut, not on a
        per-install setting. Cards render at the Cut's
        ``(width, height)`` so cards, photos and the show canvas all
        agree."""
        from core.cut_aspect import aspect_dimensions, normalise
        from mira.ui.shared.separator_card import render_separator_image
        eg = self._eg
        day_meta = {d.day_number: d for d in eg.trip_days()}
        aspect = normalise(getattr(cut, "aspect", "16:9"))
        _, canvas_h = aspect_dimensions(aspect)
        card_style = eg.cut_card_style(cut)

        def write(target: Path, day) -> None:
            meta = day_meta.get(day)
            img = render_separator_image(
                day_number=day,
                date=getattr(meta, "date", None),
                location=getattr(meta, "location", None),
                description=getattr(meta, "description", "") or "",
                aspect=aspect, height=canvas_h,
                card_style=card_style, seed_key=f"{cut.id}:{day}")
            if not img.save(str(target), "JPG", 92):
                raise OSError(f"could not write {target}")
        return write

    def _on_publish_cut(self, cut_id: str) -> None:
        """spec/76 §B.3 — publish the event Cut to the library publish
        slot with a manifest. Re-publish overwrites the slot. The
        publish root comes from ``library_publish_root`` (settings),
        defaulting to ``<library_root>/Published/``."""
        eg = self._eg
        cut = eg.cut(cut_id) if eg else None
        if cut is None:
            return
        from mira.paths import library_root as _library_root_from_paths
        root = _library_root_from_paths()
        if root is None:
            QMessageBox.warning(
                self, tr("Publish failed"),
                tr("Mira couldn't resolve the library root — publish "
                   "needs a library to write into."))
            return
        event = eg.event()
        from mira.shared.cut_publish import (
            CutPublishError, publish_cut,
        )
        try:
            result = publish_cut(
                eg, cut,
                event_root=Path(eg.event_root),
                event_uuid=event.uuid,
                library_root_path=root,
                settings=self._settings(),
            )
        except CutPublishError as exc:
            QMessageBox.warning(self, tr("Publish failed"), str(exc))
            return
        QMessageBox.information(
            self, tr("Cut published"),
            tr("{n} frame(s) + manifest published to:\n{path}").replace(
                "{n}", str(len([
                    p for p in result.target.iterdir()
                    if p.is_file() and p.name != "manifest.json"
                ]))).replace(
                "{path}", str(result.target)))

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
        choices = self._pick_export_target(cut)
        if choices is None:
            return
        target = choices.target
        seps = self._separators_on()
        opener_writer = None
        if seps:
            from core.cut_aspect import aspect_dimensions, normalise
            from mira.ui.shared.separator_card import (
                cut_opener_lines, render_cut_opener_image,
            )
            # spec/111 — aspect lives on the Cut. The renderer takes
            # the aspect string + a canvas height (width is derived);
            # we pass the canonical pixel height so the card matches
            # the (width, height) the PTE override (spec/107) writes.
            aspect = normalise(getattr(cut, "aspect", "16:9"))
            _, canvas_h = aspect_dimensions(aspect)
            totals = eg.cut_show_totals(cut.id)
            lines = cut_opener_lines(cut, totals, cut.photo_s)
            tag_text = cut_names.display_tag(cut.tag)
            card_style = eg.cut_card_style(cut)
            cut_id_seed = cut.id

            def opener_writer(target: Path) -> None:  # noqa: F811
                img = render_cut_opener_image(
                    tag_text=tag_text, lines=lines,
                    aspect=aspect, height=canvas_h,
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
                include_originals=choices.include_originals,
                copy_mode=choices.copy_mode,
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
        # spec/105 §6 — originals counts when the user opted in.
        if result.originals_linked or result.originals_copied:
            bits.append(tr(
                "{n} originals ({linked} linked, {copied} copied)"
            ).replace("{n}", str(
                result.originals_linked + result.originals_copied))
                .replace("{linked}", str(result.originals_linked))
                .replace("{copied}", str(result.originals_copied)))
        lines.append(" · ".join(bits))
        if result.missing:
            lines.append(tr(
                "{n} member file(s) were missing on disk and were "
                "skipped.").replace("{n}", str(len(result.missing))))
        if result.missing_originals:
            lines.append(tr(
                "{n} original file(s) could not be exported "
                "(missing on disk or no source)."
            ).replace("{n}", str(len(result.missing_originals))))
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
        box.setWindowTitle(tr("Delete Collection"))
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
