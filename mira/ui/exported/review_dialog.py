"""spec/159 — the Exported Collection review viewer.

Opens against a lineage row (one exported file), shows the actual
exported file's bytes through
:class:`~mira.ui.media.photo_viewport.PhotoViewport`, and surfaces the
per-version classification controls (Style + stars + colour label +
portfolio flag + "marked for deletion" toggle).

This is the spec/159 Session B viewer in its minimal-viable form.
Spec §5 calls for Editor reuse with a ``review_mode`` flag; this
implementation goes lighter — purpose-built chrome controls, no
creative-edit machinery — because the rating use case doesn't need
any of it. If Editor reuse turns out to be the better long-term
call, the DCDetailPage already routes through ``review_requested``
so the swap is local.

Keyboard map (spec/159 §5.4):

* ``1-5``        — set stars 1..5
* ``0``          — clear stars
* ``Shift+1..5`` — set colour label (red/yellow/green/blue/purple)
* ``Shift+0``    — clear colour label
* ``K``          — toggle portfolio flag
* ``D`` / ``Delete`` — toggle "marked for deletion"
* ``←/→``        — prev / next version
* ``F`` / ``F11`` — fullscreen
* ``Esc``        — close

Visual treatment lives in :mod:`mira.ui.exported.rating_widgets` —
custom-painted widgets so the dialog carries zero inline
``setStyleSheet`` (spec/92 §7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.design import ghost_button
from mira.ui.exported.rating_widgets import (
    COLOR_LABEL_ORDER,
    ColorLabelRow,
    DeleteToggle,
    FlagToggle,
    PreferredToggle,
    StarRow,
    StylePicker,
)
from mira.ui.i18n import tr

log = logging.getLogger(__name__)


@dataclass
class ReviewItem:
    """One exported lineage row exposed to the review dialog.

    Carries everything the chrome row needs to render + write back:
    the per-version ratings (``stars`` / ``color_label`` / ``flag`` /
    ``to_delete``) plus the per-item Style (``item_id`` /
    ``classification``). The dialog never reads back through the
    gateway — the host pushes a fresh list on open and the dialog
    emits signals on user change.
    """

    export_relpath: str
    abs_path: Path
    stars: Optional[int] = None
    color_label: Optional[str] = None
    flag: bool = False
    to_delete: bool = False
    title: str = ""
    #: Source item id (``lineage.source_item_id``) — needed to write
    #: Style classification through the gateway. ``None`` disables the
    #: Style picker for this row (defensive — shouldn't happen in the
    #: spec/159 flow but the schema allows ``source_item_id`` NULL on
    #: third-party returns).
    item_id: Optional[str] = None
    #: Current ``item.classification`` for the source item; ``None`` =
    #: unclassified (Style picker resolves to ``'general'``).
    classification: Optional[str] = None
    #: spec/159 §6+ — whether this row is currently marked as the
    #: preferred version of its source item.
    is_preferred: bool = False
    #: True when this row has at least one sibling lineage row for the
    #: same source item; controls the PreferredToggle's visibility
    #: (single-version cells are implicitly preferred and don't need
    #: the affordance).
    has_siblings: bool = False


class ReviewMediaDialog(QDialog):
    """spec/159 — review viewer for the Exported Collection."""

    #: ``(export_relpath, stars 1..5 or None)``
    stars_changed = pyqtSignal(str, object)
    #: ``(export_relpath, label or None)``
    color_label_changed = pyqtSignal(str, object)
    #: ``(export_relpath, bool)``
    flag_changed = pyqtSignal(str, bool)
    #: ``(export_relpath, bool)``
    to_delete_changed = pyqtSignal(str, bool)
    #: ``(item_id, classification key)`` — per-item; propagates across
    #: every version of that source item (spec/159 §2.2).
    classification_changed = pyqtSignal(str, str)
    #: ``(export_relpath, bool)`` — preferred-version toggle. The host
    #: routes through :meth:`EventGateway.set_lineage_preferred`, which
    #: also clears any sibling row's flag.
    preferred_changed = pyqtSignal(str, bool)

    def __init__(
        self,
        items: List[ReviewItem],
        start_index: int = 0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewMediaDialog")
        self.setWindowTitle(tr("Review exported file"))
        self.setModal(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._items = list(items)
        self._index = max(0, min(start_index, len(self._items) - 1))
        self._was_fullscreen = False
        self._first_show_pending = True
        # spec/159 (Nelson 2026-07-02) — mouse-wheel navigation.
        # ``PhotoViewport``'s own ``wheelEvent`` only walks its
        # internal item list (we hand it one item at a time), and a
        # native ``QVideoWidget`` on Windows swallows wheels sent to
        # the dialog. An app-level event filter installed while the
        # modal is up catches the wheel wherever it lands and routes
        # it into ``_go_prev`` / ``_go_next``. 120-unit accumulation
        # mirrors :class:`PhotoViewport` so mouse notches and high-
        # precision touchpad scrolls both feel consistent.
        self._wheel_units = 0
        self._app_filter_installed = False
        self._build_ui()
        # Push the chrome state now (the rating controls don't depend
        # on widget size); the photo viewport waits for the first show
        # so it samples its final geometry, not the pre-layout 0×0.
        # spec/159 viewport-race fix (Nelson 2026-06-30 round 2).
        self._refresh_chrome(self._current_or_blank())
        if parent is not None:
            geo = parent.geometry()
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        else:
            self.resize(1200, 800)

    # ── UI build ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        from mira.ui.media.photo_viewport import PhotoViewport

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        # ── top row: Back + Style + filename label ──────────────────
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self._back_btn = ghost_button(tr("← Back"))
        self._back_btn.setToolTip(tr("Close the review viewer (Esc)"))
        self._back_btn.clicked.connect(self.close)
        top.addWidget(self._back_btn)
        self._style_picker = StylePicker(self)
        self._style_picker.style_picked.connect(self._on_style_picked)
        top.addWidget(self._style_picker)
        self._title_lbl = QLabel("")
        self._title_lbl.setObjectName("Sub")
        self._title_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top.addWidget(self._title_lbl, stretch=1)
        # Counter ("3 / 12") on the far right of the top row so the
        # nav row at the bottom is just chevron buttons.
        self._counter = QLabel("")
        self._counter.setObjectName("PageHint")
        top.addWidget(self._counter)
        top_w = QWidget()
        top_w.setLayout(top)
        outer.addWidget(top_w)

        # ── chrome row: stars · colour · flag · delete ──────────────
        chrome = QHBoxLayout()
        chrome.setContentsMargins(0, 0, 0, 0)
        chrome.setSpacing(18)
        self._star_row = StarRow(self)
        self._star_row.value_changed.connect(self._on_stars_changed)
        chrome.addWidget(self._star_row)
        self._color_row = ColorLabelRow(self)
        self._color_row.value_changed.connect(self._on_color_changed)
        chrome.addWidget(self._color_row)
        self._flag_btn = FlagToggle(self)
        self._flag_btn.toggled.connect(self._on_flag_toggled)
        chrome.addWidget(self._flag_btn)
        # spec/159 §6+ — PreferredToggle sits between flag and delete.
        # Hidden when the row has no siblings (single-version cell).
        self._preferred_btn = PreferredToggle(self)
        self._preferred_btn.toggled.connect(self._on_preferred_toggled)
        chrome.addWidget(self._preferred_btn)
        chrome.addStretch(1)
        self._delete_btn = DeleteToggle(self)
        self._delete_btn.toggled.connect(self._on_delete_toggled)
        chrome.addWidget(self._delete_btn)
        chrome_w = QWidget()
        chrome_w.setLayout(chrome)
        outer.addWidget(chrome_w)

        # ── photo viewport ──────────────────────────────────────────
        self._viewport = PhotoViewport(self)
        self._viewport.set_corner_inspect_visible(False)
        self._viewport.set_lens_tools_visible(True)
        self._viewport.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        outer.addWidget(self._viewport, 1)

        # ── nav row (bottom) ────────────────────────────────────────
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self._prev_btn = ghost_button("← " + tr("Previous"))
        self._prev_btn.clicked.connect(self._go_prev)
        nav.addWidget(self._prev_btn)
        nav.addStretch(1)
        self._next_btn = ghost_button(tr("Next") + " →")
        self._next_btn.clicked.connect(self._go_next)
        nav.addWidget(self._next_btn)
        nav_w = QWidget()
        nav_w.setLayout(nav)
        outer.addWidget(nav_w)

        # ── keyboard shortcuts ──────────────────────────────────────
        # ApplicationShortcut context (Nelson 2026-07-02) — the default
        # WindowShortcut misses key events when :class:`PhotoViewport`'s
        # embedded ``QVideoWidget`` (a native HWND on Windows) has
        # focus: key events land in the native window and never reach
        # Qt's shortcut manager. Application-scoped shortcuts fire on
        # every keystroke while any Mira window is active, and the
        # dialog is modal so no other Mira surface can steal them.
        app_ctx = Qt.ShortcutContext.ApplicationShortcut
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self.close, context=app_ctx)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=self._go_prev, context=app_ctx)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=self._go_next, context=app_ctx)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self,
                  activated=self._toggle_fullscreen, context=app_ctx)
        QShortcut(QKeySequence(Qt.Key.Key_F), self,
                  activated=self._toggle_fullscreen, context=app_ctx)
        # K → flag toggle
        QShortcut(QKeySequence(Qt.Key.Key_K), self,
                  activated=lambda: self._on_flag_toggled(
                      not self._current_or_blank().flag),
                  context=app_ctx)
        # D → to-delete toggle
        QShortcut(QKeySequence(Qt.Key.Key_D), self,
                  activated=lambda: self._on_delete_toggled(
                      not self._current_or_blank().to_delete),
                  context=app_ctx)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self,
                  activated=lambda: self._on_delete_toggled(
                      not self._current_or_blank().to_delete),
                  context=app_ctx)

        # Number keys → stars / colour label. QShortcut (rather than
        # keyPressEvent) so the ApplicationShortcut context reaches
        # keystrokes even while a native ``QVideoWidget`` has focus.
        for n in range(1, 6):
            QShortcut(QKeySequence(str(n)), self,
                      activated=lambda n=n: self._set_stars_from_key(n),
                      context=app_ctx)
            QShortcut(QKeySequence(f"Shift+{n}"), self,
                      activated=lambda n=n: self._set_color_from_key(
                          COLOR_LABEL_ORDER[n - 1]),
                      context=app_ctx)
        QShortcut(QKeySequence("0"), self,
                  activated=lambda: self._set_stars_from_key(None),
                  context=app_ctx)
        QShortcut(QKeySequence("Shift+0"), self,
                  activated=lambda: self._set_color_from_key(None),
                  context=app_ctx)

    # ── helpers ─────────────────────────────────────────────────────

    def _current_or_blank(self) -> ReviewItem:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return ReviewItem("", Path("."))

    def _load_index(self) -> None:
        """Show the media at ``self._index`` and repaint the chrome so
        the rating widgets reflect the current item's values.

        Video exports (spec/56 clips, spec/138 exports) route through
        :class:`PhotoViewport`'s ``kind='video'`` branch (Nelson
        2026-07-02) — QMediaPlayer + QVideoWidget play the clip inline
        with the rating chrome intact. Photos keep the ``kind='photo'``
        pixmap path unchanged.
        """
        from core.video_discovery import VIDEO_EXTENSIONS
        from mira.ui.media.photo_viewport import ViewportItem

        if not self._items:
            return
        idx = self._index
        item = self._items[idx]
        kind = (
            "video"
            if item.abs_path.suffix.lower() in VIDEO_EXTENSIONS
            else "photo"
        )
        self._viewport.set_items(
            [ViewportItem(path=item.abs_path, kind=kind)],
            current=0,
        )
        self._counter.setText(f"{idx + 1} / {len(self._items)}")
        self._title_lbl.setText(item.export_relpath)
        self._refresh_chrome(item)
        self._prev_btn.setEnabled(idx > 0)
        self._next_btn.setEnabled(idx < len(self._items) - 1)

    def _refresh_chrome(self, item: ReviewItem) -> None:
        """Push the row's current values onto every chrome widget
        without firing any user-input signal (each setter blocks)."""
        self._star_row.setValue(item.stars)
        self._color_row.setValue(item.color_label)
        self._flag_btn.setValue(item.flag)
        self._delete_btn.setValue(item.to_delete)
        # Style picker: disable when there's no source item to write to.
        if item.item_id:
            self._style_picker.setEnabled(True)
            self._style_picker.setStyle(item.classification)
        else:
            self._style_picker.setStyle(item.classification)
            self._style_picker.setEnabled(False)
        # spec/159 §6+ — hide the Preferred toggle on single-version
        # cells (there's no other version to compete with, so the
        # "use this" concept is moot).
        self._preferred_btn.setValue(item.is_preferred)
        self._preferred_btn.setVisible(item.has_siblings)

    # ── click / change handlers ────────────────────────────────────

    def _on_stars_changed(self, value) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        item.stars = value
        self.stars_changed.emit(item.export_relpath, value)

    def _on_color_changed(self, value) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        item.color_label = value
        self.color_label_changed.emit(item.export_relpath, value)

    def _on_flag_toggled(self, on: bool) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        on = bool(on)
        item.flag = on
        # Keep the widget in sync when the trigger was the keyboard
        # shortcut rather than the widget's own mousePressEvent.
        if self._flag_btn.value() != on:
            self._flag_btn.setValue(on)
        self.flag_changed.emit(item.export_relpath, on)

    def _on_delete_toggled(self, on: bool) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        on = bool(on)
        item.to_delete = on
        if self._delete_btn.value() != on:
            self._delete_btn.setValue(on)
        self.to_delete_changed.emit(item.export_relpath, on)

    def _on_preferred_toggled(self, on: bool) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        on = bool(on)
        item.is_preferred = on
        # spec/159 §6+ — at most one preferred per source item. When
        # we set this one ON, clear the cached flag on every sibling
        # in the dialog's loaded list so ←/→ to a sibling reads the
        # right state without the host round-tripping the gateway.
        if on and item.item_id:
            for other in self._items:
                if (other.item_id == item.item_id
                        and other.export_relpath != item.export_relpath):
                    other.is_preferred = False
        if self._preferred_btn.value() != on:
            self._preferred_btn.setValue(on)
        self.preferred_changed.emit(item.export_relpath, on)

    def _on_style_picked(self, key: str) -> None:
        item = self._current_or_blank()
        if not item.export_relpath or not item.item_id:
            return
        if not key:
            return
        item.classification = key
        # spec/159 §2.2 — classification is per-item; every loaded
        # version of the same source item carries the new value so the
        # next ←/→ visit reads it without round-tripping the gateway.
        for other in self._items:
            if other.item_id and other.item_id == item.item_id:
                other.classification = key
        self.classification_changed.emit(item.item_id, key)

    # ── nav ────────────────────────────────────────────────────────

    def _go_prev(self) -> None:
        if self._index <= 0:
            return
        self._index -= 1
        self._load_index()

    def _go_next(self) -> None:
        if self._index >= len(self._items) - 1:
            return
        self._index += 1
        self._load_index()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self._was_fullscreen = False
        else:
            self.showFullScreen()
            self._was_fullscreen = True

    # ── first-show fit ─────────────────────────────────────────────

    def showEvent(self, ev) -> None:  # noqa: N802 — Qt
        """Hand the photo to the viewport once the dialog has been
        laid out at its final size.

        Nelson 2026-06-30 — earlier the photo sometimes paints small
        in the centre until any later event triggers a re-fit. Cause:
        ``set_items`` ran in ``__init__`` while the viewport was still
        at its zero-default size, so the request issued at that small
        target and the pixmap landed at the small target. A simple
        ``QTimer.singleShot(0, refresh_current)`` after ``showEvent``
        was unreliable on Windows — Qt's layout pass sometimes runs
        after the 0ms post. The robust fix is to skip ``set_items``
        in ``__init__`` entirely and call ``_load_index`` only after
        the first ``showEvent`` plus one event-loop turn (so layout
        has settled). Re-entries (the dialog gets hidden + shown
        again) are no-ops — we don't want to reset the viewport on
        every show.
        """
        super().showEvent(ev)
        if not self._app_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.installEventFilter(self)
                self._app_filter_installed = True
        if self._first_show_pending:
            self._first_show_pending = False
            QTimer.singleShot(0, self._first_show_load)

    def closeEvent(self, ev) -> None:  # noqa: N802 — Qt
        """Uninstall the app-level wheel filter on close so it doesn't
        leak into other Mira surfaces after the modal dismisses."""
        if self._app_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_filter_installed = False
        super().closeEvent(ev)

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt
        """Route wheel events anywhere in the modal into prev / next.

        Accumulates 120 units per notch (mirrors
        :class:`~mira.ui.media.photo_viewport.PhotoViewport`) so a
        single mouse-wheel click walks one item and a high-precision
        touchpad scrolls smoothly. Photos and videos both go through
        this path — the native ``QVideoWidget`` swallows key + wheel
        events sent to the dialog otherwise (Windows HWND focus)."""
        if ev.type() == QEvent.Type.Wheel and self.isVisible():
            self._wheel_units += ev.angleDelta().y()
            while self._wheel_units >= 120:
                self._wheel_units -= 120
                self._go_prev()
            while self._wheel_units <= -120:
                self._wheel_units += 120
                self._go_next()
            return True
        return super().eventFilter(obj, ev)

    def _first_show_load(self) -> None:
        try:
            self._load_index()
        except Exception:                                      # noqa: BLE001
            log.exception("ReviewMediaDialog: first-show load failed")

    # spec/159 (Nelson 2026-07-02) — the previous ``keyPressEvent``
    # number-key handler was replaced by the ApplicationShortcut
    # QShortcuts above. Native ``QVideoWidget`` focus meant
    # ``keyPressEvent`` never fired for photos-inside-videos runs; the
    # shortcut route is uniform for both photo and video items.

    def _set_stars_from_key(self, n: Optional[int]) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        # LRC convention: same-N clears.
        if n is not None and item.stars == n:
            n = None
        item.stars = n
        self._star_row.setValue(n)
        self.stars_changed.emit(item.export_relpath, n)

    def _set_color_from_key(self, label: Optional[str]) -> None:
        item = self._current_or_blank()
        if not item.export_relpath:
            return
        if label is not None and item.color_label == label:
            label = None
        item.color_label = label
        self._color_row.setValue(label)
        self.color_label_changed.emit(item.export_relpath, label)


__all__ = ["ReviewItem", "ReviewMediaDialog"]
