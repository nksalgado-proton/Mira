"""
EXIF extraction via bundled exiftool.
Reads all tags needed for mode classification in a single batch call.
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def _get_exiftool_path() -> Path:
    """Find exiftool.exe relative to this file.

    Search order:
      1. Project root / bin / exiftool.exe (dev + Nuitka onefile).
      2. cwd / bin / exiftool.exe (field packages, exe next to bin/).
      3. Walk up parent directories of this file looking for a
         sibling ``bin/exiftool.exe``. This lets a git-worktree under
         ``.claude/worktrees/<name>/`` find the main checkout's
         ``bin/`` directory — ``bin/`` is gitignored, so worktrees
         don't get a copy of their own.

    Returns the first hit; if none, returns the primary path anyway
    so callers get a clear "not found" error pointing at the expected
    location.
    """
    primary = Path(__file__).resolve().parent.parent / "bin" / "exiftool.exe"
    if primary.exists():
        return primary

    cwd_candidate = Path.cwd() / "bin" / "exiftool.exe"
    if cwd_candidate.exists():
        return cwd_candidate

    # Walk-up fallback for worktrees. Stop at the drive root.
    current = Path(__file__).resolve().parent
    for _ in range(8):   # safety cap; main checkout is typically 2-5 levels up
        parent = current.parent
        if parent == current:
            break
        candidate = parent / "bin" / "exiftool.exe"
        if candidate.exists():
            return candidate
        current = parent

    return primary

# Tags we extract for classification (exiftool short names)
TAGS = [
    'Make', 'DateTimeOriginal', 'Model',
    # Body serial numbers — used to distinguish two physically
    # distinct cameras of the same model (Nelson 2026-05-23: two
    # G9 MkIIs in the family, each potentially on a different TZ).
    # ``InternalSerialNumber`` (Panasonic/Sony/Canon/Nikon
    # MakerNotes) is the most reliable; ``BodySerialNumber`` is
    # newer-camera standard EXIF; ``SerialNumber`` is the
    # original-EXIF fallback. Phones (iPhone, most Android) write
    # none of these → same model → indistinguishable, by design.
    'InternalSerialNumber', 'BodySerialNumber', 'SerialNumber',
    'LensModel', 'LensType', 'LensID',  # lens info — read all three; brand profile picks in priority order
    'FocalLength',
    'FNumber', 'ExposureTime', 'ISO',
    'Flash', 'FocusMode', 'AFAreaMode', 'AFSubjectDetection',
    'ShootingMode', 'BurstMode', 'Bracketing',
    # Brand-agnostic exposure-mode fallback (EXIF 2.x standard).
    # ExposureProgram values: 0=undefined, 1=manual, 2=normal-program,
    # 3=aperture-priority, 4=shutter-priority, 5=creative,
    # 6=action/sports, 7=portrait, 8=landscape, 9=bulb. Used by
    # brand profiles whose MakerNotes don't carry an explicit
    # shooting-mode tag — Sony falls back to this; phones leave it
    # empty (correct — they have no mode dial).
    'ExposureProgram',
    # Photo style / creative intent — read all three; brand profile picks priority
    'PhotoStyle', 'CreativeLook', 'CreativeStyle', 'PictureProfile',
    'ImageStabilization', 'ShutterType',
    'ExtTeleConv', 'MacroMode', 'SilentMode', 'ColorEffect',
    'ExposureMode', 'WhiteBalance', 'Orientation',
    'FocusStepCount', 'FocusBracket', 'SequenceNumber',
    # Distance to focused subject — used by macro disambiguation (close
    # focus + macro lens → macro even in AF mode).
    'FocusDistance',
    # Brand-specific focus-position signals consumed by the
    # focus_position_normalized brand-profile method:
    #   - Panasonic: FocusStepNear / FocusStepCount (motor steps;
    #     0 = at minimum focus distance, count = at infinity).
    #   - Apple (iPhone): FocusDistanceRange in MakerNotes, formatted
    #     as 'X.XX - Y.YY m'. iPhone macro shots show e.g. '0.12 - 0.16 m'
    #     (verified on IMG_3475.HEIC, Costa Rica 2026-04). Front-camera
    #     selfies don't write it.
    #   - Standard EXIF 2.x: SubjectDistance (metres) and
    #     SubjectDistanceRange (Unknown / Macro / Close / Distant).
    #     Rarely written by Panasonic but other brands may.
    'FocusStepNear', 'FocusDistanceRange',
    'SubjectDistance', 'SubjectDistanceRange',
    # AF-point rectangle (docs/18 §AF, brand-aware `read_af_point`).
    # These were missing from the whitelist, so the E7 brand-AF rule
    # had nothing to read on ANY body (the AF overlay never drew).
    #   - Panasonic: AFPointPosition (normalized x/y). NOTE: empirically
    #     the Lumix G9 II / DC-G9M2 writes the literal 'n/a' here even
    #     for AF-S/AF-C — it records no AF coordinate at all. Older
    #     bodies / other brands do; the parser treats 'n/a'/'none' as
    #     "no AF" (graceful degradation → global default).
    #   - Sony: FocusLocation (imgW imgH afX afY) + FocusFrameSize.
    'AFPointPosition', 'FocusLocation', 'FocusFrameSize',
    # XMP face regions (iPhone face detection). Reliable signal for
    # subject_detection=human; SubjectArea on iPhone is sometimes just
    # the default centre AF box and not a face.
    'RegionType',
    # MWG region geometry (XMP-mwg-rs) — the AF-box source for
    # iPhone HEIC (af_point kind 'mwg_face_regions'). X/Y are the
    # region *centre*, W/H the size, all normalized 0..1. Parallel
    # arrays when several faces. Was missing → the overlay couldn't
    # draw on phone photos (Nelson 2026-05-16).
    'RegionAreaX', 'RegionAreaY', 'RegionAreaW', 'RegionAreaH',
    # Video-specific tags. ``MediaCreateDate`` / ``TrackCreateDate``
    # are QuickTime-container timestamps GoPro and other video-only
    # cameras populate (they don't write EXIF ``DateTimeOriginal``).
    # The timestamp-resolution fallback below uses these so the same
    # ``PhotoExif`` shape works for video files in the Reconcile and
    # video-discovery flows.
    'ImageWidth', 'ImageHeight', 'VideoFrameRate', 'Duration',
    'CompressorName', 'VideoCodec', 'CreateDate', 'CreationDate',
    'MediaCreateDate', 'TrackCreateDate',
    # spec/45 — phone-driven TZ correction. ``OffsetTimeOriginal`` is the
    # EXIF 2.31 UTC offset paired with ``DateTimeOriginal`` (e.g. ``"+02:00"``);
    # modern phones populate it consistently, dedicated cameras almost never.
    # GPS pair lets Slice TZ-2 derive country per day from a phone's centroid.
    # ExifTool returns GPSLatitude/GPSLongitude as signed decimal degrees when
    # invoked with ``-n`` numeric mode — we are NOT in numeric mode, so the
    # values arrive as strings like ``"43 deg 0' 0.00\" N"`` and need parsing
    # alongside the ``GPSLatitudeRef``/``GPSLongitudeRef`` hemisphere chars.
    'OffsetTimeOriginal', 'OffsetTime',
    'GPSLatitude', 'GPSLatitudeRef', 'GPSLongitude', 'GPSLongitudeRef',
]


@dataclass
class PhotoExif:
    path: Path
    timestamp: datetime | None = None
    model: str = ''
    lens: str = ''
    focal_length: float = 0.0
    aperture: float = 0.0
    shutter_speed: float = 0.0  # in seconds
    iso: int = 0
    flash_fired: bool = False
    focus_mode: str = ''
    af_area_mode: str = ''
    af_subject: str = ''
    shooting_mode: str = ''
    burst_mode: bool = False
    bracketing: str = ''
    photo_style: str = ''
    shutter_type: str = ''
    ext_tele_conv: str = ''
    silent_mode: bool = False
    focus_step_count: int = 0  # total frames in focus bracket
    focus_bracket_step: int = -1  # which step this frame is
    sequence_number: int = 0
    duration_seconds: float = 0.0  # video running time (0 for stills / unknown)
    # spec/45 — phone-driven TZ correction. ``None`` when the EXIF tag is
    # absent (most dedicated cameras); minutes east-of-UTC when present
    # (e.g. ``120`` for ``+02:00``, ``-180`` for ``-03:00``). Signed decimal
    # degrees for GPS coordinates; ``None`` when unknown.
    tz_offset_minutes: int | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def focal_35mm(self) -> float:
        """MFT crop factor ~2x."""
        return self.focal_length * 2

    @property
    def is_flash_shot(self) -> bool:
        return self.flash_fired

    @property
    def is_focus_bracket(self) -> bool:
        return 'focus' in (self.bracketing or '').lower()

    @property
    def is_long_exposure(self) -> bool:
        return self.shutter_speed >= 1.0


def _parse_float(s: str) -> float:
    """Parse values like '6.3', '1/2000', '0.005'."""
    if not s:
        return 0.0
    s = str(s).strip()
    if '/' in s:
        try:
            n, d = s.split('/')
            return float(n) / float(d)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_int(s: str) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError):
        return 0


def _parse_duration_seconds(val) -> float:
    """ExifTool ``Duration`` → seconds. Without ``-n`` it arrives formatted as either
    ``"H:MM:SS"`` / ``"MM:SS"`` (QuickTime/MP4 containers) or ``"N.NN s"`` (some codecs);
    occasionally a bare number. Returns 0.0 for stills / unreadable values."""
    if val is None:
        return 0.0
    s = str(val).strip()
    if not s:
        return 0.0
    s = s.replace(" s", "").strip() if s.endswith(" s") else s
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return 0.0
        secs = 0.0
        for n in nums:                       # H:MM:SS or MM:SS — accumulate base-60
            secs = secs * 60 + n
        return secs
    try:
        return float(s)
    except ValueError:
        return 0.0


def _pick_capture_timestamp(entry: dict) -> str:
    """Walk the capture-time fallback chain and return the raw string.

    Order: ``DateTimeOriginal`` (standard EXIF, local wall-clock) →
    ``CreationDate`` (QuickTime local-with-TZ-trailer) → ``CreateDate``
    → ``MediaCreateDate`` → ``TrackCreateDate``. The empty-string
    fallback lets callers safely treat the absence as "no
    timestamp"."""
    chain = (
        "DateTimeOriginal",
        "CreationDate",
        "CreateDate",
        "MediaCreateDate",
        "TrackCreateDate",
    )
    for field_name in chain:
        value = entry.get(field_name)
        if value:
            return str(value)
    return ""


