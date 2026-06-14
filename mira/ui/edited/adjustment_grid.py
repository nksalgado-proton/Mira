"""AdjustmentGrid — the aligned slider panel for the Process surface.

The shipped Process page composed N self-contained ``LabeledSlider``
widgets, each with its OWN internal layout that stacked the label
*above* the bar. Two visual defects followed (Nelson 2026-05-28):
within one slider the bar sat below the centre-line of the (full-
height) reset button + value field; and across sliders each instance
sized its own columns, so rows never aligned.

This widget fixes both **by construction**: it owns **one**
``QGridLayout`` and lays every parameter out as a single row whose
cells live in the *same* grid —

    ┌──────────┬───────────────────────────┬────────┬─────┐
    │  label   │   slider (the only stretch)│  value │  ⟲  │
    └──────────┴───────────────────────────┴────────┴─────┘
       col 0              col 1                col 2   col3

Because every row's cells share the grid's columns, labels, bars,
value fields and reset buttons all fall on the same verticals, every
row is the same height, and each row has a single vertical centre-
line (cells are centre-aligned in their grid cell). Only the slider
column stretches; the other three are fixed-width and therefore
identical across rows.

It is a *separate* widget from ``ui.base.labeled_slider`` on purpose:
the cull phase still uses ``LabeledSlider`` (peaking sensitivity) and
must not be disturbed by this Process-only relayout (docs/25 §10).

Visual treatment is QSS-only (roles ``AdjustmentLabel`` /
``AdjustmentSlider`` / ``AdjustmentValue`` / ``AdjustmentReset`` in
both themes); zero inline ``setStyleSheet``. Pure value↔tick mapping
is linear (ticks 0..``_TICKS``), matching ``LabeledSlider``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QWidget,
)

from mira.ui.i18n import tr

# Internal QSlider tick resolution — same fixed grid as LabeledSlider
# so the snap-to-step path behaves identically.
_TICKS = 1000

# Fixed widths (px) for the non-stretch columns so every row is
# identical regardless of label/value text. Tuned for the Process
# parameter set (labels up to "Highlights", values up to "+100" /
# "+0.40 EV"). Eyeball-tunable in one place.
_VALUE_WIDTH = 64
_RESET_WIDTH = 28


@dataclass(frozen=True)
class AdjustmentSpec:
    """One parameter row. Domain values (LRC vocabulary), not ticks.

    ``hint`` is the photography-domain tooltip text the row shows on
    hover. When empty, the row falls back to the generic mechanic
    ("Adjust {label}. Drag, type a value, or ⟲ to reset.") — fine for
    surfaces that don't have anything more useful to say, but every
    Process-page tone slider should carry a real one. See the audit
    memo ``[[backlog_audit_all_hints]]``."""

    key: str
    label: str
    minimum: float
    maximum: float
    default: float
    step: float = 1.0
    decimals: int = 0
    suffix: str = ""
    hint: str = ""


def _clampf(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class _Row:
    """Mutable per-row state + widget handles."""

    spec: AdjustmentSpec
    label: QLabel
    slider: QSlider
    field: QLineEdit
    reset: QPushButton
    value: float
    guard: bool = False


class AdjustmentGrid(QWidget):
    """An aligned panel of adjustment sliders.

    Emits :pyattr:`valueChanged` ``(key, value)`` on any user-driven
    change (drag, field edit, reset). Programmatic
    :meth:`set_value` / :meth:`set_values` default to **not** emitting
    so photo-load / AUTO-repopulate don't echo back into the render
    loop.
    """

    valueChanged = pyqtSignal(str, float)

    def __init__(
        self,
        specs: Sequence[AdjustmentSpec],
        *,
        columns: int = 1,
        header_widgets: Optional[Sequence[Optional[QWidget]]] = None,
        parent: QWidget | None = None,
    ) -> None:
        """``columns`` lays the parameters out in that many side-by-side
        column-groups (each group is the 4-cell label|slider|value|reset
        block). The six tone sliders use ``columns=3`` for a 3×2 grid;
        a single column is the default.

        ``header_widgets`` (optional) is a sequence of length ``columns``;
        each entry is a widget placed on **row 0** of that column-group,
        spanning all 4 cells (label|slider|value|reset). Parameters then
        skip the occupied slots when laying out left-to-right. Use this
        to mount a custom widget alongside a parameter on the first
        row — e.g. the tone grid puts Strength in (row=0, col-group=0)
        and a ``Style + AUTO`` header widget in (row=0, col-group=1)
        so the 6 tone sliders below align with Strength's columns.
        Pass ``None`` for a column-group with no header.
        """
        super().__init__(parent)
        self.setObjectName("AdjustmentGrid")
        if columns < 1:
            raise ValueError("columns must be ≥ 1")
        if header_widgets is not None and len(header_widgets) != columns:
            raise ValueError(
                f"header_widgets must have length == columns ({columns}); "
                f"got {len(header_widgets)}")
        self._rows: dict[str, _Row] = {}

        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        # 4 px vertical spacing (was 9) — Nelson 2026-06-09 compaction
        # pass; with the merged 4-row tone grid the previous 9-px gaps
        # added up to 27 px of pure breathing room which made the panel
        # taller than it needed to be. 4 still reads as separated rows.
        grid.setVerticalSpacing(4)

        # Each column-group occupies 4 grid columns; a 1-col spacer
        # column separates adjacent groups so the slider of group A
        # doesn't touch the label of group B.
        cells_per_group = 4
        span = cells_per_group + 1  # + spacer
        for gi in range(columns):
            base = gi * span
            grid.setColumnStretch(base + 1, 1)          # slider stretches
            if gi < columns - 1:
                grid.setColumnMinimumWidth(base + cells_per_group, 16)

        # Place headers (row 0, span the 4 cells of their column-group).
        # Remember which slots are taken so parameter placement skips them.
        header_slots: set[tuple[int, int]] = set()
        if header_widgets:
            for gi, w in enumerate(header_widgets):
                if w is None:
                    continue
                base = gi * span
                grid.addWidget(w, 0, base, 1, cells_per_group)
                header_slots.add((0, gi))

        # Parameters fill left-to-right, top-to-bottom, **skipping** the
        # header slots so the visual order is preserved.
        slot = 0
        for spec in specs:
            row = slot // columns
            col_group = slot % columns
            while (row, col_group) in header_slots:
                slot += 1
                row = slot // columns
                col_group = slot % columns
            self._add_row(grid, spec, row, col_group * span)
            slot += 1

    # ── Construction ────────────────────────────────────────────

    def _add_row(
        self, grid: QGridLayout, spec: AdjustmentSpec, row: int, base: int,
    ) -> None:
        label = QLabel(tr(spec.label))
        label.setObjectName("AdjustmentLabel")
        label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("AdjustmentSlider")
        slider.setMinimum(0)
        slider.setMaximum(_TICKS)
        slider.setSingleStep(1)
        slider.setPageStep(max(1, _TICKS // 20))
        slider.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        field = QLineEdit()
        field.setObjectName("AdjustmentValue")
        field.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        field.setFixedWidth(_VALUE_WIDTH)
        # No input-time validator (a QDoubleValidator choked on the
        # suffix in the displayed text — " EV" / "%" — and blocked
        # digit entry). Instead `_on_field` parses + clamps + snaps on
        # commit, and restores the prior value on garbage. Typing is
        # therefore unrestricted; the value is sanitised when committed.
        # The value field IS editable (type a precise value). Click to
        # focus (not Tab — the host page owns Tab); Enter commits and
        # releases focus back to the page so keyboard shortcuts resume.
        field.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        field.returnPressed.connect(field.clearFocus)

        reset = QPushButton("⟲")
        reset.setObjectName("AdjustmentReset")
        reset.setFixedWidth(_RESET_WIDTH)
        reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # Tooltips — every interactive control carries a hint
        # (the editable-fields-need-hints standard). Prefer the spec's
        # photography-domain ``hint`` when present; fall back to the
        # generic mechanic for any spec that hasn't been written up yet.
        hint = (tr(spec.hint) if spec.hint
                else tr("Adjust {p}. Drag, type a value, or ⟲ to reset.")
                .replace("{p}", tr(spec.label)))
        label.setToolTip(hint)
        slider.setToolTip(hint)
        field.setToolTip(
            tr("Type a value between {lo} and {hi}.")
            .replace("{lo}", self._fmt(spec, spec.minimum, suffix=False))
            .replace("{hi}", self._fmt(spec, spec.maximum, suffix=False)))
        reset.setToolTip(
            tr("Reset {p} to {d}.")
            .replace("{p}", tr(spec.label))
            .replace("{d}", self._fmt(spec, spec.default)))

        grid.addWidget(label, row, base + 0)
        grid.addWidget(slider, row, base + 1)
        grid.addWidget(field, row, base + 2)
        grid.addWidget(reset, row, base + 3)

        r = _Row(
            spec=spec, label=label, slider=slider, field=field,
            reset=reset, value=_clampf(spec.default, spec.minimum, spec.maximum),
        )
        self._rows[spec.key] = r

        slider.valueChanged.connect(
            lambda tick, key=spec.key: self._on_slider(key, tick))
        field.editingFinished.connect(
            lambda key=spec.key: self._on_field(key))
        reset.clicked.connect(lambda _=False, key=spec.key: self.reset(key))

        self._apply_to_widgets(r)

    # ── Public API ──────────────────────────────────────────────

    def set_slider_minimum_width(self, key: str, px: int) -> None:
        """Widen one row's slider TRACK. The slider column is the
        grid's only stretch, so the track absorbs exactly this width
        (first user: the F10 lens doubles its Sensitivity slider —
        Nelson 2026-06-12)."""
        r = self._rows.get(key)
        if r is not None:
            r.slider.setMinimumWidth(int(px))

    def keys(self) -> list[str]:
        return list(self._rows.keys())

    def value(self, key: str) -> float:
        return self._rows[key].value

    def values(self) -> dict[str, float]:
        return {k: r.value for k, r in self._rows.items()}

    def set_value(self, key: str, value: float, *, emit: bool = False) -> None:
        """Set one row's domain value (clamped + snapped). Defaults to
        **not** emitting (programmatic set)."""
        r = self._rows.get(key)
        if r is None:
            return
        v = self._snap(r.spec, _clampf(
            float(value), r.spec.minimum, r.spec.maximum))
        changed = v != r.value
        r.value = v
        self._apply_to_widgets(r)
        if emit and changed:
            self.valueChanged.emit(key, v)

    def set_values(
        self, values: dict[str, float], *, emit: bool = False,
    ) -> None:
        """Bulk set (e.g. AUTO repopulation / photo load)."""
        for k, v in values.items():
            self.set_value(k, v, emit=emit)

    def reset(self, key: str) -> None:
        """Reset one row to its configured default (emits — it is a
        user action)."""
        r = self._rows.get(key)
        if r is not None:
            self.set_value(key, r.spec.default, emit=True)

    def reset_all(self, *, emit: bool = True) -> None:
        for k, r in self._rows.items():
            self.set_value(k, r.spec.default, emit=emit)

    def set_row_visible(self, key: str, visible: bool) -> None:
        """Show/hide a single parameter row (label · slider · value field ·
        reset button). Used e.g. to hide Strength when AUTO is off — the
        rest of the grid stays visible."""
        r = self._rows.get(key)
        if r is None:
            return
        for w in (r.label, r.slider, r.field, r.reset):
            w.setVisible(bool(visible))

    # ── Internals ───────────────────────────────────────────────

    def _snap(self, spec: AdjustmentSpec, value: float) -> float:
        step = spec.step if spec.step > 0 else 1.0
        n = math.floor((value - spec.minimum) / step + 0.5)
        snapped = _clampf(spec.minimum + n * step, spec.minimum, spec.maximum)
        return round(snapped, spec.decimals)

    def _fmt(
        self, spec: AdjustmentSpec, value: float, *, suffix: bool = True,
    ) -> str:
        text = f"{value:.{spec.decimals}f}"
        return f"{text}{spec.suffix}" if suffix and spec.suffix else text

    def _apply_to_widgets(self, r: _Row) -> None:
        if r.guard:
            return
        r.guard = True
        try:
            span = (r.spec.maximum - r.spec.minimum) or 1.0
            pos = (r.value - r.spec.minimum) / span
            tick = int(round(min(1.0, max(0.0, pos)) * _TICKS))
            r.slider.setValue(tick)
            r.field.setText(self._fmt(r.spec, r.value))
        finally:
            r.guard = False

    def _on_slider(self, key: str, tick: int) -> None:
        r = self._rows[key]
        if r.guard:
            return
        span = (r.spec.maximum - r.spec.minimum) or 1.0
        raw = r.spec.minimum + (tick / float(_TICKS)) * span
        self.set_value(key, raw, emit=True)

    def _on_field(self, key: str) -> None:
        r = self._rows[key]
        if r.guard:
            return
        text = r.field.text().strip()
        if r.spec.suffix:
            text = text.replace(r.spec.suffix.strip(), "").strip()
        try:
            raw = float(text)
        except ValueError:
            self._apply_to_widgets(r)        # reject garbage, restore
            return
        self.set_value(key, raw, emit=True)


__all__ = ["AdjustmentGrid", "AdjustmentSpec"]
