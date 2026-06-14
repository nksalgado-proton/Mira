"""F-019 — Pre-ingest plan-confirm dialog ("Confirm trip plan and timezone…", spec/13).

PORTED from legacy ``ui/pages/preingest_dialog.py`` (charter §0/§5.2). UI + flow verbatim;
the ONLY changes are the data seam: the plan persist (legacy ``save_event``) now goes
through the gateway (``save_trip_days``), and the remembered camera-TZ read/write uses the
gateway SettingsRepo instead of legacy ``core.settings``. The ``camera_clocks`` suppression
write is dropped (its only consumer — the Cull-phase clock dialog — is not yet ported; the
Camera row carries the offset when it is).

Runs once per source before the Quick Sweep launches (and before
the existing Mode A / Mode B chooser). For every day the source
carries photos for, the user sees:

* The plan's day row — description / location / TZ — as **editable
  fields**. On Apply, edits write back to the plan via
  ``data.event_store.save_event``.
* The camera info (Make + Model) + EXIF-derived capture-time range
  for that day.
* The TZ-sanity warnings the engine computed (future-dated,
  older-than-trip, night-majority, stale-gap). Each warning gets a
  visible chip so the user notices before the bake runs.
* The per-brand "how to set the clock correctly on this body"
  instructions (collapsed by default; expanded when any TZ warning
  fired).

Bottom of the dialog: a single ``Shift all photos by N hours when
copying`` field. This matches the existing bake pipeline's one-
offset contract — ``bake_offload_manifest(manifest, offset_hours,
...)``. If the user genuinely has per-day different offsets (rare —
crossed into a different TZ mid-trip), they cancel out, fix the
plan's per-day TZ, and re-run; the bake will still apply ONE shift
across the whole source.

Apply returns ``(offset_hours, remember)`` — same shape as
``calibration_offset_for_offload``. MainWindow's ``_on_capture_phase``
uses that to construct a ``BackUpCardDialog`` with the offset
already resolved, so the dialog's own clock-prompt step is
skipped (the CameraClockDialog suppression discussed during the
F-019 design freeze, Nelson 2026-05-25).

Cancel = no plan changes, no bake; the whole capture flow aborts.

Spec: ``docs/18-culler-spec.md`` §"Pre-ingest plan-confirm dialog".
Engine: ``core.preingest_check``.
"""

from __future__ import annotations

import logging
from datetime import date as _date_cls
from datetime import timedelta
from pathlib import Path
from typing import Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.country_lookup import country_code_for
from core.fresh_source import SourceItem
from core.models import Event, TripDay
from core.phone_tz import phone_day_summaries
from core.preingest_check import (
    BrandTip,
    PerDayVerdict,
    PreingestPlan,
    TzWarning,
    build_preingest_plan,
)
from core.tz_locations import format_utc_offset
from mira.ui.base.classification_panel import ClassificationPanel
from mira.ui.base.country_picker import (
    country_code_from_combo,
    display_label_for_code,
    make_single_country_combo,
)
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

# F-019 follow-up (Nelson 2026-05-25): the camera's last-known
# timezone, persisted per camera_id. Stored separately from the
# legacy ``saved_camera_offsets`` (which the sidebar entry's
# legacy calibration prompt still reads as a *derived offset*).
# Stable across trips — if the user doesn't reset the camera
# between a São Paulo trip and a Mexico trip, the camera is still
# on UTC-3 and the dialog should pre-fill UTC-3, not the prior
# trip's offset. Schema: ``{camera_id: tz_hours}``.
_SAVED_CAMERA_TZ_KEY = "saved_camera_tz"

log = logging.getLogger(__name__)


