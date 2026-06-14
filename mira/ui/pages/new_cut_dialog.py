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

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import (
    GLYPH_CROSS,
    GLYPH_CUT,
    ghost_button,
    line_input,
    pill_toggle,
    primary_button,
    select,
    tag,
    tinted_svg_pixmap,
)
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
    line = PALETTE[_palette_mode()]["line"]
    d.setStyleSheet(
        f"background: {line}; max-height: 1px; min-height: 1px;"
    )
    return d


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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Cut")
        self.setModal(True)
        self.resize(660, 880)
        self._ctx = ctx or NewCutContext()
        self._was_applied = False
        self._pool_counts: dict[str, int] = {
            name: 0 for name in (self._ctx.selected_pools or [])
        }
        for sel in (self._ctx.selected_pools or []):
            self._pool_counts[sel] = 1
        self._style_chips: dict[str, QPushButton] = {}
        self._build_ui()
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
        tile.setFixedSize(32, 32)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            f"background: {p['accent_soft']}; color: {p['accent']};"
            " border: none; border-radius: 9px;"
        )
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
        load = ghost_button("Load template…")
        h.addWidget(load)
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
        close.setStyleSheet(
            "QPushButton#DialogClose {"
            f" background: transparent;"
            f" border: 1px solid {p['line']}; border-radius: 9px;"
            "}"
            "QPushButton#DialogClose:hover {"
            f" border-color: {p['accent']};"
            "}"
        )
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
        # Add row — inline styling because this dialog's nested-QFrame
        # paint pass eats QSS bg/border on descendants intermittently
        # (the Card2 frame children stayed invisible even with WA_
        # StyledBackground). Inline makes the chip-hosts paint reliably.
        p = PALETTE[_palette_mode()]
        add_row = QHBoxLayout()
        add_row.setSpacing(10)
        add_label = QLabel("add:")
        add_label.setStyleSheet(
            f"color: {p['ink_soft']}; font-size: 12px; font-weight: 600;"
        )
        add_row.addWidget(add_label)
        for pool in self._ctx.available_pools:
            # Parent the chip_host to pool_box explicitly so the styled
            # background paints from the moment it's added to the layout
            # (without this, parenting is deferred until pool_layout adds
            # the row, and Qt has been observed to never set up the
            # paint pipeline for these chips on this dialog).
            chip_host = QFrame(self._pool_box)
            chip_host.setStyleSheet(
                f"QFrame {{"
                f"  background: {p['card2']};"
                f"  border: 1px solid {p['line']};"
                f"  border-radius: 14px;"
                "}"
            )
            ch = QHBoxLayout(chip_host)
            ch.setContentsMargins(10, 4, 6, 4)
            ch.setSpacing(6)
            label = QLabel(f"{pool.name}")
            label.setStyleSheet(
                f"color: {p['ink']}; font-size: 12px;"
                " font-weight: 600; border: none; background: transparent;"
            )
            ch.addWidget(label)
            count_lbl = QLabel(f"({pool.count})")
            count_lbl.setStyleSheet(
                f"color: {p['ink_faint']}; font-size: 12px;"
                " border: none; background: transparent;"
            )
            ch.addWidget(count_lbl)
            minus = QPushButton("−")
            minus.setFixedSize(22, 22)
            minus.setCursor(Qt.CursorShape.PointingHandCursor)
            minus.setStyleSheet(
                f"QPushButton {{"
                f" background: transparent; color: {p['ink_soft']};"
                f" border: 1px solid {p['line']}; border-radius: 11px;"
                " padding: 0; font-size: 13px; font-weight: 700;"
                "}"
                f"QPushButton:hover {{"
                f" border-color: {p['accent']}; color: {p['accent']};"
                "}"
            )
            minus.clicked.connect(lambda _c=False, n=pool.name: self._step_pool(n, -1))
            plus = QPushButton("+")
            plus.setFixedSize(22, 22)
            plus.setCursor(Qt.CursorShape.PointingHandCursor)
            plus.setStyleSheet(minus.styleSheet())
            plus.clicked.connect(lambda _c=False, n=pool.name: self._step_pool(n, +1))
            ch.addWidget(minus)
            ch.addWidget(plus)
            add_row.addWidget(chip_host)
        add_row.addStretch()
        self._pool_layout.addLayout(add_row)
        # Live summary
        self._pool_summary = QLabel("pool: 0 files")
        self._pool_summary.setStyleSheet(
            f"color: {p['ink_soft']}; font-size: 13px; font-weight: 600;"
        )
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
        media_col.addWidget(self._photos_cb)
        self._videos_cb = QCheckBox("Videos")
        self._videos_cb.setObjectName("DaysTableCheck")
        self._videos_cb.setChecked(self._ctx.include_videos)
        media_col.addWidget(self._videos_cb)
        row_sm.addLayout(style_col, 1)
        row_sm.addLayout(media_col, 1)
        v.addLayout(row_sm)

        # Match count
        self._match_label = QLabel("0 of 0 match")
        self._match_label.setStyleSheet("color: #34d399; font-weight: 700;")
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
        save_tpl = ghost_button("Save as template…")
        h.addWidget(save_tpl)
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
        p = PALETTE[_palette_mode()]
        accent = p["accent"]
        ink = p["ink"]
        ink_soft = p["ink_soft"]
        ink_faint = p["ink_faint"]
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
            hint.setStyleSheet(
                f"color: {ink_faint}; font-size: 12px; font-style: italic;"
            )
            self._selected_chips_row.addWidget(hint)
        else:
            for i, (name, mult) in enumerate(active):
                sign = "+" if mult > 0 else "−"
                if i == 0 and mult > 0:
                    pass  # implicit +
                else:
                    op = QLabel(sign)
                    op.setStyleSheet(
                        f"color: {accent}; font-size: 15px; font-weight: 800;"
                    )
                    self._selected_chips_row.addWidget(op)
                token = QLabel(name)
                token.setStyleSheet(
                    f"color: {ink}; font-size: 14px; font-weight: 700;"
                )
                self._selected_chips_row.addWidget(token)
                mult_lbl = QLabel(f"× {abs(mult)}")
                mult_lbl.setStyleSheet(
                    f"color: {ink_soft}; font-size: 12px; font-weight: 600;"
                )
                self._selected_chips_row.addWidget(mult_lbl)
        self._selected_chips_row.addStretch()

    def _refresh_pool_summary(self) -> None:
        total = 0
        for pool in self._ctx.available_pools:
            mult = self._pool_counts.get(pool.name, 0)
            total += pool.count * mult
        total = max(0, total)
        self._pool_summary.setText(f"pool: {total} files")
        self._match_label.setText(
            f"{total} of {total} match" if total > 0 else "0 of 0 match"
        )
        # The formula tokens now live inside _selected_chips_row — that
        # row is rebuilt whenever _refresh_selected_chips runs (which
        # _step_pool already calls), so no extra refresh is needed here.


    def _refresh_start_enabled(self) -> None:
        name_ok = bool(self._name_edit.text().strip()) if hasattr(self, "_name_edit") else False
        pool_ok = any(v > 0 for v in self._pool_counts.values())
        if hasattr(self, "_start_btn"):
            self._start_btn.setEnabled(name_ok and pool_ok)

    def _on_name_changed(self, t: str) -> None:
        cleaned = t.strip().lower().replace(" ", "_")
        if cleaned:
            self._name_tag_hint.setText(f"#{cleaned}")
        else:
            self._name_tag_hint.setText("(tag will preview here)")

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