def _parse_timestamp(s: str) -> datetime | None:
    """Parse an EXIF / QuickTime timestamp string into a naive datetime.

    Thin wrapper over :func:`_parse_timestamp_and_tz` that drops the
    trailing TZ offset. Kept for the callers that only want the wall
    clock (tests, direct EXIF probes). ``_read_photos_batch`` uses the
    detailed function so it can shift UTC-tagged timestamps into
    camera-local wall clock at parse time."""
    dt, _ = _parse_timestamp_and_tz(s)
    return dt


def _parse_timestamp_and_tz(
    s: str,
) -> tuple[datetime | None, Optional[int]]:
    """Parse a timestamp string and return ``(naive_dt, tz_offset_seconds)``.

    The returned ``naive_dt`` is ALWAYS wall-clock naive (no ``tzinfo``);
    the second element carries the timezone information the string
    carried:

    * ``None`` — string had no TZ trailer (naive local wall-clock,
      like a photo's ``DateTimeOriginal``).
    * ``0`` — ``Z`` designator (UTC).
    * signed int — offset in seconds (``+02:00`` → ``7200``,
      ``-03:00`` → ``-10800``).

    Tolerates fractional seconds (``.123``) between the calendar and
    the TZ. Unrecognised trailers fall back to ``None`` so the caller
    keeps the "naive local wall-clock" semantics the rest of the
    codebase relies on. Introduced 2026-07-02 as the parsing half of
    the QuickTimeUTC handoff — see :func:`read_exif_batch`."""
    import re
    if not s:
        return None, None
    s = str(s).strip()
    cal_part = s[:19]
    naive_dt: datetime | None = None
    for fmt in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            naive_dt = datetime.strptime(cal_part, fmt)
            break
        except ValueError:
            continue
    if naive_dt is None:
        return None, None

    trailer = s[19:]
    # Skip fractional seconds (``.NNN``).
    if trailer.startswith('.'):
        i = 1
        while i < len(trailer) and trailer[i].isdigit():
            i += 1
        trailer = trailer[i:]
    trailer = trailer.strip()
    if not trailer:
        return naive_dt, None
    if trailer.upper() == 'Z':
        return naive_dt, 0
    m = re.match(r'^([+-])(\d{2}):?(\d{2})$', trailer)
    if m:
        sign = 1 if m.group(1) == '+' else -1
        hours = int(m.group(2))
        minutes = int(m.group(3))
        return naive_dt, sign * (hours * 3600 + minutes * 60)
    return naive_dt, None


