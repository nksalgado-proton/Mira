# spec/04 — App settings (Domain 5)

**Build-sequence: the foundation before the UI rebuild (charter §5.7).** One
application **Settings** class holding *every* customizable default the app uses — no
magic numbers scattered through code. Persisted as JSON in the user-data area under the
**one protection contract** (spec/02 §1). Built **NOW** so every new module reads its
defaults from one place from day one.

This spec drives `mira/settings/` (the class + the JSON repo) and the two shared
foundations every JSON-domain repo needs: `mira/paths.py` (user-data location) and
`mira/protect.py` (the §1 contract, ported clean from the one good part of the
legacy persistence so `core/` can be archived without breaking the new app).

---

## 1. Principles (from the charter + spec/02)

- **Single class, all tiers.** Every default lives in the `Settings` class regardless of
  whether a user can edit it. The class is the union of every tunable in the app.
- **Two customizability tiers** (a per-field attribute, not two classes):
  - **`user`** — surfaced in the tabbed Settings dialog (built downstream at UI reassembly).
  - **`app`** — the app writes these (last-window-geometry, last-event, detected tools,
    remembered camera offsets); not in the dialog, but **hand-editable in the JSON**.
- **Defaults ≠ per-event overrides.** Settings hold app-wide *defaults*. An event may
  *override* some of these (e.g. phase default-state, aspect ratio, calibration mode);
  those overrides live in the **event store** (`event.db`), never here. Keep the boundary
  clean: a settings key is the seed a new event copies, not the live per-event value.
- **Typed access only.** No client reads/writes the JSON bytes directly; everything goes
  through `SettingsRepo` → a `Settings` instance. (spec/02 §1.)
- **Tolerant load, never crash on boot.** Missing file → defaults (and seed the file).
  Corrupt/unreadable file → defaults (and preserve the bad file for forensics). Unknown
  keys ignored; missing keys take their default. This carries the legacy resilience
  policy forward — the app must never fail to launch because of a bad settings file.

## 2. DQ4 resolved — settings versioning

**The settings JSON carries a top-level `schema_version` (int).** `mira/settings/`
owns `SETTINGS_SCHEMA_VERSION` and an ordered `MIGRATIONS` list, mirroring
`mira/store/schema.py`. We own this format now, so we own its migrations — unlike
the legacy schema-less `settings.json` that silently accreted keys.

