"""Back up SD card / Capture offload dialog (spec/13, Option 1).

PORTED from legacy ``ui/pages/back_up_card_dialog.py`` (charter §0/§5.2). UI + the
offload+verify+wipe flow are VERBATIM. Data-seam changes only: the EXIF **bake is
dropped** (virtual EXIF, charter §3 — Nelson OK 2026-05-31, "database only now"); after a
passed verify the copied files are **recorded into the event DB** via
``mira.ingest.offload_record.record_offload`` (capture_time_raw + corrected, no
bake) instead of ``save_event``; ``photos_base``/event_root come from the gateway. The
rglob wipe gate is unchanged.

Original header follows:

Back up SD card dialog (task #9 — frozen 2026-05-19 scope
expansion; docs/14 §"Destructive card offload"). The user-facing
surface that drives ``core.event_backup_card.offload_to_captured``
+ ``verify_offload``, and — only when the source is a removable
drive AND verify passed — offers the destructive wipe behind a
two-confirmation gate (CLAUDE.md invariant #9).

Flow:

1. Source: folder picker (the SD card / card-image / wherever the
   originals live).
2. Camera + event: which event does this backup belong to, what
   camera_id, what bucket (cameras / phones / other).
3. Run offload → run verify. Show progress.
4. Result summary: how many files copied, how many bytes, verify
   pass/fail.
5. If source is removable AND verify passed: offer to delete the
   source files. Two confirmations, audit-logged.

Source files are NEVER touched by this dialog except in the explicit
wipe branch.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.cull_export import CollisionPolicy
from core.event_backup_card import (
    OffloadConfig,
    offload_to_captured,
    verify_offload,
)
from core.models import Event
from core.path_builder import (
    CAPTURED_CAMERAS_SUBDIR,
    CAPTURED_OTHER_SUBDIR,
    CAPTURED_PHONES_SUBDIR,
)
from core.removable_drive import is_removable
from mira.ui.i18n import tr
from mira.ui.pages.offload_calibration_dialog import calibration_offset_for_offload
from mira.ingest.offload_record import record_offload

log = logging.getLogger(__name__)


_BUCKET_OPTIONS = [
    (CAPTURED_CAMERAS_SUBDIR, "Camera (DSLR / mirrorless / action cam)"),
    (CAPTURED_PHONES_SUBDIR, "Phone"),
    (CAPTURED_OTHER_SUBDIR, "Other"),
]


class BackUpCardDialog(QDialog):
    """Modal driving the Stage D offload + verify + (optional) wipe.

    Two invocation modes:

    * From the **sidebar** entry — pass ``events=list[Event]``; a
      dropdown lets the user pick which event to back up into.
    * From the **Capture phase button** on an event — pass a single
      ``Event``; the dropdown is replaced by a read-only label
      showing the event name. The user is already in the event, so
      asking them to pick it again is friction.
    """

    backup_done = pyqtSignal(str)  # event_id

    def __init__(
        self,
        events_or_event,
        parent: QWidget | None = None,
        *,
        included_names: Optional[frozenset[str]] = None,
        prepicked_source: Optional[Path] = None,
        precomputed_offset: Optional[float] = None,
        gateway=None,
        event_root: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Back up SD card"))
        self.setMinimumWidth(640)
        # Data seam (charter §0): the gateway is the only persistence path; ``event_root`` is
        # the resolved on-disk root of the locked event (the offload writes under its
        # ``Original Media`` and the records land in its ``event.db``).
        self._gateway = gateway
        self._event_root = Path(event_root) if event_root else None
        if isinstance(events_or_event, list):
            self._events = events_or_event
            self._locked_event = None
        else:
            self._events = [events_or_event]
            self._locked_event = events_or_event
        # Task #84 — Mode B filter. When non-None, the offload run
        # only copies source files whose basename is in this set
        # (the kept set the user produced in the standalone culler).
        self._included_names = included_names
        # F-019 (Nelson 2026-05-25): the pre-ingest plan-confirm
        # dialog resolves source + offset upstream and hands them
        # in here. When set, the source picker is read-only +
        # pre-filled and the calibration prompt is skipped (the
        # supplied offset is used directly for the bake).
        # ``None`` for both = legacy behavior (sidebar entry).
        self._prepicked_source = (
            Path(prepicked_source) if prepicked_source else None
        )
        self._precomputed_offset = precomputed_offset
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        intro = QLabel(tr(
            "Copy every file from the SD card / source folder into the "
            "event's <code>Original Media/</code> safety mirror, then "
            "verify every copy by SHA-256. <b>Originals are never "
            "touched</b> by this step. Only after verify passes — and "
            "only for removable drives (SD / USB) — is the destructive "
            "wipe even offered."
        ))
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(intro)

        form = QFormLayout()

        # Source picker. When F-019 (the pre-ingest dialog) handed
        # in a prepicked source we pre-fill + hide the Browse button
        # — the user already committed to this source upstream and
        # changing it mid-flow would invalidate the plan-confirm
        # answers they just gave.
        src_layout = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText(tr("<no folder picked yet>"))
        self._src_edit.setReadOnly(True)
        src_browse = QPushButton(tr("Browse…"))
        src_browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        src_browse.clicked.connect(self._pick_source)
        src_layout.addWidget(self._src_edit, stretch=1)
        src_layout.addWidget(src_browse)
        self._src_kind_label = QLabel("")
        if self._prepicked_source is not None:
            self._src_edit.setText(str(self._prepicked_source))
            self._update_src_kind(self._prepicked_source)
            src_browse.setVisible(False)
        form.addRow(tr("Source folder:"), src_layout)
        form.addRow("", self._src_kind_label)

        # Event picker — dropdown for the sidebar invocation; a
        # read-only label for the per-event Capture-button entry
        # (the user already picked the event by being on it).
        self._event_combo: Optional[QComboBox] = None
        if self._locked_event is None:
            self._event_combo = QComboBox()
            for ev in self._events:
                self._event_combo.addItem(
                    f"{ev.display_name or ev.name or ev.id}", ev.id,
                )
            self._event_combo.setCursor(
                QCursor(Qt.CursorShape.PointingHandCursor))
            form.addRow(tr("Event:"), self._event_combo)
        else:
            ev = self._locked_event
            locked_label = QLabel(
                f"<b>{ev.display_name or ev.name or ev.id}</b>")
            locked_label.setTextFormat(Qt.TextFormat.RichText)
            form.addRow(tr("Event:"), locked_label)

        # Camera id.
        self._camera_id_edit = QLineEdit()
        self._camera_id_edit.setPlaceholderText(
            tr("e.g. G9_mkII, iPhone_13, Hero11"))
        form.addRow(tr("Camera ID:"), self._camera_id_edit)

        # Bucket.
        self._bucket_combo = QComboBox()
        for value, label in _BUCKET_OPTIONS:
            self._bucket_combo.addItem(tr(label), value)
        self._bucket_combo.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        form.addRow(tr("Bucket:"), self._bucket_combo)

        outer.addLayout(form)

        # Buttons.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._run_btn = QPushButton(tr("Back up & verify"))
        self._run_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._run)
        buttons.addButton(self._run_btn,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── Source picker + removable detection ────────────────────

    def _pick_source(self) -> None:
        base = self._gateway.photos_base_path() if self._gateway else None
        start_dir = str(base) if base else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Pick the SD card / source folder"), start_dir,
        )
        if not chosen:
            return
        self._src_edit.setText(chosen)
        self._update_src_kind(Path(chosen))

    def _update_src_kind(self, path: Path) -> None:
        removable = is_removable(path)
        if removable:
            self._src_kind_label.setText(tr(
                "Removable drive — wipe will be OFFERED after verify "
                "passes. (Not yet — only after backup + verify.)"
            ))
        else:
            self._src_kind_label.setText(tr(
                "Non-removable source — wipe is NOT offered (only "
                "removable drives ever see the wipe prompt)."
            ))

    # ── Run ─────────────────────────────────────────────────────

    def _selected_event(self) -> Optional[Event]:
        if self._locked_event is not None:
            return self._locked_event
        if self._event_combo is None:
            return None
        idx = self._event_combo.currentIndex()
        if idx < 0:
            return None
        event_id = self._event_combo.itemData(idx)
        return next((e for e in self._events if e.id == event_id), None)

    def _run(self) -> None:
        src = self._src_edit.text().strip()
        if not src or not Path(src).is_dir():
            QMessageBox.warning(
                self, tr("Pick a source"),
                tr("Choose the source folder first."),
            )
            return
        event = self._selected_event()
        if event is None:
            QMessageBox.warning(
                self, tr("Pick an event"),
                tr("Pick which event to back up into."),
            )
            return
        camera_id = self._camera_id_edit.text().strip()
        if not camera_id:
            QMessageBox.warning(
                self, tr("Camera ID required"),
                tr("Give the source camera a name (e.g. "
                   "<code>G9_mkII</code>). It becomes a folder name "
                   "inside the safety mirror."),
            )
            return
        bucket = self._bucket_combo.currentData()

        # Data seam: the event root is resolved by the gateway (charter §0/§5.9), passed in.
        event_root = self._event_root
        if event_root is None:
            QMessageBox.warning(
                self, tr("Event location unavailable"),
                tr("This event's folder couldn't be resolved."),
            )
            return

        config = OffloadConfig(
            source_dir=Path(src),
            event_root=Path(event_root),
            camera_id=camera_id,
            bucket=bucket,
            day_by_number=(
                {d.day_number: d for d in event.trip_days}
                if event.trip_days else None
            ),
            collision=CollisionPolicy.UNIQUE,
            ran_at=datetime.now(),
            # Task #84 — Mode B: filter to only the files the user
            # kept during the pre-cull step. None = legacy "copy
            # everything found" behavior.
            included_names=self._included_names,
        )

        # Step 1: offload
        progress = self._show_progress(tr("Copying files…"))

        def _emit(msg: str, cur: int, tot: int) -> None:
            if tot > 0:
                progress.setMaximum(tot)
                progress.setValue(cur)
            progress.setLabelText(msg)
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

        try:
            offload = offload_to_captured(config, progress=_emit)
        except Exception as exc:                # noqa: BLE001
            progress.close()
            progress.deleteLater()
            log.exception("offload failed")
            QMessageBox.critical(
                self, tr("Backup failed"),
                tr("The backup did not complete: {err}").replace(
                    "{err}", str(exc)),
            )
            return

        # Step 2: verify
        progress.setLabelText(tr("Verifying SHA-256…"))
        try:
            verified = verify_offload(offload.manifest, progress=_emit)
        finally:
            progress.close()
            progress.deleteLater()

        # Step 3 (rebuild, spec/13 Option 1): record into the DB instead of baking EXIF.
        # The legacy baked the offset into the copied files' EXIF; the new model leaves the
        # originals byte-pristine and stores capture_time_raw + capture_time_corrected in the
        # item rows (charter §3, Nelson OK 2026-05-31). The offset is resolved exactly as
        # before — the F-019 precomputed value when present, else the single-camera prompt
        # (sidebar path) — then ``record_offload`` projects the manifest into item rows.
        recorded = 0
        if verified.passed:
            if self._precomputed_offset is not None:
                calibration = (float(self._precomputed_offset), False)
            else:
                settings_dict = (
                    self._gateway.settings.load().to_dict()
                    if self._gateway is not None else {}
                )
                calibration = calibration_offset_for_offload(
                    camera_id, settings_dict, parent=self,
                )
            if calibration is not None:
                offset_hours, remember = calibration
                try:
                    recorded = record_offload(
                        offload.manifest,
                        gateway=self._gateway, event_id=event.id,
                        camera_id=camera_id, bucket=bucket,
                        offset_hours=float(offset_hours),
                        event_root=event_root,
                    )
                except Exception:                          # noqa: BLE001
                    log.exception(
                        "Capture: failed to record offload for %s into event %s",
                        camera_id, getattr(event, "id", "?"),
                    )
                # Optional "remember" (sidebar path only — F-019 owns it for Capture):
                # persist the offset for this camera via the gateway settings.
                if remember and self._gateway is not None:
                    try:
                        saved = dict(getattr(
                            self._gateway.settings.load(),
                            "saved_camera_offsets", {}) or {})
                        saved[camera_id] = float(offset_hours)
                        self._gateway.settings.update(saved_camera_offsets=saved)
                    except Exception:                       # noqa: BLE001
                        log.exception(
                            "Failed to persist saved_camera_offsets for %s", camera_id,
                        )

        # Step 4: report
        summary_lines = [
            tr("Copied {n} file(s), {b} bytes.").replace(
                "{n}", str(offload.written_count)).replace(
                    "{b}", _fmt_bytes(offload.manifest.total_bytes)),
        ]
        if recorded:
            summary_lines.append(
                tr("Recorded {n} item(s) in the event database.").replace(
                    "{n}", str(recorded))
            )
        if offload.errors:
            summary_lines.append(
                tr("{n} error(s).").replace(
                    "{n}", str(len(offload.errors))))
        if verified.passed:
            summary_lines.append(
                tr("Integrity verify: PASSED ({n} file(s)).").replace(
                    "{n}", str(len(verified.ok))))
        else:
            summary_lines.append(
                tr("Integrity verify: FAILED — "
                   "{m} missing, {x} mismatch.").replace(
                       "{m}", str(len(verified.missing))).replace(
                           "{x}", str(len(verified.mismatch))))

        # Step 4: maybe offer wipe (very strict conditions)
        will_offer_wipe = (
            verified.passed
            and not offload.errors
            and is_removable(Path(src))
        )
        if will_offer_wipe:
            summary_lines.append("")
            summary_lines.append(tr(
                "Source is a removable drive AND verify passed — "
                "you may now safely delete the source files."
            ))
            self._maybe_offer_wipe(Path(src), summary_lines)
        else:
            QMessageBox.information(
                self, tr("Backup complete"),
                "\n".join(summary_lines),
            )

        self.backup_done.emit(event.id)
        if verified.passed:
            self.accept()

    def _maybe_offer_wipe(
        self, source: Path, summary_lines: list[str],
    ) -> None:
        """Two-confirmation wipe gate (CLAUDE.md invariant #9).

        Nelson 2026-05-22 (Model 3 v2): the confirmation text now
        also mentions the bake state — the user needs to know that
        Original Media is no longer byte-identical to the card (the
        EXIF has been corrected); the card was the byte-equal
        fallback up to this point.
        """
        first = QMessageBox.question(
            self, tr("Backup complete"),
            "\n".join(summary_lines) + "\n\n" + tr(
                "Delete the original files from the source now? "
                "This is irreversible.\n\n"
                "Original Media has been verified byte-for-byte against "
                "the source. After wipe, the Original Media copy is your "
                "only copy of these photos."
            ),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if first != QMessageBox.StandardButton.Yes:
            return
        # Second confirmation: type the drive letter to confirm.
        second = QMessageBox.warning(
            self, tr("Last chance"),
            tr(
                "About to delete every file under {p}. "
                "Are you absolutely sure?"
            ).replace("{p}", str(source)),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if second != QMessageBox.StandardButton.Yes:
            return
        # Wipe: delete every file the offload knew about from the
        # source. Folders left behind (the card's typical DCIM tree).
        # Audit-logged.
        removed = 0
        errors: list[tuple[Path, str]] = []
        for p in source.rglob("*"):
            if p.is_file():
                try:
                    p.unlink()
                    removed += 1
                except OSError as exc:
                    errors.append((p, str(exc)))
        log.warning(
            "Card-wipe: source=%s, removed=%d, errors=%d (user-confirmed "
            "twice, verify passed)", source, removed, len(errors),
        )
        msg = tr("Deleted {n} file(s) from {p}.").replace(
            "{n}", str(removed)).replace("{p}", str(source))
        if errors:
            msg += "\n" + tr(
                "{n} file(s) could not be deleted (likely in-use)."
            ).replace("{n}", str(len(errors)))
        QMessageBox.information(self, tr("Wipe complete"), msg)

    # ── Progress helper ────────────────────────────────────────

    def _show_progress(self, label: str) -> QProgressDialog:
        dlg = QProgressDialog(label, None, 0, 0, self)
        dlg.setWindowTitle(tr("Please wait"))
        dlg.setMinimumDuration(0)
        dlg.setModal(True)
        dlg.setCancelButton(None)
        dlg.show()
        return dlg


def _fmt_bytes(n: int) -> str:
    """Compact human byte formatter — 1.4 GB instead of 1416 MB."""
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024.0
    return f"{n} B"
