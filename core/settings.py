"""App-level settings persistence (JSON in %LOCALAPPDATA%).

Includes a small recovery layer (XdTd pattern, per docs/14): on a
``JSONDecodeError`` the loader attempts a regex-based repair pass
(strip trailing commas — the most common hand-edit mistake) before
falling back to defaults. The aim is "user's settings file got slightly
broken → app still boots with whatever data could be salvaged",
not "build a JSON parser." Anything we can't salvage falls through
to DEFAULT_SETTINGS with a logged warning so the user sees something
went wrong on next idle.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
import platform

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    # Global paths — all empty by default.
    # The user sets photos_base_path explicitly during onboarding.
    # Never hardcode a path like D:\Photos — the app has no right to assume
    # where the user wants their photos stored.
    "photos_base_path": "",
    "exiftool_path": "",
    # spec/82 §G — Backups tab defaults.
    "backup_snapshots_enabled": True,
    "backup_periodic_minutes": 15,
    "backup_keep_milestone": 10,
    "backup_keep_periodic": 3,
    "backup_snapshots_root": "",
    "event_backup_destination": "",
    "event_backup_verify": True,
    # backup_on_quit: the on-quit Part-B bundle export. Re-pointed
    # at ``event_backup_destination`` in spec/82 §G; the legacy
    # ``backup_on_quit_root`` key folded into the same destination
    # by the v1→v2 settings migration.
    "backup_on_quit_enabled": False,
    # Audio library root — folder containing music/ and sfx/
    # subtrees. Used by the Audio Library page to scan for tracks
    # (slideshow soundtrack helper). Empty = not configured; the
    # page prompts the user to pick a folder on first open.
    "audio_library_path": "",
    # F-003 (2026-05-24) — destination folder for the Curate
    # browse "Print" action (hotkey P). The print-preview dialog
    # copies the source-as-is to this folder; collisions get a
    # ``(2)`` / ``(3)`` ... suffix. Empty = the dialog prompts the
    # user to set it on first use (then re-tries with the new value).
    "print_export_path": "",

    # Home timezone (UTC offset, e.g. -3.0 for São Paulo)
    "home_timezone": round(-time.timezone / 3600, 1),

    # UI
    "theme": "dark",
    "language": "en",  # ISO 639-1; v1 ships en + pt (v1.1 adds es)
    "font_scale": 1.0,
    "window_geometry": "",
    "window_state": "",

    # Last used
    "last_event_id": "",

    # Resume hint — populated when the user is mid-cull. On next launch
    # the app checks this together with the journal's committed_at to
    # decide whether to offer Resume / Discard / Cancel. None when no
    # resume target. Shape when set::
    #
    #     {
    #         "page": "culler",
    #         "event_id": "<uuid>",
    #         "day_number": <int>,
    #         "bucket": "Individual",
    #     }
    #
    # Cleared explicitly after a successful commit. Walking-skeleton
    # step 9: covers the culler only. Curate / process / video / stack
    # journals are Phase 5.
    "last_screen": None,

    # Tool preferences per step ("auto" = best available, or tool key)
    "tool_preferences": {
        "focus_stack": "auto",
        "denoise": "builtin",
        "video_trim": "ffmpeg",
    },

    # Optional Helicon Focus integration. The Process Stacks tab
    # uses Helicon for focus brackets when both fields below are
    # set; otherwise it falls back to the embedded OpenCV engine.
    # Exposure brackets always use the embedded Mertens fusion
    # (Helicon doesn't do exposure stacking).
    "helicon_path": "",
    "prefer_helicon_for_focus": True,

    # Cached tool detection results
    "detected_tools": {},

    # Moment-cluster window for the bucket scanner, in seconds. The
    # scanner groups photos shot within this many seconds of each other
    # into the same moment cluster. Camera content (rapid bursts) wants
    # tight windows; phone content (whole scenes — sunset, dinner) wants
    # looser ones. Two separate values so the trip-tab imports don't
    # need to ask before each scan — set once in Settings and forget.
    "cluster_window_camera_seconds": 60.0,
    "cluster_window_phone_seconds": 300.0,

    # PlanEditorDialog persistence — geometry (base64-encoded
    # QByteArray from QWidget.saveGeometry) + first-3-column widths
    # in pixels. Description column always stretches to fill the
    # remainder. Empty geometry on first run = Qt default placement.
    "plan_editor_geometry": "",
    "plan_editor_column_widths": [110, 70, 160],

    # Focus-peaking colour name. Values: magenta (default), yellow,
    # red, cyan. Used by MediaCanvas when the host enables peaking.
    # Default-per-mode is policy of the host page (Mode A OFF, Mode B
    # ON per the design discussion); the *colour* is a global user
    # preference here.
    "peaking_color": "magenta",

    # Focus-peaking sensitivity (0-100) for the single-photo path.
    # Drives a content-adaptive percentile cut (NOT an absolute
    # threshold) per docs/18 §"Focus peaking". 50 = neutral default;
    # the culler's LabeledSlider edits this live. The stack-film
    # overlay ignores this — it stays on the fast legacy path.
    "peaking_sensitivity": 50,

    # Preferred action genre for BURST buckets — the bucket-level
    # style tie-breaker when a burst's EXIF is ambiguous (docs/18
    # §"Bucket cull surfaces" frozen tie-breaker table; the other
    # types have fixed defaults: Video→General, Focus→Macro,
    # Exposure→Landscape). A core.vocabulary.Scenario value, e.g.
    # "wildlife" / "sports" / "motorsport" / "aviation". The
    # first-run wizard owns this question (docs/04); until it ships,
    # this settings default stands in and the wizard later just
    # writes the same key — zero rework.
    "preferred_burst_genre": "wildlife",

    # User's preferred output aspect ratio — a label from
    # core.aspect_ratio.ASPECT_RATIOS ("Original", "4:3", "3:2",
    # "16:9", "1:1", "5:4"). Primary role: the default crop ratio
    # seeded for new events' Process phase (event_settings
    # ["default_aspect_ratio"]). Reused by the cull grid as the
    # tile-shape tie-break when a bucket has no dominant orientation.
    # "Original" = no imposed crop (the safe non-destructive default);
    # the grid then falls back to square only when no orientation
    # dominates. Unknown/empty → Original (core.aspect_ratio guards).
    "preferred_aspect_ratio": "Original",

    # User's two preferred photo genres — captured by the first-run
    # wizard (docs/04, Nelson 2026-05-21 freeze). Drive the Curate
    # phase's theme passes (each becomes a "Mark as <Genre>" entry
    # between Best and Short). Lower-case strings matching
    # core.vocabulary.Scenario values. Default is Nelson's pair
    # (macro / wildlife); other users pick their own via Settings
    # or the wizard. Unknown/missing → DEFAULT_CURATE_THEMES.
    "preferred_genres": ["macro", "wildlife"],

    # Slideshow per-tier seconds-per-slide (F-025, Nelson 2026-05-26;
    # defaults revised 2026-05-29 to the Curate tier design — docs/27
    # §6). This is the ONE slide-duration knob per tier: it drives the
    # Curate budget counters, the overview chart, the closed-EventCard
    # / EventPlanPage recap, AND the standalone slideshow viewer pace —
    # one number everywhere (each tier's slide count × its seconds-per-
    # slide → minutes). User-overridable via Settings.
    "slideshow_seconds_per_slide_short": 4.0,
    "slideshow_seconds_per_slide_medium": 6.0,
    "slideshow_seconds_per_slide_long": 6.0,

    # Slideshow per-tier MAX TIME budget in MINUTES (docs/27 §6, Nelson
    # 2026-05-29). The Curate time-remaining counter counts down toward
    # these as the user builds each tier: Short 3 min (highlight reel),
    # Medium 15 min, Long 30 min (45 min is the soft boredom ceiling,
    # not a setting). User-overridable via Settings.
    "slideshow_max_minutes_short": 3.0,
    "slideshow_max_minutes_medium": 15.0,
    "slideshow_max_minutes_long": 30.0,

    # Model 3 v2 — calibration mode (FROZEN 2026-05-22, Nelson).
    # Drives how the per-camera TZ offset is captured during ingest,
    # before the bake step writes it into 00-Captured EXIF. After
    # the bake, 00-Captured is contract-frozen (only the explicit
    # "Adjust event TZ" operation modifies it again — docs/14
    # §"Adjust event TZ").
    #
    # Values:
    # - "prompt" (default for new users; safest): the pair-picker /
    #   CameraClockDialog fires at every ingest, asking the user to
    #   calibrate each unknown camera against a reference (typically
    #   a phone with correct local time).
    # - "saved": look up the per-camera offset from
    #   ``saved_camera_offsets`` below; prompt only for cameras the
    #   user has never calibrated. Power-user setting after the first
    #   trip seeds the saved offsets.
    # - "reference_photo": at every ingest, minimal UI asks the user
    #   to point at one reference photo (with correct EXIF) per
    #   camera. No pair-picker. Lighter than "prompt" for users who
    #   carry a reliably-clocked phone.
    "calibration_mode": "prompt",

    # Saved per-camera offsets (UTC offset in hours, e.g. -3.0 for a
    # camera kept on São-Paulo wall-clock). Populated each time the
    # user successfully calibrates a camera during ingest — the
    # confirmation dialog offers "Remember this for next time" which
    # writes the camera_id → offset_hours pair here. Read by
    # ``calibration_mode == "saved"`` to skip the prompt step for
    # known cameras. Shape: ``{camera_id: offset_hours_float}``.
    # Example: {"DC-G9M2": -3.0, "iPhone Aida": 0.0}.
    "saved_camera_offsets": {},

    # Default pre-cull mode for new ingests (Model 3 v2 docs/18 v2,
    # Nelson 2026-05-22). Per-ingest user can override via the
    # offload dialog's "Change for this ingest" expander. The Mode B
    # consequence-disclosure ("discards are permanent") fires
    # regardless of default — the user must consciously elect Mode
    # B per ingest.
    #
    # Values:
    # - "verbatim" (default, safest): copy every photo from source to
    #   00-Captured; cull decisions happen later in the Cull phase
    #   where they're reversible
    # - "pre_cull": user reviews each photo during the copy; obvious
    #   garbage is discarded-now and never lands in 00-Captured.
    #   Saves disk on burst-heavy trips; discards are permanent.
    "default_pre_cull_mode": "verbatim",

    # Per-phase default state for items the user hasn't explicitly
    # K/D'd (Nelson 2026-05-28, docs/24 follow-up). Out-of-box
    # values: Cull = Discard, Select = Discard, Process = Keep.
    # Capture's default is implicit ("kept" at ingest) — not exposed
    # as a setting.
    #
    # The defaults accommodate different curation styles:
    #
    # - **Type-1 (rigorous at Cull)**: the user does careful per-item
    #   K/D at Cull, then expects what survives Cull to flow forward
    #   through Select. Setting ``pick_default_state = "kept"`` lets
    #   them skip per-item curation at Select — anything Cull kept
    #   shows up in Process by default. They still demote items
    #   explicitly if needed.
    #
    # - **Type-2 (permissive at Cull)**: the user keeps more at Cull
    #   to compare across sources at Select. Out-of-box defaults
    #   ("discarded" at Select) force them to explicitly mark Keep
    #   for anything to reach Process — the "system never infers
    #   done" principle in tightest form.
    #
    # Effective state at the end of a phase = explicit decision >
    # batch op > phase default. The filtering invariant between
    # phases operates on effective state: Process only sees items
    # whose effective Select-state is KEPT.
    #
    # Wire values match :data:`core.cull_state.STATE_*`.
    "cull_default_state": "discarded",
    "pick_default_state": "discarded",
    "process_default_state": "kept",

    # Master toggle for the style/genre classification system (Nelson
    # 2026-05-28). When True (default), the classifier runs, the F-020
    # nudge fires for kept-with-uncertain-classification photos at
    # Select bucket-exit, and the needs_review flag flows through the
    # UI as usual. When False, the user is signalling "I don't care
    # about per-style classification — leave me alone about it":
    #
    #   - F-020 classification-nudge dialog is suppressed.
    #   - (Future, F-037 scope) Reclassify button hidden; per-photo
    #     genre dropdown hidden; Curate routes everything to a single
    #     bucket per type instead of per-style folders; wizard skips
    #     the genre-block questions.
    #
    # The classifier still runs in the background so the data is
    # there if the user ever flips the setting back on — but nothing
    # in the UI calls attention to it. Out-of-box True preserves
    # current behavior; the wizard's meta-question (F-037) will set
    # this based on the user's answer.
    "classification_relevant": True,

    # Logging level: DEBUG / INFO / WARNING / ERROR / CRITICAL
    # Increase verbosity (DEBUG) when diagnosing problems; default is INFO.
    "log_level": "INFO",
}


def user_data_dir() -> Path:
    """Base directory for all Mira user data.

    Resolution order:
      1. MIRA_DATA_DIR env var (testing, custom deployments)
      2. Windows: %LOCALAPPDATA%\\Mira
      3. Other OS: ~/.mira

    This is the single source of truth for where all user-writable data lives:
    settings, lens registry, brand/body profile overrides, logs, cached
    detection results, and per-event data files.
    """
    override = os.environ.get("MIRA_DATA_DIR")
    if override:
        base = Path(override)
    elif platform.system() == "Windows":
        base = Path.home() / "AppData" / "Local" / "Mira"
    else:
        base = Path.home() / ".mira"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _settings_dir() -> Path:
    """Legacy alias for user_data_dir — kept for v1.x compatibility."""
    return user_data_dir()


def settings_path() -> Path:
    return user_data_dir() / "settings.json"


def _attempt_repair(text: str) -> dict | None:
    """Try to recover from common hand-edit JSON mistakes.

    Repairs applied (most-common-first):
      1. Trailing commas before ``}`` or ``]`` (the #1 mistake when
         a user removes a key from a JSON object by hand).

    Returns the parsed dict on success, ``None`` on failure. Never
    raises — the caller falls back to defaults if this returns ``None``.
    """
    # Trailing-comma strip: `{..., "x": 1,}` → `{..., "x": 1}`
    repaired = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        result = json.loads(repaired)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def load_settings() -> dict:
    """Load settings.json, merging into a fresh copy of DEFAULT_SETTINGS.

    Resilience policy (per Nelson's spec 2026-05-13): the app must
    never crash on launch because of a bad settings file. Any failure
    to load (OSError, JSONDecodeError, surprising structure, anything
    else) results in:

      1. The bad file is preserved as ``settings.json.bak`` for
         forensics — no information lost.
      2. ``DEFAULT_SETTINGS`` is written to ``settings.json`` so the
         next launch starts clean and the user's first edit through
         the Settings dialog persists normally.
      3. A warning is logged so the user notices the recovery.

    For a single recoverable JSON error (the most common case — a
    trailing comma after a hand edit) we still try the repair pass
    first; on success the salvaged keys merge with defaults and the
    file gets rewritten in its normalised form. Only when repair
    fails does the back-up-and-reseed path fire.

    Missing file → write defaults + return defaults (so first launch
    leaves a real settings.json on disk).
    """
    path = settings_path()
    result = dict(DEFAULT_SETTINGS)

    if not path.exists():
        try:
            save_settings(result)
            log.info("Created settings.json with hardcoded defaults at %s", path)
        except OSError as exc:
            log.warning(
                "Could not seed settings.json at %s (%s); running in-memory only",
                path, exc,
            )
        return result

    try:
        return _try_load(path, result)
    except Exception as exc:   # noqa: BLE001 — last-resort safety net
        log.warning(
            "settings.json failed to load (%s); falling back to defaults",
            exc,
        )
        _backup_and_reseed(path, result)
        return result


def _try_load(path: Path, base: dict) -> dict:
    """Inner load path. Raises on irrecoverable corruption; the
    outer ``load_settings`` catches and falls back."""
    result = dict(base)
    text = path.read_text(encoding="utf-8")

    saved: dict | None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("settings.json malformed (%s); attempting repair", exc)
        saved = _attempt_repair(text)
        if saved is None:
            # Repair didn't help — let the outer except handle the
            # back-up-and-reseed flow.
            raise
        log.info(
            "settings.json repaired successfully (recovered %d key(s))",
            len(saved),
        )
    else:
        saved = parsed if isinstance(parsed, dict) else None
        if saved is None:
            raise ValueError(
                "settings.json top-level value is not a JSON object"
            )

    # Deep merge for nested dicts; flat overwrite for everything else.
    for key, value in saved.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)
        else:
            result[key] = value
    return result


def _backup_and_reseed(path: Path, defaults: dict) -> None:
    """Move the bad settings file aside and write fresh defaults.

    Nothing here is allowed to raise — this is the last line of
    defence on app startup. Each step is wrapped so a failure in the
    backup step doesn't prevent the reseed step.
    """
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        # Replace any prior .bak so a chronic corruption doesn't
        # accumulate forever; the most recent bad file is the one
        # worth keeping for forensics.
        if backup.exists():
            backup.unlink()
        path.rename(backup)
        log.warning("Bad settings.json backed up to %s", backup)
    except OSError as exc:
        log.warning("Could not back up bad settings file: %s", exc)
    try:
        save_settings(defaults)
        log.info("Wrote fresh settings.json with hardcoded defaults")
    except OSError as exc:
        log.warning(
            "Could not reseed settings.json with defaults: %s — running "
            "in-memory only", exc,
        )


def save_settings(settings: dict) -> None:
    path = settings_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def update_setting(key: str, value) -> None:
    """Persist a single setting by key. Loads, updates, saves.

    Use this from UI code after the user picks a value you want to
    remember for next time. Example:

        chosen = QFileDialog.getExistingDirectory(...)
        if chosen:
            update_setting("photos_base_path", chosen)

    This is the canonical pattern for "remember the user's last choice".
    Every path, dropdown selection, or preference the user sets should
    be persisted this way so the next launch pre-fills with the last value.
    """
    settings = load_settings()
    settings[key] = value
    save_settings(settings)


def reset_settings_to_defaults() -> dict:
    """Overwrite the user's settings file with the in-memory DEFAULT_SETTINGS.

    Useful for the "Reset to Defaults" UI action when the user wants to
    start over after a misconfiguration. Returns the resulting settings dict
    so the caller can refresh any in-memory state immediately.

    Note: this resets ONLY the global app settings (settings.json). It does
    NOT touch the lens registry, brand/body profile overrides, events, or
    onboarding state — those are user data, not configuration.
    """
    defaults = dict(DEFAULT_SETTINGS)
    # Deep-copy nested dicts so callers can't mutate DEFAULT_SETTINGS by accident
    for key, value in DEFAULT_SETTINGS.items():
        if isinstance(value, dict):
            defaults[key] = dict(value)
    save_settings(defaults)
    return defaults
