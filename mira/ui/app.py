"""Application entry point for the new (clean-rebuild) UI.

    python -m mira.ui            # the app
    python -m mira.ui --dark     # force the dark theme (overrides the setting)

Initialise logging → load settings (Domain 5, the new ``mira.settings``) → create the
QApplication → apply the active theme → open the main window → run the event loop. Binds to
the gateway only (charter §2). No legacy ``ui/`` imports.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def _ensure_project_root_on_path() -> None:
    """Allow ``python mira/ui/app.py`` and bare launches to resolve ``mira.*``
    and the reused legacy ``core.*`` engines."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_ensure_project_root_on_path()

from PyQt6.QtWidgets import QApplication  # noqa: E402  (after path setup)

APP_NAME = "Mira"
ORG_NAME = "Mira"


def _setup_logging(data_dir: Path) -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "mira.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger("mira")
    log.setLevel(logging.INFO)
    return log


# Keeps the installed Qt message handler alive for the process lifetime
# (a garbage-collected handler would leave Qt calling into freed memory).
_QT_MESSAGE_HANDLER = None


def _quieten_ffmpeg_logging() -> None:
    """Disable the ``qt.multimedia.ffmpeg`` logging *category*.

    This silences the messages Qt's multimedia backend routes through
    its own logging category — chiefly the ``qt.multimedia.ffmpeg:
    Using Qt multimedia with FFmpeg version …`` banner. It does NOT
    touch FFmpeg's *own* demuxer chatter (the ``[mov,mp4…] Missing key
    frame …`` edit-list grumbles + the per-clip ``Input #0 …`` dump):
    that comes from libav's default callback writing straight to stderr,
    and is handled separately by :func:`_silence_libav_stderr`.

    Appends to any pre-existing ``QT_LOGGING_RULES`` rather than
    clobbering it, and stands down entirely if a ``qt.multimedia.ffmpeg``
    rule is already present (so a deliberate ``…=true`` set for debugging
    wins). Must run before the QApplication is constructed — the rule is
    read when Qt's logging registry initialises.
    """
    import os

    existing = os.environ.get("QT_LOGGING_RULES", "").strip()
    if "qt.multimedia.ffmpeg" in existing:
        return
    rule = "qt.multimedia.ffmpeg=false"
    os.environ["QT_LOGGING_RULES"] = (
        f"{existing};{rule}" if existing else rule
    )


def _install_qt_message_handler() -> None:
    """Funnel Qt's own log stream into ``mira.log`` and drop one
    known-benign cascade.

    Qt messages (``qWarning`` / ``qCritical`` from widgets and the
    multimedia backend) otherwise bypass Python logging entirely — they
    reach only the console, never the log file, so real Qt warnings are
    invisible when debugging after the fact. This routes them through
    the ``mira.qt`` logger (console + file, like everything else)
    while filtering the harmless ``QObject::disconnect: wildcard call
    disconnects from destroyed signal of QFFmpeg::…`` lines Qt's FFmpeg
    backend emits as it tears down the decode pipeline on every video
    source change.
    """
    global _QT_MESSAGE_HANDLER
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    qt_log = logging.getLogger("mira.qt")
    benign = (
        "wildcard call disconnects from destroyed signal of QFFmpeg::",
        "Missing key frame while searching for timestamp",
        "Cannot find an index entry before timestamp",
    )
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(msg_type, context, message) -> None:
        if message and any(tok in message for tok in benign):
            return
        qt_log.log(level.get(msg_type, logging.WARNING), "%s", message)

    _QT_MESSAGE_HANDLER = handler
    qInstallMessageHandler(handler)