def _extract_focal(raw_val: str) -> float:
    """Focal length may come as '400.0 mm' or '400'."""
    if not raw_val:
        return 0.0
    s = str(raw_val).replace('mm', '').strip()
    return _parse_float(s)


def _extract_aperture(raw_val: str) -> float:
    """FNumber may come as '6.3' or '63/10'."""
    return _parse_float(str(raw_val).replace('f/', '').strip())


def _extract_shutter(raw_val: str) -> float:
    """ExposureTime in seconds. '1/2000' = 0.0005."""
    return _parse_float(raw_val)


def _parse_offset_time(s) -> int | None:
    """spec/45 — parse ``OffsetTimeOriginal`` (EXIF 2.31). Examples:
    ``"+02:00"`` → ``120``, ``"-03:30"`` → ``-210``, ``"Z"`` → ``0``,
    ``""``/``None`` → ``None``. Tolerates ``+02``, ``-3:00``, and a leading
    whitespace; rejects anything else by returning ``None`` (no exception).

    Phones write this consistently; cameras virtually never do. The presence
    of a non-None value here is the spec/45 'is this a phone source' signal."""
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    if text.upper() == "Z":
        return 0
    sign = 1
    if text[0] in "+-":
        sign = -1 if text[0] == "-" else 1
        text = text[1:]
    # After stripping the optional sign the next char MUST be a digit;
    # otherwise "++02:00" / "-+02:00" / " 02:00" leak through int()'s own
    # leading-sign tolerance.
    if not text or not text[0].isdigit():
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            hours = int(parts[0])
            minutes = 0
        else:
            hours = int(parts[0])
            minutes = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours <= 14 and 0 <= minutes < 60):
        return None
    return sign * (hours * 60 + minutes)


