"""Application logging setup and helpers.

Call setup_logging() once at startup (from ui/app.py) to initialize the
per-session file handler and apply the user's configured log level.

Usage in modules:

    import logging
    from core.logging_setup import log_activity

    log = logging.getLogger(__name__)

    def load_something():
        with log_activity(log, "loading something"):
            ...  # code that may raise

The log_activity context manager emits:
  - DEBUG "Starting loading something..."
  - DEBUG "loading something completed in 0.012s"  (on success)
  - ERROR "loading something failed after 0.003s: <exc>" (on exception, then re-raises)

Log file location: {user_data_dir}/logs/mira.log
Overwrite-on-launch (Nelson 2026-07-02): each app run TRUNCATES the log
file so it always contains this session's output only. No rotation, no
accumulation. A "session started" INFO line at the top guarantees the
file exists after startup even when nothing else logs.

Troubleshooting a silent crash:
  1. Open settings.json, set "log_level": "DEBUG"
  2. Re-run the app, reproduce the problem
  3. Open {user_data_dir}/logs/mira.log — search for ERROR lines
     (App menu → "Access log file" opens it directly)
"""

import logging
import logging.handlers
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.settings import load_settings


LOG_FILE_NAME = "mira.log"
# Kept as a module-level constant for backwards-compat with the two
# call sites that still reference the name (tests, settings audit),
# but rotation itself was retired 2026-07-02: each run truncates
# ``mira.log`` on open. Nothing reads this constant at runtime.
LOG_ROTATE_KEEP_DAYS = 14
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Sentinel to avoid attaching handlers twice if setup_logging() is called more
# than once (e.g. during tests that import ui/app.py).
_HANDLERS_ATTACHED = False


def logs_dir() -> Path:
    """Directory where log files are stored.

    Uses :func:`mira.paths.user_data_dir` (Nelson 2026-07-02) so this
    module resolves to the same directory the app-startup logging in
    ``mira/ui/app.py`` writes to. That path is library-root-aware
    (``<library_root>/.mira/logs`` when a library pointer is set,
    otherwise the AppData fallback). Previously this used
    :func:`core.settings.user_data_dir` — the non-library-aware variant
    — so ``App → Access log file`` opened an empty AppData directory
    while the actual log lived under the library."""
    from mira.paths import user_data_dir
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file_path() -> Path:
    """Full path to the current log file."""
    return logs_dir() / LOG_FILE_NAME


def _resolve_level(raw: str) -> int:
    """Convert a level string to its logging module constant.

    Tolerates lowercase/mixed case. Unknown values fall back to INFO.
    """
    normalized = str(raw or "").strip().upper()
    if normalized not in _VALID_LEVELS:
        normalized = "INFO"
    return getattr(logging, normalized)