- Fresh start: `SETTINGS_SCHEMA_VERSION = 1`, `MIGRATIONS = []` (empty).
- A loaded file with `schema_version < current` runs each migration in order; a file with
  **no** `schema_version` is treated as version 1 (the legacy flat shape is *not*
  migrated — the new app starts fresh per charter §3 "v1 starts fresh, no migration from
  PhotosWorkflow"; we do not read the old `settings.json`).
- A file with `schema_version > current` (downgrade) → load tolerantly on a best-effort
  basis and log a warning (forward-compat, do not crash).

## 3. The protection contract for this domain (spec/02 §1, ported)

`mira/protect.py` provides the shared JSON-domain helper (the same three layers the
legacy `core/atomic_journal.py` proved, re-homed in the new namespace):

1. **History rotation** — before overwrite, copy the current file into
   `<parent>/.history/<stem>.<ISO-ts><suffix>`, pruned to the most recent N (default 20).
2. **Atomic write-then-rename** — write `<path>.tmp`, fsync, `os.replace` onto `<path>`.
3. **SHA-256 sidecar** — write `<path>.sha256` in `sha256sum` line format; `verify(path)`
   reports match / mismatch / missing. A missing sidecar is "can't tell", not corruption.

A sidecar **mismatch** on settings load is logged but **non-blocking** — settings are
recoverable (they are just defaults + user prefs), so we surface the warning and load the
bytes anyway rather than refusing to boot. (Contrast: event data, where a mismatch should
prompt a recovery dialog — that policy is the *caller's*, not `protect.py`'s.)

## 4. Physical layout (resolves part of DQ5)

- `mira/paths.py::user_data_dir()` — the single source of truth for user-writable
  locations in the **new** app (ported from legacy `core/settings.py`). Resolution:
  `MIRA_DATA_DIR` env (tests) → Windows `%LOCALAPPDATA%\Mira` → `~/.mira`.
- Settings file: `<user_data_dir>/settings.rebuild.json` (+ `.sha256` sidecar +
  `.history/`). **Coexistence rename (Nelson 2026-05-30):** the legacy app owns
  `settings.json` in the same `user_data_dir` and writes it in its old flat format.
  Sharing it meant whichever app ran last clobbered `photos_base_path` — the legacy points
  at the old library root (`D:\Photos`), the new app at the rebuild library
  (`D:\Photos\_mira`) — which silently broke event resolution. So the new app keeps
  its **own** settings file (`settings.rebuild.json`) for the duration of coexistence; at
  the §4-step-8 cutover (legacy archived) it can reclaim `settings.json`. Future
  Domain-2/3/4 files that share a legacy name need the same treatment.
- This sits beside `events_index.json` (spec/03 §5), all under the one `user_data_dir`.

## 5. The default catalog (every key, with tier)

Enumerated from the legacy `core/settings.py::DEFAULT_SETTINGS` census, deduplicated and
tier-tagged. Names carried forward unchanged (zero churn for downstream callers); the
*home* moves from a loose dict to a typed class.

### 5.1 — `user` tier (tabbed Settings dialog)

| Key | Default | Notes |
|---|---|---|
| `photos_base_path` | `""` | **the single absolute anchor of the whole system (charter §5.9)** — user data, set during onboarding, never hardcoded; every other persisted path is stored relative to it |
| `exiftool_path` | `""` | bundled-binary override |
| `default_ssd_path` | `""` | default ingest destination root |
| `audio_library_path` | `""` | slideshow soundtrack scan root |
| `print_export_path` | `""` | Curate-browse Print target (F-003) |
| `helicon_path` | `""` | optional Helicon Focus integration |
| `prefer_helicon_for_focus` | `True` | else embedded OpenCV focus stack |
| `backup_on_quit_enabled` | `False` | incremental mirror on close |
| `backup_on_quit_root` | `""` | backup destination |
| `home_timezone` | system UTC-offset hrs | e.g. `-3.0` São Paulo |
| `theme` | `"dark"` | `light` / `dark` |
| `language` | `"en"` | ISO 639-1; v1 = en + pt |
| `font_scale` | `1.0` | |
| `tool_preferences` | `{focus_stack:"auto", denoise:"builtin", video_trim:"ffmpeg"}` | per-step tool pick |
| `cluster_window_camera_seconds` | `60.0` | moment-cluster window, camera |
| `cluster_window_phone_seconds` | `300.0` | moment-cluster window, phone |
| `peaking_color` | `"magenta"` | focus-peaking overlay colour |
| `peaking_sensitivity` | `50` | 0–100 percentile cut |
| `preferred_burst_genre` | `"wildlife"` | BURST tie-breaker (wizard owns later) |
| `preferred_aspect_ratio` | `"Original"` | new-event Process crop seed |
| `preferred_genres` | `["macro","wildlife"]` | Curate theme passes (wizard owns later) |
| `classification_relevant` | `True` | master toggle for the style system |
| `slideshow_seconds_per_slide_short` | `4.0` | one slide-duration knob per tier |
| `slideshow_seconds_per_slide_medium` | `6.0` | |
| `slideshow_seconds_per_slide_long` | `6.0` | |
| `slideshow_max_minutes_short` | `3.0` | per-tier max-time budget |
| `slideshow_max_minutes_medium` | `15.0` | |
| `slideshow_max_minutes_long` | `30.0` | |
| `calibration_mode` | `"prompt"` | `prompt` / `saved` / `reference_photo` |
| `default_pre_cull_mode` | `"verbatim"` | `verbatim` / `pre_cull` |
| `cull_default_state` | `"discarded"` | per-phase default for un-decided items |
| `select_default_state` | `"discarded"` | |
| `process_default_state` | `"kept"` | |
| `log_level` | `"INFO"` | DEBUG…CRITICAL |

### 5.2 — `app` tier (app-managed, hand-editable, not in the dialog)

| Key | Default | Notes |
|---|---|---|
| `window_geometry` | `""` | base64 QByteArray |
| `window_state` | `""` | base64 QByteArray |
| `last_event_id` | `""` | resume hint |
| `last_screen` | `None` | resume target dict (page/event/day/bucket) |
| `detected_tools` | `{}` | cached tool-detection results |
| `plan_editor_geometry` | `""` | PlanEditorDialog geometry |
| `plan_editor_column_widths` | `[110, 70, 160]` | PlanEditorDialog columns |
| `saved_camera_offsets` | `{}` | `{camera_id: offset_hours}`, populated on calibrate |

**Dropped on the move** (legacy keys that do not belong in the clean settings class):
none functional — every legacy key is carried. (The legacy `_settings_dir` alias and the
in-place regex JSON repair are *not* ported; `protect.py`'s sidecar + history + back-up-
and-reseed supersede the ad-hoc repair.)

## 6. The code shape

`mira/settings/`:

- **`model.py`** — the `Settings` dataclass: one field per §5 key, default + `metadata`
  carrying `tier` (`"user"`/`"app"`) and a one-line `help` string (the future dialog reads
  both via `dataclasses.fields`). Plus `SETTINGS_SCHEMA_VERSION`, `MIGRATIONS`,
  `to_dict()` / `from_dict()` (tolerant: unknown keys ignored, missing keys → default).
  No Qt. `user_keys()` / `app_keys()` helpers introspect the tier metadata.
- **`repo.py`** — `SettingsRepo`: `path` (defaults to `<user_data_dir>/settings.json`),
  `load() -> Settings` (missing → seed defaults; corrupt → back up + defaults; verify
  sidecar non-blocking; apply migrations), `save(Settings)` (via `protect.write_protected`),
  `update(**kwargs)` convenience (load-mutate-save one or more keys).
- **`__init__.py`** — exports `Settings`, `SettingsRepo`, `SETTINGS_SCHEMA_VERSION`.

`mira/paths.py` — `user_data_dir()` (ported). `mira/protect.py` —
`write_protected`, `verify`, `list_history`, `read_text_protected` (ported, trimmed).

## 7. Green test gate (`tests/test_settings.py`)

Logic-only, no Qt. Must pass before this step is "done":

1. **Defaults present** — a fresh `Settings()` has every §5 key with the listed default;
   `user_keys()` + `app_keys()` partition all fields with no overlap.
2. **Round-trip** — `from_dict(s.to_dict()) == s`; `save` then `load` returns an equal
   `Settings`; the on-disk JSON carries `schema_version == SETTINGS_SCHEMA_VERSION`.
3. **Missing file → defaults** (and the file is seeded on first load, sidecar written).
4. **Corrupt file → defaults** — malformed JSON loads defaults, preserves the bad bytes
   as `settings.json.bak`, does not raise.
5. **Tolerant merge** — an on-disk file with an unknown key + a missing key loads: unknown
   ignored, missing → default, known overrides applied.
6. **Protection contract** — after a save, the `.sha256` sidecar verifies; a second save
   rotates a copy into `.history/`; an atomic-rename leaves no `.tmp` behind.
7. **Update** — `repo.update(theme="light")` persists only that change, others untouched.

## 8. Carry-forward / open

- The tabbed **Settings dialog** UI is downstream (UI reassembly), reading `tier`/`help`
  off the field metadata. This spec gives it a typed, introspectable backing.
- Domains 2/3/4 (user knowledge · rules user-layer · tone corpus) reuse `paths.py` +
  `protect.py` when built; their repos follow this same load/save shape. DQ5's full layout
  resolves then.
- When the wizard lands, `preferred_burst_genre` / `preferred_genres` /
  `classification_relevant` are *written by the wizard* into this same store (same keys) —
  zero rework, the settings default just stands in until then (spec/02 §4).