class _DayCard(QFrame):
    """One per-day card in the dialog's scroll area.

    Holds editable widgets for the day's plan row (description /
    location / TZ) and renders the read-only camera/time info +
    warnings. ``apply_to_trip_day()`` writes the current widget
    values back into the bound ``TripDay`` (in memory; the parent
    dialog persists the event afterwards).
    """

    def __init__(
        self, verdict: PerDayVerdict, parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PreingestDayCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._verdict = verdict
        self._trip_day = verdict.trip_day
        # Set by _build() when the camera-info row exists; reused
        # by set_shift_preview() to show the user what the timestamps
        # WILL look like after the bake applies.
        self._cam_label: Optional[QLabel] = None
        self._current_shift_hours: float = 0.0
        # spec/44 — per-day include flag. Default True; on Apply the dialog
        # builds the set of day_numbers whose card is checked and the capture
        # flow filters `items` to those days before BackUpCardDialog runs.
        self._include_check: Optional[QCheckBox] = None
        self._browse_button: Optional[QPushButton] = None
        # spec/45 — auto-detected ISO 3166-1 alpha-2 country code for this day
        # (from phone GPS centroid). None when no phone GPS that day. Rendered
        # as a hint below the camera info row; persisted to trip_day.extras_json
        # on Apply.
        self._detected_country_code: Optional[str] = None
        self._country_label: Optional[QLabel] = None
        self._build()

    def set_detected_country_code(self, code: Optional[str]) -> None:
        """Update the detected-country state after construction. Used by the
        parent dialog once it has run the per-day GPS centroid lookup.

        Now drives the editable :attr:`_country_combo` (spec/47 follow-up,
        Nelson 2026-06-06 eyeball: country must come pre-filled, not just
        as a hint). If the combo already carries a value (user pre-set or
        already auto-filled), don't override — user intent wins.

        Also: when a country is detected and the Location field is blank,
        pre-fill Location with the alpha-2 code so the user has a sensible
        starting value to refine (the same rule applied to past-photos in
        :mod:`mira.ui.pages.past_photos_dialog`).
        """
        self._detected_country_code = code
        # 1) Country combo — only fill when blank (don't clobber existing).
        if code:
            current = country_code_from_combo(self._country_combo) \
                if hasattr(self, "_country_combo") else None
            if not current:
                idx = self._country_combo.findData(code.upper())
                if idx >= 0:
                    self._country_combo.setCurrentIndex(idx)
            # 2) Location alpha-2 fallback — only when empty.
            if not self._loc_edit.text().strip():
                self._loc_edit.setText(code.upper())
        # 3) Legacy hint label — populates with the detected name when a code
        #    arrives so callers / tests that inspect ``_country_label`` see
        #    the same signal as before; the combo is the primary editable
        #    field, the label is the read-only confirmation hint beside it.
        if self._country_label is None:
            return
        if code:
            self._country_label.setText(tr("Detected: {label}").replace(
                "{label}", display_label_for_code(code)))
            self._country_label.setVisible(True)
        else:
            self._country_label.setVisible(False)

    def detected_country_code(self) -> Optional[str]:
        return self._detected_country_code

    def is_included(self) -> bool:
        """``True`` iff this day is included in the upcoming copy (Slice C)."""
        return self._include_check is None or self._include_check.isChecked()

    def set_included(self, included: bool) -> None:
        if self._include_check is not None:
            self._include_check.setChecked(included)

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)

        # Header row: include checkbox · title · Browse button.
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self._include_check = QCheckBox(tr("Include"))
        self._include_check.setChecked(True)
        self._include_check.setToolTip(tr(
            "Uncheck to skip this day — its files will not be copied into the "
            "event. At least one day must stay checked."
        ))
        self._include_check.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        header_row.addWidget(self._include_check)

        title = QLabel(
            f"<b>Dia {self._trip_day.day_number}</b>"
            f" · {self._trip_day.date.isoformat()}"
            f" · {len(self._verdict.file_paths)} file(s)"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        header_row.addWidget(title, stretch=1)

        # Browse opens the shared DayBrowseDialog with this day's source files —
        # the same affordance Manage Days offers for an imported event, but on
        # the SOURCE bytes (SD card / external folder) BEFORE the copy.
        self._browse_button = QPushButton(tr("Browse…"))
        self._browse_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._browse_button.setToolTip(tr(
            "Preview this day's photos from the source — pick the files you "
            "want without committing to the copy yet."
        ))
        self._browse_button.setEnabled(bool(self._verdict.file_paths))
        self._browse_button.clicked.connect(self._on_browse_clicked)
        header_row.addWidget(self._browse_button)

        outer.addLayout(header_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)

        self._desc_edit = QLineEdit(self._trip_day.description or "")
        self._desc_edit.setToolTip(tr(
            "Short description for this day — what you planned to "
            "shoot. Edits here update the trip plan."
        ))
        form.addRow(tr("Description:"), self._desc_edit)

        self._loc_edit = QLineEdit(self._trip_day.location or "")
        self._loc_edit.setToolTip(tr(
            "Where you were on this day (free text or a place id). "
            "Used by Curate to consolidate days into multi-day "
            "stays. Edits here update the trip plan."
        ))
        form.addRow(tr("Location:"), self._loc_edit)

        # spec/47 follow-up (Nelson 2026-06-06 eyeball — preingest): country
        # is a first-class editable field, pre-filled from phone GPS at the
        # parent dialog's detection step (set_detected_country_code), so it
        # behaves consistently with Timezone (also pre-filled).
        self._country_combo = make_single_country_combo(
            getattr(self._trip_day, "country_code", None),
        )
        self._country_combo.setToolTip(tr(
            "ISO country for the day. Auto-filled from phone GPS — "
            "arrival country wins on travel days. Distinct from Location, "
            "which stays free-text for city / venue detail."
        ))
        form.addRow(tr("Country:"), self._country_combo)

        self._tz_picker = TzPicker()
        if self._trip_day.tz_offset is not None:
            self._tz_picker.setValue(float(self._trip_day.tz_offset))
        self._tz_picker.setToolTip(tr(
            "Timezone the day was actually at. If wrong, fix it "
            "here — the bake step will use this as the target."
        ))
        form.addRow(tr("Timezone:"), self._tz_picker)

        outer.addLayout(form)

        # Camera + time-range read-out. Stored so set_shift_preview()
        # can refresh the line with the shifted range when the user
        # picks a different camera TZ at the dialog level.
        if self._verdict.camera_make or self._verdict.camera_model \
                or self._verdict.capture_time_range is not None:
            self._cam_label = QLabel(self._camera_line(0.0))
            self._cam_label.setObjectName("WizardHint")
            self._cam_label.setWordWrap(True)
            outer.addWidget(self._cam_label)

        # spec/45 — phone-derived country hint. Hidden by default; the parent
        # dialog populates it via set_detected_country_code() once the
        # per-day GPS centroid lookup runs. Always created so the label
        # widget exists for late updates.
        self._country_label = QLabel("")
        self._country_label.setObjectName("WizardHint")
        self._country_label.setWordWrap(True)
        self._country_label.setVisible(False)
        outer.addWidget(self._country_label)

        # Warning chips. One label per warning so the colour can
        # differ — high-severity rendered with the QSS error palette,
        # low-severity yellow.
        for warning in self._verdict.warnings:
            outer.addWidget(_warning_label(warning))

        # Brand tip — show always-collapsed; the dialog-level
        # banner already prompts the user. The user can expand to
        # see the menu path.
        if self._verdict.brand_tip is not None:
            outer.addWidget(_brand_tip_block(self._verdict.brand_tip))

    def _camera_line(self, shift_hours: float) -> str:
        """Build the camera-info / time-range readout. When
        ``shift_hours`` is non-zero, the displayed range is the
        *post-bake* time (so the user sees what the EXIF will read
        after the shift); the raw EXIF range is shown alongside so
        the diff is visible. ``shift_hours == 0`` shows the raw
        range alone — the user has confirmed the camera was right."""
        v = self._verdict
        bits: list[str] = []
        if v.camera_make or v.camera_model:
            bits.append(
                f"Camera: {(v.camera_make + ' ' + v.camera_model).strip()}"
            )
        if v.capture_time_range is not None:
            t0, t1 = v.capture_time_range
            raw = (
                f"EXIF: {t0.strftime('%Y-%m-%d %H:%M')} → "
                f"{t1.strftime('%H:%M')}"
            )
            if shift_hours:
                delta = timedelta(hours=shift_hours)
                bits.append(
                    f"After shift: "
                    f"{(t0 + delta).strftime('%Y-%m-%d %H:%M')} → "
                    f"{(t1 + delta).strftime('%H:%M')}  "
                    f"  ({raw})"
                )
            else:
                bits.append(raw)
        return "   ·   ".join(bits)

    def _on_browse_clicked(self) -> None:
        """Open :class:`DayBrowseDialog` over this day's source files. Imported
        lazily so the dialog only constructs Qt resources when actually shown.
        Errors are logged + shown — never crash the dialog from a browse."""
        from mira.ui.pages.day_browse_dialog import DayBrowseDialog
        from PyQt6.QtWidgets import QMessageBox
        try:
            paths = [Path(p) for p in self._verdict.file_paths]
            title = tr("Day {n} — {d}").replace(
                "{n}", str(self._trip_day.day_number),
            ).replace("{d}", self._trip_day.date.isoformat())
            DayBrowseDialog(paths, title=title, parent=self).exec()
        except Exception as exc:                            # noqa: BLE001
            log.exception(
                "F-019: browse failed for day %s", self._trip_day.day_number,
            )
            QMessageBox.warning(
                self, tr("Couldn't open the browser"),
                tr("This day's files couldn't be browsed.\n\n{err}").replace(
                    "{err}", f"{type(exc).__name__}: {exc}",
                ),
            )

    def set_shift_preview(self, shift_hours: float) -> None:
        """Refresh the camera/time-range readout to show what the
        EXIF will look like after applying ``shift_hours``. Called
        by the parent dialog whenever the user changes the camera-
        TZ picker; lets the user verify the shift is right BEFORE
        clicking Apply."""
        self._current_shift_hours = float(shift_hours)
        if self._cam_label is not None:
            self._cam_label.setText(self._camera_line(shift_hours))

    def apply_to_trip_day(self) -> None:
        """Write current widget values back into the bound TripDay
        (in memory). Caller persists the event after every card has
        applied. Idempotent — re-running on unchanged values is a
        no-op."""
        self._trip_day.description = self._desc_edit.text().strip()
        loc = self._loc_edit.text().strip()
        # Empty string → None; the model treats both the same but
        # None is the canonical empty value (matches load defaults).
        self._trip_day.location = loc if loc else None
        self._trip_day.tz_offset = float(self._tz_picker.value())
        # spec/47 follow-up — write the country combo's alpha-2 back.
        self._trip_day.country_code = country_code_from_combo(
            self._country_combo,
        )


def _warning_label(warning: TzWarning) -> QLabel:
    label = QLabel(warning.message)
    label.setWordWrap(True)
    label.setObjectName(
        "PreingestWarningHigh" if warning.severity == "high"
        else "PreingestWarningLow"
    )
    label.setToolTip(
        f"{warning.kind} · {warning.severity}"
    )
    return label


def _brand_tip_block(tip: BrandTip) -> QWidget:
    """Collapsible-ish tip block. To keep the UI dependency-free we
    render a simple labeled list with a "▸ Show tips" button that
    toggles the list's visibility. Fits any QSS without needing a
    custom collapsible widget."""
    host = QFrame()
    host.setObjectName("PreingestBrandTip")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 4, 0, 0)
    layout.setSpacing(2)

    header_text = (
        tr("Tips for setting this on {model}:").replace(
            "{model}", tip.camera_id)
        if tip.source == "model"
        else tr("General TZ-setting tips for this brand:")
    )
    toggle = QPushButton(tr("▸ Show camera-setting tips"))
    toggle.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    toggle.setObjectName("PreingestBrandTipToggle")
    toggle.setFlat(True)
    layout.addWidget(toggle)

    body = QFrame()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(16, 4, 8, 4)
    body_layout.setSpacing(2)
    header = QLabel(header_text)
    header.setWordWrap(True)
    header.setObjectName("PreingestBrandTipHeader")
    body_layout.addWidget(header)
    for step in tip.steps:
        bullet = QLabel(f"• {step}")
        bullet.setWordWrap(True)
        body_layout.addWidget(bullet)
    body.setVisible(False)

    def _toggle() -> None:
        on = not body.isVisible()
        body.setVisible(on)
        toggle.setText(
            tr("▾ Hide camera-setting tips") if on
            else tr("▸ Show camera-setting tips")
        )

    toggle.clicked.connect(_toggle)
    layout.addWidget(body)
    return host