def _silence_libav_stderr() -> None:
    """Set FFmpeg's own log level to QUIET so the bundled libav stops
    writing demuxer chatter straight to the process's stderr.

    The ``[mov,mp4…] Missing key frame …`` edit-list grumbles and the
    per-clip ``Input #0 …`` dump come from FFmpeg's *default* ``av_log``
    callback (the bright-red ANSI lines), which writes to fd 2 directly
    — it never passes through Qt's logging category or our message
    handler, so neither :func:`_quieten_ffmpeg_logging` nor the handler
    can reach it. The only lever is libav's own ``av_log_set_level``.

    We resolve the exact ``avutil`` DLL Qt's multimedia backend loads
    (Windows dedupes shared libraries by name, so the level we set is
    the one Qt's FFmpeg sees) and call ``av_log_set_level(AV_LOG_QUIET)``.
    Best-effort and Windows-only: any failure is swallowed — a noisy
    console is a far smaller problem than a failed launch. Genuine decode
    failures still reach the user through ``QMediaPlayer.errorOccurred``
    (→ ``PickerPage`` video error display, independent of this, so
    QUIET hides nothing the user needs. Raise the level to ``16``
    (``AV_LOG_ERROR``) here if you want real FFmpeg errors back on the
    console while debugging.

    Must run after the QApplication exists (so Qt's DLL directory is on
    the search path) but before the first video is opened.
    """
    if sys.platform != "win32":
        return
    import ctypes
    import glob

    AV_LOG_QUIET = -8
    candidates: list[str] = []
    try:
        import PyQt6
        qt_bin = Path(PyQt6.__file__).resolve().parent / "Qt6" / "bin"
        candidates.extend(sorted(glob.glob(str(qt_bin / "avutil-*.dll"))))
    except Exception:  # noqa: BLE001 — fall through to bare names
        pass
    # Bare names resolve via the DLL search path PyQt6 sets up on import;
    # a fallback for layouts where the glob above finds nothing.
    candidates.extend(
        ["avutil-59", "avutil-60", "avutil-58", "avutil-57", "avutil-56",
         "avutil"]
    )
    log = logging.getLogger("mira")
    for cand in candidates:
        try:
            lib = ctypes.WinDLL(cand)
        except OSError:
            continue
        fn = getattr(lib, "av_log_set_level", None)
        if fn is None:
            continue
        try:
            fn.argtypes = [ctypes.c_int]
            fn.restype = None
            fn(AV_LOG_QUIET)
        except Exception:  # noqa: BLE001
            continue
        log.debug("libav stderr silenced via %s", cand)
        return
    log.debug("libav stderr: no avutil found to silence (harmless)")


