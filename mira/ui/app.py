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


def _install_excepthook(log: logging.Logger, log_path: Path) -> None:
    """Route uncaught Python exceptions to the log + a dialog.

    Without this, an unhandled exception — including one raised inside a Qt
    slot (PyQt6 funnels those through ``sys.excepthook`` and then aborts) —
    writes its traceback only to stderr. In the packaged build
    (``--windows-console-mode=disable``) there is no stderr, so the app dies
    silently and the crash is undiagnosable. This is exactly what hid the
    2026-06-17 ``database disk image is malformed`` crash. We log the full
    traceback to ``mira.log`` and, if a QApplication exists, show a dialog
    pointing at the log, then chain to the previous hook.
    """
    import traceback

    prev_hook = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            prev_hook(exc_type, exc, tb)
            return
        log.critical(
            "UNCAUGHT EXCEPTION\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        try:
            from PyQt6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    None,
                    "Mira — unexpected error",
                    f"Mira hit an unexpected error and may be unstable. "
                    f"Please restart it.\n\n"
                    f"{exc_type.__name__}: {exc}\n\n"
                    f"Full details were written to:\n{log_path}",
                )
        except Exception:  # noqa: BLE001 — a dialog failure must not mask the crash
            pass
        prev_hook(exc_type, exc, tb)

    sys.excepthook = hook


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


def _resolve_library_root(settings_obj, data_dir: Path) -> Path:
    """Where the library's writer lock lives (spec/76 §A.1 + §B.4).

    Resolution order (post spec/76 §B.4):

      1. :func:`mira.paths.library_root` — the bootstrap-pointer-driven
         user-chosen root, written by the first-run dialog. This is the
         primary path on every normal launch.
      2. Legacy fallback: ``settings.photos_base_path`` when set (older
         installs that haven't migrated through first-run yet — for
         instance the bootstrap path right before the dialog runs).
      3. ``data_dir`` (= :func:`mira.paths.user_data_dir`) — final
         fallback for the moments before any user choice exists, so the
         lock always has somewhere to live (never a hardcoded path —
         invariant #2).

    The lock file itself relocates inside the root per spec/76 §B.4:
    ``<root>/.mira/writer.lock`` (was ``<root>/.mira-writer.lock``); the
    move is owned by :mod:`core.library_lock`.
    """
    from mira.paths import library_root as _library_root_from_paths
    root = _library_root_from_paths()
    if root is not None:
        return root
    raw = getattr(settings_obj, "photos_base_path", "") or ""
    return Path(raw) if raw else data_dir


def _show_lock_conflict_dialog(holder) -> str:
    """Modal dialog when another Mira instance owns the writer lock.

    The spec/76 §A.4 contract: name the editing machine + the time
    they acquired, offer **Open read-only** (→ the §B.1 read-only
    session this app drops into) or **Cancel** (don't launch). No
    "Take over editing" button — ``core.library_lock.acquire`` auto-
    takes over stale locks before startup ever reaches this dialog
    (Nelson 2026-06-17 confirmation), so the button has no path to
    fire.

    Returns ``"read_only"`` when the user accepts read-only mode,
    ``"cancel"`` otherwise.
    """
    from mira.ui.design.dialogs import MessageDialog
    from mira.ui.i18n import tr
    msg = tr(
        "This library is open for editing on {host} (since {since}). "
        "Opening in read-only mode — decisions, edits, exports and "
        "plan changes will be disabled in this window until the other "
        "Mira closes."
    ).replace("{host}", holder.hostname).replace("{since}", holder.acquired_at)
    dlg = MessageDialog(
        intent="warning",
        title=tr("Library is in use"),
        message=msg,
        primary_text=tr("Open read-only"),
        ghost_text=tr("Cancel"),
    )
    dlg.exec()
    return "read_only" if dlg.result_kind() == "primary" else "cancel"


# spec/76 §A.2 — the heartbeat QTimer must outlive ``main()`` so the
# library lock stays fresh for the lifetime of the QApplication. A
# module-level reference keeps Qt's parent-less timer from being GC'd
# the moment ``main()`` returns to the caller (Python REPL, tests).
_LIBRARY_LOCK_HEARTBEAT = None


