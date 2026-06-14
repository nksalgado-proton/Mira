"""Rewrite EXIF DateTimeOriginal preserving the original value.

The Reconcile workflow corrects camera-clock errors retroactively.
That requires writing a new ``DateTimeOriginal`` on existing files —
a destructive operation. To make it reversible and auditable, this
module:

1. Records the original capture time in a sidecar EXIF tag
   (``UserComment``, formatted as ``OriginalCaptureTime:<ISO time>``)
   the FIRST time a file is rewritten. Subsequent rewrites of the
   same file leave that tag alone — so the user can refine
   calibration and re-run Reconcile without losing the true original.
2. Writes both ``DateTimeOriginal`` and ``CreateDate`` (some Lumix /
   Sony bodies key off CreateDate; iPhone HEIC uses both) to the
   corrected value.
3. Operates atomically by leveraging exiftool's ``-overwrite_original``
   (which itself is atomic — exiftool writes a new file and renames).

Cross-format support: works on RW2, JPG, HEIC, TIFF — anything
exiftool can write. GoPro MP4 video timestamps are deferred to a
future module (Phase 3) because they live in XMP/QuickTime metadata
rather than EXIF and need a different approach.

Qt-free; uses subprocess to call the bundled exiftool. Caller is
expected to batch operations where possible — invoking exiftool per
file is much slower than feeding it an argfile.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.exif_reader import _get_exiftool_path
from core.proc import no_window_kwargs as _no_window_kwargs
from core.proc import run as _run_hidden  # window-suppressed (core/proc)

log = logging.getLogger(__name__)


# UserComment value format. Plain text so it survives EXIF copy /
# Lightroom export. ISO-8601 with ``T`` separator chosen for sortability.
_ORIGINAL_TIME_PREFIX = "OriginalCaptureTime:"
_ORIGINAL_TIME_FMT = "%Y-%m-%dT%H:%M:%S"

# EXIF DateTimeOriginal format expected by exiftool: "YYYY:MM:DD HH:MM:SS"
_EXIF_DT_FMT = "%Y:%m:%d %H:%M:%S"

# Extensions that go through the video-mode rewrite path: timestamps
# in QuickTime/MP4 metadata (``CreateDate`` / ``MediaCreateDate`` /
# ``TrackCreateDate``) instead of EXIF ``DateTimeOriginal``.
_VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mov", ".m4v"})


def _is_video(path: Path) -> bool:
    """True for files whose timestamps live in QuickTime metadata
    rather than EXIF. Drives the rewrite tag list."""
    return path.suffix.lower() in _VIDEO_EXTS


@dataclass
class RewriteOutcome:
    """Result of a single file rewrite. Successful rewrites set
    ``new_time`` and leave ``error`` blank; failures set ``error``."""
    path: Path
    new_time: Optional[datetime] = None
    preserved_original: bool = False
    error: str = ""


def format_original_time_marker(original: datetime) -> str:
    """Public helper so callers can compute the expected UserComment
    value for assertions / display without re-importing the format."""
    return f"{_ORIGINAL_TIME_PREFIX}{original.strftime(_ORIGINAL_TIME_FMT)}"


def parse_original_time_marker(comment: str) -> Optional[datetime]:
    """Inverse of ``format_original_time_marker``. Returns ``None`` if
    ``comment`` doesn't carry the expected prefix or doesn't parse."""
    if not comment.startswith(_ORIGINAL_TIME_PREFIX):
        return None
    raw = comment[len(_ORIGINAL_TIME_PREFIX):].strip()
    try:
        return datetime.strptime(raw, _ORIGINAL_TIME_FMT)
    except ValueError:
        return None


def _read_existing_tags(path: Path) -> dict:
    """Read the few tags we need to know about before deciding what to
    write: existing UserComment (so we don't overwrite a previously
    preserved original) and current capture time (used as the
    "original" if no UserComment exists yet). For video files, the
    capture time falls back through the QuickTime tag chain because
    ``DateTimeOriginal`` typically isn't present."""
    exiftool = _get_exiftool_path()
    cp = _run_hidden(
        [
            str(exiftool), "-json", "-fast2",
            "-DateTimeOriginal", "-CreateDate",
            "-MediaCreateDate", "-TrackCreateDate",
            # GoPro / iOS MP4 also writes a brand-specific
            # ``CreationDate`` (often the local-time wall-clock with
            # TZ offset). Read it so the rewrite path can preserve
            # the original under UserComment if needed (Nelson
            # 2026-05-28: was previously ignored — see write block).
            "-CreationDate",
            "-UserComment",
            "-charset", "filename=UTF8",
            str(path),
        ],
        # errors="replace": exiftool echoes the SourceFile path in its output, and on Windows
        # a path with non-ASCII (e.g. the em-dash in a "Day 1 — <date>" folder) comes back in
        # the system codepage, not UTF-8. Strict decode crashed the subprocess reader thread,
        # leaving stdout=None → a downstream NoneType.strip (Nelson 2026-06-03, Nepal snapshot
        # bake). The batch/session path already decodes tolerantly; match it here.
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if cp.returncode != 0 or not (cp.stdout or "").strip():
        log.warning("exiftool read failed for %s: %s", path, cp.stderr.strip())
        return {}
    import json
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        log.warning("exiftool returned non-JSON for %s: %s", path, exc)
        return {}
    if not data:
        return {}
    return data[0] if isinstance(data, list) else data


def _parse_exif_dt(s: str) -> Optional[datetime]:
    """Parse the ``YYYY:MM:DD HH:MM:SS`` (or with subseconds) format
    exiftool returns. Returns ``None`` on any parse failure."""
    if not s:
        return None
    s = s.strip().split(".")[0].split("+")[0].split("-07:00")[0]
    try:
        return datetime.strptime(s, _EXIF_DT_FMT)
    except ValueError:
        return None


def rewrite_capture_time(
    path: Path,
    new_time: datetime,
    *,
    preserve_original: bool = True,
) -> RewriteOutcome:
    """Set ``DateTimeOriginal`` (and ``CreateDate``) on ``path`` to
    ``new_time``.

    When ``preserve_original=True`` (default) and the file's
    ``UserComment`` does NOT already begin with the
    ``OriginalCaptureTime:`` marker, the existing
    ``DateTimeOriginal`` is captured into ``UserComment`` first.
    Idempotent: re-running with a different ``new_time`` does not
    rewrite the preserved-original marker (the FIRST rewrite wins).

    The exiftool call uses ``-overwrite_original`` so the file is
    modified in place (exiftool itself is atomic). On any tool
    failure, the file is left untouched and ``RewriteOutcome.error``
    is populated.
    """
    if not path.exists():
        return RewriteOutcome(path=path, error="file not found")

    is_video = _is_video(path)
    # The existing-tags read feeds ONLY the preserve-original block below, so skip it entirely
    # when ``preserve_original=False`` — that was a wasted exiftool launch per file (the
    # materializer's snapshot bake does ~100 of these; Nelson 2026-06-03 materialise perf).
    current_user_comment = ""
    current_dto: Optional[datetime] = None
    if preserve_original:
        existing = _read_existing_tags(path)
        current_user_comment = str(existing.get("UserComment", "") or "")
        if is_video:
            # Video files don't carry DateTimeOriginal — fall through the
            # QuickTime tag chain to find the existing capture time.
            current_dto = (
                _parse_exif_dt(str(existing.get("CreateDate", "") or ""))
                or _parse_exif_dt(str(existing.get("MediaCreateDate", "") or ""))
                or _parse_exif_dt(str(existing.get("TrackCreateDate", "") or ""))
            )
        else:
            current_dto = _parse_exif_dt(
                str(existing.get("DateTimeOriginal", "") or "")
            )

    # Build the exiftool args. Photo writes target EXIF
    # ``DateTimeOriginal`` + ``CreateDate``; video writes target the
    # QuickTime triple (``CreateDate`` + ``MediaCreateDate`` +
    # ``TrackCreateDate``) so all the surfaces that consumers
    # typically read are kept in sync.
    formatted = new_time.strftime(_EXIF_DT_FMT)
    args: list[str] = [
        str(_get_exiftool_path()),
        "-overwrite_original",
        "-charset", "filename=UTF8",
    ]
    if is_video:
        # QuickTime stores its dates as UTC internally; exiftool
        # synthesises ``DateTimeOriginal`` from ``MediaCreateDate``
        # rendered in the SYSTEM local TZ — which means after we
        # bake ``CreateDate``/``MediaCreateDate``/``TrackCreateDate``
        # to the trip-local value, a subsequent read of DTO can
        # come back at a different wall-clock time depending on
        # the OS TZ (Nelson 2026-05-23: a Dubai MP4 read back 3h
        # earlier than its JPG neighbours because the cull canvas
        # sorts by DTO). Writing DTO explicitly stores it as an
        # XMP tag on the MP4, so the read path is format-agnostic
        # and the sort is stable.
        #
        # ``CreationDate`` (Nelson 2026-05-28): brand-specific tag
        # GoPro + iOS MP4 write as the LOCAL wall-clock with the
        # camera's TZ at recording time. The bucket scanner reads
        # it FIRST in its TIMESTAMP_TAGS chain, so leaving it
        # untouched while the QuickTime triple gets baked means the
        # scanner reads stale (camera-was-in-wrong-TZ) values and
        # sorts the video before/after its true position. Repro:
        # Nepal trip with GoPro clock set to SP — bake baked
        # CreateDate/MediaCreateDate/TrackCreateDate but
        # CreationDate stayed in SP wall-clock, so Lukla-morning
        # videos sorted at "yesterday evening" relative to G9 photos.
        # Baking CreationDate alongside the QuickTime triple keeps
        # all downstream readers in sync.
        args.extend([
            f"-CreateDate={formatted}",
            f"-MediaCreateDate={formatted}",
            f"-TrackCreateDate={formatted}",
            f"-CreationDate={formatted}",
            f"-DateTimeOriginal={formatted}",
        ])
    else:
        args.extend([
            f"-DateTimeOriginal={formatted}",
            f"-CreateDate={formatted}",
        ])

    preserved = False
    already_preserved = parse_original_time_marker(current_user_comment) is not None
    if preserve_original and not already_preserved:
        # Use the file's CURRENT DateTimeOriginal as the original. If
        # it can't be read, skip preservation rather than make up a
        # value — we'd rather lose audit data than write wrong audit
        # data.
        if current_dto is not None:
            marker = format_original_time_marker(current_dto)
            args.append(f"-UserComment={marker}")
            preserved = True

    args.append(str(path))

    try:
        cp = _run_hidden(
            args, capture_output=True, text=True, encoding="utf-8",
            errors="replace",        # tolerate non-UTF-8 path echo on Windows (see read above)
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return RewriteOutcome(path=path, error="exiftool timeout (60s)")
    except OSError as exc:
        return RewriteOutcome(path=path, error=f"exiftool launch failed: {exc}")

    if cp.returncode != 0:
        return RewriteOutcome(
            path=path,
            error=f"exiftool failed (code {cp.returncode}): {cp.stderr.strip()}",
        )

    return RewriteOutcome(
        path=path, new_time=new_time, preserved_original=preserved,
    )


def rewrite_capture_times_batch(
    operations: list[tuple[Path, datetime]],
    *,
    preserve_original: bool = True,
    progress=None,                                  # Optional[Callable[[str,int,int],None]]
) -> list[RewriteOutcome]:
    """Apply ``rewrite_capture_time`` to many files using a SINGLE
    persistent exiftool process (``-stay_open`` mode).

    The naive per-file path launches exiftool twice per file (one
    read + one write). On Windows that's ~0.5 s of pure process
    startup per file — for 1300 files that's ~21 min of nothing
    but process spawning (Nelson 2026-05-22 reported exactly this).
    The persistent-session path keeps one exiftool alive for the
    whole batch and feeds commands via stdin, dropping the same
    job to seconds.

    Falls back to per-file ``rewrite_capture_time`` if the session
    fails to start — so this is always safe to call.

    Returns one ``RewriteOutcome`` per input, in the same order.
    ``progress(message, current, total)`` fires after every file.
    """
    if not operations:
        return []

    try:
        session = ExiftoolSession()
        session.start()
    except (OSError, RuntimeError) as exc:
        log.warning(
            "exiftool persistent session failed to start (%s); "
            "falling back to per-file launches", exc,
        )
        out: list[RewriteOutcome] = []
        total = len(operations)
        for i, (path, new_time) in enumerate(operations, start=1):
            out.append(
                rewrite_capture_time(
                    path, new_time, preserve_original=preserve_original,
                )
            )
            if progress is not None:
                progress(
                    f"Correcting capture timestamps in your photos… "
                    f"({i}/{total})",
                    i, total,
                )
        return out

    out: list[RewriteOutcome] = []
    total = len(operations)
    session_dead = False
    try:
        for i, (path, new_time) in enumerate(operations, start=1):
            if session_dead:
                # Session went south — finish the run via per-file
                # launches so the user at least gets the bake done.
                out.append(
                    rewrite_capture_time(
                        path, new_time,
                        preserve_original=preserve_original,
                    )
                )
            else:
                try:
                    out.append(
                        _rewrite_via_session(
                            session, path, new_time,
                            preserve_original=preserve_original,
                        )
                    )
                except RuntimeError as exc:
                    log.warning(
                        "exiftool persistent session lost mid-batch "
                        "(%s); falling back to per-file launches for "
                        "the remaining %d file(s)",
                        exc, total - i + 1,
                    )
                    session_dead = True
                    out.append(
                        rewrite_capture_time(
                            path, new_time,
                            preserve_original=preserve_original,
                        )
                    )
            if progress is not None:
                progress(
                    f"Rewriting EXIF… ({i}/{total})", i, total,
                )
    finally:
        try:
            session.close()
        except Exception:                          # noqa: BLE001
            pass
    return out


# ── Persistent exiftool session ────────────────────────────────


# Sentinel exiftool prints to stdout after each ``-execute<N>`` — the
# host reads stdout up to ``{ready<N>}`` to know one batch is done.
# Note the counter goes INSIDE the braces (exiftool: ``{ready1}``),
# not after them. Stderr is drained on a background thread (exiftool
# doesn't emit a reliable per-batch stderr boundary, so we cannot
# block on it — Nelson 2026-05-22 hit a 6-minute hang doing exactly
# that).
_READY_PREFIX = "{ready"
_READY_SUFFIX = "}"


class ExiftoolSession:
    """Persistent exiftool process driven via ``-stay_open True -@ -``.

    Lifecycle::

        s = ExiftoolSession()
        s.start()
        try:
            stdout, stderr, rc = s.execute([
                "-DateTimeOriginal=2026:05:22 13:00:00",
                "-overwrite_original", "/path/to/file.jpg",
            ])
        finally:
            s.close()

    Works as a context manager too::

        with ExiftoolSession() as s:
            s.execute([...])

    One process handles the whole batch — process-launch overhead
    is paid ONCE regardless of file count. Stdin is fed argfile-
    style (one arg per line, terminated by ``-execute<n>\\n``);
    stdout is read until the ``{ready<n>}`` boundary; stderr is
    drained on a background daemon thread into a buffer that
    ``execute`` snapshots after stdout reaches the boundary. That
    means: we never block on stderr (it has no reliable per-batch
    sentinel), but we still report any errors the batch wrote.
    """

    # Per-execute timeout. Generous enough for any single file's
    # read+write, but small enough that the UI doesn't hang
    # silently for minutes if something goes wrong on the exiftool
    # side. Tripping this raises so the batch helper can fall back
    # to per-file launches.
    _EXECUTE_TIMEOUT_SEC = 30.0

    def __init__(self, exiftool_path: Optional[Path] = None) -> None:
        self._exiftool = exiftool_path or _get_exiftool_path()
        self._proc: Optional[subprocess.Popen] = None
        self._counter = 0
        # Stdout drain: a background thread pulls lines from
        # exiftool's stdout (binary mode → decoded here) and pushes
        # them into ``_stdout_queue``. ``execute`` reads from the
        # queue with a timeout so it never blocks on a pipe.
        # Threaded I/O is the only reliable way around Windows
        # pipe-buffering quirks (Nelson 2026-05-22: text-mode
        # readline() hung for 6 minutes on the first batch).
        from queue import Queue
        self._stdout_queue: "Queue[bytes]" = Queue()
        self._stderr_lock = threading.Lock()
        self._stderr_lines: list[bytes] = []
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    def __enter__(self) -> "ExiftoolSession":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        """Spawn the persistent exiftool. Raises ``OSError`` /
        ``RuntimeError`` if the tool can't be launched."""
        if self._proc is not None:
            return
        cmd = [
            str(self._exiftool),
            "-stay_open", "True",
            "-@", "-",
            "-common_args",
            "-charset", "filename=UTF8",
        ]
        try:
            # Binary pipes: TextIOWrapper buffers reads in chunks
            # on Windows and can hide exiftool's flush of
            # ``{ready<n>}`` for minutes. Reading raw bytes per
            # line bypasses the wrapper entirely. bufsize=0 keeps
            # stdin unbuffered so our writes land immediately.
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **_no_window_kwargs(),
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"failed to launch persistent exiftool: {exc}"
            ) from exc
        self._stdout_thread = threading.Thread(
            target=self._drain_stdout, daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True,
        )
        self._stderr_thread.start()

    def _drain_stdout(self) -> None:
        """Read exiftool stdout line by line (binary) and queue
        each line for the foreground ``execute`` to consume."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, b""):
                self._stdout_queue.put(line)
        except (OSError, ValueError):
            return
        finally:
            # Sentinel so ``execute`` wakes up on close.
            self._stdout_queue.put(b"")

    def _drain_stderr(self) -> None:
        """Background reader that pulls stderr lines into
        ``_stderr_lines`` so ``execute`` never has to block on
        them. Exits when the pipe closes."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in iter(proc.stderr.readline, b""):
                with self._stderr_lock:
                    self._stderr_lines.append(line)
        except (OSError, ValueError):
            return

    def close(self) -> None:
        """Send the ``-stay_open False`` shutdown command and wait
        for exiftool to exit. Safe to call repeatedly."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.write(b"-stay_open\nFalse\n")
                    proc.stdin.flush()
                except (OSError, BrokenPipeError):
                    pass
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=2)
            self._stdout_thread = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
            self._stderr_thread = None

    def execute(self, args: list[str]) -> tuple[str, str, int]:
        """Run one batch of args through the persistent process and
        return ``(stdout, stderr, returncode)``.

        Each call has a hard ``_EXECUTE_TIMEOUT_SEC`` cap; on
        timeout, ``RuntimeError`` is raised and the batch helper's
        try/except falls back to per-file launches for the
        remaining files.

        ``returncode`` is 0 when stderr is empty for this batch,
        non-zero otherwise (mirroring ``subprocess.CompletedProcess``
        semantics).
        """
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("session not started")
        from queue import Empty
        proc = self._proc
        self._counter += 1
        marker = str(self._counter)
        # exiftool prints ``{ready<N>}`` — counter INSIDE the braces.
        ready_stdout = f"{_READY_PREFIX}{marker}{_READY_SUFFIX}"
        payload = "\n".join(args)
        try:
            proc.stdin.write((payload + "\n").encode("utf-8"))
            proc.stdin.write(f"-execute{marker}\n".encode("utf-8"))
            proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise RuntimeError(f"exiftool write failed: {exc}") from exc

        stdout_chunks: list[bytes] = []
        deadline_remaining = self._EXECUTE_TIMEOUT_SEC
        import time as _time
        start = _time.monotonic()
        while True:
            try:
                line = self._stdout_queue.get(
                    timeout=deadline_remaining)
            except Empty:
                raise RuntimeError(
                    f"exiftool execute timed out after "
                    f"{self._EXECUTE_TIMEOUT_SEC}s"
                )
            if not line:
                raise RuntimeError(
                    "exiftool stdout closed unexpectedly")
            decoded = line.decode("utf-8", errors="replace")
            if decoded.rstrip("\r\n") == ready_stdout:
                break
            stdout_chunks.append(line)
            deadline_remaining = max(
                0.1,
                self._EXECUTE_TIMEOUT_SEC - (
                    _time.monotonic() - start),
            )

        # Snapshot + clear the stderr buffer.
        with self._stderr_lock:
            err_chunks = list(self._stderr_lines)
            self._stderr_lines.clear()

        out = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        err = b"".join(err_chunks).decode("utf-8", errors="replace")
        rc = 0 if not err.strip() else 1
        return out, err, rc


def _read_existing_tags_via_session(
    session: ExiftoolSession, path: Path,
) -> dict:
    """Same as :func:`_read_existing_tags` but routed through the
    persistent session."""
    import json
    stdout, stderr, rc = session.execute([
        "-json", "-fast2",
        "-DateTimeOriginal", "-CreateDate",
        "-MediaCreateDate", "-TrackCreateDate",
        # GoPro / iOS MP4 brand-specific tag — see _read_existing_tags.
        "-CreationDate",
        "-UserComment",
        str(path),
    ])
    if rc != 0 or not stdout.strip():
        log.warning(
            "exiftool session read failed for %s: %s",
            path, stderr.strip(),
        )
        return {}
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        log.warning(
            "exiftool session returned non-JSON for %s: %s",
            path, exc,
        )
        return {}
    if not data:
        return {}
    return data[0] if isinstance(data, list) else data


def _rewrite_via_session(
    session: ExiftoolSession,
    path: Path,
    new_time: datetime,
    *,
    preserve_original: bool = True,
) -> RewriteOutcome:
    """Per-file rewrite, same semantics as :func:`rewrite_capture_time`,
    but using a persistent session for ALL exiftool I/O."""
    if not path.exists():
        return RewriteOutcome(path=path, error="file not found")

    existing = _read_existing_tags_via_session(session, path)
    current_user_comment = str(existing.get("UserComment", "") or "")
    is_video = _is_video(path)
    if is_video:
        current_dto = (
            _parse_exif_dt(str(existing.get("CreateDate", "") or ""))
            or _parse_exif_dt(str(existing.get("MediaCreateDate", "") or ""))
            or _parse_exif_dt(str(existing.get("TrackCreateDate", "") or ""))
        )
    else:
        current_dto = _parse_exif_dt(
            str(existing.get("DateTimeOriginal", "") or "")
        )

    formatted = new_time.strftime(_EXIF_DT_FMT)
    args: list[str] = ["-overwrite_original"]
    if is_video:
        # QuickTime stores its dates as UTC internally; exiftool
        # synthesises ``DateTimeOriginal`` from ``MediaCreateDate``
        # rendered in the SYSTEM local TZ — which means after we
        # bake ``CreateDate``/``MediaCreateDate``/``TrackCreateDate``
        # to the trip-local value, a subsequent read of DTO can
        # come back at a different wall-clock time depending on
        # the OS TZ (Nelson 2026-05-23: a Dubai MP4 read back 3h
        # earlier than its JPG neighbours because the cull canvas
        # sorts by DTO). Writing DTO explicitly stores it as an
        # XMP tag on the MP4, so the read path is format-agnostic
        # and the sort is stable. ``CreationDate`` baked alongside
        # for the same reason (Nelson 2026-05-28, Nepal GoPro case
        # — see rewrite_capture_time docstring for the full repro).
        args.extend([
            f"-CreateDate={formatted}",
            f"-MediaCreateDate={formatted}",
            f"-TrackCreateDate={formatted}",
            f"-CreationDate={formatted}",
            f"-DateTimeOriginal={formatted}",
        ])
    else:
        args.extend([
            f"-DateTimeOriginal={formatted}",
            f"-CreateDate={formatted}",
        ])

    preserved = False
    already_preserved = parse_original_time_marker(current_user_comment) is not None
    if preserve_original and not already_preserved and current_dto is not None:
        marker = format_original_time_marker(current_dto)
        args.append(f"-UserComment={marker}")
        preserved = True

    args.append(str(path))

    try:
        _stdout, stderr, rc = session.execute(args)
    except RuntimeError as exc:
        return RewriteOutcome(
            path=path, error=f"exiftool session error: {exc}",
        )
    if rc != 0:
        return RewriteOutcome(
            path=path,
            error=f"exiftool failed: {stderr.strip()}",
        )
    return RewriteOutcome(
        path=path, new_time=new_time, preserved_original=preserved,
    )
