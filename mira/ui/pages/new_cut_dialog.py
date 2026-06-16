"""Surface 13 — New Cut builder dialog.

Modal opened from Surface 09 (Share / Cuts) via + New Cut. Configures
and starts a new cut: name + pool composition (additive/subtractive over
named pools) + style + media types + slide cards + start state + timing
+ music.

Composition (design-system §Surface 13):
    Header:        cut icon tile · 'New Cut' + event subtitle ·
                   ghost Load template… · circular close ✕.
    Body (scroll):
      Name:        line input + hint.
      Pool:        Card2 hosting selected-pool chips + an additive 'add:'
                   row with available pools (count + ± steppers) + live
                   'pool: N files' summary.
      Style + Media type: two-column row — style pill_toggles + media
                   checkboxes (Photos / Videos).
      Match count: 'N of M match' in green.
      Slide cards / Start as: two columns of radio-style pill toggle
                   groups.
      Timing & music: 4-column row — Target / Max steppers · Per-photo
                   spin · Music select. Hint: '≈ N photo slides fit'.
    Footer:        ghost Save as template…   |   ghost Cancel · primary
                   ▶ Start (gated on Name + non-empty pool).

Public surface mirrors the surrounding cut surfaces: constructor takes
``existing_info`` (template prefill) + ``event_name``; ``cut_info()`` returns
a dict the host hands to the Cut composer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import cut_budget
from mira.ui.design import (
    GLYPH_CROSS,
    GLYPH_CUT,
    ghost_button,
    line_input,
    pill_toggle,
    primary_button,
    select,
    tinted_svg_pixmap,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"

log = logging.getLogger(__name__)


@dataclass
class PoolOption:
    """One available named pool the user can add to / subtract from."""

    name: str          # e.g. '#exported'
    count: int = 0


@dataclass
class NewCutContext:
    """Prefill data for the dialog. Templates persist this shape."""

    event_name: str = ""
    available_pools: list[PoolOption] = field(default_factory=list)
    selected_pools: list[str] = field(default_factory=list)
    # Optional explicit per-pool multipliers; overrides ``selected_pools``
    # when present. Used for Edit-mode prefill where a pool can appear
    # with a multiplier other than 1 (e.g. ``{"#exported": 1,
    # "#all_time_best_macro": -1}``). The dialog reads this in
    # ``__init__`` so the initial chip composition + formula are correct
    # without a post-build mutation (post-build mutations broke the
    # add-row chips' paint pass — Qt re-laying out the pool box after
    # widgets were already added lost their backing-store).
    selected_pool_counts: dict[str, int] = field(default_factory=dict)
    name: str = ""
    styles: list[str] = field(default_factory=list)
    selected_styles: list[str] = field(default_factory=list)
    include_photos: bool = True
    include_videos: bool = True
    slide_cards: str = "all_black"   # all_black | one_random | per_day
    start_as: str = "all_skipped"     # all_skipped | all_picked
    target_minutes: int = 10
    max_minutes: int = 12
    per_photo_seconds: float = 6.0
    music_options: list[str] = field(default_factory=lambda: ["(none)"])
    music_choice: str = "(none)"


def _micro(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("Micro")
    return lbl


def _divider() -> QFrame:
    d = QFrame()
    d.setObjectName("DialogDivider")
    return d


class _PoolChip(QFrame):
    """"Available pool" chip — ``#exported (23) [-] [+]``.

    Uses the global ``QFrame#PoolChipHost`` QSS rule (card2 bg + line
    border + 14px radius) and ``WA_StyledBackground`` so the cascade
    reaches it inside the dialog's QScrollArea + QDialog nest. Earlier
    attempts to style this inline-per-instance or via a custom
    paintEvent both failed when ``_step_pool`` was invoked before the
    first paint pass — the global QSS rule survives that path.

    Signals: ``stepped(delta)`` — +1 / -1 from the +/- click; the host
    owns the pool-counts ledger and refreshes the formula.
    """

    stepped = pyqtSignal(int)

    def __init__(self, name: str, count: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("PoolChipHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._name = name
        self._count = count
        self.setMinimumHeight(30)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 6, 4)
        h.setSpacing(6)
        name_lbl = QLabel(name, self)
        name_lbl.setObjectName("PoolChipName")
        h.addWidget(name_lbl)
        count_lbl = QLabel(f"({count})", self)
        count_lbl.setObjectName("PoolChipCount")
        h.addWidget(count_lbl)
        for label, delta in (("−", -1), ("+", +1)):
            btn = QPushButton(label, self)
            btn.setObjectName("PoolStepperBtn")
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, d=delta: self.stepped.emit(d))
            h.addWidget(btn)


def _radio_group(
    options: list[tuple[str, str]], current: str,
) -> tuple[QWidget, QButtonGroup]:
    """Vertical radio-style PillToggle group. options = [(key, label)]."""
    host = QWidget()
    v = QVBoxLayout(host)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)
    group = QButtonGroup(host)
    group.setExclusive(True)
    for key, label in options:
        chip = pill_toggle(label, checked=(key == current))
        chip.setProperty("_key", key)
        group.addButton(chip)
        v.addWidget(chip)
    return host, group


class NewCutDialog(QDialog):
    """Surface 13 — the New Cut configuration dialog."""

    def __init__(
        self,
        *,
        ctx: Optional[NewCutContext] = None,
        pool_probe: Optional[Callable[[list], int]] = None,
        totals_probe: Optional[
            Callable[[list, list, str], cut_budget.ShowTotals]
        ] = None,
        templates: Sequence[object] = (),
        template_saver: Optional[Callable[[str, dict], None]] = None,
        separators_on: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Cut")
        self.setModal(True)
        self.resize(660, 880)
        self._ctx = ctx or NewCutContext()
        self._was_applied = False
        # Live probes — when the host wires resolve_pool / pool_show_totals
        # the pool size and the match count come from the gateway on every
        # change instead of being multiplied out of the (static) declared
        # pool counts. When absent (smokes / standalone tests) the dialog
        # falls back to the signed-mult arithmetic so it still draws
        # something honest.
        self._pool_probe = pool_probe
        self._totals_probe = totals_probe
        self._separators_on = bool(separators_on)
        self._totals = cut_budget.ShowTotals()
        self._templates = list(templates or [])
        self._template_saver = template_saver
        # Seed _pool_counts BEFORE building the UI so the formula + chips
        # row paint correctly on the first paint pass. Edit-mode prefill
        # passes ``selected_pool_counts`` to express signed multipliers;
        # otherwise fall back to ``selected_pools`` with +1 per entry.
        if self._ctx.selected_pool_counts:
            self._pool_counts: dict[str, int] = dict(
                self._ctx.selected_pool_counts)
        else:
            self._pool_counts = {
                name: 1 for name in (self._ctx.selected_pools or [])
            }
        self._style_chips: dict[str, QPushButton] = {}
        self._build_ui()
        # Seed the Name field from prefill (Edit mode) before the first
        # show — same reason: keep all mutations pre-build.
        if self._ctx.name:
            self._name_edit.setText(self._ctx.name)
        self._refresh_pool_summary()
        self._refresh_start_enabled()

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header_bar())
        outer.addWidget(_divider())
        outer.addWidget(self._build_body(), 1)
        outer.addWidget(_divider())
        outer.addWidget(self._build_footer())

    def _build_header_bar(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(18, 14, 14, 14)
        h.setSpacing(12)
        p = PALETTE[_palette_mode()]
        # Accent cut tile — line-icon scissors instead of the Unicode ✂
        # the migration used. Theme-aware so the light theme picks up the
        # right accent_soft (#eceaff) instead of dark's #211f3a.
        tile = QLabel()
        tile.setObjectName("CutHeaderTile")
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setPixmap(
            tinted_svg_pixmap(GLYPH_CUT, 18, QColor(p["accent"]))
        )
        h.addWidget(tile)
        block = QHBoxLayout()
        block.setSpacing(8)
        title = QLabel("New Cut")
        title.setObjectName("CardTitle")
        block.addWidget(title)
        if self._ctx.event_name:
            sub = QLabel(f"· {self._ctx.event_name}")
            sub.setObjectName("Sub")
            block.addWidget(sub)
        block.addStretch()
        h.addLayout(block, 1)
        # Load template… — opens a popup menu of saved recipes, applies
        # the chosen one into the dialog state (pool / styles / times /
        # music / cards). Disabled when the host has no templates.
        self._load_btn = ghost_button("Load template…")
        self._load_btn.setEnabled(bool(self._templates))
        self._load_btn.setToolTip(
            tr("Pre-fill every field below from a saved template — the "
               "pool re-evaluates against THIS event's Cuts.")
            if self._templates else
            tr("No saved templates yet — configure a Cut and use \"Save "
               "as template…\" to create one."))
        self._load_btn.clicked.connect(self._on_load_template)
        h.addWidget(self._load_btn)
        # Close X — line-icon cross.svg in the 9px squircle (mockup
        # `.mh .x`). Same fix as Surfaces 02/04/etc: Unicode ✕ was
        # invisible in both themes.
        close = QPushButton()
        close.setObjectName("DialogClose")
        close.setFixedSize(30, 30)
        close.setIcon(QIcon(
            tinted_svg_pixmap(GLYPH_CROSS, 14, QColor(p["ink_soft"]))
        ))
        close.setIconSize(QSize(14, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.reject)
        h.addWidget(close)
        return host

    def _build_body(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 18, 22, 18)
        v.setSpacing(14)

        # Name
        v.addWidget(_micro("Name"))
        self._name_edit = line_input("Type a name to see its tag.")
        self._name_edit.textChanged.connect(
            lambda _t: self._refresh_start_enabled()
        )
        v.addWidget(self._name_edit)
        self._name_tag_hint = QLabel("(tag will preview here)")
        self._name_tag_hint.setObjectName("Faint")
        self._name_edit.textChanged.connect(self._on_name_changed)
        v.addWidget(self._name_tag_hint)

        # Pool. The QFrame#Card2 container the migration used wouldn't
        # paint its bg/border reliably inside this dialog's QScrollArea
        # (descendant paint pass under the styled QFrame swallowed both
        # the bg and several siblings). Flat layout inside an unwrapped
        # host renders all children consistently — the formula is the
        # box's visual signature anyway, the chrome wasn't carrying
        # weight.
        v.addWidget(_micro("Pool"))
        self._pool_box = QWidget()
        self._pool_layout = QVBoxLayout(self._pool_box)
        self._pool_layout.setContentsMargins(0, 0, 0, 0)
        self._pool_layout.setSpacing(10)
        # Formula + chips share one row. Building them as separate rows
        # inside #Card2 produced a known Qt issue where descendant
        # background painting was obscured by the QFrame's styled-bg
        # pass — the formula labels had geometry but never reached
        # screen pixels when nested deep in a layout under a parented
        # QFrame. Coexisting in one HBoxLayout dodges the problem and
        # reads as one continuous composition (the §3.13 ask).
        self._selected_chips_row = QHBoxLayout()
        self._selected_chips_row.setSpacing(8)
        self._pool_layout.addLayout(self._selected_chips_row)
        # Add row — uses the _PoolChip custom-painted widget so the
        # pill bg + border paint reliably (Qt's QSS cascade on nested
        # styled QFrame children dropped these bgs inside this
        # dialog's QScrollArea + QDialog).
        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        add_label = QLabel("add:")
        add_label.setObjectName("PoolAddLabel")
        add_row.addWidget(add_label)
        for pool in self._ctx.available_pools:
            chip = _PoolChip(pool.name, pool.count, self._pool_box)
            chip.stepped.connect(
                lambda delta, n=pool.name: self._step_pool(n, delta)
            )
            add_row.addWidget(chip)
        add_row.addStretch()
        self._pool_layout.addLayout(add_row)
        # Live summary — sits below the add row. Same custom-paint
        # workaround would apply but a plain QLabel with inline color
        # paints fine since there's no bg/border to render.
        self._pool_summary = QLabel("pool: 0 files")
        self._pool_summary.setObjectName("PoolSummary")
        self._pool_layout.addWidget(self._pool_summary)
        v.addWidget(self._pool_box)
        self._refresh_selected_chips()

        # Style + Media type (two columns)
        row_sm = QHBoxLayout()
        row_sm.setSpacing(14)
        style_col = QVBoxLayout()
        style_col.setSpacing(6)
        style_col.addWidget(_micro("Style"))
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        for style in (self._ctx.styles or []):
            chip = pill_toggle(style, checked=(style in self._ctx.selected_styles))
            chip.toggled.connect(lambda _c: self._refresh_pool_summary())
            self._style_chips[style] = chip
            chips_row.addWidget(chip)
        chips_row.addStretch()
        style_col.addLayout(chips_row)
        style_col.addWidget(QLabel("None selected = all styles"))
        style_col.itemAt(style_col.count() - 1).widget().setObjectName("Faint")
        media_col = QVBoxLayout()
        media_col.setSpacing(6)
        media_col.addWidget(_micro("Media type"))
        # Accent checkboxes — the §3.13 ask, satisfied by riding the
        # `DaysTableCheck` QSS rule (Surface 04 introduced) so the 18px
        # accent-fill tile + white check inherit consistently across the
        # redesign instead of QCheckBox's 14px native indicator.
        self._photos_cb = QCheckBox("Photos")
        self._photos_cb.setObjectName("DaysTableCheck")
        self._photos_cb.setChecked(self._ctx.include_photos)
        self._photos_cb.toggled.connect(lambda _c: self._refresh_pool_summary())
        media_col.addWidget(self._photos_cb)
        self._videos_cb = QCheckBox("Videos")
        self._videos_cb.setObjectName("DaysTableCheck")
        self._videos_cb.setChecked(self._ctx.include_videos)
        self._videos_cb.toggled.connect(lambda _c: self._refresh_pool_summary())
        media_col.addWidget(self._videos_cb)
        row_sm.addLayout(style_col, 1)
        row_sm.addLayout(media_col, 1)
        v.addLayout(row_sm)

        # Match count — N of M match, green; empty pool reads faint.
        self._match_label = QLabel("0 of 0 match")
        self._match_label.setObjectName("MatchCount")
        v.addWidget(self._match_label)

        # Slide cards + Start as (two columns radio groups)
        row_ss = QHBoxLayout()
        row_ss.setSpacing(14)
        sc_col = QVBoxLayout()
        sc_col.setSpacing(6)
        sc_col.addWidget(_micro("Slide cards"))
        sc_host, self._slide_group = _radio_group(
            [
                ("all_black", "All black"),
                ("one_random", "One random color"),
                ("per_day", "A color per day"),
            ],
            self._ctx.slide_cards,
        )
        sc_col.addWidget(sc_host)
        sa_col = QVBoxLayout()
        sa_col.setSpacing(6)
        sa_col.addWidget(_micro("Start as"))
        sa_host, self._start_group = _radio_group(
            [
                ("all_skipped", "All skipped — pick the keepers in"),
                ("all_picked",  "All picked — weed out"),
            ],
            self._ctx.start_as,
        )
        sa_col.addWidget(sa_host)
        row_ss.addLayout(sc_col, 1)
        row_ss.addLayout(sa_col, 1)
        v.addLayout(row_ss)

        # Timing & music
        v.addWidget(_micro("Timing & music"))
        timing = QHBoxLayout()
        timing.setSpacing(10)
        # Target
        timing.addWidget(self._stepper_block(
            "Target (min)", self._ctx.target_minutes, 1, 240,
            suffix=" min",
        ))
        # Max
        timing.addWidget(self._stepper_block(
            "Max (min)", self._ctx.max_minutes, 1, 480,
            suffix=" min",
        ))
        # Per photo — QDoubleSpinBox so "6.00 s" reads natively.
        timing.addWidget(self._stepper_block(
            "Per photo (s)",
            self._ctx.per_photo_seconds, 0.5, 60.0,
            decimal=True, suffix=" s",
        ))
        # Music
        music_col = QVBoxLayout()
        music_col.setSpacing(4)
        music_col.addWidget(_micro("Music"))
        self._music = select(self._ctx.music_options)
        self._music.setCurrentText(self._ctx.music_choice)
        music_col.addWidget(self._music)
        music_box = QWidget()
        music_box.setLayout(music_col)
        timing.addWidget(music_box, 1)
        v.addLayout(timing)
        self._timing_hint = QLabel(
            "≈ 99 photo slides fit the target · includes 1 day separator."
        )
        self._timing_hint.setObjectName("Faint")
        v.addWidget(self._timing_hint)

        v.addStretch()
        scroll.setWidget(inner)
        return scroll

    def _stepper_block(
        self, label: str, value, mn, mx,
        *, decimal: bool = False, suffix: str = "",
    ) -> QWidget:
        """Labelled spinner column. Integer by default; the per-photo
        block opts into a real QDoubleSpinBox (§3.13) so the value reads
        as ``6.00 s`` instead of the ugly ``60  (×0.1 s)`` workaround."""
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_micro(label))
        spin: QSpinBox | QDoubleSpinBox
        if decimal:
            spin = QDoubleSpinBox()
            spin.setObjectName("DesignSpin")
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setRange(float(mn), float(mx))
            spin.setValue(float(value))
        else:
            spin = QSpinBox()
            spin.setObjectName("DesignSpin")
            spin.setRange(int(mn), int(mx))
            spin.setValue(int(value))
        if suffix:
            spin.setSuffix(suffix)
        v.addWidget(spin)
        host.setProperty("_spin", spin)
        return host

    def _build_footer(self) -> QWidget:
        host = QWidget()
        h = QHBoxLayout(host)
        h.setContentsMargins(22, 14, 22, 14)
        h.setSpacing(10)
        # Save as template… — disabled until the host wires a saver.
        self._save_tpl_btn = ghost_button("Save as template…")
        self._save_tpl_btn.setEnabled(self._template_saver is not None)
        self._save_tpl_btn.setToolTip(tr(
            "Save these choices — pool, filters, default, times, music — "
            "as a reusable recipe for any event."))
        self._save_tpl_btn.clicked.connect(self._on_save_template)
        h.addWidget(self._save_tpl_btn)
        h.addStretch()
        cancel = ghost_button("Cancel")
        cancel.clicked.connect(self.reject)
        h.addWidget(cancel)
        self._start_btn = primary_button("▶ Start")
        self._start_btn.clicked.connect(self._on_start)
        h.addWidget(self._start_btn)
        return host

    # ── pool ──────────────────────────────────────────────────────────

    def _step_pool(self, name: str, delta: int) -> None:
        cur = self._pool_counts.get(name, 0)
        new = max(-3, min(3, cur + delta))
        if new == 0:
            self._pool_counts.pop(name, None)
        else:
            self._pool_counts[name] = new
        self._refresh_selected_chips()
        self._refresh_pool_summary()
        self._refresh_start_enabled()

    def _refresh_selected_chips(self) -> None:
        """Rebuild the pool composition row — formula tokens (algebraic
        view) followed by the click-to-remove chips. One row keeps the
        formula reliably painted (a nested under-#Card2 host obscured
        it) and reads as a single continuous composition."""
        while self._selected_chips_row.count():
            it = self._selected_chips_row.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        # Stable order — available pools in declared order, then any
        # template-only pool the user dragged in.
        ordered = [pp.name for pp in self._ctx.available_pools
                   if pp.name in self._pool_counts]
        for name in self._pool_counts:
            if name not in ordered:
                ordered.append(name)
        active = [
            (name, int(self._pool_counts[name])) for name in ordered
            if int(self._pool_counts[name]) != 0
        ]
        # Formula tokens — operators tinted accent, terms in ink with
        # ink_soft multipliers. §3.13's "composition should read as a
        # formula."
        if not active:
            hint = QLabel("compose the pool below")
            hint.setObjectName("PoolFormulaHint")
            self._selected_chips_row.addWidget(hint)
        else:
            for i, (name, mult) in enumerate(active):
                sign = "+" if mult > 0 else "−"
                if i == 0 and mult > 0:
                    pass  # implicit +
                else:
                    op = QLabel(sign)
                    op.setObjectName("PoolFormulaOp")
                    self._selected_chips_row.addWidget(op)
                token = QLabel(name)
                token.setObjectName("PoolFormulaTerm")
                self._selected_chips_row.addWidget(token)
                mult_lbl = QLabel(f"× {abs(mult)}")
                mult_lbl.setObjectName("PoolFormulaMult")
                self._selected_chips_row.addWidget(mult_lbl)
        self._selected_chips_row.addStretch()

    def _pool_expr(self) -> list:
        """The signed-mult ``_pool_counts`` flattened into the
        ``[(op, tag), ...]`` shape ``resolve_pool`` / ``pool_show_totals``
        expect — one entry per unit of multiplier, ASCII operators
        (the gateway raises ``ValueError`` on anything else)."""
        out: list = []
        for prefixed in self._pool_counts:
            mult = int(self._pool_counts[prefixed])
            if mult == 0:
                continue
            t = prefixed.lstrip("#")
            op = "+" if mult > 0 else "-"
            for _ in range(abs(mult)):
                out.append((op, t))
        return out

    def _active_style_filter(self) -> list:
        return [s for s, chip in self._style_chips.items() if chip.isChecked()]

    def _active_type_filter(self) -> Optional[str]:
        ph, vi = self._photos_cb.isChecked(), self._videos_cb.isChecked()
        if ph and vi:
            return "both"
        if ph:
            return "photo"
        if vi:
            return "video"
        return None

    def _refresh_pool_summary(self) -> None:
        expr = self._pool_expr()
        # Pool size — the count of files the algebra resolves to, BEFORE
        # filters. ``pool_probe`` gives the live gateway answer; without
        # it we fall back to the signed-mult arithmetic so the dialog
        # still draws an honest number in smokes / standalone tests.
        if self._pool_probe is not None:
            try:
                pool_n = int(self._pool_probe([list(t) for t in expr]))
            except Exception:                                  # noqa: BLE001
                log.exception("pool_probe raised — falling back to mult")
                pool_n = self._fallback_pool_count()
        else:
            pool_n = self._fallback_pool_count()
        self._pool_summary.setText(f"pool: {pool_n} files")
        # Match count — what's left AFTER the style + media-type filters.
        tf = self._active_type_filter()
        if tf is None:
            self._totals = cut_budget.ShowTotals()
            self._match_label.setText("pick photos, videos, or both")
            self._match_label.setProperty("state", "empty")
        elif self._totals_probe is not None:
            try:
                self._totals = self._totals_probe(
                    [list(t) for t in expr],
                    self._active_style_filter(), tf)
            except Exception:                                  # noqa: BLE001
                log.exception("totals_probe raised — empty totals")
                self._totals = cut_budget.ShowTotals()
            match_n = self._totals.photo_count + self._totals.video_count
            self._match_label.setText(f"{match_n} of {pool_n} match")
            self._match_label.setProperty(
                "state", "" if match_n > 0 else "empty")
        else:
            # No totals_probe: best-effort = full pool counts as match.
            self._totals = cut_budget.ShowTotals(photo_count=pool_n)
            self._match_label.setText(f"{pool_n} of {pool_n} match")
            self._match_label.setProperty(
                "state", "" if pool_n > 0 else "empty")
        # repolish so the state= property change actually re-applies the
        # QSS rule (Qt doesn't repaint property changes by default).
        self._match_label.style().unpolish(self._match_label)
        self._match_label.style().polish(self._match_label)
        self._refresh_start_enabled()

    def _fallback_pool_count(self) -> int:
        """Signed-mult arithmetic over the declared pool counts. Honest
        only when the active pools are disjoint (the common case for
        ``#exported`` alone); used when the host does not wire a probe."""
        total = 0
        for pool in self._ctx.available_pools:
            mult = self._pool_counts.get(pool.name, 0)
            total += pool.count * mult
        return max(0, total)

    def _refresh_start_enabled(self) -> None:
        name_ok = bool(self._name_edit.text().strip()) if hasattr(self, "_name_edit") else False
        pool_ok = any(v > 0 for v in self._pool_counts.values())
        match_ok = True
        if hasattr(self, "_totals") and self._totals_probe is not None:
            match_ok = (
                self._totals.photo_count + self._totals.video_count > 0
            )
        type_ok = self._active_type_filter() is not None if hasattr(
            self, "_photos_cb") else True
        if hasattr(self, "_start_btn"):
            self._start_btn.setEnabled(
                name_ok and pool_ok and match_ok and type_ok)

    def _on_name_changed(self, t: str) -> None:
        cleaned = t.strip().lower().replace(" ", "_")
        if cleaned:
            self._name_tag_hint.setText(f"#{cleaned}")
        else:
            self._name_tag_hint.setText("(tag will preview here)")

    # ── templates ─────────────────────────────────────────────────────
    # spec/61 §2: the recipe is replayable per event. The host owns the
    # template store; the dialog only knows how to read a recipe object
    # into its widgets and how to ask the host to persist one.

    def _on_load_template(self) -> None:
        if not self._templates:
            return
        menu = QMenu(self)
        for t in self._templates:
            menu.addAction(str(getattr(t, "name", "")),
                           lambda t=t: self._apply_template(t))
        menu.exec(self._load_btn.mapToGlobal(
            self._load_btn.rect().bottomLeft()))

    def _apply_template(self, t) -> None:
        """Pre-fill EVERY field from the recipe (all still editable).
        Unknown tags in the pool expression stay visible as chips — the
        algebra treats them as empty contributions, honestly."""
        name = str(getattr(t, "name", "") or "")
        self._name_edit.setText(name)
        try:
            expr = [tuple(it) for it in
                    json.loads(getattr(t, "pool_expr_json", "") or "[]")]
        except (ValueError, TypeError):
            expr = []
        counts: dict[str, int] = {}
        for op, tag in expr:
            key = f"#{tag}" if not tag.startswith("#") else tag
            counts[key] = counts.get(key, 0) + (1 if op == "+" else -1)
        self._pool_counts = counts
        self._refresh_selected_chips()
        try:
            styles = set(json.loads(
                getattr(t, "style_filter_json", "") or "[]"))
        except (ValueError, TypeError):
            styles = set()
        for s, chip in self._style_chips.items():
            chip.setChecked(s in styles)
        tf = getattr(t, "type_filter", "both") or "both"
        self._photos_cb.setChecked(tf in ("both", "photo"))
        self._videos_cb.setChecked(tf in ("both", "video"))
        start_as = ("all_picked"
                    if getattr(t, "default_state", "skipped") == "picked"
                    else "all_skipped")
        for b in self._start_group.buttons():
            if b.property("_key") == start_as:
                b.setChecked(True)
                break
        target_s = getattr(t, "target_s", None)
        max_s = getattr(t, "max_s", None)
        self._set_spin(0, max(1, int(round((target_s or 0) / 60))) or 1)
        self._set_spin(1, max(1, int(round((max_s or 0) / 60))) or 1)
        photo_s = getattr(t, "photo_s", None)
        if photo_s is not None:
            try:
                self._set_spin(2, float(photo_s))
            except (TypeError, ValueError):
                pass
        music_category = getattr(t, "music_category", None) or "(none)"
        # The music dropdown's "(no music)" entry uses different wording
        # across surfaces; accept either form so a template saved from
        # this dialog round-trips against the adapter (which uses "(no
        # music)" specifically) AND any older entries persisted as
        # "(none)".
        idx = self._music.findText(str(music_category))
        if idx < 0 and music_category in ("(none)", None):
            for cand in ("(no music)", "(none)"):
                idx = self._music.findText(cand)
                if idx >= 0:
                    break
        if idx >= 0:
            self._music.setCurrentIndex(idx)
        card = getattr(t, "card_style", "black") or "black"
        slide_key = {
            "black": "all_black", "single": "one_random", "multi": "per_day",
        }.get(card, "all_black")
        for b in self._slide_group.buttons():
            if b.property("_key") == slide_key:
                b.setChecked(True)
                break
        self._refresh_pool_summary()

    def _set_spin(self, idx: int, value) -> None:
        # Make sure the cached spin list is populated.
        self._spin_value(0)
        if idx < len(getattr(self, "_timing_spins", [])):
            spin = self._timing_spins[idx]
            if isinstance(spin, QDoubleSpinBox):
                spin.setValue(float(value))
            else:
                spin.setValue(int(value))

    def _on_save_template(self) -> None:
        if self._template_saver is None:
            return
        default = self._name_edit.text().strip()
        dlg = _TemplateNameDialog(default=default, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._template_saver(dlg.template_name(), self.cut_info())
        except Exception:                                      # noqa: BLE001
            log.exception("template_saver raised")

    def _on_start(self) -> None:
        self._was_applied = True
        self.accept()

    # ── output ────────────────────────────────────────────────────────

    def cut_info(self) -> dict:
        slide_cards = "all_black"
        for b in self._slide_group.buttons():
            if b.isChecked():
                slide_cards = b.property("_key")
                break
        start_as = "all_skipped"
        for b in self._start_group.buttons():
            if b.isChecked():
                start_as = b.property("_key")
                break
        selected_styles = [s for s, chip in self._style_chips.items() if chip.isChecked()]
        return {
            "name": self._name_edit.text().strip(),
            "pool": dict(self._pool_counts),
            "styles": selected_styles,
            "include_photos": self._photos_cb.isChecked(),
            "include_videos": self._videos_cb.isChecked(),
            "slide_cards": slide_cards,
            "start_as": start_as,
            "target_minutes": int(self._spin_value(0)),
            "max_minutes": int(self._spin_value(1)),
            "per_photo_seconds": float(self._spin_value(2)),
            "music": self._music.currentText(),
        }

    def _spin_value(self, idx: int) -> float:
        """Read the spin widget out of the idx-th stepper_block. Returns
        float so the per-photo QDoubleSpinBox round-trips cleanly; the
        integer steppers cast at the call site."""
        if not hasattr(self, "_timing_spins"):
            self._timing_spins = []
            for widget in self.findChildren(QWidget):
                if widget.property("_spin") is not None:
                    self._timing_spins.append(widget.property("_spin"))
        if idx < len(self._timing_spins):
            return float(self._timing_spins[idx].value())
        return 0.0

    def was_applied(self) -> bool:
        return self._was_applied


class _TemplateNameDialog(QDialog):
    """Name the template — one titled field (the form grammar), free
    text (a template name is a label, not a tag)."""

    def __init__(self, *, default: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Save as template"))
        self.setModal(True)
        self.setMinimumWidth(380)
        box = QVBoxLayout(self)
        group = QGroupBox(tr("Template name"))
        group.setObjectName("FormFieldGroup")
        gbox = QVBoxLayout(group)
        self._edit = QLineEdit(default)
        self._edit.setToolTip(tr(
            "How this recipe appears in the Load template… list."))
        self._edit.textChanged.connect(self._refresh)
        gbox.addWidget(self._edit)
        box.addWidget(group)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok, parent=self)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setObjectName("Primary")
            self._ok.setText(tr("Save"))
            self._ok.setToolTip(tr("Save the recipe under this name."))
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Don't save a template."))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self._edit.text().strip()))

    def template_name(self) -> str:
        return self._edit.text().strip()
