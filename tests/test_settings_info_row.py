"""The settings ``info`` widget kind (spec/63 slice 7 disk honesty):
read-only value row from an injected provider + optional confirmed
action that re-reads the provider. No settings key, no binding."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QLabel, QMessageBox, QPushButton

from mira.ui.base.settings_dialog import SettingsDialog

_SCHEMA = [
    {
        "tab": "Advanced",
        "fields": [
            {
                "widget": "info",
                "label": "Screen copies",
                "info_id": "proxy_cache",
                "tooltip": "Disk used by browsing copies.",
                "action_id": "clear_proxy_cache",
                "action_label": "Clear…",
                "action_tooltip": "Delete the copies.",
                "action_confirm": "Delete this event's browsing copies?",
            },
        ],
    },
]


def _labels(dialog) -> list[str]:
    return [w.text() for w in dialog.findChildren(QLabel)]


def test_info_row_renders_provider_value_and_no_binding(qapp):
    dlg = SettingsDialog(
        schema=_SCHEMA,
        info_providers={"proxy_cache": lambda: "12 copies · 8 MB"},
        info_actions={"clear_proxy_cache": lambda: "ok"},
    )
    assert "12 copies · 8 MB" in _labels(dlg)
    # Info rows never bind to a settings key (nothing to persist).
    assert not dlg._bindings
    # Every-control-hint: the value label and the action button carry
    # tooltips.
    value = next(w for w in dlg.findChildren(QLabel)
                 if w.text() == "12 copies · 8 MB")
    assert value.toolTip()
    button = next(w for w in dlg.findChildren(QPushButton)
                  if w.text() == "Clear…")
    assert button.toolTip()
    dlg.deleteLater()


def test_info_row_missing_provider_shows_dash_and_no_button(qapp):
    dlg = SettingsDialog(schema=_SCHEMA)
    assert "—" in _labels(dlg)
    assert not [w for w in dlg.findChildren(QPushButton)
                if w.text() == "Clear…"]
    dlg.deleteLater()


def test_info_action_runs_after_confirm_and_refreshes_value(
        qapp, monkeypatch):
    state = {"value": "2 copies · 1 MB", "cleared": 0}

    def _clear() -> str:
        state["cleared"] += 1
        state["value"] = "None yet for this event"
        return "2"

    dlg = SettingsDialog(
        schema=_SCHEMA,
        info_providers={"proxy_cache": lambda: state["value"]},
        info_actions={"clear_proxy_cache": _clear},
    )
    # Tests never exec real modals: auto-answer the NoIcon confirm.
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    button = next(w for w in dlg.findChildren(QPushButton)
                  if w.text() == "Clear…")
    button.click()
    assert state["cleared"] == 1
    assert "None yet for this event" in _labels(dlg)
    dlg.deleteLater()


def test_info_action_declined_confirm_does_nothing(qapp, monkeypatch):
    state = {"cleared": 0}
    dlg = SettingsDialog(
        schema=_SCHEMA,
        info_providers={"proxy_cache": lambda: "5 copies · 3 MB"},
        info_actions={
            "clear_proxy_cache":
                lambda: state.__setitem__("cleared", state["cleared"] + 1)},
    )
    monkeypatch.setattr(
        QMessageBox, "exec", lambda self: QMessageBox.StandardButton.No)
    button = next(w for w in dlg.findChildren(QPushButton)
                  if w.text() == "Clear…")
    button.click()
    assert state["cleared"] == 0
    dlg.deleteLater()
