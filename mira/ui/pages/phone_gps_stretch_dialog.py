"""``PhoneGpsStretchDialog`` — the no-GPS-days prompt (spec/78 §A,
revising the spec/64 §4.4 per-stretch loop).

Replaces today's silent home-country / TZ autofill for days where the
phone didn't supply usable location info. Fires **once** during Collect
(after the scan, before the Days Table dialog) with every no-GPS day
listed in one shot — not one prompt per consecutive stretch, which got
unbearable on grab-bag past-photos imports whose days are scattered.

UX shape:

* States the count of no-GPS days and lists their dates so the user
  knows what the choice covers.
* One Country dropdown + one TZ picker that apply to **every** listed
  day.
* Pre-filled with the user's home country / TZ as suggestions when
  available — the user confirms or overrides.
* Apply = use these values for every no-GPS day. Skip = leave them
  blank so the user can fill via the Days Table dialog later.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.country_picker import (
    country_code_from_combo,
    make_single_country_combo,
)
from mira.ui.base.tz_picker import TzPicker
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


class PhoneGpsStretchDialog(QDialog):
    """Every no-GPS day in one prompt; the user picks country + TZ
    once and the values apply across every listed day (spec/78 §A).

    Returns:
    * Apply → :meth:`result_values` returns ``(country_code, tz_minutes)``
      with whatever the user picked. Either can be ``None`` if the user
      explicitly cleared the picker (the Days Table dialog handles
      partial entries later).
    * Skip / Cancel → :meth:`was_applied` returns ``False``; the caller
      leaves the no-GPS rows blank.
    """

    def __init__(
        self,
        dates: List[date],
        *,
        initial_country: Optional[str] = None,
        initial_tz_minutes: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Days without location data"))
        self.setModal(True)
        self.resize(540, 360)

        self._dates = list(dates)
        self._was_applied = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # Header — count of no-GPS days (spec/78 §A). Days can be
        # non-consecutive (grab-bag past-photos imports), so a count is
        # honest where a "from-to" range would mislead.
        count = len(self._dates)
        if count == 1:
            heading_text = tr(
                "{date} has no location data — set a default country "
                "and time zone for it. You can fine-tune it later in "
                "the Event Days Table."
            ).replace("{date}", self._dates[0].isoformat())
        else:
            heading_text = tr(
                "{count} day(s) have no location data. Set a default "
                "country and time zone for them — you can fix individual "
                "days afterwards in the Event Days Table."
            ).replace("{count}", str(count))
        heading = QLabel(heading_text)
        heading.setObjectName("PageHint")
        heading.setWordWrap(True)
        outer.addWidget(heading)

        # Day list — small read-only display so the user sees exactly
        # which dates the choice covers.
        if count > 1:
            dates_box = QGroupBox(tr("Dates"))
            dates_box.setObjectName("FormFieldGroup")
            dates_layout = QVBoxLayout(dates_box)
            dates_layout.setContentsMargins(10, 14, 10, 10)
            dates_label = QLabel(", ".join(d.isoformat() for d in self._dates))
            dates_label.setWordWrap(True)
            dates_layout.addWidget(dates_label)
            outer.addWidget(dates_box)

        # Country combo — pre-filled with the home default suggestion.
        self._country_combo = make_single_country_combo(initial_country)
        self._country_combo.setToolTip(tr(
            "Country to apply across the days listed above. Pre-filled "
            "with your home country if set — adjust if the trip went "
            "somewhere else."
        ))
        country_box = QGroupBox(tr("Country"))
        country_box.setObjectName("FormFieldGroup")
        country_layout = QHBoxLayout(country_box)
        country_layout.setContentsMargins(10, 14, 10, 10)
        country_layout.addWidget(self._country_combo)
        outer.addWidget(country_box)

        # TZ picker — pre-filled with the home default suggestion.
        initial_tz_hours = (
            initial_tz_minutes / 60.0
            if initial_tz_minutes is not None else None
        )
        self._tz_picker = TzPicker(initial_tz_hours)
        self._tz_picker.setToolTip(tr(
            "Time zone to apply across the days listed above. Pre-filled "
            "with your home time zone if set."
        ))
        tz_box = QGroupBox(tr("Time zone"))
        tz_box.setObjectName("FormFieldGroup")
        tz_layout = QHBoxLayout(tz_box)
        tz_layout.setContentsMargins(10, 14, 10, 10)
        tz_layout.addWidget(self._tz_picker)
        outer.addWidget(tz_box)

        outer.addStretch(1)

        # Footer — Apply / Skip. Skip is the explicit "I'll deal with
        # these later via the Days Table" path; the rows stay blank.
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._buttons = QDialogButtonBox()
        apply_btn = self._buttons.addButton(
            tr("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        skip_btn = self._buttons.addButton(
            tr("Skip"), QDialogButtonBox.ButtonRole.RejectRole)
        skip_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        skip_btn.setToolTip(tr(
            "Leave these days blank — fine-tune them later in the Event "
            "Days Table."
        ))
        self._buttons.accepted.connect(self._on_apply)
        self._buttons.rejected.connect(self.reject)
        footer.addWidget(self._buttons)
        outer.addLayout(footer)

    def _on_apply(self) -> None:
        self._was_applied = True
        self.accept()

    def was_applied(self) -> bool:
        return self._was_applied

    def result_values(self) -> Tuple[Optional[str], Optional[int]]:
        """``(country_code, tz_minutes)`` after Apply.

        Either may be ``None`` if the user cleared the picker
        explicitly; the caller treats ``None`` as "leave this field
        blank on the stretch's rows"."""
        country = country_code_from_combo(self._country_combo)
        tz_hours = self._tz_picker.value()
        tz_minutes = (
            int(round(tz_hours * 60)) if tz_hours is not None else None
        )
        return (country, tz_minutes)
