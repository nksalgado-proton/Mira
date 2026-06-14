"""Create-from-Past-Photos dialog — step-by-step flow (Nelson
2026-05-22 — supersedes the table-everything-up-front layout where
the per-camera calibration ran BEFORE the plan editor).

Flow:

1. **Source + event metadata** — user picks the parent folder that
   contains per-camera subfolders, names the event, picks the trip
   TZ via :class:`TzPicker`.
2. **Scan** — Mira reads every photo's EXIF DateTimeOriginal,
   builds a per-camera index, and derives the trip-day skeleton.
3. **Plan editor** — :class:`PlanEditorDialog` opens with the
   EXIF-derived days. User fills descriptions + the per-day
   ``tz_offset`` (defaults to the trip TZ).
4. **Per-TZ calibration loop** — the dialog detects the distinct
   TZs in the edited plan and opens
   :class:`CameraCalibrationDialog` once per TZ, in plan order. For
   each TZ the user picks every camera's offset (or its sync pair)
   for the days falling in that TZ. The "Step N of M" header makes
   the loop visible. A single-TZ trip naturally runs the dialog
   exactly once.
5. **Commit** — the dialog builds ``ReconcileConfig.tz_camera_groups``
   = ``{tz: [CameraInput, …]}`` and calls
   :func:`core.reconcile_pipeline.reconcile_commit`. The bake step
   picks the right calibration per-photo by looking up the day's
   ``tz_offset`` in the plan. Done in one ingest pass, no post-hoc
   adjustments.

Once accepted, the dialog emits ``event_created(event_id)`` for the
host to route to.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.fresh_source import SourceItem
from core.path_builder import sanitize_folder_name
from core.reconcile_pipeline import (
    CameraInput,
    ReconcileConfig,
    reconcile_scan,
)
from mira.gateway import Gateway
from mira.ingest import plan_from_reconcile, run_ingest
from mira.ui.base.plan_editor_dialog import PlanEditorDialog
from mira.ui.base.progress import run_with_progress
from mira.ui.i18n import tr

# NOTE (charter §5.2): this whole flow is the REUSED legacy PastPhotosDialog. The ONLY
# change from legacy is the data seam — the commit (`reconcile_commit` + `save_event`) is
# replaced by `plan_from_reconcile` → `run_ingest` (gateway), and `photos_base_path` reads
# go through the gateway. `reconcile_scan` is kept (read-only day-skeleton scan).

log = logging.getLogger(__name__)


_MEDIA_EXTS: frozenset[str] = frozenset({
    ".rw2", ".raf", ".arw", ".nef", ".cr2", ".cr3", ".dng", ".orf",
    ".pef", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff",
    ".mp4", ".mov", ".m4v",
})


def _carry_forward_fill(value_by_day: dict, all_day_numbers) -> dict:
    """Fill gaps in ``{day_number: value}`` so days BETWEEN known values get a
    value (carry-forward — last seen wins) and days BEFORE the first known
    back-fill from the first known.

    Trailing gaps — days AFTER the last known value — stay blank. spec/47
    fix #3 (Nelson 2026-06-06 eyeball: the last day of a real trip was a
    different country and the prior unconditional carry-forward filled it
    with the previous days' value, silently masking the TZ change). Leaving
    trailing gaps blank surfaces the anomaly so the user can spot + fill it.

    Returns an empty dict if ``value_by_day`` is empty.
    """
    if not value_by_day:
        return {}
    sorted_days = sorted(all_day_numbers)
    known_days = sorted(d for d in sorted_days if d in value_by_day)
    if not known_days:
        return {}
    first_known = known_days[0]
    last_known = known_days[-1]

    out: dict = {}
    current = value_by_day[first_known]
    for day in sorted_days:
        if day in value_by_day:
            current = value_by_day[day]
            out[day] = current
        elif day < first_known:
            out[day] = value_by_day[first_known]
        elif day <= last_known:
            out[day] = current
        # else: trailing gap → leave blank (the real-change signal).
    return out


def _has_direct_media(root: Path) -> bool:
    """True iff ``root`` has at least one media file at its top
    level (not in a subfolder). Single-camera flat folders like
    ``100GOPRO/`` look like this."""
    try:
        for p in root.iterdir():
            if p.is_file() and p.suffix.lower() in _MEDIA_EXTS:
                return True
    except OSError:
        pass
    return False


def _list_camera_subdirs(root: Path) -> list[Path]:
    """Subdirs of ``root`` that look like per-camera folders. Skips
    dotfiles and underscore-prefixed names (reserved for pipeline
    buckets)."""
    try:
        return sorted(
            d for d in root.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
        )
    except OSError:
        return []


class PastPhotosDialog(QDialog):
    """Step-by-step wizard for the "Create from Past Photos"
    sidebar action."""

    event_created = pyqtSignal(str)              # event_id

    def __init__(self, gateway: Gateway, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self.setWindowTitle(tr("Create event from photos"))
        self.setMinimumWidth(640)
        self.setMinimumHeight(360)
        self._build_ui()
        self._camera_ids: list[str] = []
        self._single_camera_id: Optional[str] = None
        # Set by the EXIF-scan-first caller (past_photos_cameras) to
        # short-circuit the in-dialog scan. None for standalone opens.
        from typing import Optional as _Opt
        from core.source_index import SourceIndex as _SourceIndex
        self._source_index: _Opt[_SourceIndex] = None

    # ── UI ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(tr(
            "Point Mira at a folder that holds your past photos. "
            "Any layout is fine — it scans subfolders too, and identifies "
            "the cameras from each photo's EXIF (you don't need to sort "
            "them into per-camera folders). Mira will:"
            "<ol>"
            "<li>Read every photo's EXIF date to find the trip days.</li>"
            "<li>Open the plan editor so you can fill in descriptions "
            "<b>and the timezone for each day</b> — set per-day "
            "timezones there, including any that differ from the rest "
            "of the trip.</li>"
            "<li>For each timezone in the plan, ask you to calibrate "
            "the cameras that shot on those days.</li>"
            "</ol>"
        ))
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(intro)

        # Source + event metadata. Each input lives inside a titled QGroupBox
        # so the field name reads as part of the frame, not as a stray label
        # floating to the left (Nelson 2026-06-06 eyeball #4 — same pattern
        # the FilterRail group boxes use, the preferred solution from the
        # spec/46 Slice 1 polish pass). The trip timezone field that used to
        # live here was removed 2026-05-22 — there's no single "trip TZ" in
        # a multi-TZ trip; the plan editor's per-day TZ column is the
        # canonical place to declare timezones.
        src_box = QGroupBox(tr("Photos parent folder"))
        src_box.setObjectName("FormFieldGroup")
        src_layout = QHBoxLayout(src_box)
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText(tr("<no folder picked yet>"))
        self._src_edit.setReadOnly(True)
        src_browse = QPushButton(tr("Browse…"))
        src_browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        src_browse.clicked.connect(self._pick_source)
        src_layout.addWidget(self._src_edit, stretch=1)
        src_layout.addWidget(src_browse)
        root.addWidget(src_box)

        name_box = QGroupBox(tr("Event name"))
        name_box.setObjectName("FormFieldGroup")
        name_layout = QVBoxLayout(name_box)
        self._event_name_edit = QLineEdit()
        self._event_name_edit.setPlaceholderText(
            tr("e.g. 2025 - Nepal trek"))
        name_layout.addWidget(self._event_name_edit)
        root.addWidget(name_box)

        # Camera-summary label — populated after Browse so the user
        # sees what was detected before clicking forward.
        self._cam_summary = QLabel("")
        self._cam_summary.setObjectName("PageHint")
        self._cam_summary.setWordWrap(True)
        root.addWidget(self._cam_summary)

        root.addStretch(1)

        # Buttons — the primary CTA fires the full multi-step flow.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._next_btn = QPushButton(tr("Scan photos & open plan →"))
        self._next_btn.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor))
        self._next_btn.setDefault(True)
        self._next_btn.clicked.connect(self._run_flow)
        buttons.addButton(
            self._next_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Source pick ────────────────────────────────────────────

    def _pick_source(self) -> None:
        base = self.gateway.photos_base_path()
        start_dir = str(base) if base else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, tr("Pick the photos parent folder"), start_dir,
        )
        if not chosen:
            return
        self._src_edit.setText(chosen)
        self._detect_cameras(Path(chosen))

    def _detect_cameras(self, root: Path) -> None:
        """EXIF-scan the source tree to derive camera identities from
        EXIF Make+Model (e.g. ``DC-G9M2`` / ``iPhone 11``) — NOT from
        folder names like ``iPhone_Nelson`` / ``iPhone_Aida`` which
        would be the user's organisational labels, not the actual
        camera identities.

        Nelson 2026-05-23 regression: an earlier intermediate version
        of the plan-first flow used folder names as ``camera_id``s,
        which broke the long-standing "cameras come from EXIF"
        invariant (task #30). This restores it by running
        :func:`core.source_index.scan_source_tree` at folder-pick
        time and storing the result on ``self._source_index`` so it
        flows through to reconcile_commit and every per-TZ
        calibration sub-dialog (``PastPhotosCamerasDialog``).
        """
        from core.source_index import scan_source_tree

        self._single_camera_id = None
        self._source_index = None
        self._camera_ids = []

        # Show a progress dialog — for a Nepal-sized archive
        # (~1300 files) the EXIF scan runs ~10-30 s. Without
        # feedback the dialog looks frozen.
        progress = QProgressDialog(
            tr("Scanning EXIF…"), None, 0, 0, self,
        )
        progress.setWindowTitle(tr("Please wait"))
        progress.setMinimumDuration(0)
        progress.setModal(True)
        progress.setCancelButton(None)
        progress.show()

        def _emit(msg: str, cur: int, tot: int) -> None:
            if tot > 0:
                progress.setMaximum(tot)
                progress.setValue(cur)
            progress.setLabelText(msg)
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()

        try:
            index = scan_source_tree(root, progress=_emit)
        finally:
            progress.close()
            progress.deleteLater()

        if index.total_files == 0:
            self._cam_summary.setText(tr(
                "No photos found here. Pick a folder that holds your "
                "photos."
            ))
            return

        self._source_index = index
        scanned = index.cameras_sorted()
        self._camera_ids = [c.camera_id for c in scanned]
        if len(scanned) == 1:
            # Single-camera path — reconcile_pipeline takes a
            # ``single_camera_id`` to bypass per-subdir walking.
            self._single_camera_id = scanned[0].camera_id

        # Render the summary using EXIF-derived names plus file counts
        # so the user can spot any obvious mis-identification before
        # proceeding (e.g., an "Unknown" bucket would show up here).
        bits = []
        for c in scanned:
            bits.append(
                f"<code>{c.camera_id}</code> ({c.file_count})"
            )
        self._cam_summary.setText(tr(
            "<b>{n} camera(s) detected (from EXIF):</b> {names}"
        ).replace("{n}", str(len(scanned)))
         .replace("{names}", ", ".join(bits)))

    # ── Flow ───────────────────────────────────────────────────

    def _run_flow(self) -> None:
        """The full step-by-step flow: validate → scan → plan editor
        → per-TZ calibration loop → reconcile_commit.

        Timezone handling (Nelson 2026-05-22, plan-first refactor):
        the dialog no longer collects a single "trip TZ" upfront —
        the plan editor's per-day TZ column is the authoritative
        source. For the pre-flight scan we use the user's system-
        local TZ as the placeholder (the reference camera is
        usually NTP-synced to local-trip-TZ anyway, so the value
        only matters for the initial plan skeleton's day clustering;
        the user adjusts each day's TZ in the editor next). After
        the plan editor returns we derive the dominant TZ from the
        edited plan to use as ``trip_tz_offset`` for commit.
        """
        if not self._validate_inputs():
            return

        src = Path(self._src_edit.text().strip())
        name = self._event_name_edit.text().strip()

        # Name-collision guard (Nelson 2026-05-23): two events sharing a name resolve to
        # the same on-disk folder. Matches now come from the gateway index (the data seam).
        from mira.ui.base.name_collision import confirm_name_collision
        matches = [
            e for e in self.gateway.list_events()
            if (e.get("name") or "").strip().lower() == name.lower()
        ]
        if not confirm_name_collision(self, name, matches):
            self._event_name_edit.setFocus()
            return

        base = self.gateway.photos_base_path()
        photos_base = str(base) if base else ""
        if not photos_base:
            QMessageBox.warning(
                self, tr("Photos folder not configured"),
                tr("Set the Photos folder in Settings first — that's "
                   "where Mira writes new events."),
            )
            return

        # Initial scan TZ — system-local. This drives the per-day
        # clustering in the plan skeleton. The user replaces each
        # day's TZ in the plan editor next.
        scan_tz = _system_local_tz_offset()

        # Pre-flight scan with placeholder CameraInputs (one per
        # detected camera, configured_tz=scan_tz, phone-guessed by
        # name, first phone = reference). reconcile_scan only needs
        # the reference + the timestamps; the actual offsets don't
        # matter for finding the days.
        pre_cameras = self._build_placeholder_cameras(scan_tz)
        scan_cfg = ReconcileConfig(
            per_camera_source=src,
            per_day_source=None,
            photos_base_path=Path(photos_base),
            event_name=name,
            trip_tz_offset=scan_tz,
            cameras=pre_cameras,
            single_camera_id=self._single_camera_id,
            is_past_photos=True,
            source_index=self._source_index,
        )
        scan = reconcile_scan(scan_cfg)

        # Plan editor loop (re-opens if descriptions are missing;
        # preserves user edits).
        from core.trip_plan_parser import parse_trip_plan
        from core.trip_plan_skeleton import days_to_plan_text

        # Bug fix #119 (Nelson 2026-05-23): rebuild the day skeleton
        # from EVERY camera's photos, not just the reference camera.
        # The old `reconcile_scan` path uses
        # `generate_plan_skeleton_from_per_camera` which only walks
        # the reference's folder — if that camera (typically a phone)
        # wasn't shooting on a given day but another camera was, the
        # day was missing from the initial plan and the user had to
        # add it via the orphan-dates recovery dialog. The
        # source_index already has every photo's EXIF timestamp, so
        # derive the date set directly from it.
        plan_text = self._build_initial_plan_text(scan_tz, scan.plan_text)
        if not plan_text:
            errors = [w.message for w in scan.warnings
                      if w.severity == "error"]
            QMessageBox.warning(
                self, tr("Couldn't build a plan"),
                "\n".join(errors) or tr(
                    "No usable photos were found in those subfolders."),
            )
            return

        parsed_days = parse_trip_plan(plan_text, home_timezone=scan_tz)

        # spec/47 — phone-driven auto-fill. Phones write OffsetTimeOriginal +
        # GPS to EXIF; we derive per-day TZ (majority vote) and per-day country
        # (arrival GPS — last point of the day wins for travel days) and
        # pre-fill the plan so the user only has to type when there's no
        # phone signal. Camera-only sources get nothing here (parity with
        # PreingestPlanConfirmDialog's behaviour in the capture flow).
        phone_tz_by_day, country_by_day, description_by_day, paths_by_day = (
            self._derive_phone_autofill_and_paths(parsed_days)
        )
        # Unconditional override (Nelson 2026-06-06 eyeball #2): parse_trip_plan
        # calls _resolve_tz_inheritance, so every day's tz_offset is already set
        # to scan_tz (system local) before we get here — a `if d.tz_offset is
        # None` gate would never fire. Phone EXIF is authoritative when present,
        # so override. Country has no pre-existing source; setting it is also
        # always safe.
        #
        # spec/47 fix #3: when auto-fill resolves a country, pre-fill the free-
        # text Location field with the alpha-2 code (only if Location is empty
        # — never override user intent from a [LOC:..] tag). Gives the user a
        # sensible starting value to refine ("CR" → "La Fortuna, CR" etc.).
        for d in parsed_days:
            if d.day_number in phone_tz_by_day:
                d.tz_offset = phone_tz_by_day[d.day_number] / 60.0
            if d.day_number in country_by_day:
                cc = country_by_day[d.day_number]
                d.country_code = cc
                if not d.location:
                    d.location = cc
            # Nelson 2026-06-06: also pre-fill description with the
            # nearest city + admin region (e.g. "San Carlos, Salta"). Only
            # when description is empty — never override user intent.
            if d.day_number in description_by_day and not (d.description or "").strip():
                d.description = description_by_day[d.day_number]

        edited_days = self._run_plan_editor(parsed_days, paths_by_day)
        if edited_days is None:                                # cancelled
            return

        # Now derive the trip's dominant TZ from the EDITED plan —
        # this is what flows through to ``trip_tz_offset`` for
        # commit. Picks the most common per-day TZ; ties broken by
        # plan order (first occurrence wins).
        trip_tz = _dominant_tz_from_days(edited_days, scan_tz)

        # Per-TZ calibration loop. Detect distinct TZs from the
        # edited plan; for each, open CameraCalibrationDialog once.
        # Carries the user's phone/reference/configured_tz
        # decisions forward so they don't re-tick boxes per TZ.
        distinct_tzs = _distinct_tzs_in_plan(edited_days, trip_tz)
        if not distinct_tzs:
            QMessageBox.warning(
                self, tr("No timezone in plan"),
                tr("The plan has no timezone information. Set at "
                   "least one day's TZ and try again."),
            )
            return
        tz_camera_groups = self._run_calibration_loop(
            distinct_tzs, edited_days, src)
        if tz_camera_groups is None:                          # cancelled
            return

        # Bug fix #119 (Nelson 2026-05-23, second half): the
        # orphan-dates check now runs AFTER the calibration loop so
        # it can use the *corrected* dates the commit will actually
        # use for routing. Before, the check used raw EXIF dates;
        # if a camera had a clock offset that shifted a photo into a
        # neighbouring day, the user-added "orphan" day would end up
        # empty because the photo was routed elsewhere by the commit.
        edited_days, skip_paths = self._handle_orphan_dates(
            edited_days, name,
            tz_camera_groups=tz_camera_groups, trip_tz=trip_tz,
        )
        if edited_days is None:                                # cancelled
            return

        # Commit — the data seam (charter §5.2). Everything above is the reused legacy
        # flow; here, instead of `reconcile_commit` (copy + EXIF-bake + `save_event`), we
        # convert the gathered plan + calibration into an engine `IngestPlan` and run the
        # new gateway-backed ingest (copy verbatim, virtual-EXIF records, materialise).
        self._commit(
            src=src, name=name, photos_base=Path(photos_base),
            edited_days=edited_days, tz_camera_groups=tz_camera_groups,
            trip_tz=trip_tz, skip_paths=skip_paths or set(),
        )

    # ── Step helpers ───────────────────────────────────────────

    def _validate_inputs(self) -> bool:
        src = self._src_edit.text().strip()
        if not src or not Path(src).is_dir():
            QMessageBox.warning(
                self, tr("Pick a folder"),
                tr("Choose the parent folder that holds your photos."),
            )
            return False
        if not self._event_name_edit.text().strip():
            QMessageBox.warning(
                self, tr("Name required"),
                tr("Give the event a name."),
            )
            return False
        if not self._camera_ids:
            QMessageBox.warning(
                self, tr("No cameras detected"),
                tr("No per-camera subfolders or photos found in the "
                   "picked folder."),
            )
            return False
        return True

    def _build_placeholder_cameras(self, trip_tz: float) -> list[CameraInput]:
        """One CameraInput per detected camera, configured_tz=trip_tz
        (no shift), with ``is_phone`` taken from the EXIF scan's
        ``ScannedCamera.is_phone`` flag when available (Nelson 2026-
        05-23: the camera_id is now EXIF-derived, so the phone flag
        should be too — the name-substring heuristic was the legacy
        folder-name path). First phone = reference. Used as the
        input to ``reconcile_scan`` (which only needs the reference
        to derive day clusters)."""
        scanned_by_id: dict[str, bool] = {}
        if self._source_index is not None:
            for c in self._source_index.cameras_sorted():
                scanned_by_id[c.camera_id] = bool(c.is_phone)
        cams = [
            CameraInput(
                camera_id=cid,
                configured_tz=trip_tz,
                is_phone=scanned_by_id.get(cid, _looks_like_phone(cid)),
            )
            for cid in self._camera_ids
        ]
        # Pick a reference: first phone, else first camera.
        ref = next((c for c in cams if c.is_phone), cams[0] if cams else None)
        if ref is not None:
            ref.is_reference = True
        return cams

    def _derive_phone_autofill_and_paths(self, parsed_days):
        """Per-day TZ + arrival-country + arrival-place auto-fill + per-day
        source paths for the plan-editor Browse column (spec/47).

        Returns ``(phone_tz_by_day, country_by_day, description_by_day, paths_by_day)``:

        * ``phone_tz_by_day``: ``{day_number: tz_offset_minutes}`` from
          :func:`core.phone_tz.phone_day_tz` (majority vote per day), then
          gap-filled across the whole trip by carry-forward — last-known
          wins, first-known back-fills any leading gap (Nelson 2026-06-06
          eyeball: TZ should be filled for *all* days, not just the ones
          where the phone happened to write OffsetTimeOriginal + GPS).
        * ``country_by_day``: ``{day_number: alpha2}`` from
          :func:`core.phone_tz.phone_day_arrival_gps` + ``country_code_for``
          on the *arrival* GPS (Nelson 2026-06-06: "use the country he has
          arrived in" — for travel days the destination wins, not the
          mean). Same carry-forward gap-fill so country reads consistently
          across the trip and stays in agreement with TZ.
        * ``paths_by_day``: ``{day_number: [Path, …]}`` from the scanned
          source items, grouped by their EXIF date matched to the plan-day's
          date. Drives the Browse column when there's no phone (the
          manual-entry support case).

        Returns three empty dicts when there's no source_index (defensive —
        unit tests can run the dialog without a real scan).
        """
        from core.country_lookup import country_code_for
        from core.phone_tz import phone_day_arrival_gps, phone_day_tz
        from core.place_lookup import describe as describe_place

        empty: dict = {}
        if self._source_index is None or not self._source_index.items:
            return empty, empty, empty, empty

        items = list(self._source_index.items)
        date_to_day = {
            d.date: d.day_number for d in parsed_days if d.date is not None
        }
        day_for_path: dict = {}
        paths_by_day: dict = {}
        for item in items:
            if item.timestamp is None:
                continue
            day_n = date_to_day.get(item.timestamp.date())
            if day_n is None:
                continue
            day_for_path[item.path] = day_n
            paths_by_day.setdefault(day_n, []).append(item.path)

        try:
            phone_tz_by_day = phone_day_tz(items, day_for_path)
        except Exception:  # noqa: BLE001 — auto-fill must fail closed
            log.exception("spec/47: phone_day_tz failed — TZ auto-fill skipped")
            phone_tz_by_day = {}

        country_by_day: dict = {}
        description_by_day: dict = {}
        try:
            arrival = phone_day_arrival_gps(items, day_for_path)
            for day_n, (lat, lon) in arrival.items():
                code = country_code_for(lat, lon)
                if code:
                    country_by_day[day_n] = code
                # Pre-fill the description with the nearest city + admin1
                # (e.g. "San Carlos, Salta"). Failure-tolerant: any one
                # day's lookup that breaks just leaves that day blank.
                try:
                    place = describe_place(lat, lon)
                    if place:
                        description_by_day[day_n] = place
                except Exception:  # noqa: BLE001
                    log.exception(
                        "spec/47: place lookup for day %s failed — "
                        "description auto-fill skipped for this day", day_n
                    )
        except Exception:  # noqa: BLE001 — same fail-closed contract
            log.exception(
                "spec/47: phone arrival-country derivation failed — "
                "country auto-fill skipped"
            )

        # Gap-fill TZ + country across the trip so every day reads consistently
        # (Nelson 2026-06-06 eyeball: country + TZ should be filled for ALL
        # days, not just the ones with phone-EXIF coverage). Description is NOT
        # gap-filled — Day 1 in Buenos Aires shouldn't drag its name across
        # Day 2 in Bariloche; per-day place names stand alone.
        all_day_numbers = [d.day_number for d in parsed_days]
        phone_tz_by_day = _carry_forward_fill(phone_tz_by_day, all_day_numbers)
        country_by_day = _carry_forward_fill(country_by_day, all_day_numbers)

        return phone_tz_by_day, country_by_day, description_by_day, paths_by_day

    def _run_plan_editor(self, parsed_days, paths_by_day=None):
        """Open PlanEditorDialog and loop until the user accepts a
        non-empty plan or cancels. Returns the edited days, or None
        if the user cancelled."""
        while True:
            editor = PlanEditorDialog(
                self, trip_days=parsed_days,
                day_photo_paths=paths_by_day or {},
            )
            if editor.exec() != QDialog.DialogCode.Accepted:
                return None
            edited_days = editor.get_trip_days()
            if not edited_days:
                QMessageBox.warning(
                    self, tr("Empty plan"),
                    tr("The plan needs at least one day."),
                )
                parsed_days = edited_days
                continue
            # Description is no longer required (Nelson 2026-06-06 eyeball #3).
            # Folder names fall back to "Dia N" when description is empty;
            # the user is free to fill descriptions later from the Event menu's
            # Edit-plan action.
            return edited_days

    def _orphan_check_pairs(
        self, *,
        tz_camera_groups: Optional[dict] = None,
        trip_tz: Optional[float] = None,
    ) -> list:
        """Per-file ``(path, timestamp)`` pairs for the orphan-dates
        check.

        When ``tz_camera_groups`` + ``trip_tz`` are provided (Bug fix
        #119 — call site is post-calibration-loop), apply each
        camera's calibration so the timestamps are corrected to
        trip-local. The orphan check then sees the same dates the
        commit will use for routing — eliminating the case where a
        clock-offset camera's photos appear under one date in the
        orphan dialog but land under a neighbouring date on disk.

        When neither is provided (legacy call), pass raw EXIF times
        through — preserves the historical behaviour for any caller
        that hasn't been moved to the post-calibration ordering."""
        items = list(self._source_index.items)

        if not tz_camera_groups or trip_tz is None:
            return [(item.path, item.timestamp) for item in items]

        from core.reconcile_pipeline import _build_calibrations_for_group

        # Unified camera_id → calibration map across every TZ group.
        # Phones have no calibration (no entry in the map) and pass
        # through raw, matching commit-time behaviour
        # (``_build_calibrations`` skips phones).
        cal_by_camera = {}
        for tz, cams in tz_camera_groups.items():
            calibrations, _warnings = _build_calibrations_for_group(
                cams, float(tz))
            cal_by_camera.update(calibrations)

        pairs = []
        for item in items:
            if item.timestamp is None:
                continue
            cal = cal_by_camera.get(item.camera_id)
            if cal is not None and cal.has_any_source:
                corrected = item.timestamp + cal.offset_at(item.timestamp)
            else:
                corrected = item.timestamp
            pairs.append((item.path, corrected))
        return pairs

    def _build_initial_plan_text(
        self, scan_tz: float, fallback_plan_text: str,
    ) -> str:
        """Bug fix #119: derive the initial plan from EVERY camera's
        photos, not just the reference. Uses ``self._source_index``
        (already populated with every file's EXIF timestamp) to find
        the full set of dates the source covers.

        Returns the canonical plan text the editor will load. Falls
        back to ``fallback_plan_text`` (the legacy reference-only
        skeleton from ``reconcile_scan``) when the source_index is
        missing or empty — preserving existing behaviour for code
        paths that don't populate the index."""
        if self._source_index is None or not self._source_index.items:
            return fallback_plan_text or ""

        from datetime import datetime as _dt
        all_dates = sorted({
            item.timestamp.date()
            for item in self._source_index.items
            if isinstance(item.timestamp, _dt)
        })
        if not all_dates:
            return fallback_plan_text or ""

        from core.models import TripDay
        from core.trip_plan_skeleton import days_to_plan_text
        days = [
            TripDay(
                day_number=i + 1,
                date=d,
                description="",
                tz_offset=scan_tz,
            )
            for i, d in enumerate(all_dates)
        ]
        return days_to_plan_text(days, scan_tz)

    def _handle_orphan_dates(
        self,
        edited_days,
        event_name: str,
        *,
        tz_camera_groups: Optional[dict] = None,
        trip_tz: Optional[float] = None,
    ) -> tuple[Optional[list], set]:
        """Capture-time plan-disk consistency check (task #109).

        Looks at the source's EXIF dates against the edited plan;
        for any dates not in the plan, shows the OrphanDatesDialog
        and acts on the user's choice:

          * Add → extend edited_days with placeholder TripDays
          * Skip → return the set of file paths to exclude from
            the commit (caller filters the SourceIndex)
          * Cancel → returns ``(None, set())`` so the caller bails

        Returns ``(edited_days_maybe_extended, skip_paths)``.
        ``edited_days`` is None when the user cancelled. When no
        orphans exist, returns the input unchanged with empty
        skip_paths.

        Bug fix #119 (Nelson 2026-05-23): when
        ``tz_camera_groups`` and ``trip_tz`` are provided, the check
        applies each camera's calibration to compute the corrected
        date — matching where the commit will actually route each
        photo. Without this, a clock-offset camera could land its
        photos on a different day than the user saw in the dialog,
        leaving the just-added "orphan day" folder empty.
        """
        if self._source_index is None or not self._source_index.items:
            return edited_days, set()

        from core.capture_plan_check import (
            extend_plan_with_dates,
            find_orphan_dates,
            summarise_orphans,
        )

        pairs = self._orphan_check_pairs(
            tz_camera_groups=tz_camera_groups, trip_tz=trip_tz,
        )
        orphans = find_orphan_dates(pairs, edited_days)
        if not orphans:
            return edited_days, set()

        from mira.ui.pages.orphan_dates_dialog import (
            OrphanDatesDialog,
            Result as OrphanResult,
        )

        dlg = OrphanDatesDialog(
            orphans=summarise_orphans(orphans),
            event_name=event_name,
            parent=self,
        )
        dlg.exec()
        choice = dlg.result_choice
        dlg.deleteLater()

        if choice == OrphanResult.CANCEL:
            return None, set()

        if choice == OrphanResult.SKIP_FILES:
            # Build the set of paths to drop from the SourceIndex.
            skip = {
                p for paths in orphans.values() for p in paths
            }
            return edited_days, skip

        # ADD_TO_PLAN: extend edited_days with the orphan dates.
        # We mutate a throw-away Event so extend_plan_with_dates can
        # use its existing in-place mutation API — the returned
        # edited_days list IS the updated set.
        from core.models import Event as _Event

        stub_event = _Event(name=event_name)
        stub_event.trip_days = list(edited_days)
        extend_plan_with_dates(stub_event, list(orphans.keys()))
        return stub_event.trip_days, set()

    def _run_calibration_loop(
        self,
        distinct_tzs: list[float],
        edited_days,
        source_root: Path,
    ) -> Optional[dict[float, list[CameraInput]]]:
        """Open :class:`PastPhotosCamerasDialog` (the per-camera
        Mode-column dialog) ONCE PER distinct TZ in the plan. Same
        UX as the single-TZ case — just looped, with "Step N of M"
        labelling and the day-numbers callout. Returns the per-TZ
        groups, or None on cancel.

        For each TZ the user chooses, per camera:
          * **I know the timezone** → TzPicker (defaults to this
            TZ); the offset is ``tz_picker_value − cam_clock_tz``,
            here translated to ``configured_tz`` on a CameraInput.
          * **I don't know — pick a sync pair** → SyncPairPickerDialog
            against the reference camera; the derived pair is
            attached to the CameraInput for that TZ.
        """
        from mira.ui.pages.past_photos_cameras import (
            PastPhotosCamerasDialog,
            _looks_like_phone as _looks_like_phone_ext,
        )

        groups: dict[float, list[CameraInput]] = {}
        total = len(distinct_tzs)
        # When multi-TZ, the source_index path needs to be reused so
        # the per-camera row hints (file count / date range) are
        # consistent across the loop. ``self._source_index`` is set
        # by EXIF-scan-first callers; standalone opens fall back to
        # the camera_ids path.
        for i, tz in enumerate(distinct_tzs, start=1):
            days_for_tz = sorted(
                d.day_number for d in edited_days
                if float(
                    d.tz_offset if d.tz_offset is not None else tz
                ) == tz
            )
            if self._source_index is not None:
                dlg = PastPhotosCamerasDialog(
                    source_index=self._source_index,
                    root_dir=str(source_root),
                    trip_tz=tz,
                    ordinal=(i, total),
                    day_numbers=days_for_tz,
                    parent=self,
                )
            else:
                dlg = PastPhotosCamerasDialog(
                    camera_ids=self._camera_ids,
                    root_dir=str(source_root),
                    trip_tz=tz,
                    ordinal=(i, total),
                    day_numbers=days_for_tz,
                    parent=self,
                )
            if dlg.exec() != QDialog.DialogCode.Accepted:
                dlg.deleteLater()
                return None
            per_camera = dlg.per_camera()
            ref_id = dlg.reference_id
            dlg.deleteLater()

            # Convert the dialog's per_camera dict to a list of
            # CameraInput for this TZ.
            cams: list[CameraInput] = []
            for cam_id, conf in per_camera.items():
                pair = conf.get("pair")
                cams.append(CameraInput(
                    camera_id=cam_id,
                    configured_tz=float(
                        conf.get("configured_tz", tz)),
                    calibration_pairs=[pair] if pair is not None else [],
                    is_phone=_looks_like_phone_ext(cam_id),
                    is_reference=(cam_id == ref_id),
                ))
            groups[tz] = cams
        return groups

    def _commit(
        self, *, src: Path, name: str, photos_base: Path,
        edited_days, tz_camera_groups, trip_tz: float, skip_paths: set,
    ) -> None:
        """The data seam (charter §5.2): build an engine ``IngestPlan`` from the reused
        flow's gathered plan + calibration, then run the gateway-backed ingest behind the
        standard progress dialog. Replaces the legacy ``reconcile_commit`` + ``save_event``
        — copy verbatim, virtual-EXIF records, materialise (no bake)."""
        import uuid

        event_root = photos_base / sanitize_folder_name(name)
        items = [
            SourceItem(it.path, it.timestamp, it.camera_id)
            for it in (self._source_index.items if self._source_index else [])
            if it.path not in skip_paths
        ]
        plan = plan_from_reconcile(
            event_id=uuid.uuid4().hex, event_name=name,
            event_root=event_root, source_root=src,
            edited_days=edited_days, tz_camera_groups=tz_camera_groups, trip_tz=trip_tz,
        )
        ok, result = run_with_progress(
            self, tr("Creating event"),
            lambda p: run_ingest(plan, self.gateway, source_items=items, progress=p),
            label=tr("Copying photos…"),
        )
        if not ok:
            log.error("ingest failed: %s", result)
            QMessageBox.warning(self, tr("Import failed"), str(result))
            return

        log.info(
            "Past-photos import done: event=%s (%d photos, %d videos, %d out-of-range, "
            "%d quarantined)",
            plan.event_id, result.photos, result.videos,
            result.out_of_day_range, result.quarantined,
        )
        if result.items_created == 0:
            QMessageBox.warning(
                self, tr("Nothing imported"),
                tr("No photos were imported into the new event."),
            )
            self.event_created.emit(plan.event_id)
            return

        summary = [
            tr("Imported {p} photo(s) and {v} video(s) into the new event.")
            .replace("{p}", str(result.photos)).replace("{v}", str(result.videos))
        ]
        if result.out_of_day_range:
            summary.append(tr(
                "{o} fell outside the trip's day range and were placed in "
                "_out_of_day_range/ — review them in the culler."
            ).replace("{o}", str(result.out_of_day_range)))
        if result.quarantined:
            summary.append(tr(
                "{q} had no readable timestamp and were quarantined."
            ).replace("{q}", str(result.quarantined)))
        if result.integrity_failures:
            summary.append(tr(
                "{n} failed an integrity check — see the log."
            ).replace("{n}", str(len(result.integrity_failures))))
        QMessageBox.information(self, tr("Done"), " ".join(summary))

        # Nelson 2026-06-06 — surface the unified Event dialog inline so
        # Type/Subtype/Tags/People/Notes are collected as part of the
        # creation flow, not deferred to a later edit. The user lands on
        # the Information tab; if they want to tweak the plan after seeing
        # the actual ingest result, the Plan tab is right there. If they
        # cancel the dialog the event stays "unclassified" and can be
        # edited later via the tile's title zone.
        try:
            from mira.ui.pages.event_dialog import EventDialog, TAB_INFO
            evt_dlg = EventDialog(
                self.gateway, plan.event_id,
                parent=self, initial_tab=TAB_INFO,
            )
            evt_dlg.exec()
        except Exception:  # pragma: no cover — never block flow on dialog issues
            log.exception("EventDialog failed for event %s", plan.event_id)

        self.event_created.emit(plan.event_id)
        self.accept()


def _distinct_tzs_in_plan(days, trip_tz: float) -> list[float]:
    """Distinct TZs in the edited plan, in plan order. Days with
    ``tz_offset is None`` use ``trip_tz``. The trip_tz itself is
    always first in the list so the calibration loop's "Step 1 of N"
    starts with the main TZ."""
    seen: list[float] = []
    for d in days:
        tz = float(
            d.tz_offset if d.tz_offset is not None else trip_tz)
        if tz not in seen:
            seen.append(tz)
    # Pull the trip TZ to position 0 if present and not already there.
    if trip_tz in seen and seen[0] != trip_tz:
        seen.remove(trip_tz)
        seen.insert(0, trip_tz)
    return seen


def _source_index_excluding(index, skip_paths):
    """Return a NEW SourceIndex with the same shape as ``index`` but
    with files in ``skip_paths`` removed (Nelson 2026-05-23 task #109
    — used when the user picks 'Skip these photos' at the
    orphan-dates dialog).

    The per-camera ``ScannedCamera.paths`` / ``timestamps`` and the
    flat ``items`` list are filtered in step; camera entries that
    end up with zero files are dropped entirely so reconcile doesn't
    see an empty bucket."""
    from dataclasses import replace as _replace
    from core.source_index import ScannedCamera, SourceIndex

    skip = set(skip_paths or [])
    if not skip:
        return index
    new_cameras: dict[str, ScannedCamera] = {}
    for cam_id, cam in index.cameras.items():
        kept = tuple(p for p in cam.paths if p not in skip)
        if not kept:
            continue
        kept_timestamps = {
            p: t for p, t in cam.timestamps.items() if p not in skip
        }
        new_cameras[cam_id] = _replace(
            cam,
            paths=kept,
            timestamps=kept_timestamps,
            file_count=len(kept),
        )
    new_items = [it for it in index.items if it.path not in skip]
    return SourceIndex(
        root=index.root,
        cameras=new_cameras,
        total_files=len(new_items),
        items=new_items,
    )


def _system_local_tz_offset() -> float:
    """The user's system-local TZ as a float (e.g. -3.0 for São
    Paulo). Used as the placeholder TZ for the pre-flight scan in
    the past-photos flow — the plan editor's per-day TZ column is
    the authoritative source. Falls back to 0.0 if the system can't
    report a UTC offset (rare; defensive)."""
    from datetime import datetime
    try:
        off = datetime.now().astimezone().utcoffset()
        if off is None:
            return 0.0
        return off.total_seconds() / 3600.0
    except (OSError, ValueError):
        return 0.0


def _dominant_tz_from_days(days, fallback: float) -> float:
    """The most common ``tz_offset`` across ``days``. Ties broken by
    first-occurrence in plan order. Days with ``tz_offset=None`` are
    treated as ``fallback``."""
    if not days:
        return fallback
    counts: dict[float, int] = {}
    order: list[float] = []
    for d in days:
        tz = float(d.tz_offset if d.tz_offset is not None else fallback)
        if tz not in counts:
            order.append(tz)
        counts[tz] = counts.get(tz, 0) + 1
    # Pick the highest count; tie → first-occurrence wins.
    best = order[0]
    best_count = counts[best]
    for tz in order[1:]:
        if counts[tz] > best_count:
            best = tz
            best_count = counts[tz]
    return best


# Phone-name heuristics — same list the rest of the codebase uses.
_PHONE_SUBSTRINGS = (
    "phone", "iphone", "android",
    "celular", "telefone", "móvel", "movel",
    "pixel", "samsung", "galaxy", "redmi", "xiaomi", "huawei",
)


def _looks_like_phone(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in _PHONE_SUBSTRINGS)
