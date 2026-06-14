"""Application logging setup and helpers.

Call setup_logging() once at startup (from ui/app.py) to initialize the
rotating file handler and apply the user's configured log level.

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

Log file location: {user_data_dir}/logs/mira.log (daily rotation, 14 days kept).

Troubleshooting a silent crash:
  1. Open settings.json, set "log_level": "DEBUG"
  2. Re-run the app, reproduce the problem
  3. Open {user_data_dir}/logs/mira.log — search for ERROR lines
"""

import logging
import logging.handlers
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.settings import load_settings, user_data_dir


LOG_FILE_NAME = "mira.log"
LOG_ROTATE_KEEP_DAYS = 14
LOG_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_VALID_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Sentinel to avoid attaching handlers twice if setup_logging() is called more
# than once (e.g. during tests that import ui/app.py).
_HANDLERS_ATTACHED = False


def logs_dir() -> Path:
    """Directory where log files are stored."""
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


def _make_file_handler(level: int) -> logging.Handler:
    # Read the user-tunable retention setting (Nelson 2026-06-09 audit);
    # fall back to LOG_ROTATE_KEEP_DAYS if Settings can't be read.
    try:
        from mira.settings.repo import SettingsRepo
        keep_days = int(SettingsRepo().load().log_rotate_keep_days)
    except Exception:                                           # noqa: BLE001
        keep_days = LOG_ROTATE_KEEP_DAYS
    handler = logging.handlers.TimedRotatingFileHandler(
        filename=str(log_file_path()),
        when="midnight",
        interval=1,
        backupCount=max(1, keep_days),
        encoding="utf-8",
        delay=True,  # don't create empty file until first emit
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


def _install_excepthook() -> None:
    """Route uncaught Python exceptions through the root logger.

    PyQt6 invokes ``sys.excepthook`` when a slot raises and nothing
    catches it. Default behaviour prints to stderr — invisible under
    ``pythonw.exe`` / a Nuitka windows-console-disabled build, which
    is exactly when silent crashes are hardest to diagnose. Routing
    through the logger means the traceback lands in ``mira.log``
    every time.

    Pre-existing hook is preserved as fallback so we don't break
    debugger integrations.
    """
    prior = sys.excepthook
    log = logging.getLogger("mira.unhandled")

    def _hook(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
        # KeyboardInterrupt should still propagate cleanly in dev.
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error(
            "Unhandled exception: %s: %s",
            exc_type.__name__,
            exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
        )
        # Chain to prior hook (typically sys.__excepthook__) so dev
        # consoles still see the traceback.
        try:
            prior(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
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

    # Route uncaught exceptions (Qt-slot crashes especially) through
    # the file logger so silent crashes leave a paper trail.
    _install_excepthook()

    _HANDLERS_ATTACHED = True
    root.debug(
        "Logging initialized: level=%s, file=%s",
        logging.getLevelName(numeric_level),
        log_file_path(),
    )
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
