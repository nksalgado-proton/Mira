"""Library page — the cross-event home (spec/76 §B.4 / spec/93 §9 /
spec/94 Phase 4a-iii / spec/162 §3.2 Round 3c).

Post-spec/162 Round 3c the page mirrors :class:`ShareCutsPage`'s
shape at library scope:

* A flush pink identity rail at the top (Share state, spec/71).
* A header row with the primary ``+ New Cut`` action.
* A Base Collection card — the library-scope ``#exported`` universe,
  aggregated across every event. ``Open`` navigates to the
  library-scope :class:`DCDetailPage` (spec/162 §3.2 / §7.2).
* A flat list of cross-event Cuts below (no bands, no tabs, no
  Manage-Collections / Recipes bands — those retired in Round 2b /
  Round 3c).

The heavy per-Cut actions (Play, Export, Publish) live in the
kebab menu on each row so the primary verbs stay ``Open`` / ``Edit
Cut``; the row shape mirrors :class:`ShareCutsPage.CutRow` visually.

Chrome follows the spec/94 Phase 3 standard: flush
``#SurfaceHeaderRail[phase="share"]``, content with the standard
28/18/28/22 margins, no inline QSS. Back lives in the shared title
bar via the :attr:`uses_titlebar_back` / :meth:`on_titlebar_back`
contract.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import cut_names
from mira.ui.design import ghost_button, primary_button, tag
from mira.ui.i18n import tr


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Base Collection card — the library-scope #exported universe
# --------------------------------------------------------------------------- #


class _LibraryPoolCard(QFrame):
    """spec/162 §3.2 / §7.2 — the library-scope ``#exported`` Base
    Collection card. Mirrors :class:`ShareCutsPage._PoolCard`'s shape;
    the aggregate count spans every event via the umbrella gateway's
    :meth:`library_exported_summary` (see :mod:`mira.gateway.gateway`).
    """

    open_requested = pyqtSignal()

    def __init__(
        self,
        file_count: int,
        event_count: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # Reuse the #CrossEventBand QSS role for the accent-framed card
        # treatment the ShareCutsPage pool card also rides. Nothing
        # library-specific in the paint; spec/92's role catalog stays
        # small.
        self.setObjectName("CrossEventBand")
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(14)

        tile = QLabel("🌐")
        tile.setFixedSize(50, 50)
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setObjectName("IconTile")
        tile.setProperty("tone", "accent")
        tile.setProperty("bordered", "true")
        _tile_font = tile.font()
        _tile_font.setPixelSize(22)
        tile.setFont(_tile_font)
        h.addWidget(tile)

        block = QVBoxLayout()
        block.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        t = QLabel("#exported")
        t.setObjectName("CardTitle")
        title_row.addWidget(t)
        title_row.addWidget(tag(tr("Base Collection")))
        title_row.addWidget(tag(tr("library")))
        title_row.addStretch()
        block.addLayout(title_row)
        # spec/162 §7.2 — subtitle reads "N exported files across M
        # events" at library scope.
        subtitle = tr("{n} exported files across {m} events").replace(
            "{n}", str(int(file_count))
        ).replace("{m}", str(int(event_count)))
        sub = QLabel(subtitle)
        sub.setObjectName("Sub")
        block.addWidget(sub)
        h.addLayout(block, 1)

        btn = ghost_button(tr("Open"))
        btn.setToolTip(tr(
            "Open the library-wide flat grid of every exported file "
            "across every event."))
        btn.clicked.connect(self.open_requested.emit)
        h.addWidget(btn)


# --------------------------------------------------------------------------- #
# One row per cross-event Cut
# --------------------------------------------------------------------------- #


class _CutRow(QFrame):
    """One cut row inside the flat Cuts list.

    spec/162 §3.2 / Round 3c — the row now mirrors :class:`ShareCutsPage
    .CutRow` visually: Open (primary) + Edit Cut (ghost) + kebab. The
    rare / heavy actions (Play, Export, Publish, Delete) live behind
    the kebab so the primary verbs stay uncluttered.

    Signals carry the ``cut_id`` so the host's dispatch table is dim
    (the row doesn't know its parents)."""

    open_requested = pyqtSignal(str)
    adjust_requested = pyqtSignal(str)
    play_requested = pyqtSignal(str)
    export_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    publish_requested = pyqtSignal(str)

    def __init__(self, *, cut_id: str, tag: str,
                 anchor_event_name: str,
                 member_count: int,
                 last_exported_at: Optional[str],
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cut_id = cut_id
        self.setObjectName("LibraryCutRow")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(2)
        tag_lbl = QLabel(cut_names.display_tag(tag))
        tag_lbl.setObjectName("LibraryCutTag")
        f = tag_lbl.font()
        f.setBold(True)
        tag_lbl.setFont(f)
        left.addWidget(tag_lbl)
        meta_text = tr("from {anchor} · {n} members").replace(
            "{anchor}", anchor_event_name or "—"
        ).replace("{n}", str(member_count))
        meta_lbl = QLabel(meta_text)
        meta_lbl.setObjectName("LibraryCutMeta")
        left.addWidget(meta_lbl)
        if last_exported_at:
            stamp = QLabel(tr("last exported: {ts}").replace(
                "{ts}", last_exported_at))
        else:
            stamp = QLabel(tr("not yet exported"))
        stamp.setObjectName("LibraryCutStamp")
        left.addWidget(stamp)
        outer.addLayout(left, 1)

        right = QHBoxLayout()
        right.setSpacing(6)
        open_btn = primary_button(tr("Open"))
        open_btn.setToolTip(tr("Open the Cut's per-event member list."))
        open_btn.clicked.connect(
            lambda: self.open_requested.emit(self._cut_id))
        right.addWidget(open_btn)
        # spec/162 Slice 9 — "Edit Cut" (was "Adjust") opens NewCutDialog
        # in edit mode with the cross-event Cut prefilled.
        adjust_btn = ghost_button(tr("Edit Cut"))
        adjust_btn.setToolTip(tr(
            "Edit this cross-event Cut's Recipe — Source, Filters, "
            "Format, Rules."))
        adjust_btn.clicked.connect(
            lambda: self.adjust_requested.emit(self._cut_id))
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(adjust_btn)
        right.addWidget(adjust_btn)
        kebab = QToolButton()
        kebab.setObjectName("IconButton")
        kebab.setProperty("shape", "kebab")
        kebab.setText("⋯")
        kebab.setToolTip(tr("More actions"))
        kebab.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        kebab.clicked.connect(self._show_kebab)
        self._kebab = kebab
        right.addWidget(kebab)
        outer.addLayout(right)

    def _show_kebab(self) -> None:
        from mira.ui.read_only import disable_if_read_only
        menu = QMenu(self)
        play_action = menu.addAction(tr("▶ Play"))
        play_action.setToolTip(tr(
            "Full-screen rehearsal — timed photos, real clip lengths, "
            "separators, music."))
        play_action.triggered.connect(
            lambda: self.play_requested.emit(self._cut_id))
        export_action = menu.addAction(tr("📤 Export"))
        export_action.setToolTip(tr(
            "Materialise this Cut as a folder of links / copies — the "
            "hand-off to PTE."))
        export_action.triggered.connect(
            lambda: self.export_requested.emit(self._cut_id))
        disable_if_read_only(export_action)
        # spec/76 §B.3 — Publish materialises the Cut to the library
        # publish slot + writes a manifest, for a TV media server to
        # read. Re-publish overwrites; the slot is the live handoff.
        publish_action = menu.addAction(tr("Publish"))
        publish_action.setToolTip(tr(
            "Materialise this Cut to the library publish slot "
            "(Jellyfin / DLNA-friendly) with a manifest."))
        publish_action.triggered.connect(
            lambda: self.publish_requested.emit(self._cut_id))
        disable_if_read_only(publish_action)
        menu.addSeparator()
        del_action = menu.addAction(tr("Delete"))
        del_action.triggered.connect(
            lambda: self.delete_requested.emit(self._cut_id))
        disable_if_read_only(del_action)
        menu.exec(self._kebab.mapToGlobal(
            self._kebab.rect().bottomLeft()))


# --------------------------------------------------------------------------- #
# The page itself
# --------------------------------------------------------------------------- #


class LibraryPage(QWidget):
    """The cross-event Cuts hub — the library counterpart of
    :class:`ShareCutsPage`.

    Constructed once at app startup; pulls fresh state from the
    umbrella :class:`Gateway` on :meth:`refresh`. The host wires this
    into the page stack under a new ``ENTRY_LIBRARY`` key (see
    :mod:`mira.ui.shell.main_window`).

    Post-spec/162 Round 3c: the outer shape mirrors ShareCutsPage's
    (rail + pool card + flat Cuts list under one header). The old
    Collections + Recipes bands + their manage buttons retired with
    the Save/Load-Collection surface (Round 2b).
    """

    #: Emitted when the user clicks Back in the shared title bar; the
    #: host routes it back to the events page (the previous
    #: destination, by default).
    back_requested = pyqtSignal()
    #: Emitted when the user clicks **+ New Cut** in the header row.
    #: The host (MainWindow) drives the actual NewCutDialog
    #: construction — the dialog needs the same gateway-side wiring
    #: the ShareCutsPage already builds (recipe_store, classify_
    #: placement, cross-event operand inventory, …) so the page stays
    #: page-shaped (no dialog construction inline).
    new_cut_requested = pyqtSignal()
    #: Emitted when the user clicks Open on the Base Collection card.
    #: The host navigates the page's internal stack to the library-
    #: scope DCDetailPage; keeping this a signal so the page stays
    #: cheap to instantiate + the DCDetailPage lifecycle lives at the
    #: MainWindow layer alongside the ShareCutsPage's pool detail.
    library_pool_open_requested = pyqtSignal()
    #: Emitted when the user clicks Edit Cut on a cross-event Cut
    #: row. The host builds the NewCutDialog in
    #: ``mode=MODE_EDIT`` with the Cut prefilled + drives the flow.
    adjust_requested = pyqtSignal(str)

    def __init__(self, gateway, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self.setObjectName("LibraryPage")
        self.uses_titlebar_back = True
        self._cuts_rows_layout: Optional[QVBoxLayout] = None
        self._cuts_empty_label: Optional[QLabel] = None
        self._pool_slot: Optional[QVBoxLayout] = None
        self._section_label: Optional[QLabel] = None
        self._pool_summary_cache = {"file_count": 0, "event_count": 0}
        self._build_layout()

    # ------------------------------------------------------------------ #
    # Title-bar Back dispatcher (spec/94 Phase 3 standard)
    # ------------------------------------------------------------------ #

    def on_titlebar_back(self) -> None:
        """Single-level Back — the page has no internal drill-down
        today; modal dialogs (Detail, Player) own their own Back. So
        clicking Back here just leaves the page."""
        self.back_requested.emit()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #

    def _build_layout(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Flush full-width Share-state pink rail at the very top —
        # spec/162 §3.2 mirrors ShareCutsPage's identity rail.
        rail = QFrame()
        rail.setObjectName("SurfaceHeaderRail")
        rail.setProperty("phase", "share")
        rail.setFixedHeight(2)
        root.addWidget(rail)

        # Scrollable content host.
        scroll = QScrollArea()
        scroll.setObjectName("LibraryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(28, 18, 28, 22)
        outer.setSpacing(18)

        # Header row — page title on the left, primary + New Cut on
        # the right (spec/162 §3.2 shape mirror).
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        title = QLabel(tr("Library"))
        title.setObjectName("PageTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        new_btn = primary_button(tr("+ New Cut"))
        new_btn.setToolTip(tr(
            "Compose a new cross-event Cut — opens the New Cut dialog "
            "at library scope."))
        new_btn.clicked.connect(self._on_new_cut)
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(new_btn)
        header_row.addWidget(new_btn)
        outer.addLayout(header_row)

        # Base Collection card slot — populated in :meth:`refresh`.
        self._pool_slot = QVBoxLayout()
        self._pool_slot.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._pool_slot)

        # Flat Cuts list beneath — same visual as ShareCutsPage's
        # single-tab pane (accent-bordered wrapper + scroll of rows).
        pane = QFrame()
        pane.setObjectName("ShareTabPane")
        pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QVBoxLayout(pane)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)
        self._section_label = QLabel(tr("Cuts · 0"))
        self._section_label.setObjectName("Micro")
        v.addWidget(self._section_label)

        cuts_scroll = QScrollArea()
        cuts_scroll.setWidgetResizable(True)
        cuts_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        cuts_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cuts_scroll.viewport().setAutoFillBackground(False)
        rows_host = QWidget()
        rows_host.setAutoFillBackground(False)
        self._cuts_rows_layout = QVBoxLayout(rows_host)
        self._cuts_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._cuts_rows_layout.setSpacing(8)
        self._cuts_rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._cuts_empty_label = QLabel(tr(
            "No cross-event Cuts yet. Click + New Cut to compose one — "
            "every Cut you make lands here."))
        self._cuts_empty_label.setObjectName("PageHint")
        self._cuts_empty_label.setWordWrap(True)
        self._cuts_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cuts_rows_layout.addWidget(self._cuts_empty_label)
        cuts_scroll.setWidget(rows_host)
        v.addWidget(cuts_scroll, 1)
        outer.addWidget(pane, 1)

        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Re-pull cross-event Cuts + the library-scope Base Collection
        summary from the gateway and rebuild the page. Called on show
        + after every modal that mutates state (new cut, delete, edit
        collection)."""
        if self._cuts_rows_layout is None:
            return
        # ── Base Collection card ──────────────────────────────────
        self._pool_summary_cache = self._safe_library_summary()
        self._rebuild_pool_card()
        # ── Cuts list ────────────────────────────────────────────
        # Remove existing _CutRow widgets (the empty label sticks
        # around — its visibility flips on the count).
        for i in reversed(range(self._cuts_rows_layout.count())):
            item = self._cuts_rows_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _CutRow):
                w.setParent(None)
                w.deleteLater()
        cuts = self._safe_cross_event_cuts()
        if self._section_label is not None:
            self._section_label.setText(
                tr("Cuts · {n}").replace("{n}", str(len(cuts))))
        if not cuts:
            if self._cuts_empty_label is not None:
                self._cuts_empty_label.setVisible(True)
        else:
            if self._cuts_empty_label is not None:
                self._cuts_empty_label.setVisible(False)
            for row in cuts:
                widget = _CutRow(
                    cut_id=row.cut_id, tag=row.tag,
                    anchor_event_name=row.anchor_event_name,
                    member_count=row.member_count,
                    last_exported_at=row.last_exported_at,
                    parent=self)
                widget.open_requested.connect(self._on_open_cut)
                widget.adjust_requested.connect(self._on_adjust_cut)
                widget.play_requested.connect(self._on_play_cut)
                widget.export_requested.connect(self._on_export_cut)
                widget.delete_requested.connect(self._on_delete_cut)
                widget.publish_requested.connect(self._on_publish_cut)
                # Insert above the empty label.
                self._cuts_rows_layout.insertWidget(0, widget)

    def _rebuild_pool_card(self) -> None:
        if self._pool_slot is None:
            return
        while self._pool_slot.count():
            it = self._pool_slot.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        card = _LibraryPoolCard(
            file_count=int(self._pool_summary_cache.get("file_count", 0)),
            event_count=int(self._pool_summary_cache.get("event_count", 0)),
            parent=self,
        )
        card.open_requested.connect(self._on_open_library_pool)
        self._pool_slot.addWidget(card)

    # ------------------------------------------------------------------ #
    # Gateway helpers
    # ------------------------------------------------------------------ #

    def _library_gateway(self):
        """Resolve the library gateway through the umbrella. Returns
        ``None`` if anything fails so the page degrades cleanly in
        tests / smoke construction."""
        try:
            return self._gateway.library_gateway()
        except Exception as exc:                              # noqa: BLE001
            log.debug("LibraryPage: library_gateway unavailable: %s", exc)
            return None

    def _safe_cross_event_cuts(self) -> list:
        try:
            return list(self._gateway.cross_event_cuts())
        except Exception as exc:                              # noqa: BLE001
            log.warning("LibraryPage: cross_event_cuts failed: %s", exc)
            return []

    def _safe_library_summary(self) -> dict:
        try:
            return dict(self._gateway.library_exported_summary())
        except Exception as exc:                              # noqa: BLE001
            log.warning(
                "LibraryPage: library_exported_summary failed: %s", exc)
            return {"file_count": 0, "event_count": 0}

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _on_new_cut(self) -> None:
        """+ New Cut — the host builds the NewCutDialog with the
        proper gateway-side wiring (recipe_store, classify_placement,
        cross-event operand inventory) and drives the flow."""
        self.new_cut_requested.emit()

    def _on_open_library_pool(self) -> None:
        """Base Collection card Open — the host swaps its internal
        stack to the library-scope :class:`DCDetailPage`."""
        self.library_pool_open_requested.emit()

    @staticmethod
    def _cut_card_style(cut) -> str:
        """spec/143/154 — the cross-event Cut's card style from
        ``extras_json`` (sibling to ``source_label``). Defaults / normalises
        to ``'black'`` so a stale blob can't park a bogus value on the
        renderer."""
        import json as _json
        try:
            extras = _json.loads(getattr(cut, "extras_json", "") or "{}")
        except (ValueError, TypeError):
            extras = {}
        style = extras.get("card_style") if isinstance(extras, dict) else None
        return style if style in ("black", "single", "multi") else "black"

    def _cross_event_card_writers(self, cut_id: str):
        """spec/154 — build the flat ``opener_writer`` + ``separator_writer``
        the cross-event exporter calls. Both render a text-less
        :func:`render_flat_background` (the words ride the generated .pte as
        :Text); the opener always rides, the separator writer is returned
        only when the Cut has separators ON (cross-event default OFF,
        spec/81 §3.1). Returns ``(opener_writer, separator_writer_or_None)``;
        ``(None, None)`` if the Cut can't be read."""
        from core.cut_aspect import aspect_dimensions, normalise
        from mira.ui.shared.separator_card import render_flat_background
        lg = self._library_gateway()
        cut = lg.cross_event_cut(cut_id) if lg is not None else None
        if cut is None:
            return None, None
        aspect = normalise(getattr(cut, "aspect", "16:9"))
        _, canvas_h = aspect_dimensions(aspect)
        card_style = self._cut_card_style(cut)

        def opener_writer(target: "Path") -> None:
            img = render_flat_background(
                aspect=aspect, height=canvas_h,
                card_style=card_style, seed_key=cut.id)
            if not img.save(str(target), "JPG", 92):
                raise OSError(f"could not write {target}")

        separator_writer = None
        if bool(getattr(cut, "separators", False)):
            def separator_writer(target: "Path", token) -> None:  # noqa: F811
                img = render_flat_background(
                    aspect=aspect, height=canvas_h,
                    card_style=card_style, seed_key=f"{cut.id}:{token}")
                if not img.save(str(target), "JPG", 92):
                    raise OSError(f"could not write {target}")
        return opener_writer, separator_writer

    @staticmethod
    def _cut_source_label_on(cut) -> bool:
        """spec/154 — read the cross-event Cut's "Source label per slide"
        flag out of ``extras_json`` (the standing house pattern for per-Cut
        presentation booleans, sibling to ``card_style``). Tolerant of a
        malformed / absent blob — defaults OFF."""
        import json as _json
        try:
            extras = _json.loads(getattr(cut, "extras_json", "") or "{}")
        except (ValueError, TypeError):
            return False
        return bool(isinstance(extras, dict) and extras.get("source_label"))

    def _cross_event_opener_lines(self, lg, cut) -> "tuple[str, list]":
        """spec/154 — the cross-event opener's title + summary lines: the
        Cut name, the source EVENTS the frames came from (provenance, not
        a story — "know where the slides came from without opening it"),
        and the count. Pure-ish data the caller renders into a card (Play)
        or emits as PTE text (one composer, two surfaces)."""
        from core import cut_names
        title = cut_names.display_tag(cut.tag)
        try:
            members = lg.cross_event_cut_members(cut.id)
        except Exception:                                          # noqa: BLE001
            members = []
        names_by_uuid = {}
        try:
            for e in lg.list_events_for_scope():
                names_by_uuid[e.get("uuid")] = e.get("name") or tr("(unnamed)")
        except Exception:                                          # noqa: BLE001
            pass
        seen: list = []
        for m in members:
            nm = names_by_uuid.get(getattr(m, "event_id", None))
            if nm and nm not in seen:
                seen.append(nm)
        lines: list = []
        if seen:
            lines.append(
                tr("From: {events}").replace("{events}", " · ".join(seen)))
        lines.append(
            tr("{n} frames").replace("{n}", str(len(members))))
        return title, lines

    def _cross_event_opener_image(self, lg, cut):
        """spec/154 — render the cross-event opener summary into a card
        image (reuses the event opener renderer; flat colour, light text).
        ``None`` on any failure so Play still opens without an opener."""
        try:
            from core.cut_aspect import aspect_dimensions, normalise
            from mira.ui.shared.separator_card import render_cut_opener_image
            title, lines = self._cross_event_opener_lines(lg, cut)
            aspect = normalise(getattr(cut, "aspect", "16:9"))
            _, canvas_h = aspect_dimensions(aspect)
            return render_cut_opener_image(
                tag_text=title, lines=lines, aspect=aspect,
                height=canvas_h, card_style="black", seed_key=cut.id)
        except Exception:                                          # noqa: BLE001
            log.exception("cross-event opener render failed for %s",
                          getattr(cut, "id", "?"))
            return None

    def _on_play_cut(self, cut_id: str) -> None:
        """Open the cross-event player. spec/94 Phase 4a-iii — the
        player resolves each member's bytes via the umbrella
        gateway's index."""
        from mira.shared.cross_event_cut_play import (
            build_cross_event_entries,
            cross_event_origin_resolver,
            cross_event_provenance_resolver,
            make_resolve_path,
        )
        from mira.ui.shared.cut_play import CutPlayerDialog
        lg = self._library_gateway()
        if lg is None:
            return
        cut = lg.cross_event_cut(cut_id)
        if cut is None:
            return
        entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id=cut_id,
            separators_on=bool(cut.separators))
        if not entries:
            QMessageBox.information(
                self, tr("Play"),
                tr("This Cut has no playable members yet."))
            return
        per_cut_ms = getattr(cut, "transition_ms", None)
        if isinstance(per_cut_ms, (int, float)):
            transition_ms = max(0, int(round(float(per_cut_ms))))
        else:
            try:
                settings = (
                    self._gateway.settings.load()
                    if self._gateway is not None else None
                )
            except Exception:                                          # noqa: BLE001
                settings = None
            raw_t = getattr(settings, "default_transition_ms", 2000)
            try:
                transition_ms = max(0, int(round(float(raw_t))))
            except (TypeError, ValueError):
                transition_ms = 2000
        opener_image = None
        if any(k == "opener" for k, _ in entries):
            opener_image = self._cross_event_opener_image(lg, cut)
        import json as _json
        try:
            overlay_fields = [
                str(f) for f in _json.loads(cut.overlay_fields_json or "[]")]
        except Exception:                                          # noqa: BLE001
            overlay_fields = []
        provenance_resolver = (
            cross_event_provenance_resolver(lg, cut_id)
            if overlay_fields else None)
        origin_resolver = (
            cross_event_origin_resolver(lg, cut_id)
            if self._cut_source_label_on(cut) else None)
        dlg = CutPlayerDialog(
            entries,
            event_root=Path(""),
            photo_s=cut.photo_s,
            day_meta=day_meta,
            resolve_path=make_resolve_path(gateway=self._gateway),
            opener_image=opener_image,
            overlay_fields=overlay_fields,
            provenance_resolver=provenance_resolver,
            origin_resolver=origin_resolver,
            transition_ms=transition_ms,
            parent=self,
        )
        dlg.setWindowTitle(
            cut_names.display_tag(cut.tag) + " — " + tr("rehearsal"))
        dlg.start()
        dlg.exec()

    def _on_adjust_cut(self, cut_id: str) -> None:
        """Edit Cut on a cross-event row — Round 3c wires the host
        into MainWindow for the actual NewCutDialog(scope=SCOPE_CROSS_
        EVENT, mode=MODE_EDIT) construction. Today the row emits the
        signal; the host connects to it. A page without a host
        connection quietly no-ops (matches ``new_cut_requested``)."""
        self.adjust_requested.emit(cut_id)

    def _on_export_cut(self, cut_id: str) -> None:
        """Pick a target folder + options, then materialise the
        cross-event Cut via :func:`export_cross_event_cut` (mira.db
        backed, spec/94 Phase 4a-ii). spec/105 §2 picks a sensible
        default home (``<library_root>/Cuts/Cross-event/<cut>/``,
        or under ``cuts_export_root`` when set); §6 surfaces the
        originals / copy-mode flags and a cross-volume notice."""
        from mira.shared.cross_event_cut_export import (
            CrossEventExportError,
            export_cross_event_cut,
        )
        from mira.shared.cut_export import resolve_cross_event_cut_target
        from mira.paths import library_root as _library_root_from_paths
        cut_row = None
        for row in self._safe_cross_event_cuts():
            if row.cut_id == cut_id:
                cut_row = row
                break
        if cut_row is None:
            return
        library_root = _library_root_from_paths()
        if library_root is None:
            QMessageBox.warning(
                self, tr("Export failed"),
                tr("Mira couldn't resolve the library root — "
                   "cross-event exports need one."))
            return
        try:
            settings = self._gateway.settings.load()
            cuts_export_root = (
                getattr(settings, "cuts_export_root", "") or "")
        except Exception:                                          # noqa: BLE001
            cuts_export_root = ""
        default_target = resolve_cross_event_cut_target(
            cut_tag=cut_row.tag,
            library_root=library_root,
            cuts_export_root=cuts_export_root or None,
        )
        from mira.ui.pages.share_cuts_page import (
            ExportChoices,
            _ExportTargetDialog,
        )
        from core import cut_names
        try:
            saved_settings = self._gateway.settings.load()
            default_overwrite = bool(
                getattr(saved_settings, "cut_export_overwrite_default", False))
        except Exception:                                          # noqa: BLE001
            default_overwrite = False
        dlg = _ExportTargetDialog(
            default_path=default_target,
            tag_display=cut_names.display_tag(cut_row.tag),
            event_root=None,
            default_overwrite=default_overwrite,
            allow_only_new=False,
            parent=self,
        )
        from PyQt6.QtWidgets import QDialog
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        choices = ExportChoices(
            target=dlg.target(),
            include_originals=dlg.include_originals(),
            copy_mode=dlg.copy_mode(),
            overwrite_existing=dlg.overwrite_existing(),
        )
        if (choices.overwrite_existing
                and self._is_non_empty_folder(choices.target)
                and not self._confirm_overwrite(
                    cut_row, choices.target)):
            return
        self._remember_overwrite_choice(choices.overwrite_existing)
        try:
            audio_root = ""
            try:
                audio_root = self._gateway.settings.load().audio_library_path or ""
            except Exception:                                  # noqa: BLE001
                audio_root = ""
            opener_writer, separator_writer = self._cross_event_card_writers(
                cut_id)
            summary = export_cross_event_cut(
                self._gateway, "", cut_id,
                target=choices.target,
                include_originals=choices.include_originals,
                copy_mode=choices.copy_mode,
                overwrite_existing=choices.overwrite_existing,
                audio_root=audio_root or None,
                opener_writer=opener_writer,
                separator_writer=separator_writer,
            )
        except CrossEventExportError as exc:
            QMessageBox.warning(
                self, tr("Export failed"), str(exc))
            return
        export_folder = Path(summary.get("folder") or choices.target)
        line_one = tr(
            "{n} member(s) materialised ({linked} linked, {copied} "
            "copied, {missing} missing)."
        ).replace("{n}", str(summary["member_count"])
        ).replace("{linked}", str(summary["linked"])
        ).replace("{copied}", str(summary["copied"])
        ).replace("{missing}", str(summary["missing"]))
        extra_lines = []
        originals_total = (
            summary.get("originals_linked", 0)
            + summary.get("originals_copied", 0))
        if originals_total:
            extra_lines.append(tr(
                "{n} original(s) placed ({linked} linked, {copied} "
                "copied)."
            ).replace("{n}", str(originals_total)
            ).replace("{linked}", str(summary.get("originals_linked", 0))
            ).replace("{copied}", str(summary.get("originals_copied", 0))))
        if summary.get("missing_originals"):
            extra_lines.append(tr(
                "{n} original(s) could not be resolved and were skipped."
            ).replace("{n}", str(len(summary["missing_originals"]))))
        pte_file = self._generate_pte_for_cross_event_cut(
            cut_row, export_folder,
            overwrite=choices.overwrite_existing)
        self._show_export_complete_box(
            [line_one] + extra_lines, export_folder, pte_file)
        self.refresh()

    # ── spec/148 helpers (shared with the per-event surface) ─────────

    def _is_non_empty_folder(self, target: "Path") -> bool:
        try:
            return target.exists() and any(target.iterdir())
        except OSError:
            return False

    def _confirm_overwrite(self, cut_row, target: "Path") -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(tr("Replace previous export?"))
        box.setText(tr(
            "Replace the previous export of this Cut?\n\n"
            "Everything in {folder} will be replaced by the new "
            "bundle. Any project file you've edited in PTE in that "
            "folder will be lost.").replace("{folder}", str(target)))
        replace_btn = box.addButton(
            tr("Replace"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(
            tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() is replace_btn

    def _remember_overwrite_choice(self, overwrite: bool) -> None:
        store = getattr(self._gateway, "settings", None)
        if store is None or not hasattr(store, "load") or not hasattr(store, "save"):
            return
        try:
            s = store.load()
            if bool(getattr(s, "cut_export_overwrite_default", False)) == overwrite:
                return
            s.cut_export_overwrite_default = overwrite
            store.save(s)
        except Exception:                                          # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "could not persist cut_export_overwrite_default = %s",
                overwrite)

    # ── spec/107 cross-event helpers ─────────────────────────────────

    def _generate_pte_for_cross_event_cut(self, cut_row,
                                          folder: "Path",
                                          *,
                                          overwrite: bool = False,
                                          ) -> "Optional[Path]":
        try:
            settings = self._gateway.settings.load()
        except Exception:  # noqa: BLE001
            return None
        if not getattr(settings, "use_pte", False):
            return None
        try:
            from mira.shared.pte_project import (
                PteAudioTrack, generate_into_folder,
            )
            from mira.paths import library_root as _library_root_from_paths
            lg = self._library_gateway()
            cut = (lg.cross_event_cut(cut_row.cut_id)
                   if lg is not None else None)
            if cut is None:
                return None
            members = self._cross_event_pte_members(lg, cut, folder)
            if not members:
                return None
            audio_dir = folder / "audio"
            tracks = []
            if audio_dir.is_dir():
                import mutagen
                for track in sorted(audio_dir.iterdir(),
                                    key=lambda p: p.name.lower()):
                    if not track.is_file():
                        continue
                    dur_ms = 0
                    try:
                        audio = mutagen.File(track)
                        if audio is not None and audio.info is not None:
                            dur_ms = int(round(audio.info.length * 1000))
                    except Exception:  # noqa: BLE001
                        pass
                    tracks.append(PteAudioTrack(
                        path=track, duration_ms=dur_ms))
            aspect = getattr(cut, "aspect", None) or "16:9"
            photo_seconds = float(getattr(cut, "photo_s", 6.0))
            transition_ms = 2000
            raw_t = getattr(settings, "default_transition_ms", 2000)
            try:
                transition_ms = max(0, int(round(float(raw_t))))
            except (TypeError, ValueError):
                pass
            return generate_into_folder(
                folder, members, tracks,
                aspect=aspect,
                photo_seconds=photo_seconds,
                library_root=_library_root_from_paths(),
                transition_ms=transition_ms,
                overwrite=overwrite,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "PTE generation failed for cross-event cut %s",
                getattr(cut_row, "tag", "?"))
            return None

    def _cross_event_pte_members(self, lg, cut, folder: "Path"):
        import json as _json
        from core import cut_overlay
        from mira.shared.cross_event_cut_play import (
            CROSS_EVENT_OPENER_FILENAME,
            build_cross_event_entries,
            cross_event_member_filename,
            cross_event_origin_resolver,
            cross_event_provenance_resolver,
            cross_event_separator_filename,
            format_capture_date,
        )
        from mira.shared.pte_project import (
            PteMember, PteText,
            TEXT_OPENER_SUB, TEXT_OPENER_TITLE, TEXT_ORIGIN,
            TEXT_PHOTO_CAPTION, TEXT_SEP_SUB, TEXT_SEP_TITLE,
        )
        entries, day_meta = build_cross_event_entries(
            library_gateway=lg, cut_id=cut.id,
            separators_on=bool(getattr(cut, "separators", False)))
        if not entries:
            return []
        try:
            overlay_fields = [
                str(f) for f in _json.loads(cut.overlay_fields_json or "[]")]
        except Exception:                                          # noqa: BLE001
            overlay_fields = []
        prov_resolve = (
            cross_event_provenance_resolver(lg, cut.id)
            if overlay_fields else None)
        origin_resolve = (
            cross_event_origin_resolver(lg, cut.id)
            if self._cut_source_label_on(cut) else None)
        opener_title, opener_lines = self._cross_event_opener_lines(lg, cut)

        members = []
        for kind, payload in entries:
            if kind == "opener":
                path = folder / CROSS_EVENT_OPENER_FILENAME
                if not path.is_file():
                    continue
                texts = [PteText(opener_title, TEXT_OPENER_TITLE)]
                sub = "  ·  ".join(str(ln) for ln in opener_lines if ln)
                if sub:
                    texts.append(PteText(sub, TEXT_OPENER_SUB))
                members.append(PteMember(kind="photo", path=path, texts=texts))
            elif kind == "sep":
                path = folder / cross_event_separator_filename(payload)
                if not path.is_file():
                    continue
                meta = day_meta.get(payload)
                texts = [PteText(
                    getattr(meta, "title", None) or tr("More moments"),
                    TEXT_SEP_TITLE)]
                date_text = format_capture_date(getattr(meta, "date", None))
                if date_text:
                    texts.append(PteText(date_text, TEXT_SEP_SUB))
                members.append(PteMember(kind="photo", path=path, texts=texts))
            else:  # file
                path = folder / cross_event_member_filename(payload)
                if not path.is_file():
                    continue
                texts = []
                if prov_resolve is not None:
                    prov = prov_resolve(payload)
                    if prov is not None:
                        lines = cut_overlay.compose_overlay_lines(
                            overlay_fields, prov)
                        if lines:
                            texts.append(PteText(
                                "  •  ".join(lines), TEXT_PHOTO_CAPTION))
                if origin_resolve is not None:
                    origin = origin_resolve(payload)
                    if origin:
                        texts.append(PteText(origin, TEXT_ORIGIN))
                members.append(PteMember(
                    kind=("video"
                          if getattr(payload, "kind", "photo") == "video"
                          else "photo"),
                    path=path,
                    duration_ms=int(getattr(payload, "duration_ms", 0) or 0),
                    texts=texts))
        return members

    def _show_export_complete_box(self, lines, folder: "Path",
                                  pte_file: "Optional[Path]") -> None:
        from PyQt6.QtWidgets import QMessageBox
        from mira.shared.pte_launch import (
            open_in_pte, pte_launch_available, reveal_in_explorer,
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setWindowTitle(tr("Export complete"))
        box.setText("\n".join(lines))
        try:
            settings = self._gateway.settings.load()
        except Exception:  # noqa: BLE001
            settings = None
        open_folder_btn = box.addButton(
            tr("Open folder"), QMessageBox.ButtonRole.ActionRole)

        def _open_folder():
            try:
                reveal_in_explorer(folder)
            except OSError as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "reveal_in_explorer failed: %s", exc)
        open_folder_btn.clicked.connect(_open_folder)
        if (settings is not None
                and getattr(settings, "use_pte", False)
                and pte_file is not None
                and pte_launch_available(
                    getattr(settings, "pte_path", ""))):
            pte_btn = box.addButton(
                tr("Open in PTE"), QMessageBox.ButtonRole.ActionRole)

            def _open_pte():
                try:
                    open_in_pte(Path(settings.pte_path), pte_file)
                except OSError as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "open_in_pte failed: %s", exc)
            pte_btn.clicked.connect(_open_pte)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_open_cut(self, cut_id: str) -> None:
        """Open the detail viewer (per-event grouping of members)."""
        from mira.ui.pages.cross_event_cut_detail_dialog import (
            CrossEventCutDetailDialog,
        )
        for row in self._safe_cross_event_cuts():
            if row.cut_id == cut_id:
                dlg = CrossEventCutDetailDialog(
                    self._gateway, row, parent=self)
                dlg.exec()
                return

    def _on_publish_cut(self, cut_id: str) -> None:
        """spec/76 §B.3 — publish the Cut to the library publish
        slot with a manifest. Re-publish overwrites the slot."""
        from mira.shared.cut_publish import (
            CutPublishError, publish_cross_event_cut,
        )
        from mira.paths import library_root as _library_root_from_paths
        root = _library_root_from_paths()
        if root is None:
            QMessageBox.warning(
                self, tr("Publish failed"),
                tr("Mira couldn't resolve the library root — publish "
                   "needs a library to write into."))
            return
        try:
            settings = self._gateway.settings.load()
            result = publish_cross_event_cut(
                self._gateway, cut_id,
                library_root_path=root, settings=settings,
            )
        except CutPublishError as exc:
            QMessageBox.warning(self, tr("Publish failed"), str(exc))
            return
        n_frames = sum(1 for _ in result.target.iterdir()
                       if _.is_file() and _.name != "manifest.json")
        QMessageBox.information(
            self, tr("Cut published"),
            tr("{n} file(s) published to:\n{path}\n\n"
               "Manifest written for the media server.").replace(
                "{n}", str(n_frames)).replace(
                "{path}", str(result.target)))

    def _on_delete_cut(self, cut_id: str) -> None:
        tag = cut_id
        for row in self._safe_cross_event_cuts():
            if row.cut_id == cut_id:
                tag = row.tag
                break
        ok = QMessageBox.question(
            self, tr("Delete Cut"),
            tr("Delete {tag}? Members + the Cut row are removed; "
               "already-exported folders on disk stay where they are."
               ).replace("{tag}", cut_names.display_tag(tag)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            # The umbrella's signature keeps anchor_event_id for back-
            # compat but ignores it — the cut lives in mira.db now
            # (spec/94 Phase 4a-ii).
            self._gateway.delete_cross_event_cut("", cut_id)
        except Exception as exc:                              # noqa: BLE001
            QMessageBox.warning(
                self, tr("Delete failed"), str(exc))
            return
        self.refresh()

    # ------------------------------------------------------------------ #
    # Show hook — refresh on first paint
    # ------------------------------------------------------------------ #

    def showEvent(self, ev) -> None:                          # noqa: N802 — Qt
        super().showEvent(ev)
        self.refresh()


__all__ = ["LibraryPage"]
