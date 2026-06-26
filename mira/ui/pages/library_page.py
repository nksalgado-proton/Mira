"""Library page — the cross-event home (spec/76 §B.4 / spec/93 §9 /
spec/94 Phase 4a-iii).

This page replaces the events-page cross-event band as the user-facing
entry to cross-event work. Three SurfaceBand sections:

* **Cross-event Cuts** — the list of Cuts that span events. Per-row
  Play (full-screen rehearsal pulling bytes from each source event),
  Export (the existing :func:`export_cross_event_cut` pipeline), Open
  (the detail viewer). Header button **+ New Cut** opens the Collection
  face of :class:`NewRecipeDialog`.
* **Collections** — count + **Manage Collections…** which opens the
  existing :class:`CrossEventDcsDialog` (renamed user-facing to
  "Collections" per spec/93 vocab).
* **Recipes** — count + a hint about the on-disk tree (spec/93 §4:
  the OS file manager is the management surface).

Chrome follows the spec/94 Phase 3 standard: flush
``#SurfaceHeaderRail[phase="share"]`` (Cuts are the Share-state
output), content with the standard 28/18/28/22 margins, no inline QSS.
Back lives in the shared title bar via the
:attr:`uses_titlebar_back` / :meth:`on_titlebar_back` contract.
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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import cut_names
from mira.ui.design import ghost_button, primary_button
from mira.ui.i18n import tr


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# One row per cross-event Cut
# --------------------------------------------------------------------------- #


class _CutRow(QFrame):
    """One cut row inside the Cross-event Cuts band.

    Layout: tag + meta on the left; Play · Export · Open · ⋯ (Delete)
    on the right. spec/05 — every clickable carries a tooltip; pointing-
    hand cursor is applied via the app-level filter.

    Signals carry the ``cut_id`` so the host's dispatch table is dim
    (the row doesn't know its parents)."""

    play_requested = pyqtSignal(str)
    export_requested = pyqtSignal(str)
    open_requested = pyqtSignal(str)
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
        play_btn = ghost_button(tr("▶ Play"))
        play_btn.setToolTip(tr(
            "Full-screen rehearsal — timed photos, real clip lengths, "
            "separators, music."))
        play_btn.clicked.connect(
            lambda: self.play_requested.emit(self._cut_id))
        right.addWidget(play_btn)
        export_btn = primary_button(tr("📤 Export"))
        export_btn.setToolTip(tr(
            "Materialise this Cut as a folder of links / copies — the "
            "hand-off to PTE."))
        export_btn.clicked.connect(
            lambda: self.export_requested.emit(self._cut_id))
        # spec/76 §B.1 — export stamps last_exported_at in mira.db
        # (a guarded mutator). Grey the button so the user doesn't try.
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(export_btn)
        right.addWidget(export_btn)
        open_btn = ghost_button(tr("Open…"))
        open_btn.setToolTip(tr("Open the Cut's per-event member list."))
        open_btn.clicked.connect(
            lambda: self.open_requested.emit(self._cut_id))
        right.addWidget(open_btn)
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
        menu = QMenu(self)
        # spec/76 §B.3 — Publish materialises the Cut to the library
        # publish slot + writes a manifest, for a TV media server to
        # read. Re-publish overwrites; the slot is the live handoff.
        publish_action = menu.addAction(tr("Publish"))
        publish_action.setToolTip(tr(
            "Materialise this Cut to the library publish slot "
            "(Jellyfin / DLNA-friendly) with a manifest."))
        publish_action.triggered.connect(
            lambda: self.publish_requested.emit(self._cut_id))
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(publish_action)
        menu.addSeparator()
        del_action = menu.addAction(tr("Delete"))
        del_action.triggered.connect(
            lambda: self.delete_requested.emit(self._cut_id))
        # spec/76 §B.1 — read-only sessions can't drop cross-event
        # Cuts; grey the menu item so the click is a no-op.
        disable_if_read_only(del_action)
        menu.exec(self._kebab.mapToGlobal(
            self._kebab.rect().bottomLeft()))


# --------------------------------------------------------------------------- #
# The page itself
# --------------------------------------------------------------------------- #


class LibraryPage(QWidget):
    """The cross-event Cuts / Collections / Recipes hub.

    Constructed once at app startup; pulls fresh state from the
    umbrella :class:`Gateway` on :meth:`refresh`. The host wires this
    into the page stack under a new ``ENTRY_LIBRARY`` key (see
    :mod:`mira.ui.shell.main_window`).
    """

    #: Emitted when the user clicks Back in the shared title bar; the
    #: host routes it back to the events page (the previous
    #: destination, by default).
    back_requested = pyqtSignal()
    #: Emitted when the user clicks **+ New Cut** in the Cuts band.
    #: The host (MainWindow) drives the actual NewRecipeDialog
    #: Collection face — the dialog construction needs the same
    #: classify-placement + recipe_store + dc_creator wiring the
    #: events page already builds, so the LibraryPage stays page-
    #: shaped (no dialog construction inline).
    new_cut_requested = pyqtSignal()

    def __init__(self, gateway, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self.setObjectName("LibraryPage")
        # spec/94 Phase 3 contract — Back lives in the shared title bar.
        self.uses_titlebar_back = True
        self._cuts_rows_layout: Optional[QVBoxLayout] = None
        self._cuts_empty_label: Optional[QLabel] = None
        self._collections_count_label: Optional[QLabel] = None
        self._recipes_count_label: Optional[QLabel] = None
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

        # Flush full-width Share-state pink rail at the very top.
        rail = QFrame()
        rail.setObjectName("SurfaceHeaderRail")
        rail.setProperty("phase", "share")
        rail.setFixedHeight(2)
        root.addWidget(rail)

        # Scrollable content host (the bands can grow tall).
        scroll = QScrollArea()
        scroll.setObjectName("LibraryScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        outer = QVBoxLayout(host)
        outer.setContentsMargins(28, 18, 28, 22)
        outer.setSpacing(12)

        # Page title row — quick orientation (no inline QSS; reuses
        # the standard PageTitle role).
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title = QLabel(tr("Library"))
        title.setObjectName("PageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        outer.addLayout(title_row)

        outer.addWidget(self._build_cuts_band())
        outer.addWidget(self._build_collections_band())
        outer.addWidget(self._build_recipes_band())
        outer.addStretch(1)

        scroll.setWidget(host)
        root.addWidget(scroll, 1)

    def _build_cuts_band(self) -> QWidget:
        band = QFrame()
        band.setObjectName("SurfaceBand")
        band.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(band)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel(tr("Cross-event Cuts"))
        title.setObjectName("CardTitle")
        header.addWidget(title)
        sub = QLabel(tr(
            "Cuts that span events — pin a Collection to materialise one."))
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        header.addWidget(sub, 1)
        new_btn = primary_button(tr("+ New Cut"))
        new_btn.setToolTip(tr(
            "Compose a new cross-event Cut from a Collection — opens "
            "the Collection face of the New Cut dialog."))
        new_btn.clicked.connect(self._on_new_cut)
        # spec/76 §B.1 — read-only sessions can browse Cuts + Play,
        # but creating a new one writes to mira.db. Grey + hint.
        from mira.ui.read_only import disable_if_read_only
        disable_if_read_only(new_btn)
        header.addWidget(new_btn)
        layout.addLayout(header)

        rows_host = QWidget()
        self._cuts_rows_layout = QVBoxLayout(rows_host)
        self._cuts_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._cuts_rows_layout.setSpacing(8)
        self._cuts_empty_label = QLabel(tr(
            "No cross-event Cuts yet. Build a Collection and pin it "
            "into a Cut — every Cut you make lands here."))
        self._cuts_empty_label.setObjectName("PageHint")
        self._cuts_empty_label.setWordWrap(True)
        self._cuts_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cuts_rows_layout.addWidget(self._cuts_empty_label)
        layout.addWidget(rows_host)

        return band

    def _build_collections_band(self) -> QWidget:
        band = QFrame()
        band.setObjectName("SurfaceBand")
        layout = QHBoxLayout(band)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(tr("Collections"))
        title.setObjectName("CardTitle")
        col.addWidget(title)
        self._collections_count_label = QLabel("")
        self._collections_count_label.setObjectName("Sub")
        col.addWidget(self._collections_count_label)
        layout.addLayout(col, 1)
        manage = ghost_button(tr("Manage Collections…"))
        manage.setToolTip(tr(
            "Browse, rename, or delete your Collections — the saved "
            "queries Cuts pin from."))
        manage.clicked.connect(self._on_manage_collections)
        layout.addWidget(manage)
        return band

    def _build_recipes_band(self) -> QWidget:
        band = QFrame()
        band.setObjectName("SurfaceBand")
        layout = QHBoxLayout(band)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(tr("Recipes"))
        title.setObjectName("CardTitle")
        col.addWidget(title)
        self._recipes_count_label = QLabel("")
        self._recipes_count_label.setObjectName("Sub")
        col.addWidget(self._recipes_count_label)
        layout.addLayout(col, 1)
        # spec/93 §4 + §9: the OS file manager IS the management surface
        # for Recipes (a folder tree under <library_root>/Recipes/). v1
        # of the Library page just surfaces the count; the cascading-menu
        # browser is the natural follow-up.
        hint = QLabel(tr(
            "Manage in your file manager under "
            "<library_root>/Recipes/."))
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return band

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        """Re-pull cross-event Cuts + counts from the gateway and
        rebuild the cuts list. Called on show + after every modal
        that mutates state (new cut, delete, edit collection)."""
        if self._cuts_rows_layout is None:
            return
        # Remove existing _CutRow widgets (the empty label sticks
        # around — its visibility flips on the count).
        for i in reversed(range(self._cuts_rows_layout.count())):
            item = self._cuts_rows_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, _CutRow):
                w.setParent(None)
                w.deleteLater()
        cuts = self._safe_cross_event_cuts()
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
                widget.play_requested.connect(self._on_play_cut)
                widget.export_requested.connect(self._on_export_cut)
                widget.open_requested.connect(self._on_open_cut)
                widget.delete_requested.connect(self._on_delete_cut)
                widget.publish_requested.connect(self._on_publish_cut)
                # Insert above the empty label.
                self._cuts_rows_layout.insertWidget(0, widget)
        self._refresh_counts()

    def _refresh_counts(self) -> None:
        lg = self._library_gateway()
        n_coll = len(lg.dynamic_collections()) if lg is not None else 0
        if self._collections_count_label is not None:
            self._collections_count_label.setText(tr(
                "{n} saved").replace("{n}", str(n_coll)))
        # Recipes — counted via the recipes_library tree if wired.
        n_rec = self._count_recipes()
        if self._recipes_count_label is not None:
            self._recipes_count_label.setText(tr(
                "{n} saved").replace("{n}", str(n_rec)))

    def _count_recipes(self) -> int:
        """Best-effort: count Recipe rows via RecipeStore if the
        umbrella exposes one. Falls back to 0 silently."""
        try:
            rs = self._gateway.recipe_store()
        except Exception:                                      # noqa: BLE001
            return 0
        try:
            return len(rs.list())
        except Exception:                                      # noqa: BLE001
            return 0

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

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _on_new_cut(self) -> None:
        """+ New Cut — opens the Collection face of NewRecipeDialog
        via the host. The dialog construction needs the gateway-side
        wiring the events page already builds (classify_placement,
        recipe_store, dc_creator, …) — so we emit a signal and let
        the host drive it. Tests connect to the signal; absent a
        connection, the click is a quiet no-op."""
        self.new_cut_requested.emit()

    def _on_manage_collections(self) -> None:
        """Open the Collections list dialog (spec/93 §9 — the file
        manager is the management surface; this dialog is the in-app
        view of the same tree)."""
        from mira.ui.pages.cross_event_dcs_dialog import CrossEventDcsDialog
        lg = self._library_gateway()
        if lg is None:
            QMessageBox.warning(self, tr("Collections"),
                                tr("The library gateway is unavailable."))
            return
        dlg = CrossEventDcsDialog(
            lg, umbrella_gateway=self._gateway, parent=self)
        dlg.exec()
        self.refresh()

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
        # spec/152 Phase 3 — videos play at 1×; the rehearsal's
        # wall-clock matches PTE because cut_play._entry_total_ms now
        # holds every photo / opener / separator slot for
        # ``photo_s + transition_s`` (same shape PTE's [Times] uses).
        # The per-Cut transition_ms is resolved here (per-Cut
        # override > umbrella settings > 2000 default) and threaded
        # explicitly so the dialog can't disagree with the PTE
        # generator on this page.
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
        # spec/154 — render the opener's provenance summary so the
        # cross-event rehearsal opens on a real title card (was blank).
        opener_image = None
        if any(k == "opener" for k, _ in entries):
            opener_image = self._cross_event_opener_image(lg, cut)
        # spec/154 — draw the photo caption (the Cut's selected When /
        # Where / Camera / Exposure fields) live on each frame, like event
        # Play. Provenance comes straight from the global_items projection.
        import json as _json
        try:
            overlay_fields = [
                str(f) for f in _json.loads(cut.overlay_fields_json or "[]")]
        except Exception:                                          # noqa: BLE001
            overlay_fields = []
        provenance_resolver = (
            cross_event_provenance_resolver(lg, cut_id)
            if overlay_fields else None)
        # spec/154 — the per-slide origin label (source event name + capture
        # date) at the top, gated by the Cut's own "Source label per slide"
        # flag (stored in extras_json). Independent of the four caption
        # fields; off by default.
        origin_resolver = (
            cross_event_origin_resolver(lg, cut_id)
            if self._cut_source_label_on(cut) else None)
        dlg = CutPlayerDialog(
            entries,
            event_root=Path(""),   # unused — resolve_path supplies the path
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
        # Look up the Cut so the dialog header carries its tag.
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
        # Use the shared _ExportTargetDialog so the cross-event flow
        # gets the same checkboxes + summary shape as the per-event
        # flow. ``event_root=None`` suppresses the cross-volume notice
        # — cross-event members span volumes by nature; per-member
        # link/copy fallback handles each independently. spec/148 —
        # honour the cross-event flow's last Overwrite vs Keep-both
        # choice from settings, same field the per-event surface
        # writes.
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
        # spec/148 — destructive-replace confirmation parallels the
        # per-event surface so the user has one last chance to back
        # out before the prior bundle gets cleared.
        if (choices.overwrite_existing
                and self._is_non_empty_folder(choices.target)
                and not self._confirm_overwrite(
                    cut_row, choices.target)):
            return
        # spec/148 — persist the radio choice so the next export pre-
        # selects the same default. Silent on settings-store hiccups.
        self._remember_overwrite_choice(choices.overwrite_existing)
        try:
            audio_root = ""
            try:
                audio_root = self._gateway.settings.load().audio_library_path or ""
            except Exception:                                  # noqa: BLE001
                audio_root = ""
            summary = export_cross_event_cut(
                self._gateway, "", cut_id,
                target=choices.target,
                include_originals=choices.include_originals,
                copy_mode=choices.copy_mode,
                overwrite_existing=choices.overwrite_existing,
                audio_root=audio_root or None,
            )
        except CrossEventExportError as exc:
            QMessageBox.warning(
                self, tr("Export failed"), str(exc))
            return
        # spec/148 — the actual folder written to (Keep-both may have
        # disambiguated to ``<tag> (2)/``). The summary surfaces the
        # disambiguated path so the Open-folder button + PTE generation
        # land on the right files.
        export_folder = Path(summary.get("folder") or choices.target)
        # spec/105 §6 summary — same shape as the per-event dialog.
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
        # spec/107 — generate slideshow.pte when "I use PTE" is on. Same
        # best-effort policy as the per-event flow: a failure logs but
        # never blocks the summary. spec/148 — overwrite=True keeps the
        # project filename at ``<stem>.pte`` (no ``(2)``) on Overwrite.
        pte_file = self._generate_pte_for_cross_event_cut(
            cut_row, export_folder,
            overwrite=choices.overwrite_existing)
        self._show_export_complete_box(
            [line_one] + extra_lines, export_folder, pte_file)
        self.refresh()

    # ── spec/148 helpers (shared with the per-event surface) ─────────

    def _is_non_empty_folder(self, target: "Path") -> bool:
        """spec/148 — confirm-gate predicate. True only when a prior
        bundle would be destroyed by an Overwrite."""
        try:
            return target.exists() and any(target.iterdir())
        except OSError:
            return False

    def _confirm_overwrite(self, cut_row, target: "Path") -> bool:
        """spec/148 — destructive-replace confirm. Returns True to
        proceed, False to cancel. Matches the per-event wording."""
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
        """spec/148 — persist the radio choice on the gateway-shared
        settings field. Silent on errors so the export still completes
        when persistence isn't available."""
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
        """When the master PTE toggle is on, walk the just-exported
        cross-event folder + write ``slideshow.pte``. Mirrors the per-
        event helper but reads the cross-event Cut's overlay / aspect /
        photo_s from the library gateway. spec/148 — ``overwrite=True``
        passes through to :func:`generate_into_folder` so the project
        filename lands at ``<stem>.pte`` without ``(2)`` disambiguation
        (the Overwrite export already wiped the prior project)."""
        try:
            settings = self._gateway.settings.load()
        except Exception:  # noqa: BLE001
            return None
        if not getattr(settings, "use_pte", False):
            return None
        try:
            from mira.shared.pte_project import (
                PteAudioTrack, PteMember,
                generate_into_folder,
            )
            from mira.paths import library_root as _library_root_from_paths
            members = []
            for entry in sorted(folder.iterdir(),
                                key=lambda p: p.name.lower()):
                if not entry.is_file():
                    continue
                suffix = entry.suffix.lower()
                if suffix in (".jpg", ".jpeg", ".png"):
                    members.append(PteMember(kind="photo", path=entry))
                elif suffix == ".mp4":
                    members.append(PteMember(
                        kind="video", path=entry,
                        duration_ms=0,
                    ))
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
            aspect = getattr(cut_row, "aspect", None) or "16:9"
            photo_seconds = float(getattr(cut_row, "photo_s", 6.0))
            overlay_mode = (
                getattr(cut_row, "overlay_mode", None) or "embedded")
            # spec/152 §3 — thread the user-configured transition time
            # through to PTE so its [Times] cumulative + the show
            # length budget + the audio playlist all agree on wall
            # time. Falls back to the existing PTE default when the
            # setting is missing on a stale Settings JSON.
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
                overlay_mode=overlay_mode,
                transition_ms=transition_ms,
                overwrite=overwrite,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                "PTE generation failed for cross-event cut %s",
                getattr(cut_row, "tag", "?"))
            return None

    def _show_export_complete_box(self, lines, folder: "Path",
                                  pte_file: "Optional[Path]") -> None:
        """Replace the plain Information popup with one that carries
        Open-folder + Open-in-PTE action buttons (spec/107 Tier 1)."""
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
        # Build a CrossEventCutRow-shaped object for the dialog —
        # it expects the umbrella's list row, not the gateway model.
        from mira.gateway.gateway import CrossEventCutRow
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
        # Resolve the row for the confirm-dialog tag.
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
