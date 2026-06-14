"""The Share-phase host (spec/61 §3) — the Cuts list + flow routing.

``CutsShellPage`` is the page the shell mounts on the Share tile
(mirrors PickPage / EditHostPage: constructed with the app Gateway,
``open_event(event_id) -> bool``, a ``closed`` signal). It hosts a
stack:

* **CutsListPage** — the landing: #exported pinned first (the built-in
  live query — informational row, can't be renamed or deleted), user
  Cuts below (tag · count · projected length · music · exported
  status), New Cut, per-row Open / Adjust (re-enter the session) /
  Rename / Delete, one-sentence empty-state hint.
* **CutSessionPage** — created per session (fresh from the New Cut
  dialog's draft, or re-entered via ``CutSession.for_cut``), torn down
  on finish/cancel.
* The Cut detail surface (flat grid + separators, spec/61 §5) mounts
  here too — slice 7.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import audio_library, cut_names
from mira.shared.cut_session import CutSession
from mira.ui.base.surface import back_button
from mira.ui.i18n import tr
from mira.ui.shared.cut_detail_page import CutDetailPage
from mira.ui.shared.cut_session_page import CutSessionPage, _fmt_mmss
from mira.ui.shared.new_cut_dialog import NewCutDialog

log = logging.getLogger(__name__)


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
        from PyQt6.QtWidgets import QDialogButtonBox, QGroupBox
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


class CutsShellPage(QWidget):
    """The Share host: list ↔ session (↔ detail, slice 7)."""

    closed = pyqtSignal()

    def __init__(self, gateway, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CutsShellPage")
        self.gateway = gateway
        self._eg = None
        self._event_id: Optional[str] = None
        self._session_page: Optional[CutSessionPage] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        # Surface 09 — the redesigned Share / Cuts list. Signal names
        # match the legacy CutsListPage contract so the slot wires stay
        # one-to-one; the only thing that changed is the visual layer.
        from mira.ui.pages.share_cuts_page import ShareCutsPage
        self.list_page = ShareCutsPage()
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

    # ── lifecycle (the PickPage/EditHostPage contract) ───────────────

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
        # Build the redesigned snapshots directly so we can carry the
        # spec/61 separator + duration semantics through the new shape.
        from mira.ui.pages.share_cuts_page import (
            CutSnapshot, PoolSnapshot,
        )
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
