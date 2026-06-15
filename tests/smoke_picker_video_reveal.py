"""Real-asset smoke: the unified Picker, photo→video sweep with the
inline transport reveal.

Drives :class:`PickerPage` directly (no event gateway, no MainWindow)
with a synthetic bucket of one real photo + one real video. Lands on
the photo (transport row hidden, canvas takes the page), then steps to
the video (transport row appears, viewport arms the player). Saves two
PNGs side-by-side for an eyeball check.

Usage:
    python tests/smoke_picker_video_reveal.py <photo.jpg> <video.mp4>
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo
from mira.ui.media.photo_viewport import ViewportItem
from mira.ui.pages.picker_page import PickerPage
from mira.ui.theme import apply_theme


def _ci(item_id: str, kind: str, path: Path) -> SimpleNamespace:
    return SimpleNamespace(item_id=item_id, path=path, kind=kind)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: smoke_picker_video_reveal.py <photo.jpg> <video.mp4>")
        return 2
    photo = Path(sys.argv[1])
    video = Path(sys.argv[2])
    if not photo.exists() or not video.exists():
        print(f"missing: {photo if not photo.exists() else video}")
        return 2

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, "dark")

    tmp = Path("_smoke_tmp_reveal")
    tmp.mkdir(exist_ok=True)
    settings = SettingsRepo(tmp / "settings.json")
    index = EventsIndex(tmp / "events_index.json")
    gw = Gateway(settings=settings, index=index)

    page = PickerPage(gw)
    page.resize(1280, 820)

    payloads = [
        _ci("p1", "photo", photo),
        _ci("v1", "video", video),
    ]
    page._items = list(payloads)
    page._state = {ci.item_id: None for ci in payloads}
    vitems = [
        ViewportItem(path=ci.path, kind=ci.kind, payload=ci)
        for ci in payloads
    ]
    page.viewport.set_items(vitems, 0)              # land on the photo
    page.show()

    state = {"step": 0}

    def _tick() -> None:
        s = state["step"]
        if s == 0:
            out = Path("_smoke_picker_photo.png")
            page.grab().save(str(out), "PNG")
            print(f"saved: {out.resolve()} "
                  f"(compact_row visible={page._surface.compact_row.isVisible()})")
            page.viewport.show_index(1)             # cross to video
            state["step"] = 1
            QTimer.singleShot(1500, _tick)
        else:
            out = Path("_smoke_picker_video.png")
            page.grab().save(str(out), "PNG")
            print(f"saved: {out.resolve()} "
                  f"(compact_row visible={page._surface.compact_row.isVisible()})")
            app.quit()

    QTimer.singleShot(1200, _tick)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
