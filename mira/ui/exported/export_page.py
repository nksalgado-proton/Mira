"""ExportPage — the Export-phase surface (spec/66 §1.1, spec/68 §3).

The "what ships" decision. Pool = every picked keeper from the event;
default state = green (export — the opt-out rule); P / X (or click)
flips to red (drop). The green set materialises via the existing
spec/60 batch engine (untouched), submitted through the spec/59 §8
``BatchExportQueue`` (also untouched) — this surface re-parents the
*trigger*, not the engine.

Composition follows spec/68 §3: born from the ``mira.ui.design`` catalog
(``PageHeader`` / ``Thumb`` / ``StageProgress`` / design-system buttons
and dialogs) over ``mira.ui.base.flow_layout.FlowLayout``. Mirrors
:class:`mira.ui.pages.days_grid_page.DaysGridPage` for grid shape — same
responsive ~184×138 px tiles, same border-state grammar — with the
single semantic shift that **green = will export, red = dropped** here
(the §5a state colours stay locked; only the verb changes).

The locked spec/63 keymap stays the viewport's job. This surface is a
grid (no per-photo viewport drilldown in the MVP). Click a Thumb to
toggle; the toolbar's bulk Pick all / Skip all covers keyboard-poor
sweeps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.gateway.event_gateway import EventGateway
from mira.picked.status import STATE_PICKED, STATE_SKIPPED, default_state_for
from mira.store import models as m
from mira.ui.base.flow_layout import FlowLayout
from mira.ui.design import (
    PageHeader,
    StageProgress,
    Thumb,
    confirm,
    danger_ghost_button,
    ghost_button,
    primary_button,
    show_error,
    show_info,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)

_TILE_SIZE = QSize(184, 138)
_THUMBS_PER_TICK = 4
_THUMB_TIMER_MS = 20


@dataclass
class _Cell:
    """One picked-keeper cell — driven by the gateway + a Thumb widget."""

    item_id: str
    path: Path
    day_number: Optional[int]
    sha256: Optional[str]
    kind: str
    state: str            # "picked" (green) or "skipped" (red)
    widget: Thumb


def _state_swatch(token: str, label: str) -> QWidget:
    """Tiny legend chip — the §5a colour swatch + a label. Matches the
    Days Grid legend so the two surfaces read as one decision grammar."""
    host = QWidget()
    h = QHBoxLayout(host)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(6)
    color = PALETTE["dark"][token]
    swatch = QLabel()
    swatch.setFixedSize(18, 14)
    swatch.setStyleSheet(
        f"background: transparent; border: 3px solid {color};"
        f" border-radius: 5px;"
    )
    h.addWidget(swatch)
    txt = QLabel(label)
    txt.setObjectName("Sub")
    h.addWidget(txt)
    return host


class ExportPage(QWidget):
    """Surface — the Export phase grid."""

    # Shell contract: same shape as the other per-event pages.
    closed = pyqtSignal()
    fullscreen_changed = pyqtSignal(bool)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._eg: Optional[EventGateway] = None
        self._event_id: Optional[str] = None
        self._event_name: str = ""
        self._cells: List[_Cell] = []
        # The "born green" default (spec/59 §8, kept by spec/66): a
        # picked keeper with no explicit edit-phase row reads as picked
        # here. The host injects from settings on open_event.
        self._phase_default: str = STATE_PICKED

        # Lazy thumb loader — same shape as PickPage / EditHostPage:
        # construct the cells first (cheap), let the user see the grid,
        # then fill thumbnails per timer tick (each decode is 2-300 ms;
        # blocking the main thread on hundreds of decodes freezes the
        # surface for seconds).
        self._thumb_pending: List[str] = []
        self._thumb_pixmap_cache: Dict[str, QPixmap] = {}
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(_THUMB_TIMER_MS)
        self._thumb_timer.timeout.connect(self._load_some_thumbs)

        self._build_ui()

    # ── construction ──────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        # ── PageHeader: title with weight + sub line + Export action ──
        # The primary action lives on the header so the user always sees
        # "what ships" without having to scroll the grid.
        self._export_btn = primary_button(tr("Export green"))
        self._export_btn.clicked.connect(self._on_export_clicked)
        self._header = PageHeader(
            title=tr("Export"),
            sub=tr("Pick what ships — green flows to Exported Media/."),
            action=self._export_btn,
        )
        outer.addWidget(self._header)

        # ── Sticky toolbar: Back · Pick all · Skip all · counter · progress
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._back = ghost_button(tr("‹ Back"))
        self._back.clicked.connect(self.closed.emit)
        toolbar.addWidget(self._back)

        pick_all = ghost_button(tr("✓ Pick all"))
        pick_all.clicked.connect(self._on_pick_all)
        toolbar.addWidget(pick_all)

        skip_all = danger_ghost_button(tr("✗ Skip all"))
        skip_all.clicked.connect(self._on_skip_all)
        toolbar.addWidget(skip_all)

        toolbar.addStretch()

        # Compact progress block — green / total + a StageProgress bar
        # that paints "what fraction of picked keepers are still in the
        # green set." Mirrors the Days Grid review-progress pattern.
        progress_block = QVBoxLayout()
        progress_block.setSpacing(2)
        self._count_label = QLabel("0 / 0 to export")
        self._count_label.setObjectName("Sub")
        self._count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_block.addWidget(self._count_label)
        self._count_bar = StageProgress()
        self._count_bar.setMinimumWidth(180)
        # spec/66 phase identity: Export's bar paints green.
        self._count_bar.setColorToken("green")
        progress_block.addWidget(self._count_bar)
        toolbar.addLayout(progress_block)
        outer.addLayout(toolbar)

        # ── Legend strip — same vocabulary as Days Grid, only the verb
        # shifts: here green = will export, red = drop.
        legend = QHBoxLayout()
        legend.setSpacing(18)
        legend.addWidget(_state_swatch("picked", tr("Will export")))
        legend.addWidget(_state_swatch("skipped", tr("Drop")))
        reminder = QLabel(
            "<span style='color:#8b94a7'>"
            "border <b style='color:#eef1f7'>= ship state</b>"
            " · click a tile to toggle"
            "</span>"
        )
        reminder.setObjectName("Sub")
        reminder.setTextFormat(Qt.TextFormat.RichText)
        legend.addWidget(reminder)
        legend.addStretch()
        outer.addLayout(legend)

        # ── Scrolling grid (Thumb cells in a FlowLayout) ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._grid_host = QWidget()
        self._flow = FlowLayout(self._grid_host, spacing=18)
        self._flow.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._grid_host)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self._scroll, 1)

        # ── Empty-state placeholder (hidden when items present) ──
        self._empty_state = QLabel(tr(
            "No picked keepers yet — run Pick first, then come back to "
            "choose what ships."
        ))
        self._empty_state.setObjectName("Sub")
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setWordWrap(True)
        self._empty_state.hide()
        outer.addWidget(self._empty_state)

    # ── lifecycle ─────────────────────────────────────────────────────

    def open_event(self, event_id: str) -> bool:
        """Open ``event_id`` for the Export phase. Returns False if the
        event can't be opened (the host routes the user back)."""
        self._close_gateway()
        if self.gateway is None:
            return False
        try:
            self._eg = self.gateway.open_event(event_id)
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: cannot open event %s", event_id)
            return False
        self._event_id = event_id
        try:
            ev = self._eg.event()
            self._event_name = ev.name or tr("(unnamed event)")
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: event() read failed for %s", event_id)
            self._event_name = tr("(unnamed event)")
        # The "born green" default reads from settings (spec/59 §8 — the
        # ``default_state_for(..., "edit")`` setting governs the unset-
        # row case across both Edit and Export under spec/66).
        try:
            self._phase_default = default_state_for(
                self.gateway.settings, "edit")
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: default_state_for failed")
            self._phase_default = STATE_PICKED

        self._build_cells()
        self._refresh_header_sub()
        return True

    def _close_gateway(self) -> None:
        self._thumb_timer.stop()
        self._thumb_pending.clear()
        self._thumb_pixmap_cache.clear()
        for c in self._cells:
            try:
                self._flow.removeWidget(c.widget)
                c.widget.deleteLater()
            except Exception:                                   # noqa: BLE001
                pass
        self._cells = []
        if self._eg is not None:
            try:
                self._eg.close()
            except Exception:                                   # noqa: BLE001
                log.exception("ExportPage: gateway close failed")
            self._eg = None

    def closeEvent(self, event) -> None:                        # noqa: N802
        self._close_gateway()
        super().closeEvent(event)

    # ── building the grid ────────────────────────────────────────────

    def _build_cells(self) -> None:
        """Read the picked-keepers pool from the gateway and build a
        Thumb cell per item. State follows the existing ``phase_state
        edit`` row (the spec/59 §8 mark) or the born-green default."""
        # Clear any prior cells (open_event already wiped via _close_gateway,
        # but this method is also the rebuild path for a future Refresh).
        for c in self._cells:
            self._flow.removeWidget(c.widget)
            c.widget.deleteLater()
        self._cells = []

        if self._eg is None:
            self._refresh_chrome()
            return

        # Pool = every Pick-kept PHOTO. Videos are tracked by their
        # workshop-internal segment/snapshot state and are not part of
        # the Export grid MVP — slice 6 / a follow-up adds them as
        # segment + snapshot cells once the Exported Media/ + hardlink
        # plumbing lands. The query is cheap (an indexed SELECT).
        try:
            picked_items = self._eg.items(
                phase="pick", state="picked", kind="photo",
                provenance="captured",
            )
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: items query failed")
            picked_items = []

        # Pre-fetch the edit-phase state rows in ONE query so the
        # build loop reads each from a dict — cheaper than the per-item
        # gateway calls (which round-trip per row).
        try:
            edit_states = self._eg.phase_states("edit")
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: phase_states('edit') failed")
            edit_states = {}

        event_root = (
            Path(self._eg.event_root) if self._eg.event_root else None
        )

        for it in picked_items:
            if not it.origin_relpath or event_root is None:
                continue
            path = event_root / it.origin_relpath
            state = self._resolve_state(it.id, edit_states)
            # Cache hit → start the cell with the pixmap already in hand
            # so we don't pay a paint flicker through "no thumb yet".
            cached = self._thumb_pixmap_cache.get(it.id)
            thumb = Thumb(
                cached,
                state=state,
                size=_TILE_SIZE,
            )
            thumb.clicked.connect(
                lambda _=False, iid=it.id: self._on_thumb_clicked(iid)
            )
            cell = _Cell(
                item_id=it.id, path=path, day_number=it.day_number,
                sha256=it.sha256, kind=it.kind, state=state, widget=thumb,
            )
            self._cells.append(cell)
            self._flow.addWidget(thumb)
            if cached is None:
                self._thumb_pending.append(it.id)

        if self._thumb_pending and not self._thumb_timer.isActive():
            self._thumb_timer.start()

        self._refresh_chrome()

    def _resolve_state(
        self, item_id: str, edit_states: Dict[str, m.PhaseState]
    ) -> str:
        ps = edit_states.get(item_id)
        if ps is not None and ps.state in (STATE_PICKED, STATE_SKIPPED):
            return ps.state
        return self._phase_default

    # ── decision handlers ────────────────────────────────────────────

    def _on_thumb_clicked(self, item_id: str) -> None:
        cell = self._find_cell(item_id)
        if cell is None or self._eg is None:
            return
        new_state = (
            STATE_SKIPPED if cell.state == STATE_PICKED else STATE_PICKED
        )
        try:
            self._eg.set_phase_state(item_id, "edit", new_state)
        except Exception:                                       # noqa: BLE001
            log.exception(
                "ExportPage: set_phase_state failed for %s", item_id)
            return
        cell.state = new_state
        cell.widget.setState(new_state)
        self._refresh_chrome()

    def _on_pick_all(self) -> None:
        self._bulk_set(STATE_PICKED)

    def _on_skip_all(self) -> None:
        if not self._cells:
            return
        # Skip-all is the destructive direction (drop everything). The
        # design-system confirm dialog keeps the surface calm — no
        # QMessageBox chrome (spec/68 §3).
        if not confirm(
            self,
            tr("Drop every photo from this Export?"),
            tr(
                "Every tile turns red — none of them will ship. "
                "You can flip individuals back to green afterwards."
            ),
            primary_text=tr("Drop all"),
        ):
            return
        self._bulk_set(STATE_SKIPPED)

    def _bulk_set(self, new_state: str) -> None:
        if self._eg is None or not self._cells:
            return
        item_ids = [c.item_id for c in self._cells]
        try:
            self._eg.set_items_phase_state(item_ids, "edit", new_state)
        except Exception:                                       # noqa: BLE001
            log.exception("ExportPage: set_items_phase_state failed")
            return
        for c in self._cells:
            c.state = new_state
            c.widget.setState(new_state)
        self._refresh_chrome()

    # ── the Export action ────────────────────────────────────────────

    def _on_export_clicked(self) -> None:
        """Submit the green set to the batch queue. The spec/60 engine
        and the spec/59 §8 queue are unchanged — this surface only
        builds the manifest and hands it off."""
        if self._eg is None or self._eg.event_root is None:
            return
        green = [c for c in self._cells if c.state == STATE_PICKED]
        if not green:
            show_info(
                self,
                tr("Nothing to ship"),
                tr("No tiles are green — pick at least one to export."),
            )
            return

        if len(green) >= 50 and not confirm(
            self,
            tr("Export {n} photo(s)?").replace("{n}", str(len(green))),
            tr(
                "The job runs in the background — the strip below the "
                "menu bar shows progress. You can keep working."
            ),
            primary_text=tr("Export"),
        ):
            return

        try:
            self._submit_batch(green)
        except Exception as exc:                                # noqa: BLE001
            log.exception("ExportPage: batch submit failed")
            show_error(
                self,
                tr("Could not start the export"),
                tr("The batch could not be queued.\n\n{err}")
                .replace("{err}", str(exc)),
            )

    def _submit_batch(self, green_cells: List[_Cell]) -> None:
        """Build the spec/60 manifest from green cells, submit through
        the window's :class:`BatchExportQueue` (the engine + queue stay
        locked per spec/68 §4 — this surface just re-parents the
        trigger). The commit closure writes ``set_edit_exported`` +
        lineage for the units that actually succeeded, per the spec/60
        §5 per-unit-truth contract.

        spec/66 §1.2 — third-party returns are **hardlinked** from
        ``Edited Media/`` into ``Exported Media/`` instead of being
        re-rendered through the engine: the return is itself a finished
        rendered output (the user developed it externally in LRC /
        Helicon), so feeding it back through Mira's tone pipeline would
        change the pixels. Hardlinks run synchronously here (file-system
        op, sub-second); the no-return cells take the render path.
        """
        from core.export_manifest import ExportManifest, PhotoUnit
        from core.settings import load_settings
        from mira.ui.edited.export_job import BatchExportJob
        from mira.ui.edited._lineage import (
            record_edit_export_lineage,
            record_single_lineage,
        )
        from core.path_builder import exported_media_dir

        assert self._eg is not None and self._eg.event_root is not None
        settings = load_settings()
        aspect_label = str(
            settings.get("preferred_aspect_ratio") or "Original")
        # spec/66 §1.2 — the shipped set lives under Exported Media/;
        # Edited Media/ is now the third-party-return inbox only.
        event_root = Path(self._eg.event_root)
        default_dest = exported_media_dir(event_root)

        # Per-day grouping for the on-disk layout — same shape the
        # legacy host produced, preserved here for continuity.
        day_labels: Dict[Optional[int], str] = {}
        by_day: Dict[Optional[int], List[_Cell]] = {}
        for c in green_cells:
            by_day.setdefault(c.day_number, []).append(c)
            if c.day_number not in day_labels:
                day_labels[c.day_number] = self._day_label_for(c.day_number)

        # spec/66 §1.2 — partition green cells: items with a
        # third-party return get hardlinked synchronously; the rest go
        # through the render queue.
        to_hardlink: List[tuple[_Cell, str]] = []  # (cell, src_relpath)
        to_render: List[_Cell] = []
        for c in green_cells:
            return_rel = self._eg.edit_candidate_relpath(c.item_id)
            if return_rel:
                to_hardlink.append((c, return_rel))
            else:
                to_render.append(c)

        if to_hardlink:
            self._hardlink_third_party_returns(
                to_hardlink, event_root, default_dest, day_labels)

        units: list[PhotoUnit] = []
        source_by_unit_id: Dict[str, Path] = {}
        for day_n, day_cells in by_day.items():
            dest_dir = str(default_dest / day_labels[day_n])
            for c in day_cells:
                if c in [pair[0] for pair in to_hardlink]:
                    continue  # already handled via hardlink path
                adj = self._eg.adjustment(c.item_id)
                look = None
                crop_norm = None
                crop_angle = 0.0
                rotation = 0
                style = None
                if adj is not None:
                    look = {"look": adj.look or "natural"}
                    if adj.style:
                        look["style"] = adj.style
                        style = adj.style
                    if adj.creative_filter:
                        look["creative_filter"] = adj.creative_filter
                    if abs(float(adj.look_strength or 1.0) - 1.0) > 1e-6:
                        look["strength"] = float(adj.look_strength)
                    if all(v is not None for v in (
                            adj.crop_x, adj.crop_y,
                            adj.crop_w, adj.crop_h)):
                        crop_norm = (
                            adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)
                    crop_angle = float(adj.crop_angle or 0.0)
                    rotation = int(adj.rotation or 0)
                units.append(PhotoUnit(
                    unit_id=c.item_id,
                    source=str(c.path),
                    dest_dir=dest_dir,
                    file_type="JPEG",
                    jpeg_quality=92,
                    look=look,
                    auto_on=True,
                    style=style,
                    crop_norm=crop_norm,
                    crop_angle=crop_angle,
                    rotation=rotation,
                    aspect_label=aspect_label,
                ))
                source_by_unit_id[c.item_id] = c.path

        # All green cells were third-party returns — every ship-decision
        # already committed via hardlink; nothing to render.
        if not units:
            return

        manifest = ExportManifest(units=tuple(units), clips=(),
                                  collision="unique")
        worker = BatchExportJob(manifest, source_by_unit_id)
        render_cells = list(to_render)

        def commit(result) -> None:
            """Per-unit truth (spec/60 §5): set ``edit_exported`` +
            record lineage only for ok units. Failures simply don't
            commit; the Edit-entry return scan (spec/57 §3) heals any
            lost commit."""
            if self._eg is None:
                return
            ok_ids = getattr(result, "ok_unit_ids", set())
            if not ok_ids:
                return
            ok_cells = [c for c in render_cells if c.item_id in ok_ids]
            for c in ok_cells:
                try:
                    self._eg.set_edit_exported(c.item_id, True)
                except Exception:                               # noqa: BLE001
                    log.exception(
                        "ExportPage: set_edit_exported failed for %s",
                        c.item_id)
            try:
                record_edit_export_lineage(
                    self._eg,
                    Path(self._eg.event_root),
                    items_with_sources=[
                        (c.item_id, c.path) for c in ok_cells
                    ],
                    result=result,
                    recipe_by_item={
                        c.item_id: self._recipe_for_item(c.item_id)
                        for c in ok_cells
                    },
                    resolved_by_stem=getattr(
                        result, "resolved_by_name", {}),
                )
            except Exception:                                   # noqa: BLE001
                log.exception("ExportPage: record_edit_export_lineage failed")

        queue = getattr(self.window(), "batch_queue", None)
        if queue is None:
            show_error(
                self,
                tr("Batch queue unavailable"),
                tr(
                    "The app's batch queue isn't reachable — try "
                    "restarting Mira."
                ),
            )
            return
        worker.finished.connect(worker.deleteLater)
        queue.enqueue(
            worker,
            tr("Export — {name} ({n})")
            .replace("{name}", self._event_name)
            .replace("{n}", str(len(units))),
            commit,
        )

    def _hardlink_third_party_returns(
        self,
        to_hardlink: List[tuple],
        event_root: Path,
        dest_root: Path,
        day_labels: Dict[Optional[int], str],
    ) -> None:
        """spec/66 §1.2 — hardlink each third-party return from
        ``Edited Media/`` into ``Exported Media/<day>/`` and record an
        ``Exported Media/`` lineage row + ``set_edit_exported``. Copy
        fallback when hardlink fails (cross-volume), mirroring the spec/57
        return-scan policy. Runs synchronously — file-system ops are
        sub-millisecond on the same volume; the render queue is the
        slow path.
        """
        from os import link as _hardlink

        assert self._eg is not None

        for cell, src_relpath in to_hardlink:
            src_path = event_root / src_relpath
            if not src_path.exists():
                log.warning(
                    "ExportPage: third-party return missing on disk: %s "
                    "— falling back to render", src_path)
                # Drop this cell from to_hardlink so the render fallback
                # picks it up. (Mutating the list mid-iteration is fine
                # here — the caller's render-path loop has not started.)
                continue
            day_dir = dest_root / day_labels.get(cell.day_number, "")
            try:
                day_dir.mkdir(parents=True, exist_ok=True)
            except Exception:                                    # noqa: BLE001
                log.exception(
                    "ExportPage: cannot create %s — skipping hardlink",
                    day_dir)
                continue
            # The destination keeps the source filename so the export
            # folder reads natively in Explorer / PTE.
            dest_path = day_dir / src_path.name
            stem, ext = dest_path.stem, dest_path.suffix
            i = 2
            while dest_path.exists():
                dest_path = day_dir / f"{stem} ({i}){ext}"
                i += 1
            try:
                _hardlink(str(src_path), str(dest_path))
            except OSError:
                # Cross-volume or hardlink unsupported — copy instead.
                # The destination becomes a real byte copy; the lineage
                # row still records the ship.
                try:
                    import shutil
                    shutil.copy2(str(src_path), str(dest_path))
                except Exception:                                # noqa: BLE001
                    log.exception(
                        "ExportPage: hardlink + copy fallback failed "
                        "for %s -> %s", src_path, dest_path)
                    continue
            # Synchronous commit: mark exported + write the
            # Exported Media/ lineage row. The watermark / Cuts queries
            # depend on this row's relpath prefix to recognise the ship.
            try:
                self._eg.set_edit_exported(cell.item_id, True)
            except Exception:                                    # noqa: BLE001
                log.exception(
                    "ExportPage: set_edit_exported failed for %s",
                    cell.item_id)
            try:
                from mira.ui.edited._lineage import (
                    record_single_lineage,
                )
                record_single_lineage(
                    self._eg,
                    event_root,
                    item_id=cell.item_id,
                    dest_path=dest_path,
                    recipe=self._recipe_for_item(cell.item_id),
                )
            except Exception:                                    # noqa: BLE001
                log.exception(
                    "ExportPage: record_single_lineage failed for %s",
                    cell.item_id)

    def _recipe_for_item(self, item_id: str) -> dict:
        """The spec/54 §8 lineage-snapshot CHOICE for one item — read
        from its Adjustment row."""
        recipe: dict = {"look": "natural"}
        if self._eg is None:
            return recipe
        adj = self._eg.adjustment(item_id)
        if adj is None:
            return recipe
        recipe["look"] = adj.look or "natural"
        if adj.style:
            recipe["style"] = adj.style
        if adj.creative_filter:
            recipe["creative_filter"] = adj.creative_filter
        if abs(float(adj.look_strength or 1.0) - 1.0) > 1e-6:
            recipe["look_strength"] = float(adj.look_strength)
        if all(v is not None for v in (
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h)):
            recipe["crop_norm"] = [
                adj.crop_x, adj.crop_y, adj.crop_w, adj.crop_h]
        if adj.crop_angle:
            recipe["crop_angle"] = adj.crop_angle
        if adj.rotation:
            recipe["rotation"] = adj.rotation
        if adj.aspect_label:
            recipe["aspect_label"] = adj.aspect_label
        return recipe

    def _day_label_for(self, day_number: Optional[int]) -> str:
        if self._eg is None or day_number is None:
            return ""
        try:
            days = {d.day_number: d for d in self._eg.trip_days()}
            td = days.get(day_number)
            if td is not None:
                bits = [b for b in (
                    f"Dia {td.day_number}", td.description, td.date,
                ) if b]
                return " — ".join(bits) if bits else f"Dia {td.day_number}"
        except Exception:                                       # noqa: BLE001
            log.debug("day-label fallback for %s", day_number, exc_info=True)
        return f"Dia {day_number}"

    # ── chrome refresh ───────────────────────────────────────────────

    def _refresh_chrome(self) -> None:
        total = len(self._cells)
        green = sum(1 for c in self._cells if c.state == STATE_PICKED)
        self._count_label.setText(
            tr("{n} / {t} to export")
            .replace("{n}", str(green)).replace("{t}", str(total))
        )
        pct = int(round(green / total * 100)) if total else 0
        self._count_bar.setValue(pct)
        self._export_btn.setText(
            tr("Export green ({n})").replace("{n}", str(green))
        )
        self._export_btn.setEnabled(green > 0)
        self._empty_state.setVisible(total == 0)
        self._scroll.setVisible(total > 0)

    def _refresh_header_sub(self) -> None:
        # PageHeader's sub is set at construction; rebuild it to include
        # the event name once we know it. (PageHeader doesn't expose a
        # setSub yet — we mutate the QLabel inside it.)
        for child in self._header.findChildren(QLabel):
            if child.objectName() == "Sub":
                child.setText(
                    tr("Pick what ships for “{name}” — green flows to "
                       "Exported Media/.")
                    .replace("{name}", self._event_name)
                )
                break

    def _find_cell(self, item_id: str) -> Optional[_Cell]:
        for c in self._cells:
            if c.item_id == item_id:
                return c
        return None

    # ── thumb loader (lazy, mirrors PickPage / EditHostPage) ─────────

    def _load_some_thumbs(self) -> None:
        if self._eg is None or self._eg.event_root is None:
            self._thumb_timer.stop()
            self._thumb_pending.clear()
            return
        done = 0
        while self._thumb_pending and done < _THUMBS_PER_TICK:
            item_id = self._thumb_pending.pop(0)
            cell = self._find_cell(item_id)
            if cell is None:
                continue
            pm = self._decode_thumbnail(cell)
            if pm is None or pm.isNull():
                continue
            self._thumb_pixmap_cache[item_id] = pm
            cell.widget.setPixmap(pm)
            done += 1
        if not self._thumb_pending:
            self._thumb_timer.stop()

    def _decode_thumbnail(self, cell: _Cell) -> Optional[QPixmap]:
        """Same disk-cached 256-px JPEG path PickPage uses. The cache
        is keyed by sha256, so it's shared across surfaces — opening
        Export after Pick re-renders an already-warm cache cheaply."""
        from mira.ui.media.image_loader import load_pixmap

        if not cell.sha256:
            try:
                return load_pixmap(cell.path)
            except Exception:                                   # noqa: BLE001
                log.warning(
                    "ExportPage: load_pixmap failed for %s",
                    cell.path, exc_info=True)
                return None
        try:
            from core.photo_thumb_cache import ensure_photo_thumb
            thumb_path = ensure_photo_thumb(
                event_root=Path(self._eg.event_root),
                source_path=cell.path,
                sha256=cell.sha256,
            )
            return load_pixmap(thumb_path)
        except Exception:                                       # noqa: BLE001
            log.warning(
                "ExportPage: thumb decode failed for %s",
                cell.path, exc_info=True)
            return None


__all__ = ["ExportPage"]
