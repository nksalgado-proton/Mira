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
SETTINGS_SCHEMA_VERSION = 1

# Ordered list of (from_version, migrate_fn(dict) -> dict). Empty on a fresh
# start; each entry bumps a loaded dict from version N to N+1.
MIGRATIONS: List = []


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
    default_ssd_path: str = _u("Default ingest destination root.", "")
    audio_library_path: str = _u("Folder scanned for slideshow soundtracks.", "")
    print_export_path: str = _u("Destination for the Share-browse Print action.", "")
    helicon_path: str = _u("Optional Helicon Focus executable for focus stacks.", "")
    prefer_helicon_for_focus: bool = _u(
        "Use Helicon for focus brackets when configured (else embedded OpenCV).", True)
    backup_on_quit_enabled: bool = _u("Mirror the last-touched event on quit.", False)
    backup_on_quit_root: str = _u("Destination root for backup-on-quit.", "")
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
    events_grid_tile_size: int = _u(
        "Events grid tile width (px). The slider in the events toolbar "
        "writes here on every change so the choice survives a restart; "
        "header text size stays constant — only the 4:3 area + donuts "
        "scale with this value (spec/77 §10.5).",
        248)
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