def _acquire_instance_lock(data_dir: Path) -> bool:
    """Write a PID lockfile; return False if another live instance is detected.

    Uses a plain text file containing the owner PID.  On the next launch we
    check whether that PID is still alive — if not the lock is stale (crash)
    and we take over.  Race conditions are acceptable for a single-user desktop
    app: the window between read-PID and write-PID is milliseconds."""
    import os

    lock = data_dir / "running.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)  # raises OSError if the process is dead
            return False     # process is alive — another instance is running
        except (ValueError, OSError):
            pass             # stale lock from a previous crash — ignore
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_instance_lock(data_dir: Path) -> None:
    try:
        (data_dir / "running.lock").unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    import os as _os
    # Suppress child-process console windows BEFORE any other import
    # that might spawn at module load (e.g. core.video_extract triggers
    # imageio_ffmpeg.get_ffmpeg_exe() which probes ffmpeg -version via
    # plain subprocess.check_call). See core.proc docstring.
    from core.proc import install_window_suppression
    install_window_suppression()

    from mira.paths import migrate_legacy_user_data, user_data_dir
    from mira.settings.repo import SettingsRepo
    from mira.ui.theme import apply_theme

    # One-shot copy from the legacy %LOCALAPPDATA%\Miracraft\ dir if this is
    # the first run after the MiraCrafter -> Mira rename. No-op once the new
    # dir has any content; non-destructive (legacy dir left in place so the
    # XMC branch's older binary can still launch against it). See paths.py.
    migrate_legacy_user_data()

    data_dir = user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    log = _setup_logging(data_dir)
    log.info("Mira (new UI) starting up")

    # Quieten the multimedia backend's per-clip console chatter (see
    # each helper's docstring). Both must run before the QApplication
    # exists: the env-var rule is read at Qt logging init, and the
    # message handler should be in place before any Qt warning fires.
    _quieten_ffmpeg_logging()
    _install_qt_message_handler()

    argv = list(sys.argv if argv is None else argv)
    force_dark = "--dark" in argv
    qt_argv = [a for a in argv if a != "--dark"]

    # High-DPI policy (spec/05 — layout robustness, target 1920×1080 @ 125–150% Windows
    # scaling). MUST be set before the QApplication exists. PassThrough renders fractional
    # scale factors (1.25/1.5) proportionally instead of rounding them to integers — the
    # rounding is what makes chrome jump / overflow at 125–150%. Guarded so the test qapp
    # fixture (which creates its own QApplication) isn't disturbed.
    if QApplication.instance() is None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication.instance() or QApplication(qt_argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)

    # Qt 6 caps image allocations at 256 MB by default — too small for high-res
    # camera files (e.g. a 45 MP RAW thumbnail can exceed that uncompressed).
    # 0 = no limit; appropriate for a dedicated photography tool.
    from PyQt6.QtGui import QImageReader
    QImageReader.setAllocationLimit(0)

    # Silence libav's direct-to-stderr demuxer chatter (the bright-red
    # `[mov,mp4…] Missing key frame …` + `Input #0 …` lines every video
    # browse prints). Native av_log, so it must be muted via FFmpeg's own
    # level — after the QApplication exists, before the first clip opens.
    _silence_libav_stderr()

    # Single-instance guard — must come after QApplication exists so we can
    # show a message box if needed, but before the main window is built.
    if not _acquire_instance_lock(data_dir):
        from PyQt6.QtWidgets import QMessageBox
        log.warning("Another Mira instance (PID in running.lock) is already running")
        QMessageBox.warning(
            None,
            "Mira already open",
            "Mira is already running.\n\nCheck the taskbar and bring that window to the front.",
        )
        return 1

    settings = SettingsRepo().load()
    theme = "dark" if force_dark else settings.theme
    if theme not in ("light", "dark"):
        log.warning("Unknown theme %r in settings — falling back to light", theme)
        theme = "light"
    apply_theme(app, theme)  # type: ignore[arg-type]
    # Nelson 2026-06-09 — font_scale was defined in the model but never
    # applied. Apply it on startup; MainWindow re-applies when the user
    # changes the setting via the dialog.
    apply_font_scale(app, getattr(settings, "font_scale", 1.0))

    from mira.ui.shell.main_window import MainWindow

    window = MainWindow()
    window.show()

    # First-run wizard auto-show. Without it, every photo classifies as
    # General — the wizard generates the per-user scenarios that feed the
    # classifier. Deferred 50ms so the main window has fully painted
    # before the modal appears (mirrors the legacy ui/app.py approach).
    from core.wizard import is_wizard_completed
    if not is_wizard_completed():
        log.info("Wizard not yet completed; opening on first run")
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, window._open_wizard)

    log.info("Event loop starting")
    code = app.exec()
    log.info("Event loop ended (exit code %d)", code)
    _release_instance_lock(data_dir)
    return code


def apply_font_scale(app, scale: float) -> None:
    """Apply a global font scale multiplier to the QApplication default
    font (Nelson 2026-06-09 — small-laptop-screen lifesaver knob).

    Scales ``pointSizeF`` against the current platform default so the
    knob composes with whatever Qt's system default is. Clamped to a
    sane band (0.5×–2.0×) so a malformed settings file can't render
    the UI unusable. Widgets that override their own font via
    ``setStyleSheet`` or explicit ``setFont`` are unaffected — those
    are the targets of the follow-up sweep to thread font_scale into
    the explicit font-size literals (status_breakdown etc.)."""
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    s = max(0.5, min(2.0, s))
    font = app.font()
    base_pt = font.pointSizeF()
    if base_pt <= 0:
        # Some platforms report pixelSize instead — fall back to a known
        # baseline (Qt's typical default is ~9pt on Windows).
        base_pt = 9.0
    new_pt = base_pt * s
    # Store the un-scaled baseline as a dynamic property so subsequent
    # apply_font_scale() calls scale against the platform baseline, not
    # against the already-scaled value.
    cached_base = app.property("_font_baseline_pt")
    if cached_base is None:
        app.setProperty("_font_baseline_pt", float(base_pt))
    else:
        new_pt = float(cached_base) * s
    font.setPointSizeF(new_pt)
    app.setFont(font)


if __name__ == "__main__":
    raise SystemExit(main())
