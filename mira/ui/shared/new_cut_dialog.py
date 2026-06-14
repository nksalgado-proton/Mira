"""The New Cut dialog (spec/61 §2) — one dialog, then one Picker session.

The single composition point for a Cut: name (live tag preview), pool
(set algebra over existing Cuts, built with chips + tiny +/− buttons —
no raw expression typing), filters (style chips, photo/video), the
session default (all skipped / all picked), the time budget (minutes are
the truth; seconds per photo; live "≈ N slides · keep ~1 in K" hint for
photo-only pools, separators counted), and the music category (the
user's own audio-library subfolders).

Kickoff amendments baked in (spec/61 §10): no camera filter; styles
default to All (no chip checked = all styles); **Load template…** sits
at the top (disabled until the templates slice wires it).

Decoupled + testable like the ExportDialog: data and probes are
injected (``pool_probe`` / ``totals_probe`` map onto
``EventGateway.resolve_pool`` / ``pool_show_totals``), the widgets are
driven directly, and accepting snapshots a :class:`CutDraft`. Creating
the cut row + committing membership belong to the picking session
(spec/61 §2 step 7), not to this dialog — an abandoned session leaves
no orphan rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import cut_budget, cut_names
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.i18n import tr

PoolExpr = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class CutDraft:
    """Everything the dialog composes; the picking session turns it into a
    ``cut`` row + membership at Create Cut (spec/61 §2 step 7)."""

    name: str
    tag: str
    pool_expr: PoolExpr
    style_filter: Tuple[str, ...]   # () = All styles
    type_filter: str                # 'both' | 'photo' | 'video'
    default_state: str              # 'skipped' | 'picked'
    target_s: Optional[int]         # None = no limit
    max_s: Optional[int]
    photo_s: float
    music_category: Optional[str]   # None = no music
    card_style: str = "black"       # 'black' | 'single' | 'multi'


class NewCutDialog(QDialog):
    """``NewCutDialog.ask(...)`` → :class:`CutDraft` on Start, ``None`` on
    Cancel."""

    def __init__(
        self,
        *,
        existing_cuts: Sequence[Tuple[str, int]],
        exported_count: int,
        style_options: Sequence[str] = (),
        music_categories: Sequence[str] = (),
        pool_probe: Optional[Callable[[list], int]] = None,
        totals_probe: Optional[Callable[[list, list, str], cut_budget.ShowTotals]] = None,
        event_label: str = "",
        separators_on: bool = True,
        templates: Sequence[object] = (),
        template_saver: Optional[Callable[[str, "CutDraft"], None]] = None,
        music_hint: Optional[str] = None,
        prefill: Optional[object] = None,
        heading_text: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NewCutDialog")
        self.setWindowTitle(heading_text or tr("New Cut"))
        self.setModal(True)
        self.setMinimumWidth(680)

        self._existing = list(existing_cuts)
        self._exported_count = int(exported_count)
        self._pool_probe = pool_probe
        self._totals_probe = totals_probe
        self._separators_on = bool(separators_on)
        self._expr: list[Tuple[str, str]] = [("+", cut_names.EXPORTED_TAG)]
        self._totals = cut_budget.ShowTotals()
        self._snapshot: Optional[CutDraft] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        # ── Heading + Load template… ───────────────────────────────
        head = QHBoxLayout()
        heading = QLabel(heading_text or tr("New Cut"))
        heading.setObjectName("PageHeading")
        head.addWidget(heading)
        head.addStretch(1)
        if event_label:
            scope = QLabel(event_label)
            scope.setObjectName("PageHint")
            head.addWidget(scope)
        self._templates = list(templates)
        self._template_saver = template_saver
        self._load_btn = QPushButton(tr("Load template…"))
        self._load_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._load_btn.setEnabled(bool(self._templates))
        self._load_btn.setToolTip(
            tr("Pre-fill every field below from a saved template — the "
               "pool re-evaluates against THIS event's Cuts.")
            if self._templates else
            tr("No saved templates yet — configure a Cut and use "
               "\"Save as template…\" to create one."))
        self._load_btn.clicked.connect(self._on_load_template)
        head.addWidget(self._load_btn)
        outer.addLayout(head)

        # ── Name + live tag preview ────────────────────────────────
        name_group = QGroupBox(tr("Name"))
        name_group.setObjectName("FormFieldGroup")
        nbox = QVBoxLayout(name_group)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr("e.g. Best macro shots"))
        self._name_edit.setToolTip(tr(
            "Type any name — accents, spaces, capitals all fine. The tag "
            "below is what the system stores; it must be unique in this "
            "event."))
        self._name_edit.textChanged.connect(self._refresh_all)
        nbox.addWidget(self._name_edit)
        self._tag_preview = QLabel("")
        self._tag_preview.setObjectName("PageHint")
        nbox.addWidget(self._tag_preview)
        outer.addWidget(name_group)

        # ── Pool — the set algebra ─────────────────────────────────
        pool_group = QGroupBox(tr("Pool"))
        pool_group.setObjectName("FormFieldGroup")
        pbox = QVBoxLayout(pool_group)
        # Both rows wrap (FlowLayout): a long expression or many existing
        # Cuts must never overflow the dialog horizontally. House pattern:
        # the flow is the layout OF a host widget added with addWidget —
        # never addLayout-nested (geometry doesn't propagate).
        expr_host = QWidget()
        self._expr_row = FlowLayout(expr_host, spacing=6)
        self._expr_row.setContentsMargins(0, 0, 0, 0)
        pbox.addWidget(expr_host)
        add_host = QWidget()
        add_row = FlowLayout(add_host, spacing=6)
        add_row.setContentsMargins(0, 0, 0, 0)
        add_lbl = QLabel(tr("add:"))
        add_lbl.setObjectName("PageHint")
        add_row.addWidget(add_lbl)
        for tag, count in self._available_terms():
            add_row.addWidget(self._make_add_entry(tag, count))
        pbox.addWidget(add_host)
        self._pool_count = QLabel("")
        self._pool_count.setObjectName("PoolCountLabel")
        pbox.addWidget(self._pool_count)
        outer.addWidget(pool_group)

        # ── Style + Media type (one titled group per field — Nelson's
        # form-grammar rule: never label-beside-input) ──────────────
        filt_row = QHBoxLayout()
        filt_row.setSpacing(12)
        style_group = QGroupBox(tr("Style"))
        style_group.setObjectName("FormFieldGroup")
        sbox = QVBoxLayout(style_group)
        chips_host = QWidget()
        chips_flow = FlowLayout(chips_host, spacing=6)
        chips_flow.setContentsMargins(0, 0, 0, 0)
        self._style_chips: dict[str, QPushButton] = {}
        for style in style_options:
            chip = QPushButton(style)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            chip.setToolTip(tr(
                "Keep only {style} items in the pool. No style selected "
                "= all styles.").replace("{style}", style))
            chip.toggled.connect(self._refresh_all)
            self._style_chips[style] = chip
            chips_flow.addWidget(chip)
        sbox.addWidget(chips_host)
        none_hint = QLabel(tr("none selected = all styles"))
        none_hint.setObjectName("PageHint")
        sbox.addWidget(none_hint)
        filt_row.addWidget(style_group, 3)
        type_group = QGroupBox(tr("Media type"))
        type_group.setObjectName("FormFieldGroup")
        ty_box = QVBoxLayout(type_group)
        self._cb_photos = QCheckBox(tr("photos"))
        self._cb_photos.setToolTip(tr("Include photos in the pool."))
        self._cb_videos = QCheckBox(tr("videos"))
        self._cb_videos.setToolTip(tr(
            "Include video clips in the pool. A clip costs its real "
            "duration against the time budget."))
        for cb in (self._cb_photos, self._cb_videos):
            cb.setChecked(True)
            cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            cb.toggled.connect(self._refresh_all)
            ty_box.addWidget(cb)
        ty_box.addStretch(1)
        filt_row.addWidget(type_group, 1)
        outer.addLayout(filt_row)
        self._match_count = QLabel("")
        self._match_count.setObjectName("PoolCountLabel")
        outer.addWidget(self._match_count)

        # ── Slide cards (separator + opener colours) ───────────────
        cards_group = QGroupBox(tr("Slide cards"))
        cards_group.setObjectName("FormFieldGroup")
        cards_box = QHBoxLayout(cards_group)
        self._card_buttons = QButtonGroup(self)
        self._card_radios: dict[str, QRadioButton] = {}
        for style_key, label, hint in (
            ("black", tr("all black"), tr(
                "The classic: every separator and the opener on a black "
                "card.")),
            ("single", tr("one random color"), tr(
                "One color for the whole Cut — picked for this Cut and "
                "kept forever (the grid, Play and the export all show "
                "the same card).")),
            ("multi", tr("a color per day"), tr(
                "Every day card gets its own color — the funnest show. "
                "Colors are stable: the same day always wears the same "
                "color.")),
        ):
            rb = QRadioButton(label)
            rb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rb.setToolTip(hint)
            self._card_buttons.addButton(rb)
            self._card_radios[style_key] = rb
            cards_box.addWidget(rb)
        self._card_radios["black"].setChecked(True)
        cards_box.addStretch(1)
        outer.addWidget(cards_group)

        # ── Start as ───────────────────────────────────────────────
        default_group = QGroupBox(tr("Start as"))
        default_group.setObjectName("FormFieldGroup")
        dbox = QHBoxLayout(default_group)
        self._default_buttons = QButtonGroup(self)
        self._rb_skipped = QRadioButton(tr("all skipped — pick the keepers in"))
        self._rb_skipped.setToolTip(tr(
            "The session starts with nothing in the Cut; you Pick the "
            "keepers. The usual way for a short Cut from a big pool."))
        self._rb_picked = QRadioButton(tr("all picked — weed out"))
        self._rb_picked.setToolTip(tr(
            "The session starts with everything in the Cut; you Skip "
            "what doesn't earn its slot. Subtractive — good when the "
            "pool is already close to what you want."))
        for rb in (self._rb_skipped, self._rb_picked):
            rb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self._default_buttons.addButton(rb)
            dbox.addWidget(rb)
        self._rb_skipped.setChecked(True)
        dbox.addStretch(1)
        outer.addWidget(default_group)

        # ── Target / Max / Per photo / Music — one titled group per
        # input (Nelson 2026-06-12 eyeball: never label-beside-input) ─
        grid = QGridLayout()
        grid.setSpacing(12)

        self._target_spin = QSpinBox()
        self._target_spin.setRange(0, 600)
        self._target_spin.setValue(10)
        self._target_spin.setSuffix(tr(" min"))
        self._target_spin.setSpecialValueText(tr("no limit"))
        self._target_spin.setToolTip(tr(
            "The show length you are aiming for. The budget line stays "
            "green while you are at or under it. \"no limit\" = a Cut "
            "with no time pressure."))
        self._target_spin.valueChanged.connect(self._refresh_all)
        grid.addWidget(self._mk_spin_group(tr("Target time"), self._target_spin), 0, 0)

        self._max_spin = QSpinBox()
        self._max_spin.setRange(0, 600)
        self._max_spin.setValue(12)
        self._max_spin.setSuffix(tr(" min"))
        self._max_spin.setSpecialValueText(tr("no limit"))
        self._max_spin.setToolTip(tr(
            "The hard ceiling. Between target and max the budget line "
            "turns amber; past max it turns red."))
        self._max_spin.valueChanged.connect(self._refresh_all)
        grid.addWidget(self._mk_spin_group(tr("Max time"), self._max_spin), 0, 1)

        self._photo_spin = QDoubleSpinBox()
        self._photo_spin.setRange(1.0, 60.0)
        self._photo_spin.setSingleStep(0.5)
        self._photo_spin.setValue(6.0)
        self._photo_spin.setSuffix(tr(" s"))
        self._photo_spin.setToolTip(tr(
            "How long each photo stays on screen. Photos and day "
            "separators cost this many seconds; clips cost their real "
            "length."))
        self._photo_spin.valueChanged.connect(self._refresh_all)
        grid.addWidget(self._mk_spin_group(tr("Per photo"), self._photo_spin), 0, 2)

        music_group = QGroupBox(tr("Music"))
        music_group.setObjectName("FormFieldGroup")
        mbox = QVBoxLayout(music_group)
        self._music_combo = QComboBox()
        self._music_combo.addItem(tr("(no music)"), None)
        for cat in music_categories:
            self._music_combo.addItem(cat, cat)
        self._music_combo.setToolTip(tr(
            "A folder from your audio library. At export (and in-app "
            "Play) enough songs are taken from it to cover the show, "
            "plus a little trim room."))
        mbox.addWidget(self._music_combo)
        hint_text = music_hint if music_hint is not None else (
            tr("folders found in your audio library") if music_categories
            else tr("Set the audio library folder in Settings to enable music."))
        music_hint_lbl = QLabel(hint_text)
        music_hint_lbl.setObjectName("PageHint")
        music_hint_lbl.setWordWrap(True)
        mbox.addWidget(music_hint_lbl)
        if not music_categories:
            self._music_combo.setEnabled(False)
        grid.addWidget(music_group, 0, 3)

        self._budget_hint = QLabel("")
        self._budget_hint.setObjectName("PageHint")
        self._budget_hint.setWordWrap(True)
        grid.addWidget(self._budget_hint, 1, 0, 1, 4)
        outer.addLayout(grid)

        outer.addStretch(1)

        # ── Buttons ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._save_tpl_btn = QPushButton(tr("Save as template…"))
        self._save_tpl_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._save_tpl_btn.setEnabled(template_saver is not None)
        self._save_tpl_btn.setToolTip(tr(
            "Save these choices — pool, filters, default, times, music — "
            "as a reusable recipe for any event."))
        self._save_tpl_btn.clicked.connect(self._on_save_template)
        btn_row.addWidget(self._save_tpl_btn)
        btn_row.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self._start = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._start is not None:
            self._start.setObjectName("Primary")
            self._start.setText(tr("Start"))
            self._start.setDefault(True)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(tr("Close without creating a Cut."))
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        outer.addLayout(btn_row)

        self._rebuild_expr_row()
        if prefill is not None:
            self._apply_template(prefill)
        self._refresh_all()

    # ── small builders ───────────────────────────────────────────────

    @staticmethod
    def _mk_spin_group(title: str, spin: QWidget) -> QGroupBox:
        """One titled FormFieldGroup per numeric input — the house form
        grammar (never label-beside-input)."""
        group = QGroupBox(title)
        group.setObjectName("FormFieldGroup")
        box = QVBoxLayout(group)
        box.addWidget(spin)
        return group

    def _available_terms(self) -> list[Tuple[str, int]]:
        """The add-row vocabulary: #exported first, then every existing Cut."""
        return [(cut_names.EXPORTED_TAG, self._exported_count)] + self._existing

    def _make_add_entry(self, tag: str, count: int) -> QWidget:
        entry = QWidget()
        row = QHBoxLayout(entry)
        row.setContentsMargins(0, 0, 8, 0)
        row.setSpacing(2)
        lbl = QLabel(f"{cut_names.display_tag(tag)} ({count})")
        lbl.setObjectName("PoolTermChipText")
        row.addWidget(lbl)
        plus = QPushButton("+")
        plus.setObjectName("PoolAddOp")
        plus.setToolTip(tr("Add {tag} to the pool.").replace(
            "{tag}", cut_names.display_tag(tag)))
        plus.clicked.connect(lambda _=False, t=tag: self._append_term("+", t))
        minus = QPushButton("−")
        minus.setObjectName("PoolAddOp")
        minus.setToolTip(tr("Subtract {tag} from the pool.").replace(
            "{tag}", cut_names.display_tag(tag)))
        minus.clicked.connect(lambda _=False, t=tag: self._append_term("-", t))
        for b in (plus, minus):
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            row.addWidget(b)
        return entry

    # ── pool expression ──────────────────────────────────────────────

    def _append_term(self, op: str, tag: str) -> None:
        self._expr.append((op, tag))
        self._rebuild_expr_row()
        self._refresh_all()

    def _remove_term(self, index: int) -> None:
        del self._expr[index]
        self._rebuild_expr_row()
        self._refresh_all()

    def _rebuild_expr_row(self) -> None:
        while self._expr_row.count():
            item = self._expr_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._expr:
            empty = QLabel(tr("empty pool — add a Cut below"))
            empty.setObjectName("PageHint")
            self._expr_row.addWidget(empty)
            return
        for i, (op, tag) in enumerate(self._expr):
            if i > 0 or op == "-":
                # The leading op shows only when it is a minus (subtracting
                # from nothing — legal but worth seeing honestly).
                op_lbl = QLabel("−" if op == "-" else "+")
                op_lbl.setObjectName("PoolOpLabel")
                self._expr_row.addWidget(op_lbl)
            self._expr_row.addWidget(self._make_term_chip(i, tag))

    def _make_term_chip(self, index: int, tag: str) -> QFrame:
        chip = QFrame()
        chip.setObjectName("PoolTermChip")
        if tag == cut_names.EXPORTED_TAG:
            chip.setProperty("builtin", "true")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(10, 3, 6, 3)
        lay.setSpacing(6)
        text = QLabel(cut_names.display_tag(tag))
        text.setObjectName("PoolTermChipText")
        lay.addWidget(text)
        kill = QPushButton("✕")
        kill.setObjectName("PoolTermChipKill")
        kill.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        kill.setToolTip(tr("Remove this term from the pool."))
        kill.clicked.connect(lambda _=False, i=index: self._remove_term(i))
        lay.addWidget(kill)
        return chip

    # ── live state ───────────────────────────────────────────────────

    def _slug(self) -> str:
        return cut_names.slugify(self._name_edit.text())

    def _tag_error(self) -> Optional[str]:
        return cut_names.check_tag(self._slug(), [t for t, _ in self._existing])

    def _style_filter(self) -> list[str]:
        return [s for s, chip in self._style_chips.items() if chip.isChecked()]

    def _type_filter(self) -> Optional[str]:
        photos, videos = self._cb_photos.isChecked(), self._cb_videos.isChecked()
        if photos and videos:
            return "both"
        if photos:
            return "photo"
        if videos:
            return "video"
        return None  # nothing checked — invalid

    def _refresh_all(self) -> None:
        # name → tag preview
        slug, err = self._slug(), self._tag_error()
        if err == "empty":
            self._tag_preview.setText(tr("type a name to see its tag"))
        elif err == "reserved":
            self._tag_preview.setText(
                tr("tag: {tag} — reserved built-in name").replace(
                    "{tag}", cut_names.display_tag(slug)))
        elif err == "taken":
            self._tag_preview.setText(
                tr("tag: {tag} — already taken in this event").replace(
                    "{tag}", cut_names.display_tag(slug)))
        else:
            self._tag_preview.setText(
                tr("tag: {tag} — available").replace(
                    "{tag}", cut_names.display_tag(slug)))

        # pool + filter counts
        pool_n = self._probe_pool_count()
        self._pool_count.setText(
            tr("pool: {n} files").replace("{n}", str(pool_n)))
        tf = self._type_filter()
        if tf is None:
            self._totals = cut_budget.ShowTotals()
            self._match_count.setText(tr("pick photos, videos, or both"))
        else:
            self._totals = self._probe_totals(self._style_filter(), tf)
            match_n = self._totals.photo_count + self._totals.video_count
            self._match_count.setText(
                tr("{m} of {n} match").replace("{m}", str(match_n)).replace(
                    "{n}", str(pool_n)))
        self._refresh_budget_hint()
        self._refresh_start()

    def _probe_pool_count(self) -> int:
        if self._pool_probe is None:
            return 0
        try:
            return int(self._pool_probe(list(self._expr)))
        except Exception:  # noqa: BLE001 — probe is best-effort
            return 0

    def _probe_totals(self, styles: list, tf: str) -> cut_budget.ShowTotals:
        if self._totals_probe is None:
            return cut_budget.ShowTotals()
        try:
            return self._totals_probe(list(self._expr), styles, tf)
        except Exception:  # noqa: BLE001
            return cut_budget.ShowTotals()

    def _refresh_budget_hint(self) -> None:
        totals = self._totals
        target_s = self._target_s()
        sep_n = totals.separator_count if self._separators_on else 0
        parts: list[str] = []
        if totals.video_count == 0 and target_s:
            hint = cut_budget.photo_only_hint(
                totals.photo_count, sep_n, float(self._photo_spin.value()),
                target_s)
            if hint is not None:
                line = tr("≈ {n} photo slides fit the target").replace(
                    "{n}", str(hint.slides_fit))
                if hint.keep_one_in:
                    line += tr(" · keep ~1 in {k}").replace(
                        "{k}", str(hint.keep_one_in))
                parts.append(line)
        if sep_n:
            parts.append(tr("includes {d} day separators").replace(
                "{d}", str(sep_n)))
        self._budget_hint.setText(" · ".join(parts))
        self._budget_hint.setVisible(bool(parts))

    def _refresh_start(self) -> None:
        if self._start is None:
            return
        tf_ok = self._type_filter() is not None
        match_n = self._totals.photo_count + self._totals.video_count
        ok = self._tag_error() is None and tf_ok and match_n > 0
        self._start.setEnabled(ok)
        if ok:
            self._start.setToolTip(tr(
                "Open the Picker on this pool and choose what's in the Cut."))
        elif self._tag_error() is not None:
            self._start.setToolTip(tr("Fix the name first."))
        else:
            self._start.setToolTip(tr("The pool is empty — nothing to pick from."))

    # ── templates (spec/61 §2: the recipe, replayable per event) ─────

    def _on_load_template(self) -> None:
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        for t in self._templates:
            menu.addAction(str(t.name),
                           lambda t=t: self._apply_template(t))
        menu.exec(self._load_btn.mapToGlobal(
            self._load_btn.rect().bottomLeft()))

    def _apply_template(self, t) -> None:
        """Pre-fill EVERY field from the recipe (all still editable).
        Unknown tags in the pool expression stay visible as chips — the
        algebra treats them as empty contributions, honestly."""
        import json as _json
        self._name_edit.setText(str(t.name))
        try:
            self._expr = [(op, tag) for op, tag in
                          _json.loads(t.pool_expr_json)]
        except (ValueError, TypeError):
            self._expr = [("+", cut_names.EXPORTED_TAG)]
        self._rebuild_expr_row()
        try:
            styles = set(_json.loads(t.style_filter_json))
        except (ValueError, TypeError):
            styles = set()
        for s, chip in self._style_chips.items():
            chip.setChecked(s in styles)
        tf = getattr(t, "type_filter", "both")
        self._cb_photos.setChecked(tf in ("both", "photo"))
        self._cb_videos.setChecked(tf in ("both", "video"))
        if getattr(t, "default_state", "skipped") == "picked":
            self._rb_picked.setChecked(True)
        else:
            self._rb_skipped.setChecked(True)
        self._target_spin.setValue(int(t.target_s or 0) // 60)
        self._max_spin.setValue(int(t.max_s or 0) // 60)
        self._photo_spin.setValue(float(getattr(t, "photo_s", 6.0)))
        idx = self._music_combo.findData(getattr(t, "music_category", None))
        self._music_combo.setCurrentIndex(idx if idx >= 0 else 0)
        card = getattr(t, "card_style", "black")
        self._card_radios.get(card, self._card_radios["black"]).setChecked(True)
        self._refresh_all()

    def _on_save_template(self) -> None:
        if self._template_saver is None:
            return
        dlg = _TemplateNameDialog(
            default=self._name_edit.text().strip(), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._template_saver(dlg.template_name(), self.draft())

    # ── values ───────────────────────────────────────────────────────

    def _target_s(self) -> Optional[int]:
        v = int(self._target_spin.value())
        return v * 60 if v > 0 else None

    def _max_s(self) -> Optional[int]:
        v = int(self._max_spin.value())
        return v * 60 if v > 0 else None

    def draft(self) -> CutDraft:
        if self._snapshot is not None:
            return self._snapshot
        return CutDraft(
            name=self._name_edit.text().strip(),
            tag=self._slug(),
            pool_expr=tuple(self._expr),
            style_filter=tuple(self._style_filter()),
            type_filter=self._type_filter() or "both",
            default_state="picked" if self._rb_picked.isChecked() else "skipped",
            target_s=self._target_s(),
            max_s=self._max_s(),
            photo_s=float(self._photo_spin.value()),
            music_category=self._music_combo.currentData(),
            card_style=next(
                (k for k, rb in self._card_radios.items() if rb.isChecked()),
                "black"),
        )

    def _on_accept(self) -> None:
        if self._start is not None and not self._start.isEnabled():
            return
        self._snapshot = self.draft()
        self.accept()

    @staticmethod
    def ask(**kwargs) -> Optional[CutDraft]:
        parent = kwargs.pop("parent", None)
        dlg = NewCutDialog(parent=parent, **kwargs)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.draft()
        return None


class _TemplateNameDialog(QDialog):
    """Name the template — one titled field (the form grammar), free
    text (a template name is a label, not a tag)."""

    def __init__(self, *, default: str = "",
                 parent: QWidget | None = None) -> None:
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