def main(argv: list[str] | None = None) -> int:
    import os as _os
    # Suppress child-process console windows BEFORE any other import
    # that might spawn at module load (e.g. core.video_extract triggers
    # imageio_ffmpeg.get_ffmpeg_exe() which probes ffmpeg -version via
    # plain subprocess.check_call). See core.proc docstring.
    from core.proc import install_window_suppression
    install_window_suppression()

    # Windows taskbar icon: without an explicit AppUserModelID, Windows
    # groups the app under the host interpreter and shows the generic
    # Python icon in the taskbar even when setWindowIcon() is set. Setting
    # an explicit AUMID makes Windows treat Mira as its own app and use the
    # window icon (from source) / the .exe icon (packaged). Must run before
    # any window is created. Harmless no-op off Windows / on failure.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "NelsonSalgado.Mira")
        except Exception:  # noqa: BLE001
            pass

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

    # Capture uncaught exceptions (incl. Qt-slot crashes) to the log + a
    # dialog, instead of dying silently in the windowed build.
    _install_excepthook(log, data_dir / "logs" / "mira.log")

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

    # Window/taskbar icon (shown in the title bar, taskbar, and Alt-Tab).
    # This is the RUNTIME icon and is separate from the .exe file icon,
    # which Nuitka stamps via --windows-icon-from-ico. Resolved the same
    # way as the themes (parents[2]/assets) so it works from source AND
    # from the Nuitka onefile (assets are bundled at the same relpath).
    from PyQt6.QtGui import QIcon
    _icon_path = (
        Path(__file__).resolve().parents[2]
        / "assets" / "icons" / "mira.ico"
    )
    if _icon_path.is_file():
        app.setWindowIcon(QIcon(str(_icon_path)))
    else:
        log.warning("App icon not found at %s", _icon_path)

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

    # spec/76 §B.4 — the bootstrap pointer (~/.config/mira/config.json or
    # %LOCALAPPDATA%\Mira\config.json) tells Mira where the library lives.
    # If it isn't set yet, show the two-doors first-run dialog BEFORE any
    # path-bound code runs: settings / events index / mira.db all live under
    # ``<library_root>/.mira/``, so they can't be loaded until the user picks
    # a root. Settings the user had in the legacy AppData dir get migrated
    # into the new ``.mira/`` as part of the Create flow.
    from mira.paths import library_root as _library_root_from_paths
    if _library_root_from_paths() is None:
        from PyQt6.QtWidgets import QDialog
        from mira.ui.wizard.first_run_library import FirstRunLibraryDialog
        dlg = FirstRunLibraryDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            log.info(
                "First-run library picker cancelled; aborting launch.")
            return 1
        if dlg.did_migrate_legacy():
            log.info(
                "First-run library: migrated legacy %%LOCALAPPDATA%% "
                "user-data into the new library's .mira/.")

    settings = SettingsRepo().load()

    # spec/76 §A — the library single-writer lock. Acquire at the
    # library root (post-§B.4, that's the bootstrap-pointer-driven path;
    # legacy fallbacks live in :func:`_resolve_library_root`). Replaces
    # the old PID-only running.lock — the advisory file + heartbeat
    # works on local disks AND the future NAS share; the old kill(pid,
    # 0) check did not.
    from core import library_lock
    from mira import session as mira_session
    library_root = _resolve_library_root(settings, data_dir)
    result = library_lock.acquire(library_root)
    if not result.acquired and result.holder is None:
        # Write failed (permissions, full disk, unreachable share) —
        # not a conflict, can't continue.
        from PyQt6.QtWidgets import QMessageBox
        log.warning(
            "Library writer lock could not be written at %s", library_root)
        QMessageBox.critical(
            None,
            "Library lock failed",
            f"Mira couldn't write its writer lock at {library_root}. "
            "Check permissions and free space, then try again.",
        )
        return 1

    read_only_mode = not result.acquired
    if read_only_mode:
        # spec/76 §B.1 — another live writer owns the lock. Open the
        # library read-only instead of declining: every mutation is
        # gated (gateway-level guard in ``EventGateway._touch()``;
        # surface-level consult in PickerPage / MainWindow menus) and
        # a persistent banner names the writer.
        holder = result.holder
        log.warning(
            "Library writer lock held by %s (pid %d) at %s — "
            "opening read-only.",
            holder.hostname, holder.pid, library_root,
        )
        # The interim Retry/Cancel dialog still shows; slice 3 replaces
        # it with the §A.4 "Open read-only / Cancel" contract. Cancel
        # still aborts launch; any other dismissal continues into the
        # read-only session.
        if _show_lock_conflict_dialog(holder) == "cancel":
            return 1
        mira_session.set_read_only(True, holder)
    else:
        log.info("Library writer lock acquired at %s", library_root)
        mira_session.set_read_only(False)

    # Heartbeat — keep the lock's mtime fresh so other machines /
    # processes know we're alive. The timer parent is the QApplication
    # so it lives as long as the event loop. Read-only sessions skip
    # this — we don't own the lock, so refreshing it would be wrong.
    global _LIBRARY_LOCK_HEARTBEAT
    if not read_only_mode:
        from PyQt6.QtCore import QTimer
        _LIBRARY_LOCK_HEARTBEAT = QTimer(app)
        _LIBRARY_LOCK_HEARTBEAT.setInterval(
            library_lock.HEARTBEAT_INTERVAL_SECONDS * 1000)

        def _heartbeat():
            if not library_lock.refresh(library_root):
                log.warning(
                    "Library writer lock heartbeat failed at %s — "
                    "lock may have been taken over.", library_root)
        _LIBRARY_LOCK_HEARTBEAT.timeout.connect(_heartbeat)
        _LIBRARY_LOCK_HEARTBEAT.start()

    # spec/76 §A.6 — release on EVERY exit path, not just the clean one.
    # The historical implementation hooked only ``aboutToQuit`` + the
    # post-``app.exec()`` belt-and-suspenders, both of which need a clean
    # Qt shutdown. A Python exception in a slot dies through
    # ``sys.excepthook → prev_hook`` and the lock leaks. Layer the
    # teardown so every shutdown path releases:
    #
    # 1. ``aboutToQuit`` — clean Qt quit (window close, Quit menu).
    # 2. ``atexit`` — fires on any interpreter exit, including uncaught
    #    Python exceptions and ``sys.exit`` (Nelson 2026-06-18 — the
    #    "library in use" problem after a paintEvent KeyError).
    # 3. ``sys.excepthook`` — release BEFORE the crash dialog so even
    #    if the user force-closes that dialog, the lock is gone.
    # 4. ``try/finally`` around ``app.exec()`` — defense in depth.
    #
    # ``release()`` is idempotent: it no-ops when the file is gone or
    # the holder doesn't match us, so duplicate calls are safe.
    _teardown_done = False

    def _teardown_library_lock(source: str = "aboutToQuit"):
        nonlocal _teardown_done
        if _teardown_done:
            return
        _teardown_done = True
        try:
            if _LIBRARY_LOCK_HEARTBEAT is not None:
                _LIBRARY_LOCK_HEARTBEAT.stop()
        except Exception:                                          # noqa: BLE001
            pass
        try:
            if library_lock.release(library_root):
                log.info(
                    "Library writer lock released at %s (%s)",
                    library_root, source)
        except Exception:                                          # noqa: BLE001
            log.exception(
                "library_lock release failed during %s teardown", source)

    app.aboutToQuit.connect(lambda: _teardown_library_lock("aboutToQuit"))

    if not read_only_mode:
        import atexit
        atexit.register(_teardown_library_lock, "atexit")
        # Chain the excepthook so an uncaught exception releases the
        # lock BEFORE the crash dialog appears (so a force-close of the
        # dialog still leaves a clean lock state).
        _prev_excepthook = sys.excepthook

        def _release_on_excepthook(exc_type, exc, tb):
            if not issubclass(exc_type, KeyboardInterrupt):
                _teardown_library_lock("excepthook")
            _prev_excepthook(exc_type, exc, tb)

        sys.excepthook = _release_on_excepthook

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
    code = 0
    try:
        code = app.exec()
        log.info("Event loop ended (exit code %d)", code)
        # 2026-06-18 corruption fix — run the clean-close path BEFORE the
        # QApplication + gateway are destroyed: drain the snapshot workers, then
        # WAL-checkpoint the user store, write its integrity sidecar, and rotate
        # a verified rolling backup. Historically NOTHING did this at exit, so
        # the protection layer never ran and the user store accumulated no
        # backups; an interrupted checkpoint of the hot ``global_items`` table
        # then corrupted it with nothing to restore from.
        try:
            window.shutdown()
        except Exception:                                          # noqa: BLE001
            log.exception("clean shutdown failed")
    finally:
        # Nelson 2026-06-18: the lock release MUST run no matter how
        # we leave the event loop — clean exit, exception bubbling
        # through ``app.exec()``, ``sys.exit`` inside a slot, anything.
        # ``_teardown_library_lock`` is idempotent so a double-release
        # via ``aboutToQuit`` + this ``finally`` is safe.
        _teardown_library_lock("finally")
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
