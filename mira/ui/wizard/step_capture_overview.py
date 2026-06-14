"""Collect & Timezones overview — first screen of the Collect
section (task #96 / #1d; rewritten 2026-06-07 for the 4-phase
pivot).

Pure-text screen, no input. Explains:

  * The four-phase pipeline at a glance (Collect → Pick → Edit →
    Share), so the user has a mental map before the settings
    screens that follow.
  * The contract-frozen guarantee for ``Original Media/`` (the
    on-disk folder name is unchanged; only the phase label moved
    from "Capture" to "Collect"): bytes land once at ingest with
    TZ-corrected EXIF, then never change again except via the
    explicit "Adjust event TZ" surface on the event's plan page.

Why this lives in the wizard (and not as a per-ingest disclosure):
this is the **educational** half of the disclosure split. New
users get the full mental model once; experienced users get a
fluid per-ingest UX because they already know what's happening
underneath.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mira.ui.i18n import tr


class StepCaptureOverview(QWidget):
    """Educational text-only screen — collects no answers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 36, 48, 36)
        layout.setSpacing(18)

        title = QLabel(tr("Collect & Timezones"))
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        intro = QLabel(tr(
            "Before you bring photos in for the first time, two "
            "short setup screens follow. Pick the defaults that "
            "match how you shoot — every choice can be changed "
            "later in Settings."
        ))
        intro.setObjectName("WelcomeSubtitle")
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Pipeline map. Plain text so the wizard works regardless
        # of icon-asset availability.
        pipeline = QLabel(tr(
            "<b>The four phases</b><br>"
            "&nbsp;&nbsp;1. <b>Collect</b> — copy photos from the "
            "SD card (or a folder) to the event, verify integrity, "
            "and bake the correct timezone into the EXIF. A fast "
            "<i>Quick Sweep</i> pass during ingest is optional.<br>"
            "&nbsp;&nbsp;2. <b>Pick</b> — Pick / Skip each photo "
            "across every camera, every day. Helpers on by default: "
            "focus peaking, AF point overlay, sharpness rating, "
            "two-photo compare grid.<br>"
            "&nbsp;&nbsp;3. <b>Edit</b> — adjust + crop the picked "
            "photos, then export as JPEG / TIFF.<br>"
            "&nbsp;&nbsp;4. <b>Share</b> — build the final "
            "narrative (slideshow / book / print set)."
        ))
        pipeline.setObjectName("BodyText")
        pipeline.setTextFormat(Qt.TextFormat.RichText)
        pipeline.setWordWrap(True)
        layout.addWidget(pipeline)

        contract = QLabel(tr(
            "<b>The Original Media contract</b><br>"
            "Every event keeps a folder called <code>Original Media/</code> "
            "(the folder name is unchanged from earlier releases; only "
            "the phase label moved from <i>Capture</i> to <i>Collect</i>). "
            "Mira writes to it <i>once</i>, at ingest, applying the "
            "timezone correction you'll set on the next screen. After "
            "that, nothing else in Mira changes those files — "
            "Pick / Edit / Share all read them but never modify them. "
            "The only exception is the explicit <b>Adjust event TZ</b> "
            "action on an event's plan page, which re-bakes the EXIF "
            "when you discover the original calibration was off.<br><br>"
            "That guarantee is what makes the rest of the pipeline "
            "safe to experiment with: you can re-pick, re-edit, "
            "re-export as many times as you like — the originals "
            "underneath are still right."
        ))
        contract.setObjectName("BodyText")
        contract.setTextFormat(Qt.TextFormat.RichText)
        contract.setWordWrap(True)
        layout.addWidget(contract)

        layout.addStretch(1)

    # No answers captured on the overview screen — it's pure text.

    def collect_answers(self) -> dict[str, str]:
        return {}

    def restore_answers(self, answers: dict[str, str]) -> None:
        return