def _parse_gps_coord(value, ref) -> float | None:
    """spec/45 — parse ExifTool GPS coords. Without ``-n``, ExifTool emits
    ``"43 deg 12' 34.5\\" N"`` for human-readable mode; with ``-n``, signed
    decimal degrees. We don't run ``-n`` (mixing modes for one tag would
    require a second batch), so we accept both forms.

    ``ref`` is ``GPSLatitudeRef`` / ``GPSLongitudeRef`` (``N``/``S``/``E``/``W``);
    when present it overrides the sign of the parsed magnitude. Returns
    ``None`` on parse failure."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    # Strip any 'deg' / "'" / '"' / ' ' chars and parse as D M S triple
    # when present. ExifTool's default format is
    # ``"43 deg 12' 34.5\" N"`` or ``"43.20958 N"``.
    cleaned = (
        text.replace("deg", " ").replace("°", " ")
            .replace("'", " ").replace('"', " ")
    )
    parts = [p for p in cleaned.split() if p]
    # Trailing hemisphere letter (some ExifTool versions tack it on)
    trailing_ref = None
    if parts and parts[-1].upper() in ("N", "S", "E", "W"):
        trailing_ref = parts[-1].upper()
        parts = parts[:-1]
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not nums:
        return None
    if len(nums) == 1:
        magnitude = nums[0]
    elif len(nums) == 2:
        magnitude = nums[0] + nums[1] / 60.0
    else:
        magnitude = nums[0] + nums[1] / 60.0 + nums[2] / 3600.0
    sign = 1
    ref_text = (str(ref).strip().upper() if ref else trailing_ref) or ""
    # Newer ExifTool versions emit "South"/"West"/"North"/"East" (full word) in
    # the GPS*Ref fields, not just the single-letter "S"/"W"/"N"/"E". Match by
    # first letter to handle both forms (Nelson 2026-06-06 Argentina photos
    # bug — south/west coords were silently read as positive).
    ref_first = ref_text[:1]
    if ref_first in ("S", "W"):
        sign = -1
    elif ref_first in ("N", "E"):
        sign = 1
    elif magnitude < 0:
        # No ref supplied but the value is already signed (decimal mode).
        return magnitude
    return sign * magnitude


def read_exif_batch(files: list[Path]) -> list[PhotoExif]:
    """Read EXIF from many files using an exiftool argfile (fast, no
    command-line length limits)."""
    if not files:
        return []

    import tempfile
    # Write file list to temp argfile (avoids Windows cmd-line length limit)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                     suffix='.txt', delete=False) as tf:
        argfile_path = tf.name
        for f in files:
            tf.write(f'{f}\n')

    if not _get_exiftool_path().exists():
        print(f"ExifTool NOT FOUND at: {_get_exiftool_path()}")
        return []

    cmd = [
        str(_get_exiftool_path()),
        # Nelson 2026-07-02 — mark QuickTime timestamps (CreateDate /
        # MediaCreateDate / TrackCreateDate) as UTC in the JSON output
        # instead of silently converting them to the machine's local
        # time. Without this flag, an MP4 whose only capture-time hint
        # is the mvhd atom's UTC seconds would come back as
        # machine-local — and when the machine's TZ differs from the
        # camera's TZ (traveling with a laptop that's still on home
        # time), the video lands on a different day than a photo shot
        # a second later. See ``_read_photos_batch``'s TZ-shift block
        # below for the reconciliation into camera-local wall clock.
        '-api', 'QuickTimeUTC=1',
        '-json',
        '-charset', 'filename=UTF8',
        '-charset', 'UTF8',
        '-@', argfile_path,
    ]
    for tag in TAGS:
        cmd.append(f'-{tag}')

    try:
        from core.proc import run as _run_hidden
        result = _run_hidden(
            cmd, capture_output=True, text=True, encoding='utf-8',
            check=False
        )
        if result.returncode != 0 and not result.stdout:
            print(f'ExifTool error: {result.stderr[:500]}')
            return []
        data = json.loads(result.stdout or '[]')
    except (subprocess.SubprocessError, json.JSONDecodeError) as e:
        print(f'ExifTool failed: {e}')
        return []
    finally:
        try:
            Path(argfile_path).unlink()
        except Exception:
            pass

    # Look up per-camera and home TZ once per batch (Nelson 2026-07-02).
    # Used to reconcile QuickTime UTC timestamps into camera-local wall
    # clock at parse time — the buggy case where a video's mvhd atom
    # gave us the shooting moment in UTC but the machine was on a
    # different TZ than the camera. Settings unavailable → falls back
    # to leaving the timestamp untouched (equivalent to pre-2026-07-02
    # behaviour).
    _saved_camera_tz: dict[str, float] = {}
    _home_tz_hours: Optional[float] = None
    try:
        from mira.settings.repo import SettingsRepo
        _s = SettingsRepo().load()
        _saved_camera_tz = dict(getattr(_s, 'saved_camera_tz', {}) or {})
        _raw_home = getattr(_s, 'home_timezone', None)
        if isinstance(_raw_home, (int, float)):
            _home_tz_hours = float(_raw_home)
    except Exception:                                              # noqa: BLE001
        pass

    def _camera_local_naive(
        raw: str, camera_model: str,
    ) -> datetime | None:
        """Return a naive datetime in the camera's local wall clock.

        When ``raw`` came from a TZ-tagged source (``Z`` or explicit
        offset — typically an MP4 ``CreateDate`` with QuickTimeUTC on),
        shift it into the camera's TZ (looked up in
        ``saved_camera_tz`` by model, or ``home_timezone`` as a
        fallback). Naive-input strings pass through unchanged, so a
        photo's ``DateTimeOriginal`` stays exactly as it was."""
        dt, tz_seconds = _parse_timestamp_and_tz(raw)
        if dt is None or tz_seconds is None:
            return dt
        tz_hours = _saved_camera_tz.get(camera_model) if camera_model else None
        if not isinstance(tz_hours, (int, float)):
            tz_hours = _home_tz_hours
        if tz_hours is None:
            return dt                       # honest — no way to reconcile
        camera_tz_seconds = int(float(tz_hours) * 3600)
        return dt + timedelta(seconds=camera_tz_seconds - tz_seconds)

    photos = []
    for entry in data:
        source = Path(entry.get('SourceFile', ''))
        # The capture-time fallback chain (spec/123 reverts spec/122's
        # UTC provenance flag — GoPro is just a camera with a known
        # configured TZ, handled by the per-camera offset_seconds).
        # ``CreationDate`` MUST come before ``CreateDate`` (Nelson
        # 2026-05-28): GoPro / iOS MP4 write ``CreationDate`` as LOCAL
        # wall-clock with TZ trailer (what the user actually sees) and
        # ``CreateDate`` as the mvhd UTC value.
        ts_raw = _pick_capture_timestamp(entry)
        photo = PhotoExif(
            path=source,
            timestamp=_camera_local_naive(
                ts_raw, str(entry.get('Model', '')).strip()),
            model=str(entry.get('Model', '')),
            lens=str(entry.get('LensModel', '')).strip(),
            focal_length=_extract_focal(entry.get('FocalLength', '')),
            aperture=_extract_aperture(entry.get('FNumber', '')),
            shutter_speed=_extract_shutter(entry.get('ExposureTime', '')),
            iso=_parse_int(entry.get('ISO', 0)),
            flash_fired=bool(entry.get('Flash', ''))
                        and 'did not fire' not in str(entry.get('Flash', '')).lower()
                        and 'off' not in str(entry.get('Flash', '')).lower()
                        and 'no flash' not in str(entry.get('Flash', '')).lower(),
            focus_mode=str(entry.get('FocusMode', '')),
            af_area_mode=str(entry.get('AFAreaMode', '')),
            af_subject=str(entry.get('AFSubjectDetection', '')),
            shooting_mode=str(entry.get('ShootingMode', '')),
            burst_mode=str(entry.get('BurstMode', '')).lower() not in
                      ('', 'off', 'none', '0'),
            bracketing=str(entry.get('BurstMode', '')),
            photo_style=str(entry.get('PhotoStyle', '')),
            shutter_type=str(entry.get('ShutterType', '')),
            ext_tele_conv=str(entry.get('ExtTeleConv', '')),
            silent_mode='on' in str(entry.get('SilentMode', '')).lower(),
            focus_step_count=_parse_int(entry.get('FocusStepCount', 0)),
            focus_bracket_step=_parse_int(entry.get('FocusBracket', -1)),
            sequence_number=_parse_int(entry.get('SequenceNumber', 0)),
            duration_seconds=_parse_duration_seconds(entry.get('Duration', '')),
            # spec/45 — phone-driven TZ + GPS for Slice TZ-1.
            tz_offset_minutes=_parse_offset_time(
                entry.get('OffsetTimeOriginal') or entry.get('OffsetTime')
            ),
            gps_lat=_parse_gps_coord(
                entry.get('GPSLatitude'), entry.get('GPSLatitudeRef'),
            ),
            gps_lon=_parse_gps_coord(
                entry.get('GPSLongitude'), entry.get('GPSLongitudeRef'),
            ),
            raw=entry,
        )
        photos.append(photo)
    return photos


def read_exif_single(path: Path) -> PhotoExif | None:
    result = read_exif_batch([path])
    return result[0] if result else None