class PreingestPlanConfirmDialog(QDialog):
    """The F-019 dialog. Lifecycle:

    * Construct with ``(event, items, parent)``.
    * ``exec()`` → ``Accepted`` on Apply, ``Rejected`` on Cancel.
    * After Accepted, the dialog has already persisted the plan
      edits via ``save_event``. Call :meth:`offset_hours` and
      :meth:`remember` to read the user's chosen TZ shift for the
      bake step.

    The dialog is **always** shown for the Capture phase flow (per
    Nelson 2026-05-25 freeze — friction is acceptable, missing a TZ
    error is not). If the user has nothing to fix they hit Apply
    with offset=0 and the bake is a no-op downstream.
    """

    def __init__(
        self,
        event: Event,
        items: Sequence[SourceItem],
        *,
        camera_make: str = "",
        camera_model: str = "",
        gateway=None,
        event_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Confirm trip plan and timezone"))
        self.setMinimumSize(720, 520)
        self._event = event
        # Data seam (charter §0): the gateway is the only persistence path. ``event_id`` is
        # the DB event whose plan we update on Apply; ``_event`` is the legacy-shaped adapter
        # the reused UI renders.
        self._gateway = gateway
        self._event_id = event_id or getattr(event, "id", "")
        self._camera_make = camera_make
        self._camera_model = camera_model
        # Build the engine output once at construction; the dialog
        # renders verdicts and never re-runs the engine (the user's
        # plan edits only affect the in-memory TripDay objects; the
        # warnings already accounted for the original timestamps).
        self._plan = build_preingest_plan(
            items, event.trip_days,
            camera_make=camera_make,
            camera_model=camera_model,
        )
        # spec/45 — per-day country code derived from phone GPS centroid.
        # ``items`` carries the EXIF-derived tz_offset_minutes + gps_lat/lon
        # populated by Slice TZ-1; ``phone_day_summaries`` rolls them up to a
        # per-day TZ + centroid; the country lookup resolves centroid → ISO
        # alpha-2. Days without phone GPS centroids (camera-only or indoor
        # selfies with location off) get None and the card's hint stays
        # hidden.
        self._country_by_day: dict[int, str] = self._derive_phone_countries(items)
        self._day_cards: list[_DayCard] = []
        self._build_ui()

    def _derive_phone_countries(
        self, items: Sequence[SourceItem],
    ) -> dict[int, str]:
        """{day_number: alpha2_code} for every day the phone centroid
        resolved to a known country. Errors fall through to an empty dict —
        a country auto-fill that fails silently is preferable to crashing the
        capture flow because the asset file is missing."""
        try:
            day_for = {
                Path(p): v.trip_day.day_number
                for v in self._plan.days
                for p in v.file_paths
            }
            summaries = phone_day_summaries(items, day_for)
            out: dict[int, str] = {}
            for day, summary in summaries.items():
                if summary.centroid is None:
                    continue
                lat, lon = summary.centroid
                code = country_code_for(lat, lon)
                if code:
                    out[day] = code
            return out
        except Exception:                                # noqa: BLE001
            log.exception(
                "spec/45: phone-country derivation failed — auto-fill skipped"
            )
            return {}

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        heading = QLabel(
            tr("Confirm trip plan and timezone for {n} day(s)").replace(
                "{n}", str(len(self._plan.days)))
        )
        heading_font = heading.font()
        heading_font.setPointSize(heading_font.pointSize() + 2)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        outer.addWidget(heading)

        hint = QLabel(tr(
            "Edits here update the trip plan in place. The "
            "timezone correction below (if any) is baked into the "
            "EXIF of every file copied in — sources are never "
            "touched. Look at your camera and tell us which "
            "timezone it was set to; the app does the math."
        ))
        hint.setWordWrap(True)
        hint.setObjectName("WizardHint")
        outer.addWidget(hint)

        # spec/44 — classification panel above the day list. Seeded from the
        # current event's stored values so users editing an existing event
        # see their classification carried in; saved back through the gateway
        # on Apply alongside the trip-day plan persist. New-event-from-photos
        # flows reach this dialog with a freshly-created event row, so the
        # panel is empty there until the user fills it.
        self._classification = ClassificationPanel()
        self._seed_classification_from_event()
        outer.addWidget(self._classification)

        # Camera info dialog-level (Quick Sweep is one-card-one-
        # camera per the docs/18 freeze 2026-05-25, so showing this
        # once at the top is more honest than repeating it per day).
        if self._camera_make or self._camera_model:
            cam_line = (
                f"<b>Camera:</b> "
                f"{(self._camera_make + ' ' + self._camera_model).strip()}"
            )
            cam = QLabel(cam_line)
            cam.setTextFormat(Qt.TextFormat.RichText)
            outer.addWidget(cam)

        # Per-day cards inside a scroll area.
        scroll = QScrollArea()
        scroll.setObjectName("PreingestScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(10)
        host_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for verdict in self._plan.days:
            card = _DayCard(verdict)
            # spec/45 — push the phone-derived country hint into the card so
            # the user sees "Detected: Italy (IT)" alongside the camera/time
            # readout. None entries leave the hint hidden.
            day_number = verdict.trip_day.day_number
            card.set_detected_country_code(self._country_by_day.get(day_number))
            host_layout.addWidget(card)
            self._day_cards.append(card)
        scroll.setWidget(host)
        outer.addWidget(scroll, stretch=1)

        # Undated-files callout (read-only).
        if self._plan.undated_files:
            undated = QLabel(tr(
                "{n} file(s) couldn't be assigned to a planned day — "
                "they will fall into an 'Undated' bucket on import."
            ).replace("{n}", str(len(self._plan.undated_files))))
            undated.setWordWrap(True)
            undated.setObjectName("PreingestWarningLow")
            outer.addWidget(undated)

        # Bottom block: camera-TZ picker → live shift readout +
        # "set your camera to X going forward" hint. Replaces the
        # "shift by N hours" spinner the first cut shipped
        # (00.015) — Nelson 2026-05-25: "ask what TZ the camera
        # was on, do the math for them, show what changes, then
        # tell them what to set on the camera going forward."
        #
        # The target TZ is day 1's plan tz. Single-card-single-camera
        # sessions almost always share one trip TZ across days; if
        # the user genuinely needs different TZs per day, they can
        # cancel + edit the per-day cards first + re-run.
        self._target_tz: float = (
            self._plan.days[0].trip_day.tz_offset
            if self._plan.days
            and self._plan.days[0].trip_day.tz_offset is not None
            else 0.0
        )
        # Pre-fill the camera-TZ picker from the last remembered
        # answer for this camera, if any. The point is to remember
        # the CAMERA'S actual TZ (not the trip-specific offset) —
        # if the user didn't touch the camera between a São Paulo
        # trip and a Mexico trip, the camera is still on UTC-3 and
        # we should pre-fill UTC-3 so the user just confirms.
        # ``saved_camera_tz`` is keyed by the same Model-based
        # camera_id the rest of the pipeline uses.
        remembered_tz: Optional[float] = None
        camera_id_key = self._camera_model or self._camera_make
        if camera_id_key:
            try:
                raw = self._saved_camera_tz_map()
                if camera_id_key in raw:
                    remembered_tz = float(raw[camera_id_key])
            except Exception:                          # noqa: BLE001
                remembered_tz = None
        initial_picker_value = (
            remembered_tz if remembered_tz is not None
            else float(self._target_tz)
        )

        block = QFrame()
        block.setObjectName("PreingestShiftBlock")
        block.setFrameShape(QFrame.Shape.StyledPanel)
        block_layout = QVBoxLayout(block)
        block_layout.setContentsMargins(12, 10, 12, 10)
        block_layout.setSpacing(6)

        picker_row = QHBoxLayout()
        picker_label = QLabel(tr(
            "Look at the camera. What timezone was it set to?"
        ))
        picker_label.setToolTip(tr(
            "Pick the timezone the camera's clock was actually on "
            "when it took these photos. If you don't know, check "
            "the camera's menu now — it's worth getting right. "
            "Same value as the trip's planned timezone means the "
            "camera was correct and no shift is applied."
        ))
        self._camera_tz_picker = TzPicker(initial=initial_picker_value)
        self._camera_tz_picker.setToolTip(picker_label.toolTip())
        self._camera_tz_picker.valueChanged.connect(
            self._on_camera_tz_changed)
        picker_row.addWidget(picker_label)
        picker_row.addSpacing(8)
        picker_row.addWidget(self._camera_tz_picker)
        picker_row.addStretch(1)
        block_layout.addLayout(picker_row)

        self._shift_readout = QLabel()
        self._shift_readout.setObjectName("PreingestShiftReadout")
        self._shift_readout.setWordWrap(True)
        block_layout.addWidget(self._shift_readout)

        self._target_hint = QLabel()
        self._target_hint.setObjectName("PreingestTargetHint")
        self._target_hint.setWordWrap(True)
        self._target_hint.setText(tr(
            "After backup completes, set your camera's timezone "
            "to {tz} so future photos land right without a shift."
        ).replace("{tz}", format_utc_offset(self._target_tz)))
        block_layout.addWidget(self._target_hint)

        self._remember_check = QCheckBox(tr(
            "Remember this for the next ingest of this camera"
        ))
        # Default ON — Nelson 2026-05-25: same trip, follow-up day
        # after the user crossed a timezone, the dialog should
        # pre-fill the *last confirmed camera TZ* so the user just
        # has to look at the camera and confirm or correct against
        # current reality. Leaving Remember off by default would
        # break this chain after the very first day. The user can
        # untick if they're sure they reset the camera every time.
        self._remember_check.setChecked(True)
        self._remember_check.setToolTip(tr(
            "Save the camera's actual timezone so the NEXT ingest "
            "of the same camera pre-fills this picker. Useful "
            "mid-trip: if you cross into a new timezone and forget "
            "to update the camera, the next ingest still pre-fills "
            "the right (stale) camera TZ and computes the new "
            "offset for you."
        ))
        block_layout.addWidget(self._remember_check)

        outer.addWidget(block)

        # Compute the initial shift (= 0 when the picker starts at
        # the target TZ) so the day cards + readout render with the
        # right state from the first paint.
        self._on_camera_tz_changed(self._camera_tz_picker.value())

        buttons = QDialogButtonBox(parent=self)
        apply_btn = QPushButton(tr("Apply"))
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._on_apply)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        cancel_btn.clicked.connect(self.reject)
        buttons.addButton(apply_btn,
                          QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel_btn,
                          QDialogButtonBox.ButtonRole.RejectRole)
        outer.addWidget(buttons)

    # ── Public outputs (read post-Accepted) ─────────────────────

    def offset_hours(self) -> float:
        """The TZ shift (hours) derived from the user's camera-TZ
        answer: ``target_tz - camera_tz``. 0.0 = the camera was
        right (no bake). Positive = shift photos forward (camera
        was behind); negative = shift backward."""
        return float(self._target_tz) - float(
            self._camera_tz_picker.value()
        )

    def camera_tz_hours(self) -> float:
        """The TZ the user said the camera's clock was actually on.
        Persisted into ``camera_clocks`` so the Cull-phase dialog
        can read it instead of asking again."""
        return float(self._camera_tz_picker.value())

    def remember(self) -> bool:
        """True if the user wants the camera's TZ persisted under
        ``settings.saved_camera_offsets`` for future ingests of the
        same camera id. (The legacy setting key name says "offsets"
        but we now store the camera-TZ — the value the user
        actually typed. Future cleanup: a separate
        ``saved_camera_tz`` key; for now we coexist.)"""
        return bool(self._remember_check.isChecked())

    def updated_event(self) -> Event:
        """The Event after plan edits have been applied + persisted.
        Call after Accepted."""
        return self._event

    # ── Live re-compute when the camera TZ picker changes ───────

    def _on_camera_tz_changed(self, camera_tz: float) -> None:
        """Refresh the shift readout + the "set your camera to X"
        hint + every per-day card's shifted preview. Called whenever
        the camera-TZ picker fires ``valueChanged``."""
        shift = float(self._target_tz) - float(camera_tz)
        if shift == 0.0:
            self._shift_readout.setText(tr(
                "✓ The camera was on the right timezone — no "
                "shift needed. Apply will copy the files as-is."
            ))
        else:
            sign = "+" if shift > 0 else "−"
            self._shift_readout.setText(
                tr(
                    "Will shift every photo by {sign}{h:.2f} h "
                    "(target {target} − camera {camera})."
                )
                .replace("{sign}", sign)
                .replace("{h}", f"{abs(shift):.2f}")
                .replace(
                    "{target}", format_utc_offset(self._target_tz))
                .replace(
                    "{camera}", format_utc_offset(camera_tz))
            )
        for card in self._day_cards:
            card.set_shift_preview(shift)

    # ── Apply handler ───────────────────────────────────────────

    def included_day_numbers(self) -> frozenset:
        """Day-numbers whose card is checked. Used by the capture flow to
        filter ``items`` before BackUpCardDialog runs the verbatim byte-copy
        (spec/44 §3 Slice C — excluded days do NOT enter ``Original Media``)."""
        return frozenset(
            c._trip_day.day_number for c in self._day_cards if c.is_included()
        )

    def included_source_paths(self) -> frozenset:
        """Source-file paths under the included days (a strict subset of every
        path the EXIF scan produced). The capture flow uses this set as the
        filter key against ``items`` so unticked days never reach the byte-copy."""
        included = set()
        for card in self._day_cards:
            if not card.is_included():
                continue
            for p in card._verdict.file_paths:
                included.add(Path(p))
        return frozenset(included)

    def _on_apply(self) -> None:
        """Write each card's edits back to its TripDay, persist the plan through the
        gateway, and accept the dialog. Persistence failure surfaces as a logged
        exception + the dialog stays open so the user can retry.

        Data seam (charter §0): legacy ``save_event(self._event)`` → gateway
        ``save_trip_days`` (the edited legacy ``TripDay`` rows → store ``TripDay``). The
        ``camera_clocks`` suppression write is dropped (no consumer in the rebuild yet —
        the Camera row carries the offset). ``_event`` is still mutated in place so the
        adapter the caller reads back via :meth:`updated_event` reflects the edits."""
        # spec/44 — at-least-one-day include guard. The byte-copy step downstream
        # needs SOMETHING to copy; the rest of the flow assumes the event has
        # files.
        if not self.included_day_numbers():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, tr("At least one day required"),
                tr("Tick at least one day to include — the event needs files to copy."),
            )
            return
        for card in self._day_cards:
            card.apply_to_trip_day()
        try:
            self._persist_plan()
            self._persist_classification()
            self._persist_phone_countries()
        except Exception:                              # noqa: BLE001
            log.exception(
                "F-019: failed to persist plan edits for event %s", self._event_id,
            )
            # Don't accept — let the user re-try / cancel.
            return
        # Persist the camera's confirmed TZ for the NEXT ingest of the same camera_id (the
        # mid-trip chain: cross a TZ, forget to update the camera, next ingest pre-fills
        # the last-confirmed value).
        if self._remember_check.isChecked():
            self._persist_remembered_tz()
        self.accept()

    def _persist_plan(self) -> None:
        """Write the edited trip days through the gateway (the data seam)."""
        if self._gateway is None or not self._event_id:
            return
        from mira.store import models as m
        store_days = [
            m.TripDay(
                day_number=d.day_number,
                date=d.date.isoformat() if d.date else None,
                description=d.description or "",
                location=getattr(d, "location", None),
                tz_minutes=(round(d.tz_offset * 60) if d.tz_offset is not None else None),
            )
            for d in (self._event.trip_days or [])
        ]
        eg = self._gateway.open_event(self._event_id)
        try:
            eg.save_trip_days(store_days)
        finally:
            eg.close()

    def _seed_classification_from_event(self) -> None:
        """Populate the classification panel from the event's current values.

        Reads through the gateway (the single data seam). New-event flows
        arrive with a freshly-created event whose classification is at the
        spec/44 defaults, so the panel renders empty. Existing-event flows
        (capture phase for an open event) carry the user's prior choices.
        Failures are logged but tolerated — the panel just stays at defaults.
        """
        if self._gateway is None or not self._event_id:
            return
        try:
            import json as _json
            eg = self._gateway.open_event(self._event_id)
            try:
                ev = eg.event()
            finally:
                eg.close()
        except Exception:                                # noqa: BLE001
            log.exception("F-019: classification seed read failed")
            return
        try:
            extras_all = _json.loads(ev.extras_json or "{}")
            extras_all = extras_all if isinstance(extras_all, dict) else {}
        except (ValueError, TypeError):
            extras_all = {}
        # The classification namespace shares extras_json with IPTC location
        # facets — pass the whole dict in and let the panel filter to the
        # keys appropriate for the current type at read time.
        # spec/52: event-level tags retired; the panel's tags field becomes
        # internal UI state with no persistence (slated for redesign in the
        # event-creation-surfaces sprint).
        self._classification.set_values(
            event_type=ev.event_type or "unclassified",
            event_subtype=ev.event_subtype,
            description=ev.description or "",
            extras=extras_all,
        )

    def _persist_phone_countries(self) -> None:
        """Push every detected per-day country code to ``trip_day.extras_json``
        via the gateway shallow-merge seam. Days the user explicitly
        unticked (Slice C include checkbox) are skipped — we don't want a
        country-code on a day that won't have any files. Failure during the
        write is logged but tolerated; the country auto-fill is a polish,
        never a blocker for the capture flow.

        Day numbers without a detected code are skipped at the dict level
        (``_country_by_day`` only carries non-None entries)."""
        if self._gateway is None or not self._event_id:
            return
        if not self._country_by_day:
            return
        included = self.included_day_numbers()
        eg = self._gateway.open_event(self._event_id)
        try:
            for card in self._day_cards:
                if not card.is_included():
                    continue
                day = card._trip_day.day_number
                if day not in included:
                    continue
                code = card.detected_country_code()
                if not code:
                    continue
                try:
                    eg.set_trip_day_extras(day, {"country_code": code})
                except Exception:                       # noqa: BLE001
                    log.exception(
                        "spec/45: country persist failed for day %s", day,
                    )
        finally:
            eg.close()

    def _persist_classification(self) -> None:
        """Push the classification panel's current values through
        :meth:`Gateway.set_classification`. Merges into ``extras_json`` rather
        than overwriting so any IPTC location facets the user set elsewhere
        survive (set_classification uses ``extras_updates`` shallow-merge)."""
        if self._gateway is None or not self._event_id:
            return
        cls = self._classification.values()
        self._gateway.set_classification(
            self._event_id,
            event_type=cls.event_type,
            event_subtype=cls.event_subtype or "",
            description=cls.description,
            extras_updates=cls.extras or None,
        )

    def _saved_camera_tz_map(self) -> dict:
        """The remembered ``{camera_id: tz_hours}`` map from the gateway settings."""
        if self._gateway is None:
            return {}
        try:
            return dict(getattr(self._gateway.settings.load(), "saved_camera_tz", {}) or {})
        except Exception:                              # noqa: BLE001
            return {}

    def _persist_remembered_tz(self) -> None:
        """Best-effort write into ``settings.saved_camera_tz`` — ``{camera_id: tz_hours}``
        — through the gateway SettingsRepo. Logged + ignored on failure."""
        camera_id = self._camera_model or self._camera_make
        if not camera_id or self._gateway is None:
            return
        try:
            saved = self._saved_camera_tz_map()
            saved[camera_id] = float(self._camera_tz_picker.value())
            self._gateway.settings.update(saved_camera_tz=saved)
        except Exception:                              # noqa: BLE001
            log.exception(
                "F-019: failed to persist %s for %s", _SAVED_CAMERA_TZ_KEY, camera_id,
            )
