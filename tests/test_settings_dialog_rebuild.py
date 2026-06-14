"""Tests for the reused Settings dialog in the new app (charter §5.2 data rewire).

The dialog is the legacy `SettingsDialog` ported into `mira/ui/`; the only change is
its load/save going through `mira.settings` (the isolated `settings.rebuild.json`).
These pin the data seam + the host's reaction to a base-path change (charter §5.9).
"""
from __future__ import annotations

from mira.gateway import EventsIndex, Gateway
from mira.settings.repo import SettingsRepo


def test_dialog_loads_and_persists_through_mira_settings(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    from mira.ui.base.settings_dialog import SettingsDialog

    SettingsRepo().update(photos_base_path=r"D:\Photos\_mira", theme="light")
    dlg = SettingsDialog()

    base_field = next(b for b in dlg._bindings if b.key == "photos_base_path")
    assert base_field.read() == r"D:\Photos\_mira"

    captured: dict = {}
    dlg.changes_applied = lambda ch: captured.update(ch)  # type: ignore[method-assign]
    theme_field = next(b for b in dlg._bindings if b.key == "theme")
    theme_field.write("dark")
    dlg._on_apply()

    assert SettingsRepo().load().theme == "dark"          # persisted to the isolated file
    assert "theme" in captured                            # host was notified


def test_main_window_settings_change_reanchors_base(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    from mira.ui.shell.main_window import MainWindow

    gw = Gateway(
        settings=SettingsRepo(tmp_path / "s.json"),
        index=EventsIndex(tmp_path / "i.json"),
    )
    win = MainWindow(gateway=gw)
    new_base = tmp_path / "lib"
    win._on_settings_changed({"photos_base_path": ("", str(new_base))})
    assert gw.photos_base_path() == new_base  # one edit re-anchors the whole library


def test_apply_veto_aborts_without_persisting(qapp, tmp_path, monkeypatch):
    """A veto from validate_changes aborts the Apply: nothing is saved, the host is not
    notified, and the user is warned (charter §5.9 base-change guard)."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    from mira.ui.base import settings_dialog as sd

    SettingsRepo().update(photos_base_path=r"D:\old", theme="light")
    dlg = sd.SettingsDialog()
    dlg.validate_changes = lambda ch: "would orphan events"  # type: ignore[method-assign]
    applied: list = []
    dlg.changes_applied = lambda ch: applied.append(ch)  # type: ignore[method-assign]
    warned: list = []
    monkeypatch.setattr(
        sd.QMessageBox, "warning",
        lambda *a, **k: warned.append(a) or sd.QMessageBox.StandardButton.Ok,
    )
    base_field = next(b for b in dlg._bindings if b.key == "photos_base_path")
    base_field.write(r"D:\new")
    dlg._on_apply()

    assert warned                                                  # user was told why
    assert not applied                                             # host NOT notified
    assert SettingsRepo().load().photos_base_path == r"D:\old"     # nothing persisted


def test_main_window_vetoes_base_change_that_orphans_events(qapp, tmp_path, monkeypatch):
    """MainWindow's validator refuses a base change that would orphan a relative-anchored
    event, and allows it once the event's files are present under the new base."""
    monkeypatch.setenv("MIRA_DATA_DIR", str(tmp_path))
    from mira.ui.shell.main_window import MainWindow

    gw = Gateway(
        settings=SettingsRepo(tmp_path / "s.json"),
        index=EventsIndex(tmp_path / "i.json"),
    )
    gw.set_photos_base_path(str(tmp_path / "old"))
    gw.index.upsert({"id": "e1", "name": "Trip", "event_relpath": "Trip",
                     "event_root_abs": None})
    win = MainWindow(gateway=gw)

    new_base = tmp_path / "new"
    msg = win._validate_settings_changes({"photos_base_path": ("", str(new_base))})
    assert msg and "Trip" in msg                                   # vetoed + names the event

    (new_base / "Trip").mkdir(parents=True)
    (new_base / "Trip" / "event.db").write_text("x", encoding="utf-8")
    assert win._validate_settings_changes(
        {"photos_base_path": ("", str(new_base))}) is None         # files present → allowed
    assert win._validate_settings_changes(
        {"theme": ("light", "dark")}) is None                      # non-base change → allowed