def _sweep_stale_log_files() -> None:
    """Delete anything in ``logs_dir()`` that isn't the current log file
    (Nelson 2026-07-02).

    Historical leftovers accumulate here: the pre-fork ``miracraft.log``
    from the ancestor repo, one-off ``native_spawn_diag.txt`` /
    ``subprocess_diag.txt`` files a since-retired diagnostic wrote, and
    any ``mira.log.<N>`` rotation siblings from before the switch to
    overwrite-on-launch. The user's rule is one log file, and this
    module owns the directory — so we sweep every other entry on each
    startup. Best-effort: any file that can't be removed (locked, no
    permission) is logged after the file handler attaches and the app
    keeps starting."""
    d = logs_dir()
    try:
        entries = list(d.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == LOG_FILE_NAME:
            continue
        if not entry.is_file():
            continue
        try:
            entry.unlink()
        except OSError as exc:
            # Deferred: the file handler isn't attached yet, so we
            # stash the failure and log it after setup_logging finishes.
            _SWEEP_FAILURES.append((entry, str(exc)))


_SWEEP_FAILURES: list[tuple[Path, str]] = []


def _make_file_handler(level: int) -> logging.Handler:
    """Overwrite-on-launch file handler (Nelson 2026-07-02).

    Opens ``mira.log`` in ``mode='w'`` so every app run truncates the
    file — the log always contains just this session's output, no
    accumulation across launches. ``delay=False`` means the file is
    created immediately on handler attach, so a session that never
    emits anything still leaves an empty (but existing) log for
    ``App → Access log file`` to open. Retired the previous
    TimedRotatingFileHandler + the ``log_rotate_keep_days`` setting.
    """
    handler = logging.FileHandler(
        filename=str(log_file_path()),
        mode="w",
        encoding="utf-8",
        delay=False,
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def _make_console_handler(level: int) -> logging.Handler:
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    return handler


def _is_interactive_terminal() -> bool:
    """Return True when running in a real terminal (dev mode).

    In Nuitka onefile/onedir builds with --windows-console-mode=disable,
    stderr is not a tty, so console logging is skipped in production.
    """
    try:
        return sys.stderr.isatty()
    except (AttributeError, OSError):
        return False


def install_excepthook(*, dialog: bool = False) -> None:
    """Route uncaught Python exceptions through the root logger.

    Consolidates the two competing installs that used to live here and
    in ``mira/ui/app.py`` (Nelson 2026-07-02). PyQt6 invokes
    ``sys.excepthook`` when a slot raises and nothing catches it;
    default behaviour prints to stderr — invisible under
    ``pythonw.exe`` / a Nuitka windows-console-disabled build, which
    is exactly when silent crashes are hardest to diagnose. Routing
    through the logger means the traceback lands in ``mira.log``
    every time.

    ``dialog=True`` also shows a ``QMessageBox`` pointing the user at
    the log file — the friendly failure mode for production runs. Off
    by default so tests + headless callers don't reach for Qt.
    Pre-existing hook is preserved as fallback so debugger
    integrations keep working.
    """
    prior = sys.excepthook
    log = logging.getLogger("mira.unhandled")

    def _hook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        # KeyboardInterrupt should still propagate cleanly in dev.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        level = logging.CRITICAL if dialog else logging.ERROR
        log.log(
            level,
            "Unhandled exception: %s: %s",
            exc_type.__name__,
            exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
        )
        if dialog:
            try:
                from PyQt6.QtWidgets import QApplication, QMessageBox
                if QApplication.instance() is not None:
                    QMessageBox.critical(
                        None,
                        "Mira — unexpected error",
                        f"Mira hit an unexpected error and may be "
                        f"unstable. Please restart it.\n\n"
                        f"{exc_type.__name__}: {exc_value}\n\n"
                        f"Full details were written to:\n"
                        f"{log_file_path()}",
                    )
            except Exception:                                # noqa: BLE001
                # A dialog failure must not mask the crash.
                pass
        # Chain to prior hook (typically sys.__excepthook__) so dev
        # consoles still see the traceback.
        try:
            prior(exc_type, exc_value, exc_tb)
        except Exception:                                    # noqa: BLE001
            pass

    sys.excepthook = _hook


def setup_logging(
    level: str | None = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Initialize the root logger with file + optional console handlers.

    Args:
        level: Optional override for log level. If None, read from settings.
        force: If True, detach existing handlers before attaching new ones.
               Useful in tests that need to reconfigure logging mid-run.

    Returns:
        The configured root logger (also accessible as logging.getLogger()).

    Safe to call multiple times — subsequent calls are no-ops unless force=True.
    """
    global _HANDLERS_ATTACHED

    root = logging.getLogger()

    if _HANDLERS_ATTACHED and not force:
        return root

    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)

    if level is None:
        try:
            settings = load_settings()
            level = settings.get("log_level", "INFO")
        except Exception:  # noqa: BLE001 — settings load must never break logging
            level = "INFO"

    numeric_level = _resolve_level(level)
    root.setLevel(numeric_level)

    # Sweep the logs dir BEFORE attaching the handler so leftover
    # ``miracraft.log`` / one-off diagnostic files don't clutter the
    # directory. Failures (if any) are drained into the fresh log
    # immediately after the handler attaches.
    _SWEEP_FAILURES.clear()
    _sweep_stale_log_files()

    # File handler — always present
    try:
        root.addHandler(_make_file_handler(numeric_level))
    except (OSError, PermissionError) as exc:
        # If the log directory is read-only or inaccessible, degrade gracefully.
        # We still want console logging to work so the user sees SOMETHING.
        sys.stderr.write(f"[logging_setup] Could not create log file: {exc}\n")

    # Console handler — only when running interactively (dev mode)
    if _is_interactive_terminal():
        root.addHandler(_make_console_handler(numeric_level))

    # The excepthook install is caller-driven (spec/logging consolidation,
    # Nelson 2026-07-02): startup calls :func:`install_excepthook` with
    # ``dialog=True`` after Qt exists; tests / headless callers can
    # opt in without a dialog or skip the hook entirely.

    _HANDLERS_ATTACHED = True
    # Session-start marker (Nelson 2026-07-02). Emitted at INFO so it
    # always lands in the freshly-truncated log — otherwise a run whose
    # every log call is DEBUG-level with log_level=INFO would leave an
    # empty file, making "did the log capture anything?" ambiguous.
    from datetime import datetime as _dt
    root.info(
        "── Session started at %s (level=%s, file=%s) ──",
        _dt.now().isoformat(timespec="seconds"),
        logging.getLevelName(numeric_level),
        log_file_path(),
    )
    # Drain sweep failures (rare — locked / read-only files) into the
    # freshly-attached handler so the user sees which leftovers
    # couldn't be removed.
    for path, reason in _SWEEP_FAILURES:
        root.warning(
            "logs sweep: could not remove %s (%s) — the app owns this "
            "directory, please delete the file manually",
            path, reason,
        )
    _SWEEP_FAILURES.clear()
    return root


def reset_logging() -> None:
    """Detach all handlers and reset the initialization flag.

    Intended for tests that need a clean logging state between runs.
    """
    global _HANDLERS_ATTACHED
    root = logging.getLogger()
    for handler in list(root.handlers):
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
        root.removeHandler(handler)
    _HANDLERS_ATTACHED = False


@contextmanager
def log_activity(
    logger: logging.Logger,
    description: str,
) -> Iterator[None]:
    """Context manager that logs start, end, and exceptions for a block.

    On entry: DEBUG "Starting {description}..."
    On success: DEBUG "{description} completed in {elapsed:.3f}s"
    On exception: ERROR "{description} failed after {elapsed:.3f}s: {exc}"
                  then the exception is re-raised (not swallowed).

    The elapsed time uses time.perf_counter() for monotonic timing.

    Example:
        log = logging.getLogger(__name__)
        with log_activity(log, "loading brand profile"):
            profile = load_profile(...)
    """
    logger.debug("Starting %s...", description)
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        elapsed = time.perf_counter() - start
        logger.error(
            "%s failed after %.3fs: %s",
            description,
            elapsed,
            exc,
            exc_info=True,
        )
        raise
    else:
        elapsed = time.perf_counter() - start
        logger.debug("%s completed in %.3fs", description, elapsed)
