"""Declarative settings dialog.

Renders a tabbed form widget-by-widget from a Python schema, so adding
a new setting is one schema entry — no hand-laid-out Qt code. Modeled
on XdTd's DtdSettingsDataDialog pattern (``docs/05`` §14 +
``docs/15`` §1.8) extended with tabs (2026-05-13) so per-module
parameters group naturally as the app grows.

Schema shape (top level = list of tabs):

    SETTINGS_SCHEMA = [
        {
            "tab": "Appearance",
            "fields": [
                {
                    "key": "theme",
                    "label": "Theme",
                    "widget": "combo",         # combo | folder | file | spinbox
                    "tooltip": "...",
                    "options": [("light", "Light"), ("dark", "Dark")],
                    "restart_required": False,
                },
            ],
        },
        ...
    ]

Tabs with no fields render a "No settings here yet" placeholder so
the structure is discoverable even before per-module params land.

Widget kinds:

  - ``combo``    — QComboBox with (value, display) options
  - ``folder``   — QLineEdit + Browse → QFileDialog.getExistingDirectory
  - ``file``     — QLineEdit + Browse → QFileDialog.getOpenFileName
  - ``spinbox``  — QSpinBox or QDoubleSpinBox (decimals key opts in)
  - ``info``     — read-only value row, NO settings key/binding: value
    from a host-injected provider (ctor ``info_providers[info_id]``),
    optional NoIcon-confirmed action button (``info_actions[action_id]``
    + ``action_label``/``action_tooltip``/``action_confirm``). The
    spec/63 slice-7 disk-honesty row is the first tenant.

When the user reuses a settings widget inline in a module (e.g. the
Picker embedding the focus-peaking control on its toolbar), the
module uses the same widget class, calls ``set_value()`` with the
current persisted setting, and reads ``.value()`` at runtime —
session-local, no persist-back unless the module explicitly saves.
That's the pattern: same widget here as in the module, with the
persisted value as the runtime default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import machine_settings as _machine_settings
from core.aspect_ratio import aspect_ratio_labels as _aspect_ratio_labels
from mira.settings.model import Settings as _Settings
from mira.settings.repo import SettingsRepo as _SettingsRepo
from mira.ui.i18n import tr  # ported into mira/ui (charter §4 step 7)


# Settings shim — the data-layer rewire of this reused dialog (charter §5.2): its
# dict-shaped load/save/reset go through the new ``mira.settings`` (Domain 5) instead
# of legacy ``core.settings``. Dict round-trip keeps the dialog's generic
# ``current = load_settings(); current[k] = v; save_settings(current)`` flow unchanged
# (tolerant ``from_dict`` drops any non-Settings key). The host overrides
# ``changes_applied`` to react to theme / photos_base_path changes (see MainWindow).
#
# spec/95 §C — ``display_quality`` is **machine-local** (per-install)
# and must NOT roam through ``SettingsRepo`` (the roaming
# ``settings.rebuild.json`` lives inside the library root and a shared
# NAS library would last-writer-wins between desktop + laptop). Route
# it via ``core.machine_settings`` instead so the schema-driven dialog
# still sees a single dict but the bytes land in the right file.
def load_settings() -> dict:
    data = _SettingsRepo().load().to_dict()
    data["display_quality"] = _machine_settings.read_display_quality()
    return data


def save_settings(data: dict) -> None:
    data = dict(data)
    dq = data.pop("display_quality", None)
    _SettingsRepo().save(_Settings.from_dict(data))
    if isinstance(dq, str) and dq in _machine_settings.DISPLAY_QUALITY_VALUES:
        _machine_settings.write_display_quality(dq)


def reset_settings_to_defaults() -> dict:
    defaults = _Settings()
    _SettingsRepo().save(defaults)
    # spec/95 §C — Reset to defaults also clears the per-install
    # display_quality override (writes the documented default so
    # the dialog reads back ``balanced`` rather than whatever the
    # user previously chose).
    _machine_settings.write_display_quality(
        _machine_settings.DEFAULT_DISPLAY_QUALITY)
    data = defaults.to_dict()
    data["display_quality"] = _machine_settings.DEFAULT_DISPLAY_QUALITY
    return data


log = logging.getLogger(__name__)


# Default settings schema. Empty tabs render a placeholder so the
# user can see where future per-module options will live.
SETTINGS_SCHEMA: list[dict] = [
    # "General" hosts cross-cutting preferences that aren't tied to
    # a specific phase or surface (Nelson 2026-05-28). When a new
    # setting is genuinely phase-specific it goes in the matching
    # tab; when it's a philosophical / app-wide preference (e.g. "do
    # you care about classification?") it belongs here.
    {
        "tab": "General",
        "fields": [
            # Master toggle for the style/genre classification system
            # (F-037). When off, Mira stops asking the user to
            # classify — the F-020 nudge at Select bucket-exit is
            # suppressed. Out-of-box ON preserves current behaviour;
            # the wizard's meta-question (when shipped) will set this
            # from the user's answer up front.
            {
                "key": "classification_relevant",
                "label": "Photo classification",
                "widget": "checkbox",
                "check_label": (
                    "Care about per-style classification "
                    "(portrait / landscape / wildlife / etc.)"
                ),
                "tooltip": (
                    "When on, Mira suggests styles for your "
                    "photos and nudges you when something kept "
                    "still has an uncertain classification — useful "
                    "if you want your photos sorted by style at "
                    "Curate. When off, the system stops asking; "
                    "the classification-nudge dialog never appears "
                    "at Select. The classifier still runs in the "
                    "background so the data is there if you ever "
                    "turn this back on. Defaults to on."
                ),
                "restart_required": False,
            },
            {
                # spec/52 §8.2 — the calibration trigger compares each
                # day's location-derived TZ to this value. Defaults to
                # the system TZ on first launch; users who don't travel
                # rarely touch it. Travelers who set their laptop clock
                # to the trip's local TZ should set this to their REAL
                # home TZ so calibration fires correctly mid-trip.
                "key": "home_timezone",
                "label": "Home timezone",
                "widget": "tz_picker",
                "tooltip": (
                    "Your home UTC offset. Used to decide when a day's "
                    "location TZ differs enough from home to ask you to "
                    "calibrate the camera clock — set this to where you "
                    "LIVE, not the trip you're on right now. Defaults to "
                    "your system timezone on first launch."
                ),
                "restart_required": False,
            },
            {
                # spec/52 §3 + Nelson 2026-06-08 — home-country fallback for
                # days with no phone GPS. When set, the scan stamps every
                # phone-less day with this country code so the user doesn't
                # have to fill it manually. The Plan dialog flags the source
                # so the user can override per day.
                "key": "home_country",
                "label": "Home country",
                "widget": "country_picker",
                "tooltip": (
                    "Used as a fallback when a scanned day has no phone "
                    "GPS. Sessions / camera-only events at home get this "
                    "country pre-filled instead of forcing you to type "
                    "it on every day. Leave blank to skip the fallback."
                ),
                "restart_required": False,
            },
        ],
    },
    {
        "tab": "Appearance",
        "fields": [
            {
                "key": "theme",
                "label": "Theme",
                "widget": "combo",
                "tooltip": "Light or dark interface theme. Applies immediately.",
                "options": [
                    ("light", "Light"),
                    ("dark", "Dark"),
                ],
                "restart_required": False,
            },
            {
                "key": "language",
                "label": "Language",
                "widget": "combo",
                "tooltip": (
                    "Interface language. Restart Mira for the change to "
                    "take effect. Portuguese translations ship in the v1 "
                    "release (English only in the walking-skeleton build)."
                ),
                "options": [
                    ("en", "English"),
                    ("pt", "Português"),
                ],
                "restart_required": True,
            },
            # Nelson 2026-06-09 — font_scale was defined in the model but
            # had zero consumers and wasn't exposed in the dialog. The
            # global font scale is now applied at QApplication startup
            # (and re-applied when this setting changes) so a small laptop
            # screen can bump up to 1.15/1.25 without a hardcoded literal
            # in every widget.
            {
                "key": "font_scale",
                "label": "Font scale",
                "widget": "spinbox",
                "tooltip": (
                    "Global font scale multiplier applied to every widget. "
                    "1.00 = system default; 1.10 / 1.25 enlarge for small "
                    "laptop screens; 0.90 shrinks. Re-applies immediately."
                ),
                "min": 0.80, "max": 1.50, "step": 0.05,
                "decimals": 2, "suffix": "×",
                "restart_required": False,
            },
            # Nelson 2026-06-09 audit — default Day Grid cell size.
            {
                "key": "default_day_grid_cell_size",
                "label": "Day Grid default cell size",
                "widget": "spinbox",
                "tooltip": (
                    "Default cell size (px) the Day Grid opens at. The "
                    "in-grid slider still lets you scrub per-session — "
                    "this is the value the grid reverts to on a fresh "
                    "open. Range 80–280 px."
                ),
                "min": 80, "max": 280, "step": 10,
                "decimals": 0, "suffix": " px",
            },
            # spec/95 §C — adaptive display resolution ceiling.
            # Machine-local (NOT in the roaming Settings): a desktop on
            # ``high`` and a laptop on ``balanced`` share one library
            # without conflict, by routing this key through
            # ``core.machine_settings`` in load_settings / save_settings
            # above.
            {
                "key": "display_quality",
                "label": "Display resolution",
                "widget": "combo",
                "tooltip": (
                    "Ceiling for the normal photo view (not Full "
                    "Resolution / F10). Balanced caps at 3840 px "
                    "(crisp on a 4K monitor, cheap on a laptop). High "
                    "caps at 5120 px for 5K/6K panels. Held-arrow "
                    "navigation always paints from the proxy first — "
                    "the higher-quality decode upgrades only after "
                    "you stop on a photo. This setting is per-machine "
                    "so a desktop and a laptop sharing one library "
                    "can each pick their own."
                ),
                "options": [
                    ("balanced", "Balanced (4K-class, 3840 px)"),
                    ("high", "High (5K-class, 5120 px)"),
                ],
                "restart_required": False,
            },
        ],
    },
    {
        "tab": "Paths",
        "fields": [
            {
                "key": "photos_base_path",
                "label": "Photos root",
                "widget": "folder",
                "tooltip": (
                    "Default base directory the app uses when prompting for "
                    "any photo input/output location. Example: D:\\Photos"
                ),
            },
            {
                "key": "audio_library_path",
                "label": "Audio library",
                "widget": "folder",
                "tooltip": (
                    "Folder containing music/ and sfx/ subtrees used by the "
                    "Audio Library page for slideshow soundtracks."
                ),
            },
            {
                "key": "print_export_path",
                "label": "Print export folder",
                "widget": "folder",
                "tooltip": (
                    "Where the Curate browse Print action (hotkey P) "
                    "copies source files for printing. Source is "
                    "copied as-is; collisions get a (2), (3), … "
                    "suffix."
                ),
            },
            {
                "key": "helicon_path",
                "label": "Helicon Focus",
                "widget": "file",
                "tooltip": (
                    "Path to the Helicon Focus executable. When set, the "
                    "Edit Stacks tab uses Helicon for focus brackets; "
                    "otherwise falls back to the embedded OpenCV engine."
                ),
            },
            # Orphan exposed (Nelson 2026-06-09).
            {
                "key": "prefer_helicon_for_focus",
                "label": "Prefer Helicon for focus stacks",
                "widget": "checkbox",
                "check_label": (
                    "Route focus brackets to Helicon Focus when its path "
                    "is set above"
                ),
                "tooltip": (
                    "When on, focus brackets are sent to Helicon Focus "
                    "(if configured above); when off, the embedded "
                    "OpenCV engine handles every bracket."
                ),
                "restart_required": False,
            },
            # spec/107 — PTE AV Studio integration. The master toggle
            # gates every PTE-specific surface; the path is what
            # Open-in-PTE actually launches.
            {
                "key": "use_pte",
                "label": "I use PTE AV Studio",
                "widget": "checkbox",
                "check_label": (
                    "Generate slideshow.pte on Cut export and show the "
                    "Open-in-PTE action"
                ),
                "tooltip": (
                    "Master switch for the PTE AV Studio integration "
                    "(spec/107). Off by default; non-PTE users never "
                    "see any PTE-related controls. On enables the "
                    "generator that writes slideshow.pte into the Cut "
                    "export folder, and the Open-in-PTE button on "
                    "the export summary."
                ),
                "restart_required": False,
            },
            {
                "key": "pte_path",
                "label": "PTE AV Studio",
                "widget": "file",
                "tooltip": (
                    "Path to the PTE AV Studio executable (e.g. "
                    "PicturesToExe.exe). The Open-in-PTE action "
                    "spawns this with the generated .pte; leave blank "
                    "to keep the action disabled."
                ),
            },
        ],
    },
    # spec/82 §G — Backups tab. ONE home for every cadence, count
    # and destination the snapshot + bundle features read; legacy
    # default_ssd_path / backup_on_quit_root migrated into
    # event_backup_destination by mira.settings.model._v1_to_v2.
    {
        "tab": "Backups",
        "fields": [
            {
                "key": "backup_snapshots_enabled",
                "label": "Automatic safety snapshots",
                "widget": "checkbox",
                "check_label": (
                    "Take automatic snapshots of each event's "
                    "database (on close, before risky operations, "
                    "after every added day)"
                ),
                "tooltip": (
                    "Master toggle for the spec/82 §A safety net. "
                    "Off disables both milestone and periodic "
                    "snapshots; the manual Restore from backup… "
                    "menu still works against the existing files."
                ),
            },
            {
                "key": "backup_periodic_minutes",
                "label": "Periodic-while-open cadence",
                "widget": "spinbox",
                "tooltip": (
                    "How often Mira takes a crash-insurance snapshot "
                    "of the currently-open event. Off-thread; skipped "
                    "when the database hasn't changed since the last "
                    "snapshot. Set to 0 to disable; milestone "
                    "snapshots still fire."
                ),
                "min": 0, "max": 120, "step": 1,
                "decimals": 0, "suffix": " min",
            },
            {
                "key": "backup_keep_milestone",
                "label": "Keep last N milestone snapshots",
                "widget": "spinbox",
                "tooltip": (
                    "Retention for milestone snapshots (close-if-"
                    "dirty, pre-risky-op, per-day-add, manual). "
                    "Higher = more rollback history at the cost of "
                    "disk; lower = tighter footprint."
                ),
                "min": 1, "max": 50, "step": 1,
                "decimals": 0,
            },
            {
                "key": "backup_keep_periodic",
                "label": "Keep last N periodic snapshots",
                "widget": "spinbox",
                "tooltip": (
                    "Retention for periodic snapshots (the N-minute "
                    "timer). These are crash insurance; a small N is "
                    "fine because the milestone snapshots carry the "
                    "longer history."
                ),
                "min": 1, "max": 20, "step": 1,
                "decimals": 0,
            },
            {
                "key": "backup_snapshots_root",
                "label": "Safety snapshots folder",
                "widget": "folder",
                "tooltip": (
                    "Where automatic safety snapshots live. Leave "
                    "blank to use <library>/.mira-backups (rides "
                    "your NAS RAID + snapshots). Set to a different "
                    "drive for true offsite of the DB."
                ),
            },
            {
                "key": "event_backup_destination",
                "label": "Default event-backup destination",
                "widget": "folder",
                "tooltip": (
                    "Where the Back up event… action and the "
                    "automatic on-quit bundle export land. Pre-fills "
                    "the file dialog; the manual action still lets "
                    "you confirm a different folder each time."
                ),
            },
            {
                "key": "event_backup_verify",
                "label": "Verify event bundles after copy",
                "widget": "checkbox",
                "check_label": (
                    "Re-hash every file in the bundle against its "
                    "manifest before finalising the export"
                ),
                "tooltip": (
                    "Catches a copy that went bad mid-transfer. On "
                    "by default; can be turned off to skip the "
                    "verify pass on very large events when you "
                    "trust the destination drive."
                ),
            },
            {
                "key": "backup_on_quit_enabled",
                "label": "Automatic backup on quit",
                "widget": "checkbox",
                "check_label": (
                    "Export the active event as a bundle to the "
                    "destination above every time you quit Mira"
                ),
                "tooltip": (
                    "When on, Mira runs a Part-B bundle export of "
                    "the currently-open event to "
                    "event_backup_destination on quit — an automatic "
                    "offsite copy that complements the manual Back "
                    "up event… action."
                ),
            },
        ],
    },
    {
        "tab": "Collect",
        "fields": [
            {
                "key": "cluster_window_camera_seconds",
                "label": "Camera moment-cluster window",
                "widget": "spinbox",
                "tooltip": (
                    "Photos shot within this many seconds of each other get "
                    "grouped into the same moment cluster on a camera import. "
                    "Tight (bursts), so a relatively short window. Default 60s."
                ),
                "min": 5.0, "max": 600.0, "step": 5.0,
                "decimals": 0, "suffix": " s",
            },
            {
                "key": "cluster_window_phone_seconds",
                "label": "Phone moment-cluster window",
                "widget": "spinbox",
                "tooltip": (
                    "Same idea as Camera, looser. Phone scenes (sunset, "
                    "dinner) span a few minutes naturally. Default 300s = 5 min."
                ),
                "min": 30.0, "max": 1800.0, "step": 30.0,
                "decimals": 0, "suffix": " s",
            },
            # Nelson 2026-06-09 audit — repeat-cluster window promotion.
            {
                "key": "repeat_window_seconds",
                "label": "Repeat-cluster window (span first→last)",
                "widget": "spinbox",
                "tooltip": (
                    "Phone photos whose total span from first to last is "
                    "within this many seconds cluster as a repeat. Default "
                    "2 s catches the \"tap-twice-just-in-case\" pattern. "
                    "Bump to 5 s for slower / deliberate doublets."
                ),
                "min": 0.5, "max": 30.0, "step": 0.5,
                "decimals": 1, "suffix": " s",
            },
            {
                "key": "peek_target_photos",
                "label": "Browse peek — sample size",
                "widget": "spinbox",
                "tooltip": (
                    "How many photos to sample for the per-day Browse peek. "
                    "Default 20. Bigger = more thorough preview but slower "
                    "to render."
                ),
                "min": 4, "max": 100, "step": 2,
                "decimals": 0,
            },
        ],
    },
    {
        "tab": "Pick",
        "fields": [
            # Nelson 2026-06-09 redesign — the legacy "Picker" (Cull) +
            # "Select" tabs collapse into one "Pick" tab, matching the
            # spec/48 4-phase model. The old ``cull_default_state`` key
            # had no consumer in the rebuild settings model; the live
            # key is ``pick_default_state``.
            {
                "key": "pick_default_state",
                "label": "Default state for untouched items",
                "widget": "combo",
                "tooltip": (
                    "What state items start in at the Pick phase when "
                    "you haven't explicitly marked them. Default "
                    "Discard matches the strict \"system never infers "
                    "done\" model — you must actively mark Pick for "
                    "anything to flow forward. Set to Pick if you "
                    "prefer to start permissive and demote the "
                    "exceptions."
                ),
                "options": [
                    ("skipped", "Skip"),
                    ("picked", "Pick"),
                ],
                "restart_required": False,
            },
            # Quick Sweep gets its own default (Nelson 2026-06-09 — the
            # capture-time triage default doesn't have to match the main
            # Pick phase). Default Pick matches Quick Sweep's "preserve
            # on inattention, yank the obvious garbage" philosophy; flip
            # to Skip for a stricter "actively pick keepers" flow.
            {
                "key": "quick_sweep_default_state",
                "label": "Default state for untouched items (Quick Sweep)",
                "widget": "combo",
                "tooltip": (
                    "What state items start in during the Quick Sweep "
                    "(capture-time triage on raw card files) when you "
                    "haven't explicitly marked them. Default Pick "
                    "preserves the photo on inattention — the user "
                    "must actively Skip the obvious garbage. Set to "
                    "Skip if you'd rather pick keepers explicitly "
                    "during capture-time triage."
                ),
                "options": [
                    ("picked", "Pick"),
                    ("skipped", "Skip"),
                ],
                "restart_required": False,
            },
            # Orphan exposed (Nelson 2026-06-09).
            {
                "key": "preferred_burst_genre",
                "label": "Preferred burst genre",
                "widget": "combo",
                "tooltip": (
                    "When a burst is detected without a clear scene "
                    "classification, treat it as this genre. Pick the "
                    "genre you shoot bursts of most often (e.g. "
                    "wildlife) so the suggested scenario matches."
                ),
                "options": [
                    ("", "(none)"),
                    ("wildlife", "Wildlife"),
                    ("sports", "Sports"),
                    ("kids", "Kids"),
                    ("portrait", "Portrait"),
                    ("street", "Street"),
                    ("landscape", "Landscape"),
                ],
                "restart_required": False,
            },
        ],
    },
    {
        "tab": "Edit",
        "fields": [
            {
                "key": "preferred_aspect_ratio",
                "label": "Preferred aspect ratio",
                "widget": "combo",
                "tooltip": (
                    "Your default output aspect ratio. Seeds the crop "
                    "ratio for new events in the Process phase, and "
                    "shapes the cull grid tiles when a bucket has no "
                    "dominant orientation. \"Original\" = no imposed "
                    "crop (grid falls back to square)."
                ),
                "options": [
                    (lbl, lbl) for lbl in _aspect_ratio_labels()
                ],
                "restart_required": False,
            },
            # Edit default — items have already survived Pick, so
            # adjusting individually is the gesture and demoting is the
            # exception. Flip to Skip for a strict final filter.
            {
                "key": "edit_default_state",
                "label": "Default state for untouched items",
                "widget": "combo",
                "tooltip": (
                    "What state items start in at the Edit phase when "
                    "you haven't explicitly marked them. Default Pick "
                    "— items have already survived Pick; adjusting is "
                    "the common action, demoting is the exception. "
                    "Set to Skip if you want Edit to be a strict "
                    "final filter requiring active Pick on each item."
                ),
                "options": [
                    ("skipped", "Skip"),
                    ("picked", "Pick"),
                ],
                "restart_required": False,
            },
            # Nelson 2026-06-09 audit — JPEG export quality promotion.
            {
                "key": "jpeg_export_quality",
                "label": "JPEG export quality",
                "widget": "spinbox",
                "tooltip": (
                    "Quality (0–100) for processed JPEGs written by Edit. "
                    "Default 95 — visually transparent at most sizes. "
                    "Drop to 85 to halve filesizes; push to 100 only when "
                    "every byte matters."
                ),
                "min": 50, "max": 100, "step": 1,
                "decimals": 0,
            },
            # spec/59 §8 — the Exported watermark's one and only
            # control: the app-wide hide switch. The watermark itself
            # is system-set (lineage-driven), never per-item.
            {
                "key": "show_exported_watermark",
                "label": "Exported watermark",
                "widget": "checkbox",
                "check_label": (
                    "Show a diagonal 'Exported' over photos that "
                    "already have an exported version"
                ),
                "tooltip": (
                    "Photos that already have an exported or "
                    "externally-edited version (from Edit exports or "
                    "files this app associated back) wear a translucent "
                    "diagonal 'Exported' in the Edit grids and photo "
                    "view, so you can tell at a glance what's done. "
                    "Turn off to hide the watermark everywhere — the "
                    "underlying export records are untouched. "
                    "Defaults to on."
                ),
                "restart_required": False,
            },
            # spec/96 §2 — Quick Sweep's exposure pill (legacy gate; the
            # Picker / Editor moved to viewer_overlay_fields per
            # spec/134).
            {
                "key": "show_exposure_overlay",
                "label": "Exposure overlay (Quick Sweep)",
                "widget": "checkbox",
                "check_label": (
                    "Show the exposure pill over photos in Quick Sweep"
                ),
                "tooltip": (
                    "The pill at the bottom of single-photo views in "
                    "Quick Sweep shows the camera + shutter / aperture "
                    "/ ISO / focal length + file type and size. Turn "
                    "off to hide it; the EXIF in the file is untouched. "
                    "Defaults to on. (Picker + Editor use the "
                    "configurable Photo viewer overlay below.)"
                ),
                "restart_required": False,
            },
            # spec/134 — configurable photo-viewer overlay for the
            # Picker + Editor. Reuses the cut-overlay vocabulary so
            # Photos and Cuts speak one language.
            {
                "key": "viewer_overlay_fields",
                "label": "Photo viewer overlay",
                "widget": "overlay_fields",
                "tooltip": (
                    "Choose what to show over photos in the Picker and "
                    "Editor. Each tick adds one line: When (date / time), "
                    "Where (city / country), Camera (camera / lens / "
                    "flash), Exposure (focal length / aperture / shutter "
                    "/ ISO). Empty = overlay off. Defaults to Exposure."
                ),
                "restart_required": False,
            },
            # Nelson 2026-06-09 audit — focus-peaking opacity promotion.
            # Completes the existing peaking_color + peaking_sensitivity
            # pair; the third knob users routinely want.
            {
                "key": "focus_peaking_opacity",
                "label": "Focus peaking opacity",
                "widget": "spinbox",
                "tooltip": (
                    "How opaque the focus-peaking overlay is over the "
                    "photo. 0.0 = invisible, 1.0 = fully opaque. Default "
                    "0.7 is a balance that lets you still see what's in "
                    "focus underneath."
                ),
                "min": 0.0, "max": 1.0, "step": 0.05,
                "decimals": 2,
            },
        ],
    },
    # Tone calibration trims (spec/54 §4.1 + spec/55, Nelson 2026-06-10):
    # the field-calibration drawer. The Edit surface stays zero-slider;
    # here the calibrating user trims every Look/filter from -100 (off /
    # same as Natural) through 0 (the shipped recipe, the default) to
    # +100 (double strength). Settled positions get harvested as new
    # shipped defaults. Rendered as ONE AdjustmentGrid (widget "slider").
    {
        "tab": "Calibration",
        "fields": [
            {
                "key": "look_scale_natural", "label": "Natural correction",
                "widget": "slider",
                "tooltip": (
                    "How strongly the calibrated automatic correction "
                    "applies everywhere. -100 = no correction, 0 = as "
                    "calibrated, +100 = double."
                ),
            },
            {
                "key": "look_scale_brighter", "label": "Brighter look",
                "widget": "slider",
                "tooltip": (
                    "How far the Brighter look moves from Natural. "
                    "-100 = identical to Natural, 0 = shipped, +100 = "
                    "double the push."
                ),
            },
            {
                "key": "look_scale_deeper", "label": "Deeper look",
                "widget": "slider",
                "tooltip": (
                    "How far the Deeper look moves from Natural. -100 = "
                    "identical to Natural, 0 = shipped, +100 = double "
                    "the push."
                ),
            },
            {
                "key": "filter_scale_vivid", "label": "Vivid filter",
                "widget": "slider",
                "tooltip": "Vivid strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_bw", "label": "B&W filter",
                "widget": "slider",
                "tooltip": "B&W strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_sepia", "label": "Sepia filter",
                "widget": "slider",
                "tooltip": "Sepia strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_faded", "label": "Faded filter",
                "widget": "slider",
                "tooltip": "Faded strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_golden", "label": "Golden filter",
                "widget": "slider",
                "tooltip": "Golden strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_cinema", "label": "Cinema filter",
                "widget": "slider",
                "tooltip": "Cinema strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_bleach", "label": "Bleach filter",
                "widget": "slider",
                "tooltip": "Bleach strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_dramatic", "label": "Dramatic filter",
                "widget": "slider",
                "tooltip": "Dramatic strength: -100 off · 0 shipped · +100 double.",
            },
            {
                "key": "filter_scale_crisp", "label": "Crisp filter",
                "widget": "slider",
                "tooltip": "Crisp strength: -100 off · 0 shipped · +100 double.",
            },
        ],
    },
    {
        "tab": "Share",
        "fields": [
            {
                "key": "preferred_genres",
                "label": "Preferred genres",
                "widget": "genre_pair",
                "tooltip": (
                    "Your two favourite photo genres. Curate adds one "
                    "review pass per genre (between Best and Short), "
                    "and the Cuts/ folder tree gets a flat "
                    "archive folder per genre. Defaults are Macro + "
                    "Wildlife — pick whichever pair fits your trips."
                ),
                "restart_required": False,
            },
            # Per-tier slideshow pacing (Nelson 2026-05-26). The
            # closed-event slideshow viewer reads these when the user
            # clicks the corresponding bucket chip on EventPlanPage:
            # Short → fast, Long → slow. The browse page's spinner
            # lets the user override mid-play; these are the defaults.
            {
                "key": "slideshow_seconds_per_slide_short",
                "label": "Short slideshow — seconds per slide",
                "widget": "spinbox",
                "tooltip": (
                    "How long each slide stays on screen in the "
                    "Short tier. Default 4 s. Also drives the Short "
                    "time-budget counter (how many photos fit in the "
                    "max time below) and the EventCard recap."
                ),
                "min": 0.5, "max": 60.0, "step": 0.5,
                "decimals": 1, "suffix": " s",
            },
            {
                "key": "slideshow_seconds_per_slide_medium",
                "label": "Medium slideshow — seconds per slide",
                "widget": "spinbox",
                "tooltip": (
                    "How long each slide stays on screen in the "
                    "Medium tier. Default 6 s. Also drives the Medium "
                    "time-budget counter and the EventCard recap."
                ),
                "min": 0.5, "max": 60.0, "step": 0.5,
                "decimals": 1, "suffix": " s",
            },
            {
                "key": "slideshow_seconds_per_slide_long",
                "label": "Long slideshow — seconds per slide",
                "widget": "spinbox",
                "tooltip": (
                    "How long each slide stays on screen in the "
                    "Long tier. Default 6 s. Also drives the Long "
                    "time-budget counter and the EventCard recap."
                ),
                "min": 0.5, "max": 60.0, "step": 0.5,
                "decimals": 1, "suffix": " s",
            },
            # Per-tier MAX TIME budgets in minutes (docs/27 §6, Nelson
            # 2026-05-29). The Curate time-remaining counter counts
            # down toward these as the user builds each tier.
            {
                "key": "slideshow_max_minutes_short",
                "label": "Short slideshow — max time",
                "widget": "spinbox",
                "tooltip": (
                    "Target length of the Short tier — the highlight "
                    "reel for casual viewers. Default 3 min. The Curate "
                    "counter counts down toward this as you add photos."
                ),
                "min": 0.5, "max": 600.0, "step": 0.5,
                "decimals": 1, "suffix": " min",
            },
            {
                "key": "slideshow_max_minutes_medium",
                "label": "Medium slideshow — max time",
                "widget": "spinbox",
                "tooltip": (
                    "Target length of the Medium tier, trimmed down "
                    "from Long. Default 15 min. The Curate counter "
                    "counts down toward this as you remove slides."
                ),
                "min": 0.5, "max": 600.0, "step": 0.5,
                "decimals": 1, "suffix": " min",
            },
            {
                "key": "slideshow_max_minutes_long",
                "label": "Long slideshow — max time",
                "widget": "spinbox",
                "tooltip": (
                    "Target length of the Long tier — the full "
                    "day-by-day recollection. Default 30 min (45 min "
                    "is the practical boredom ceiling). The Curate "
                    "counter counts down toward this as you add slides."
                ),
                "min": 0.5, "max": 600.0, "step": 0.5,
                "decimals": 1, "suffix": " min",
            },
        ],
    },
    {
        "tab": "Video",
        "fields": [
            # spec/138 §2D — global default playback speed for video
            # surfaces. Sticky for the session; the transport combo
            # still lets the user change it live.
            {
                "key": "default_video_speed",
                "label": "Default video speed",
                "widget": "combo",
                "tooltip": (
                    "Default video playback speed (×) the Picker / "
                    "Editor video transport opens at. Sticky for the "
                    "session — you can still pick a different rate "
                    "live from the speed control on any clip."
                ),
                "options": [
                    (0.25, "0.25×"),
                    (0.5, "0.5×"),
                    (1.0, "1×"),
                    (1.5, "1.5×"),
                    (2.0, "2×"),
                ],
                "restart_required": False,
            },
            # Nelson 2026-06-09 audit — video clip CRF promotion.
            {
                "key": "video_clip_crf",
                "label": "Clip export CRF",
                "widget": "spinbox",
                "tooltip": (
                    "ffmpeg Constant Rate Factor used when exporting "
                    "clips and motion fragments. Lower = higher quality "
                    "(bigger file). 18–22 is the visually-lossless band; "
                    "default 20 is a balanced choice. 23–28 trades "
                    "quality for storage on long clips."
                ),
                "min": 14, "max": 32, "step": 1,
                "decimals": 0,
            },
        ],
    },

    {
        "tab": "Advanced",
        "fields": [
            {
                "key": "log_level",
                "label": "Log level",
                "widget": "combo",
                "tooltip": (
                    "Verbosity of the log file. DEBUG when diagnosing a "
                    "problem; INFO otherwise."
                ),
                "options": [
                    ("DEBUG", "Debug"),
                    ("INFO", "Info"),
                    ("WARNING", "Warning"),
                    ("ERROR", "Error"),
                ],
                "restart_required": False,
            },
            # Nelson 2026-06-09 audit — log retention promotion.
            {
                "key": "log_rotate_keep_days",
                "label": "Log retention",
                "widget": "spinbox",
                "tooltip": (
                    "How many days of rotated log files to keep on disk. "
                    "Older logs are deleted at startup. 14 days is plenty "
                    "for typical debugging; shorter saves disk space."
                ),
                "min": 1, "max": 365, "step": 1,
                "decimals": 0, "suffix": " days",
            },
            # spec/63 slice 7 — proxy-tier disk honesty. Read-only;
            # the value provider + clear action are injected by
            # MainWindow (they need the open event).
            {
                "widget": "info",
                "label": "Screen copies",
                "info_id": "proxy_cache",
                "tooltip": (
                    "Disk used by the open event's browsing copies "
                    "(~2560 px JPEGs under the event's .cache folder). "
                    "They make photo browsing fast; originals are "
                    "untouched and full resolution stays one F10 away. "
                    "Safe to clear — they rebuild quietly while you "
                    "browse."
                ),
                "action_id": "clear_proxy_cache",
                "action_label": "Clear…",
                "action_tooltip": (
                    "Delete the open event's browsing copies. Nothing "
                    "is lost — they regenerate in the background the "
                    "next time you browse the event."
                ),
                "action_confirm": (
                    "Delete this event's browsing copies?\n\n"
                    "Browsing will be slower until they rebuild "
                    "(automatic, in the background). Originals are "
                    "not touched."
                ),
            },
        ],
    },
]


@dataclass
class _FieldBinding:
    """Internal binding between a schema entry and the rendered widget."""
    key: str
    widget: QWidget
    read: Callable[[], Any]
    write: Callable[[Any], None]


class SettingsDialog(QDialog):
    """Declaratively-rendered tabbed settings dialog.

    Reads current values from ``load_settings()``, lets the user edit
    via the schema-driven widgets, writes back via ``save_settings()``
    on Apply. The on-disk write is atomic (write-then-rename) per
    ``core/settings.save_settings``.

    Reset Settings is a destructive button (with confirmation) that
    writes ``DEFAULT_SETTINGS`` back to the user's settings.json.

    Override ``changes_applied(changed_dict)`` to react to applied
    changes (the host MainWindow re-applies theme / language live).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        schema: list[dict] | None = None,
        info_providers: dict[str, Callable[[], str]] | None = None,
        info_actions: dict[str, Callable[[], str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._schema = schema if schema is not None else SETTINGS_SCHEMA
        self._bindings: list[_FieldBinding] = []
        self._initial_values: dict[str, Any] = {}
        # Read-only "info" rows (spec/63 slice 7 disk honesty): the
        # host injects live value providers keyed by the schema entry's
        # ``info_id`` (e.g. the open event's proxy-cache size) and
        # optional action callables keyed by ``action_id`` (run on the
        # row's button after a NoIcon confirm; the returned string
        # refreshes nothing — the provider is re-read instead). Rows
        # whose provider is missing render an honest em-dash.
        self._info_providers = info_providers or {}
        self._info_actions = info_actions or {}

        self.setWindowTitle(tr("Settings"))
        self.setModal(True)
        self.resize(640, 480)

        self._build_ui()
        self._load_initial()

    # ── Construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self._tabs = QTabWidget(self)
        for tab_entry in self._schema:
            tab_widget = self._build_tab(tab_entry)
            self._tabs.addTab(tab_widget, tr(tab_entry["tab"]))
        root.addWidget(self._tabs, stretch=1)

        # Button row: Reset Settings on the far left, then OK/Cancel.
        button_row = QHBoxLayout()
        self._reset_button = QPushButton(tr("Reset Settings…"))
        self._reset_button.setCursor(
            QCursor(Qt.CursorShape.PointingHandCursor),
        )
        self._reset_button.setToolTip(tr(
            "Overwrite all settings with the hardcoded defaults. "
            "The current values are not preserved — confirm before "
            "applying."
        ))
        self._reset_button.clicked.connect(self._on_reset_clicked)
        button_row.addWidget(self._reset_button)
        button_row.addStretch(1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Ok,
        ).setText(tr("Apply"))
        self._buttons.accepted.connect(self._on_apply)
        self._buttons.rejected.connect(self.reject)
        button_row.addWidget(self._buttons)

        root.addLayout(button_row)

    def _build_tab(self, tab_entry: dict) -> QWidget:
        """Build one tab page. Empty fields list → placeholder text so
        the user sees the tab exists and where future settings will live."""
        host = QWidget()
        fields = tab_entry.get("fields", [])
        if not fields:
            layout = QVBoxLayout(host)
            layout.setContentsMargins(40, 40, 40, 40)
            placeholder = QLabel(tr(
                "No settings here yet. This tab will host the "
                "{tab} module's options as they land."
            ).replace("{tab}", tr(tab_entry["tab"])))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setObjectName("WizardHint")
            placeholder.setWordWrap(True)
            layout.addWidget(placeholder)
            return host

        form = QFormLayout(host)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(12)
        form.setContentsMargins(16, 16, 16, 16)
        i = 0
        while i < len(fields):
            entry = fields[i]
            # Runs of consecutive ``slider`` fields render as ONE shared
            # AdjustmentGrid (the house slider widget — label | slider |
            # value | reset, columns aligned across rows; Nelson
            # 2026-06-10, the calibration-trims slice).
            if entry.get("widget") == "slider":
                run: list[dict] = []
                while i < len(fields) and fields[i].get("widget") == "slider":
                    run.append(fields[i])
                    i += 1
                grid, accessors = self._build_slider_grid(run)
                form.addRow(grid)
                for run_entry, (reader, writer) in zip(run, accessors):
                    self._bindings.append(_FieldBinding(
                        key=run_entry["key"], widget=grid,
                        read=reader, write=writer,
                    ))
                continue
            # Read-only info rows carry no settings key and no binding —
            # they show a host-provided live value (+ optional action).
            if entry.get("widget") == "info":
                self._add_info_row(form, entry)
                i += 1
                continue
            label_text = tr(entry["label"])
            if entry.get("restart_required"):
                label_text = label_text + tr(" (restart)")
            widget, reader, writer = self._build_field_widget(entry)
            if entry.get("tooltip"):
                widget.setToolTip(tr(entry["tooltip"]))
            form.addRow(QLabel(label_text), widget)
            self._bindings.append(_FieldBinding(
                key=entry["key"], widget=widget, read=reader, write=writer,
            ))
            i += 1
        return host

    def _add_info_row(self, form: QFormLayout, entry: dict) -> None:
        """One read-only row: value label from the injected provider
        (``info_id``), plus an optional action button (``action_id``,
        NoIcon-confirmed) that re-reads the provider afterwards."""
        provider = self._info_providers.get(entry.get("info_id", ""))
        value_label = QLabel(self._info_value(provider))
        if entry.get("tooltip"):
            value_label.setToolTip(tr(entry["tooltip"]))
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        row_layout.addWidget(value_label, stretch=1)
        action = self._info_actions.get(entry.get("action_id", ""))
        if action is not None and entry.get("action_label"):
            button = QPushButton(tr(entry["action_label"]))
            button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            if entry.get("action_tooltip"):
                button.setToolTip(tr(entry["action_tooltip"]))

            def _run_action() -> None:
                confirm_text = entry.get("action_confirm")
                if confirm_text:
                    box = QMessageBox(self)
                    box.setIcon(QMessageBox.Icon.NoIcon)
                    box.setWindowTitle(tr(entry["label"]))
                    box.setText(tr(confirm_text))
                    box.setStandardButtons(
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.No)
                    box.setDefaultButton(QMessageBox.StandardButton.No)
                    if box.exec() != QMessageBox.StandardButton.Yes:
                        return
                try:
                    action()
                except Exception:                                  # noqa: BLE001
                    log.exception(
                        "settings info action failed: %s",
                        entry.get("action_id"))
                value_label.setText(self._info_value(provider))

            button.clicked.connect(_run_action)
            row_layout.addWidget(button)
        form.addRow(QLabel(tr(entry["label"])), row)

    @staticmethod
    def _info_value(provider: Callable[[], str] | None) -> str:
        if provider is None:
            return "—"
        try:
            return str(provider())
        except Exception:                                            # noqa: BLE001
            log.exception("settings info provider failed")
            return "—"

    def _build_slider_grid(
        self, entries: list[dict],
    ) -> tuple[QWidget, list[tuple[Callable[[], Any], Callable[[Any], None]]]]:
        """One AdjustmentGrid for a run of ``slider`` schema entries.
        Each entry: key/label/tooltip + optional min/max/default/step/
        decimals (defaults: -100..100, 0, 1, 0 — the calibration-trim
        shape). The first entry may set ``grid_columns``."""
        from mira.ui.edited.adjustment_grid import (
            AdjustmentGrid,
            AdjustmentSpec,
        )
        specs = [
            AdjustmentSpec(
                e["key"], tr(e["label"]),
                float(e.get("min", -100)), float(e.get("max", 100)),
                float(e.get("default", 0)), float(e.get("step", 1)),
                int(e.get("decimals", 0)),
                hint=tr(e["tooltip"]) if e.get("tooltip") else "",
            )
            for e in entries
        ]
        grid = AdjustmentGrid(
            specs, columns=int(entries[0].get("grid_columns", 1)))
        accessors: list[tuple[Callable[[], Any], Callable[[Any], None]]] = []
        for e in entries:
            key = e["key"]
            decimals = int(e.get("decimals", 0))

            def _read(key=key, decimals=decimals) -> Any:
                v = grid.value(key)
                return int(round(v)) if decimals == 0 else float(v)

            def _write(value: Any, key=key) -> None:
                grid.set_value(key, float(value or 0))

            accessors.append((_read, _write))
        return grid, accessors

    def _build_field_widget(
        self, entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Build the input widget for a schema entry + accessors.

        Returns ``(widget, read_fn, write_fn)``. ``read_fn`` returns
        the current widget value; ``write_fn(v)`` sets it.
        """
        kind = entry.get("widget", "combo")
        if kind == "combo":
            return self._build_combo(entry)
        if kind == "folder":
            return self._build_path(entry, mode="folder")
        if kind == "file":
            return self._build_path(entry, mode="file")
        if kind == "spinbox":
            return self._build_spinbox(entry)
        if kind == "checkbox":
            return self._build_checkbox(entry)
        if kind == "overlay_fields":
            return self._build_overlay_fields(entry)
        if kind == "genre_pair":
            return self._build_genre_pair(entry)
        if kind == "tz_picker":
            return self._build_tz_picker(entry)
        if kind == "country_picker":
            return self._build_country_picker(entry)
        raise ValueError(f"Unknown widget kind in schema: {kind!r}")

    def _build_country_picker(
        self, _entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Single-country picker (spec/52 §8 + Nelson 2026-06-08 home-country
        fallback). Returns the user's ISO 3166-1 alpha-2 code (e.g. 'BR'),
        or an empty string when no country picked. Reuses the shared
        ``country_picker`` factory so the dropdown looks identical to the
        Plan dialog's per-day country picker."""
        from mira.ui.base.country_picker import (
            country_code_from_combo, make_single_country_combo,
        )
        combo = make_single_country_combo()

        def _read() -> Any:
            return country_code_from_combo(combo) or ""

        def _write(value: Any) -> None:
            if not value:
                combo.setCurrentIndex(0)                     # blank entry
                return
            idx = combo.findData(str(value).upper())
            combo.setCurrentIndex(idx if idx >= 0 else 0)

        return combo, _read, _write

    def _build_tz_picker(
        self, _entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Named-location TZ picker (spec/52 §8). Wraps the shared
        :class:`mira.ui.base.tz_picker.TzPicker` so the user
        gets the same picker here as inside the per-day plan, avoiding
        the +5:45 vs +5.45 decimal trap on a free-text TZ entry."""
        from mira.ui.base.tz_picker import TzPicker
        picker = TzPicker()

        def _read() -> Any:
            return float(picker.value())

        def _write(value: Any) -> None:
            try:
                picker.setValue(float(value))
            except (TypeError, ValueError):
                picker.setValue(0.0)

        return picker, _read, _write

    def _build_genre_pair(
        self, _entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Two side-by-side combos for the user's preferred genres
        (Nelson 2026-05-21). Each combo lists every FINAL Scenario;
        read returns ``[g1, g2]`` (lowercase Scenario values) ready
        for ``settings["preferred_genres"]``. write accepts the same
        list and pre-selects each combo.
        """
        from core.vocabulary import FINAL_SCENARIOS

        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Two combos populated from the Scenario enum. Title-cased
        # display so 'macro' reads as 'Macro' on screen; the stored
        # value stays lowercase to match preferred_genres_for_user.
        combo_a = QComboBox()
        combo_b = QComboBox()
        for g in FINAL_SCENARIOS:
            combo_a.addItem(tr(g.value.title()), g.value)
            combo_b.addItem(tr(g.value.title()), g.value)
        combo_a.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        combo_b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout.addWidget(combo_a)
        layout.addWidget(combo_b)
        layout.addStretch(1)

        def _read() -> Any:
            return [
                combo_a.currentData(),
                combo_b.currentData(),
            ]

        def _write(value: Any) -> None:
            if not isinstance(value, list):
                return
            if len(value) >= 1:
                idx = combo_a.findData(value[0])
                combo_a.setCurrentIndex(idx if idx >= 0 else 0)
            if len(value) >= 2:
                idx = combo_b.findData(value[1])
                # If the user picked the same genre twice in
                # settings.json (paranoia), shift combo_b to the
                # next one so they see two distinct picks.
                if idx >= 0 and value[1] == value[0]:
                    idx = (idx + 1) % combo_b.count()
                combo_b.setCurrentIndex(idx if idx >= 0 else 0)

        return host, _read, _write

    def _build_overlay_fields(
        self, _entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """spec/134 — four checkboxes (When / Where / Camera /
        Exposure) → ``list[str]`` of the selected OVERLAY_FIELDS keys
        in canonical order. spec/119 multi-select pattern: real
        ``QCheckBox`` ticks (not pill toggles), independent state,
        read returns the canonical-order subset regardless of click
        order."""
        from core.cut_overlay import (
            FIELD_HOW1, FIELD_HOW2, FIELD_WHEN, FIELD_WHERE,
            OVERLAY_FIELDS,
        )

        # (key, label) pairs in canonical OVERLAY_FIELDS order so the
        # row reads When / Where / Camera / Exposure left → right.
        spec = (
            (FIELD_WHEN, "When"),
            (FIELD_WHERE, "Where"),
            (FIELD_HOW1, "Camera"),
            (FIELD_HOW2, "Exposure"),
        )

        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        checks: dict[str, QCheckBox] = {}
        for key, label in spec:
            cb = QCheckBox(tr(label))
            cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            layout.addWidget(cb)
            checks[key] = cb
        layout.addStretch(1)

        def _read() -> Any:
            # Iterate OVERLAY_FIELDS (the canonical order), not the
            # widget map's insertion — so the persisted list is stable
            # regardless of how the user clicked through.
            return [k for k in OVERLAY_FIELDS if checks[k].isChecked()]

        def _write(value: Any) -> None:
            chosen = set(value or [])
            for key, cb in checks.items():
                cb.setChecked(key in chosen)

        return host, _read, _write

    def _build_checkbox(
        self, entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Boolean toggle. Settings persist as Python ``bool``; the
        widget shows the optional ``check_label`` next to the tick
        for an inline hint (e.g. "Enable" beside the field's main
        label)."""
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox(entry.get("check_label", ""))
        cb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout.addWidget(cb)
        layout.addStretch(1)

        def _read() -> Any:
            return cb.isChecked()

        def _write(value: Any) -> None:
            cb.setChecked(bool(value))

        return host, _read, _write

    def _build_combo(
        self, entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        combo = QComboBox()
        for value, display in entry.get("options", []):
            combo.addItem(tr(display), value)

        def _read() -> Any:
            return combo.currentData()

        def _write(value: Any) -> None:
            idx = combo.findData(value)
            if idx < 0:
                idx = 0
            combo.setCurrentIndex(idx)

        return combo, _read, _write

    def _build_path(
        self, entry: dict, *, mode: str,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        """Folder or file picker: line edit + Browse button.

        ``mode`` is ``"folder"`` (QFileDialog.getExistingDirectory) or
        ``"file"`` (QFileDialog.getOpenFileName).
        """
        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        line = QLineEdit()
        line.setPlaceholderText(tr("(not set)"))
        layout.addWidget(line, stretch=1)
        browse = QPushButton(tr("Browse…"))
        browse.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        layout.addWidget(browse)

        def _on_browse() -> None:
            current = line.text().strip()
            if mode == "folder":
                chosen = QFileDialog.getExistingDirectory(
                    self, tr("Choose folder"), current,
                    QFileDialog.Option.ShowDirsOnly,
                )
            else:
                chosen, _ = QFileDialog.getOpenFileName(
                    self, tr("Choose file"), current,
                )
            if chosen:
                line.setText(chosen)

        browse.clicked.connect(_on_browse)

        def _read() -> Any:
            return line.text().strip()

        def _write(value: Any) -> None:
            line.setText(str(value) if value else "")

        return host, _read, _write

    def _build_spinbox(
        self, entry: dict,
    ) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        decimals = int(entry.get("decimals", 0))
        if decimals > 0:
            spin: QSpinBox | QDoubleSpinBox = QDoubleSpinBox()
            spin.setDecimals(decimals)
        else:
            spin = QSpinBox()
        spin.setMinimum(int(entry.get("min", 0)) if decimals == 0 else
                        float(entry.get("min", 0.0)))
        spin.setMaximum(int(entry.get("max", 1000)) if decimals == 0 else
                        float(entry.get("max", 1000.0)))
        spin.setSingleStep(int(entry.get("step", 1)) if decimals == 0 else
                           float(entry.get("step", 1.0)))
        if entry.get("suffix"):
            spin.setSuffix(entry["suffix"])

        def _read() -> Any:
            return spin.value()

        def _write(value: Any) -> None:
            try:
                spin.setValue(float(value) if decimals > 0 else int(value))
            except (TypeError, ValueError):
                pass

        return spin, _read, _write

    # ── Load / Apply / Reset ────────────────────────────────────────

    def _load_initial(self) -> None:
        """Pre-populate every widget from current settings."""
        current = load_settings()
        for binding in self._bindings:
            value = current.get(binding.key)
            self._initial_values[binding.key] = value
            binding.write(value)

    def _on_apply(self) -> None:
        """Read every bound widget; persist changed values atomically."""
        current = load_settings()
        changed: dict[str, tuple[Any, Any]] = {}
        for binding in self._bindings:
            new_value = binding.read()
            old_value = current.get(binding.key)
            if new_value != old_value:
                current[binding.key] = new_value
                changed[binding.key] = (old_value, new_value)

        if changed:
            # Pre-commit veto (charter §5.9, Nelson 2026-06-01): a host may refuse a change
            # BEFORE anything is persisted — e.g. switching photos_base_path would orphan
            # events anchored relative to the current base. A returned message aborts the
            # Apply with nothing written and the dialog left open so the user can fix it.
            veto = self.validate_changes(changed)
            if veto:
                QMessageBox.warning(self, tr("Can't apply these settings"), veto)
                return
            save_settings(current)
            log.info(
                "Settings updated: %s",
                ", ".join(
                    f"{k} {old!r}→{new!r}"
                    for k, (old, new) in changed.items()
                ),
            )
            # Tone-calibration trims are cached inside the engine
            # (core.photo_auto) — drop the cache so the next render
            # anywhere picks the new values up (spec/54 §4.1 trims).
            if any(k.startswith(("look_scale_", "filter_scale_"))
                   for k in changed):
                from core.photo_auto import invalidate_tone_scaling_cache
                invalidate_tone_scaling_cache()
            self.changes_applied(changed)
        else:
            log.debug("Settings dialog Apply with no changes")
        self.accept()

    def _on_reset_clicked(self) -> None:
        """Confirm + reset all settings to hardcoded defaults."""
        choice = QMessageBox.question(
            self,
            tr("Reset Settings"),
            tr(
                "Overwrite ALL settings with the hardcoded defaults?\n\n"
                "This cannot be undone — you'll have to reconfigure paths "
                "and preferences. The lens registry, events, and other "
                "user data are NOT affected."
            ),
            QMessageBox.StandardButton.Reset
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Reset:
            return
        old = load_settings()
        defaults = reset_settings_to_defaults()
        changed: dict[str, tuple[Any, Any]] = {}
        for key, new_value in defaults.items():
            old_value = old.get(key)
            if new_value != old_value:
                changed[key] = (old_value, new_value)
        # Reload all widgets from the freshly-written defaults.
        for binding in self._bindings:
            binding.write(defaults.get(binding.key))
        log.info(
            "Settings reset to defaults (%d key(s) changed)", len(changed),
        )
        # Trims back to 0 — drop the engine cache (spec/54 §4.1).
        from core.photo_auto import invalidate_tone_scaling_cache
        invalidate_tone_scaling_cache()
        if changed:
            self.changes_applied(changed)

    # ── Override hooks ──────────────────────────────────────────────

    def validate_changes(
        self, changed: dict[str, tuple[Any, Any]],
    ) -> str | None:
        """Pre-commit veto hook invoked on Apply BEFORE anything is persisted.

        Return a user-facing message to **abort** the Apply (nothing is written, the
        dialog stays open); return ``None`` to allow it. The host MainWindow overrides
        this to refuse a ``photos_base_path`` change that would orphan events anchored
        relative to the current base (charter §5.9). Default allows everything."""
        return None

    def changes_applied(self, changed: dict[str, tuple[Any, Any]]) -> None:
        """Override hook invoked after a successful Apply (or Reset)
        with at least one changed setting. Default implementation
        does nothing; the host MainWindow overrides to reapply theme,
        reload translations, etc."""
        return


# ── First-run prompt for photos_base_path ─────────────────────────────


def maybe_prompt_for_photos_base_path(parent: QWidget | None = None) -> bool:
    """First-run helper: prompt for ``photos_base_path`` if it's empty.

    Returns True if the path is set (either it was already set OR the
    user picked one). Returns False if the user cancelled and the path
    remains empty.

    The caller (MainWindow on first launch) shows this before the
    wizard. The user can change it later via Settings → Paths.
    """
    settings = load_settings()
    existing = (settings.get("photos_base_path") or "").strip()
    if existing and Path(existing).exists():
        return True

    chosen = QFileDialog.getExistingDirectory(
        parent,
        tr("Welcome to Mira — choose your photos root"),
        existing or "",
        QFileDialog.Option.ShowDirsOnly,
    )
    if not chosen:
        return False
    settings["photos_base_path"] = chosen
    save_settings(settings)
    log.info("First-run: photos_base_path set to %s", chosen)
    return True
