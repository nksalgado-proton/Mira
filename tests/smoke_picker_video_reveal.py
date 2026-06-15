"""Real-asset smoke: the unified Picker, sweeping
portrait photo → landscape photo → video. Confirms the blurred-fill
backdrop on every item and the inline transport reveal on the video.

Drives :class:`PickerPage` directly (no event gateway, no MainWindow)
with a synthetic bucket of three real items. Saves three PNGs side-by-
side for an eyeball check.

Usage:
    python tests/smoke_picker_video_reveal.py <portrait.jpg> <landscape.jpg> <video.mp4>
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
    if len(sys.argv) < 4:
        print("usage: smoke_picker_video_reveal.py "
              "<portrait.jpg> <landscape.jpg> <video.mp4>")
        return 2
    portrait = Path(sys.argv[1])
    landscape = Path(sys.argv[2])
    video = Path(sys.argv[3])
    for p in (portrait, landscape, video):
        if not p.exists():
            print(f"missing: {p}")
            return 2
    theme = sys.argv[4] if len(sys.argv) >= 5 else "dark"

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, theme)

    tmp = Path("_smoke_tmp_reveal")
    tmp.mkdir(exist_ok=True)
    settings = SettingsRepo(tmp / "settings.json")
    index = EventsIndex(tmp / "events_index.json")
    gw = Gateway(settings=settings, index=index)

    page = PickerPage(gw)
    page.resize(1280, 820)

    payloads = [
        _ci("portrait", "photo", portrait),
        _ci("landscape", "photo", landscape),
        _ci("video", "video", video),
    ]
    page._items = list(payloads)
    page._state = {ci.item_id: None for ci in payloads}
    vitems = [
        ViewportItem(path=ci.path, kind=ci.kind, payload=ci)
        for ci in payloads
    ]
    page.viewport.set_items(vitems, 0)              # land on the portrait
    page.show()

    step = [0]
    targets = [
        (f"_smoke_picker_portrait_{theme}.png", 0),
        (f"_smoke_picker_landscape_{theme}.png", 1),
        (f"_smoke_picker_video_{theme}.png", 2),
    ]

    def _tick() -> None:
        i = step[0]
        out, idx = targets[i]
        page.viewport.show_index(idx)

        def _shoot() -> None:
            page.grab().save(out, "PNG")
            print(f"saved: {Path(out).resolve()} "
                  f"(compact_row={page._surface.compact_row.isVisible()}, "
                  f"transport={page._transport_bar.isVisible()})")
            step[0] += 1
            if step[0] < len(targets):
                QTimer.singleShot(1200, _tick)
            else:
                app.quit()

        QTimer.singleShot(900, _shoot)

    QTimer.singleShot(900, _tick)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
