"""The one application Settings class (spec/04 §5–6).

Every customizable default the app uses, in one typed place — no magic numbers
scattered through code (charter §5.7). One field per default; each field carries
``metadata`` declaring its **tier** (``"user"`` = tabbed Settings dialog;
``"app"`` = app-managed but hand-editable) and a one-line ``help`` string. The
future Settings dialog introspects both via :func:`dataclasses.fields`.

No Qt. Pure data. ``to_dict`` / ``from_dict`` are tolerant — unknown keys ignored,
missing keys take their default — so a hand-edited or older file always loads.

Boundary: these are app-wide *defaults*. An event may override some (phase
default-state, aspect ratio, calibration mode); those overrides live in the event
store (``event.db``), never here (spec/04 §1).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional

# We own this format now, so we own its migrations (DQ4 resolved, spec/04 §2).
SETTINGS_SCHEMA_VERSION = 2

# Ordered list of (from_version, migrate_fn(dict) -> dict). Each entry bumps a
# loaded dict from version N to N+1.


def _v1_to_v2(data: Dict[str, Any]) -> Dict[str, Any]:
    """spec/82 Part G — fold the legacy backup-destination keys into
    the new unified ``event_backup_destination``.

    ``default_ssd_path`` was meant for "default external backup
    destination" but never plugged into a real feature; spec/82 makes
    it the destination for the new **Back up event…** action and the
    automatic backup-on-quit (both Part-B bundle exports).
    ``backup_on_quit_root`` had the same intent; it folds in too so
    the user has ONE home for "where bundle exports land". Whichever
    legacy key has a non-empty value wins; both are dropped after
    the move so they can't drift apart.
    """
    out = dict(data)
    legacy = (
        (out.get("event_backup_destination") or "")
        or (out.get("default_ssd_path") or "")
        or (out.get("backup_on_quit_root") or "")
    )
    if legacy:
        out["event_backup_destination"] = legacy
    out.pop("default_ssd_path", None)
    out.pop("backup_on_quit_root", None)
    return out


MIGRATIONS: List = [(1, _v1_to_v2)]


def _u(help: str, default: Any = None, **kw):
    """A user-tier field (surfaced in the Settings dialog)."""
    md = {"tier": "user", "help": help}
    return field(default=default, metadata=md, **kw) if "default_factory" not in kw \
        else field(metadata=md, **kw)


def _a(help: str, default: Any = None, **kw):
    """An app-managed field (hand-editable in the JSON, not in the dialog)."""
    md = {"tier": "app", "help": help}
    return field(default=default, metadata=md, **kw) if "default_factory" not in kw \
        else field(metadata=md, **kw)


def _system_tz_hours() -> float:
    """System UTC offset in hours, e.g. -3.0 for São Paulo. Matches the legacy
    ``home_timezone`` default derivation."""
    return round(-time.timezone / 3600, 1)


@dataclass
class Settings:
    # ── Paths (user) ──────────────────────────────────────────────────────
    photos_base_path: str = _u("Root folder where events are stored.", "")
    exiftool_path: str = _u("Override path to the ExifTool binary.", "")
    audio_library_path: str = _u("Folder scanned for slideshow soundtracks.", "")
    print_export_path: str = _u("Destination for the Share-browse Print action.", "")
    helicon_path: str = _u("Optional Helicon Focus executable for focus stacks.", "")
    prefer_helicon_for_focus: bool = _u(
        "Use Helicon for focus brackets when configured (else embedded OpenCV).", True)
    # ── Backups (user) — spec/82 ─────────────────────────────────────────
    # Live on the new Backups tab. The slice-1 retention split, the
    # slice-3 periodic cadence, the slice-7 Back up event… default
    # destination, and the slice-8 automatic backup-on-quit all read
    # from here. ``default_ssd_path`` + ``backup_on_quit_root`` migrated
    # into ``event_backup_destination`` (see ``_v1_to_v2``).
    backup_snapshots_enabled: bool = _u(
        "Master toggle for automatic DB safety snapshots (spec/82 §A). "
        "Off disables both milestone and periodic snapshots.", True)
    backup_periodic_minutes: int = _u(
        "Periodic-while-open cadence in minutes (spec/82 §A.1). "
        "0 = off — milestone snapshots still fire.", 15)
    backup_keep_milestone: int = _u(
        "Retention for milestone snapshots (close-if-dirty, pre-risky-"
        "op, per-day-add, manual). spec/82 §A.2.", 10)
    backup_keep_periodic: int = _u(
        "Retention for periodic snapshots (the N-minute timer). "
        "spec/82 §A.2.", 3)
    backup_snapshots_root: str = _u(
        "Override for the safety snapshots directory. Blank = "
        "<library_root>/.mira-backups (spec/79 default). Set to a "
        "different drive for true offsite of the DB.", "")
    event_backup_destination: str = _u(
        "Default destination for the Back up event… action (Part-B "
        "bundle export). Pre-fills the file dialog; the user can "
        "still confirm a different folder each time.", "")
    event_backup_verify: bool = _u(
        "Re-hash the exported bundle against its manifest after copy "
        "(Part-B step 5). On by default; can be turned off to skip "
        "the verify pass on very large events.", True)
    backup_on_quit_enabled: bool = _u(
        "Automatically export the active event as a Part-B bundle to "
        "event_backup_destination on quit. Both ship (automatic + "
        "the manual Back up event… action).", False)
    home_timezone: float = _u(
        "Home UTC offset in hours (e.g. -3.0 for São Paulo).",
        default_factory=_system_tz_hours)
    home_country: str = _u(
        "Home country (ISO 3166-1 alpha-2, e.g. 'BR' for Brazil). "
        "Used as a fallback when scanned photos have no phone GPS to "
        "reverse-geocode a country from (Nelson 2026-06-08). Empty = "
        "no fallback (scan leaves country blank, user fills per day).",
        "")

    # ── UI (user) ─────────────────────────────────────────────────────────
    theme: str = _u("UI theme: light or dark.", "dark")
    language: str = _u("UI language, ISO 639-1 (v1 ships en + pt).", "en")
    font_scale: float = _u("Global font scale multiplier.", 1.0)

    # ── Share / Cuts (user) — spec/61 ─────────────────────────────────────
    use_separators: bool = _u(
        "Generate day-separator slides in Cuts (shown in the grid, played "
        "in the rehearsal, exported with the handoff).", True)
    separator_aspect: str = _u(
        "Separator slide aspect ratio — matches the screen your shows "
        "play on (e.g. 16:9).", "16:9")
    # spec/76 §B.3 — Cut publish target. Where a TV media server
    # (Jellyfin / DLNA / a smart TV's folder slideshow) reads from.
    # Empty defaults to <library_root>/Published/ so the publish slot
    # rides with the library when it relocates. Override to point at
    # any other folder (e.g. a share dedicated to TV content).
    library_publish_root: str = _u(
        "Where published Cuts land for the TV media server. Empty = "
        "<library_root>/Published/. Set to a different folder when "
        "the media server reads from a fixed location.", "")
    # spec/105 §1 — Cut export root override. Blank = the volume-aware
    # default (`<library_root>/Cuts/<event>/<cut>/` for same-volume
    # events, `<event_root>/Cuts/<cut>/` for off-volume / external
    # events, `<library_root>/Cuts/Cross-event/<cut>/` for cross-event
    # Cuts) so hardlinks keep working wherever an event physically
    # lives. Set this to override and always write Cuts under one
    # folder — the dialog warns when that folder is off-volume from
    # the event's media (links → copies).
    cuts_export_root: str = _u(
        "Root for exported Cuts, as <root>/<event>/<cut>. Blank = a "
        "Cuts/ folder on the same volume as each event (keeps "
        "hardlinks).", "")
    # spec/148 — Overwrite vs Keep-both is offered on every Cut export.
    # The dialog defaults to the user's last choice so a power user who
    # always overwrites doesn't keep flipping the radio. False = Keep
    # both (today's folder-disambiguation behaviour); True = Overwrite
    # (write into <tag>/, clearing the prior bundle). App-tier — it is
    # a sticky UI preference, not a value worth surfacing in the
    # Settings dialog.
    cut_export_overwrite_default: bool = _a(
        "Last choice the user made for the Cut export Overwrite vs "
        "Keep-both option (spec/148). True = Overwrite, False = Keep "
        "both. The dialog pre-selects this value on the next export.",
        False)

    # spec/107 — PTE AV Studio integration. ``use_pte`` is the master
    # toggle gating ALL PTE UI (off by default — non-PTE users never
    # see it). ``pte_path`` is the executable; resolved at launch time
    # so a relocated install of PTE just needs the path corrected.
    # When both are set, exporting a Cut writes ``slideshow.pte`` into
    # the export folder (spec/107 §3) and the export-complete summary
    # offers an "Open in PTE" button.
    use_pte: bool = _u(
        "Master switch for the PTE AV Studio integration. Off (default) "
        "hides every PTE-specific control; on enables the .pte "
        "generator on Cut export and the Open-in-PTE launch action.",
        False)
    pte_path: str = _u(
        "Path to the PTE AV Studio executable (e.g. PicturesToExe.exe). "
        "Required for the Open-in-PTE action.", "")

    # ── Tooling (user) ────────────────────────────────────────────────────
    tool_preferences: Dict[str, str] = _u(
        "Preferred external tool per processing step.",
        default_factory=lambda: {
            "focus_stack": "auto", "denoise": "builtin", "video_trim": "ffmpeg"})

    # ── Scanner (user) ────────────────────────────────────────────────────
    cluster_window_camera_seconds: float = _u(
        "Moment-cluster window for camera content, seconds.", 60.0)
    cluster_window_phone_seconds: float = _u(
        "Moment-cluster window for phone content, seconds.", 300.0)

    # ── Focus peaking (user) ──────────────────────────────────────────────
    peaking_color: str = _u("Focus-peaking overlay colour.", "magenta")
    peaking_sensitivity: int = _u("Focus-peaking sensitivity, 0–100.", 50)

    # ── Classification / genres (user) ────────────────────────────────────
    preferred_burst_genre: str = _u(
        "Tie-breaker genre for ambiguous BURST buckets (wizard owns later).", "wildlife")
    preferred_aspect_ratio: str = _u(
        "Default crop ratio seeded for new events' Edit phase.", "Original")
    preferred_genres: List[str] = _u(
        "The user's two preferred photo genres (Share theme passes; wizard owns later).",
        default_factory=lambda: ["macro", "wildlife"])
    classification_relevant: bool = _u(
        "Master toggle for the style/genre classification system.", True)

    # ── Slideshow tiers (user) ────────────────────────────────────────────
    slideshow_seconds_per_slide_short: float = _u("Short-tier seconds per slide.", 4.0)
    slideshow_seconds_per_slide_medium: float = _u("Medium-tier seconds per slide.", 6.0)
    slideshow_seconds_per_slide_long: float = _u("Long-tier seconds per slide.", 6.0)
    slideshow_max_minutes_short: float = _u("Short-tier max-time budget, minutes.", 3.0)
    slideshow_max_minutes_medium: float = _u("Medium-tier max-time budget, minutes.", 15.0)
    slideshow_max_minutes_long: float = _u("Long-tier max-time budget, minutes.", 30.0)

    # ── Ingest / calibration (user) ───────────────────────────────────────
    calibration_mode: str = _u(
        "Per-camera TZ capture mode: prompt / saved / reference_photo.", "prompt")
    default_quick_sweep_mode: str = _u(
        "Default ingest mode: verbatim (copy all) or quick_sweep.", "verbatim")

    # ── Per-phase default state (user) ────────────────────────────────────
    pick_default_state: str = _u("Default state for un-decided items at Select.", "skipped")
    edit_default_state: str = _u("Default state for un-decided items at Edit.", "picked")
    # spec/52 Quick Sweep redesign (Nelson 2026-06-09). Distinct from
    # ``pick_default_state``: Quick Sweep runs at capture time on raw
    # source items (before any ingest), with a deliberately permissive
    # default so users keep everything and just yank the obvious garbage.
    # Defaults to 'picked'; a user who triages aggressively at capture
    # can flip to 'skipped' for a stricter "actively pick keepers" flow.
    quick_sweep_default_state: str = _u(
        "Default state for un-decided items at Quick Sweep.", "picked")

    # ── Audit-promoted thresholds (user, Nelson 2026-06-09) ───────────────
    # Previously hardcoded constants surfaced so users can adjust the
    # defaults when our anticipated values don't fit their workflow.
    repeat_window_seconds: float = _u(
        "Repeat-cluster window — photos within this total span (first to "
        "last) cluster together. Phone-only; spec/52.",
        2.0)
    peek_target_photos: int = _u(
        "Browse-peek: how many photos to sample per day.",
        20)
    jpeg_export_quality: int = _u(
        "JPEG quality (0–100) used when Edit exports a processed photo.",
        95)
    # spec/59 §8 — the Exported watermark (diagonal text over photos
    # that already have an exported/associated version; lineage-driven,
    # system-set). The ONLY control is this app-wide hide switch.
    show_exported_watermark: bool = _u(
        "Show the diagonal 'Exported' watermark over photos that "
        "already have an exported version.", True)
    # spec/96 §2 — the single-view exposure pill (camera · shutter ·
    # aperture · ISO · focal · type · size). Viewing preference, NOT
    # hardware-bound, so it lives in the roaming Settings (contrast
    # spec/95's machine-local display_quality). Default ON preserves
    # today's Picker / Quick Sweep behaviour. spec/134 retired this
    # as the Picker / Editor gate (those now read viewer_overlay_fields);
    # Quick Sweep continues to honour it for backwards-compat.
    show_exposure_overlay: bool = _u(
        "Show the exposure pill (camera · shutter · aperture · ISO · "
        "focal length · file type · size) over photos in Quick Sweep "
        "single views. (Picker / Editor use viewer_overlay_fields.)",
        True)
    # spec/134 — configurable photo-viewer overlay. Reuses the cut
    # overlay vocabulary (core.cut_overlay.OVERLAY_FIELDS): When /
    # Where / Camera (how1) / Exposure (how2). The Picker + Editor
    # photo views compose their pill from the selected fields via
    # ``compose_overlay_lines``. Default ``["how2"]`` (exposure only)
    # so today's behaviour is unchanged until the user opts in;
    # ``[]`` hides the overlay.
    viewer_overlay_fields: List[str] = _u(
        "Fields to show on the Picker / Editor photo overlay. Pick any "
        "subset of When / Where / Camera / Exposure (the cut-overlay "
        "vocabulary). Empty = overlay off.",
        default_factory=lambda: ["how2"])
    # spec/134 — text size (px) of the on-photo provenance/exposure pill,
    # shared by the Picker/Editor single-view overlay and the Cut-play
    # overlay. Substituted into the QSS roles GridTileExif / CutPlayOverlay
    # at theme-apply time; changing it re-applies the theme live. Default
    # 9 reads small on a 1080p panel and scales up with the user's display
    # DPI — bump it here if the pill reads too large/small.
    overlay_exif_font_px: int = _u(
        "Text size (in pixels) of the exposure / provenance pill drawn "
        "over photos (Picker / Editor / Cut play single views, the Quick "
        "Sweep pill and the compare-grid tiles). Smaller = less "
        "obtrusive. Default 9.", 9)
    # spec/136 — startup splash sourced from a random exported photo of
    # a random closed event (or the bundled mark when none / opt-out).
    # Off → the bundled mark splash; the splash itself still covers the
    # ~2 s MainWindow construction window so the boot flicker stays
    # masked even when the user opts out of the photo source.
    startup_photo_splash: bool = _u(
        "Show a recent photo on startup (a random exported frame from "
        "a random closed event). Off → the bundled Mira mark.",
        True)
    # spec/138 §2B + §2D — global default video playback speed. The
    # speed is sticky for the session (carry-over across clips wanted),
    # initialised from this default on each fresh viewport; the user
    # can still pick a different rate live from the transport bar
    # combo. Allowed values match the combo: 0.25 / 0.5 / 1 / 1.5 / 2.
    default_video_speed: float = _u(
        "Default video playback speed (×). Applies to fresh sessions; "
        "the transport bar combo still lets you change it live.", 1.0)
    # spec/152 §4 — global default crossfade duration (ms) between
    # consecutive Cut slides. Counted in cut_budget.seconds() so the
    # show total / audio playlist / PTE [Times] all agree on wall
    # time. ``0`` = hard cuts (legacy behaviour). Matches the existing
    # ``pte_project.DEFAULT_TRANSITION_MS``.
    default_transition_ms: int = _u(
        "Default crossfade transition between Cut slides, in ms. "
        "Counted in the show length and the audio playlist; PTE picks "
        "up the same value. 0 = no transition.", 2000)
    # ── Tone calibration trims (spec/54 §4.1 + spec/55, Nelson
    # 2026-06-10). Field-calibration knobs: -100..100, 0 = the shipped
    # recipe exactly. The Edit surface stays zero-slider (its thesis);
    # these live in Settings — the tinkerer's drawer — so daily usage
    # tunes the Looks/filters in vivo, and the settled positions get
    # harvested as new shipped defaults after a month or two.
    # Looks: natural trims the FITTED CORRECTION's strength; brighter /
    # deeper trim THEIR BIAS only (spec/54 §4.1 semantics). Filters:
    # blend toward (-100 = off) or past (+100 = double) the recipe.
    look_scale_natural: int = _u(
        "Natural correction strength trim (-100 = none, 0 = calibrated, "
        "+100 = double).", 0)
    look_scale_brighter: int = _u(
        "Brighter look bias trim (-100 = same as Natural, 0 = shipped, "
        "+100 = double).", 0)
    look_scale_deeper: int = _u(
        "Deeper look bias trim (-100 = same as Natural, 0 = shipped, "
        "+100 = double).", 0)
    filter_scale_vivid: int = _u("Vivid filter strength trim.", 0)
    filter_scale_bw: int = _u("B&W filter strength trim.", 0)
    filter_scale_sepia: int = _u("Sepia filter strength trim.", 0)
    filter_scale_faded: int = _u("Faded filter strength trim.", 0)
    filter_scale_golden: int = _u("Golden filter strength trim.", 0)
    filter_scale_cinema: int = _u("Cinema filter strength trim.", 0)
    filter_scale_bleach: int = _u("Bleach filter strength trim.", 0)
    filter_scale_dramatic: int = _u("Dramatic filter strength trim.", 0)
    filter_scale_crisp: int = _u("Crisp filter strength trim.", 0)
    video_clip_crf: int = _u(
        "ffmpeg CRF for clip / motion exports (lower = higher quality).",
        20)
    focus_peaking_opacity: float = _u(
        "Focus-peaking overlay opacity (0.0 transparent — 1.0 opaque).",
        0.7)
    default_day_grid_cell_size: int = _u(
        "Day-grid default cell size (px). Slider on the day grid still "
        "lets you scrub per-session; this is the value the grid opens at.",
        140)
    log_rotate_keep_days: int = _u(
        "How many days of rotated log files to keep.",
        14)

    # ── Diagnostics (user) ────────────────────────────────────────────────
    log_level: str = _u("Logging verbosity: DEBUG / INFO / WARNING / ERROR / CRITICAL.", "INFO")

    # ── Dashboard / filter rail (app-managed; persists across launches) ────
    events_dashboard_sort: str = _u(
        "Events dashboard sort: newest / oldest / name / type.", "newest")

    # ── App-managed (hand-editable, not in the dialog) ────────────────────
    window_geometry: str = _a("Main-window geometry (base64 QByteArray).", "")
    window_state: str = _a("Main-window state (base64 QByteArray).", "")
    last_event_id: str = _a("Last-opened event id (resume hint).", "")
    last_screen: Optional[Dict[str, Any]] = _a("Resume target (page/event/day/bucket).", None)
    detected_tools: Dict[str, Any] = _a(
        "Cached external-tool detection results.", default_factory=dict)
    plan_editor_geometry: str = _a("PlanEditorDialog geometry (base64).", "")
    plan_editor_column_widths: List[int] = _a(
        "PlanEditorDialog first-3-column widths, px.",
        default_factory=lambda: [110, 70, 160])
    saved_camera_offsets: Dict[str, float] = _a(
        "Remembered per-camera TZ offsets in hours, {camera_id: offset}.",
        default_factory=dict)
    saved_camera_tz: Dict[str, float] = _a(
        "Remembered per-camera clock TZ in hours, {camera_id: tz} — the F-019 pre-ingest "
        "dialog pre-fills its picker from this on the next ingest of the same camera.",
        default_factory=dict)

    # ── (de)serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Plain dict of every field (no schema_version — the repo stamps that)."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        """Tolerant build: unknown keys ignored, missing keys take their default."""
        known = {f.name for f in fields(cls)}
        kept = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**kept)


def user_keys() -> List[str]:
    """Field names in the ``user`` tier (the Settings dialog set)."""
    return [f.name for f in fields(Settings) if f.metadata.get("tier") == "user"]


def app_keys() -> List[str]:
    """Field names in the ``app`` tier (app-managed, hand-editable)."""
    return [f.name for f in fields(Settings) if f.metadata.get("tier") == "app"]
