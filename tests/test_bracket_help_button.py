"""Pins the spec/110 bracket help button + panel contract.

Three axes:

* **Visibility** — the inviting Help button shows iff the open cluster's
  ``kind`` is ``focus_bracket`` / ``exposure_bracket``; hidden for burst /
  repeat / single photos.
* **Focus panel** — exposes the link-stem prefix the spec/57 §3.2 matcher
  expects; the Copy-name action copies it to the clipboard.
* **Exposure panel** — exposes the Merge-in-Mira action (spec/109);
  invoking it surfaces the bracket key the spec/110 flow routes to the
  spec/109 batch entry point.

Plus the regression guard: the help button's gating doesn't break the
existing Play / Combined visibility logic.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List

import pytest

from PyQt6.QtGui import QGuiApplication

from core.bracket_help import BracketHelpContext, build_help_context
from mira.gateway import EventsIndex, Gateway
from mira.picked import CullBucket, CullItem
from mira.settings.repo import SettingsRepo
from mira.ui.pages.picker_page import (
    PickerPage, _BRACKET_KINDS, _COMBINED_KINDS, _PLAY_KINDS,
)
from mira.ui.picked.bracket_help_panel import BracketHelpPanel


# --------------------------------------------------------------------- #
# Fixtures — synthetic CullBuckets + a stub gateway shape
# --------------------------------------------------------------------- #


def _bucket(kind: str, *, key: str = "k1", n: int = 3) -> CullBucket:
    """A synthetic CullBucket carrying just enough for the help panel
    to render. We bypass the real scanner here — the test pins the
    button/panel contract, not the clustering."""
    items = tuple(
        CullItem(
            item_id=f"item-{i}", path=Path(f"/tmp/x_{i}.jpg"),
            kind="photo", capture_time_corrected=f"2026-04-03T08:00:0{i}",
        )
        for i in range(n)
    )
    status = SimpleNamespace(color=None)
    return CullBucket(
        bucket_key=key, kind=kind, title="t",
        items=items, status=status,
        detection_source="auto", camera="G9",
    )


class _StubItem:
    def __init__(self, item_id: str, ordinal: int) -> None:
        self.id = item_id
        self.origin_relpath = (
            f"Original Media/_cameras/d3/G9/IMG_{ordinal:04d}.RW2")
        self.camera_id = "G9"
        self.day_number = 3
        self.capture_time_corrected = f"2026-04-03T08:00:0{ordinal}"


class _StubGateway:
    """Minimal gateway shim the help-context builder needs — just
    ``item(id)`` + ``event_root``. Lets us stay independent of the full
    EventGateway plumbing for the panel-level tests."""

    def __init__(self, items: List[_StubItem], event_root: Path) -> None:
        self._items = {it.id: it for it in items}
        self.event_root = event_root

    def item(self, item_id: str):
        return self._items.get(item_id)


@pytest.fixture()
def page(qapp, tmp_path):
    settings = SettingsRepo(tmp_path / "settings.json")
    index = EventsIndex(tmp_path / "events_index.json")
    gw = Gateway(settings=settings, index=index)
    p = PickerPage(gw)
    yield p
    p.deleteLater()


# --------------------------------------------------------------------- #
# Visibility — gated on bracket kinds, no regression on Play / Combined
# --------------------------------------------------------------------- #


def test_bracket_kinds_constant_matches_play_and_combined():
    """spec/110 §2 — the Help button gates on the bracket subset.
    Pinning the constant guards against a future refactor accidentally
    pulling burst (which Play covers) into the help set."""
    assert _BRACKET_KINDS == frozenset(
        {"focus_bracket", "exposure_bracket"})
    # The help kinds must be a subset of the Play kinds (every bracket
    # is also play-stepable) but burst must NOT be in help.
    assert _BRACKET_KINDS.issubset(_PLAY_KINDS)
    assert "burst" in _PLAY_KINDS and "burst" not in _BRACKET_KINDS
    # Combined still gates on exposure-only — Help adds focus too.
    assert _COMBINED_KINDS == frozenset({"exposure_bracket"})


def test_help_button_visible_for_focus_bracket(page):
    page._bucket = _bucket("focus_bracket")
    page._refresh_cluster_buttons()
    assert page._bracket_help_btn.isVisibleTo(page) or \
        page._bracket_help_btn.isVisible() is True or \
        not page._bracket_help_btn.isHidden()
    assert "focus" in page._bracket_help_btn.text().lower()
    # Regression: Play stays visible (focus_bracket is a Play kind);
    # Combined stays hidden (focus is NOT a Combined kind).
    assert not page._film_btn.isHidden()
    assert page._combined_btn.isHidden()


def test_help_button_visible_for_exposure_bracket(page):
    page._bucket = _bucket("exposure_bracket")
    page._refresh_cluster_buttons()
    assert not page._bracket_help_btn.isHidden()
    assert "exposure" in page._bracket_help_btn.text().lower()
    # Regression: BOTH Play + Combined stay visible for exposure.
    assert not page._film_btn.isHidden()
    assert not page._combined_btn.isHidden()


def test_help_button_hidden_for_burst(page):
    """Burst is Play-stepable but NOT a bracket — no Help button.
    spec/110 §2 explicitly excludes burst / repeat / individuals."""
    page._bucket = _bucket("burst")
    page._refresh_cluster_buttons()
    assert page._bracket_help_btn.isHidden()
    # Regression: Play remains visible for burst; Combined hidden.
    assert not page._film_btn.isHidden()
    assert page._combined_btn.isHidden()


def test_help_button_hidden_for_single_photo_kinds(page):
    """Moment / individual / day clusters are flattened to single-photo
    cells (spec/32 §8.2) — the help button must stay hidden so the
    Picker's everyday photo browse isn't cluttered."""
    for kind in ("moment", "individual", "video", "day", ""):
        page._bucket = _bucket(kind) if kind else None
        page._refresh_cluster_buttons()
        assert page._bracket_help_btn.isHidden(), \
            f"help button leaked into kind={kind!r}"
        # Play stays governed by its own set — no regression.
        assert page._film_btn.isVisible() is (kind in _PLAY_KINDS)
        assert page._combined_btn.isVisible() is (kind in _COMBINED_KINDS)


def test_help_button_uses_helpinvite_qss_role(page):
    """spec/110 §2 — the inviting style rides QSS, not inline
    setStyleSheet. The objectName is the QSS selector hook."""
    assert page._bracket_help_btn.objectName() == "HelpInvite"


# --------------------------------------------------------------------- #
# Focus panel — copy-name action surfaces the real bracket-member stem
# --------------------------------------------------------------------- #


def test_focus_panel_name_prefix_starts_with_real_member_stem(qapp, tmp_path):
    """spec/110 §3 focus — the panel's Copy name prefix action yields
    the link stem the spec/57 §3.2 matcher will accept. The stem must
    start with a real bracket-member's deterministic prefix
    (``D{day:02d}_{camera}_{originalname}``)."""
    items = [_StubItem(f"item-{i}", ordinal=i + 1) for i in range(3)]
    gw = _StubGateway(items, event_root=tmp_path)
    bucket = _bucket("focus_bracket")
    # Re-wire the bucket items to point at the stub item ids so the
    # context builder finds them.
    bucket = CullBucket(
        bucket_key=bucket.bucket_key, kind=bucket.kind, title=bucket.title,
        items=tuple(
            CullItem(
                item_id=items[i].id,
                path=Path(items[i].origin_relpath),
                kind="photo",
                capture_time_corrected=items[i].capture_time_corrected,
            )
            for i in range(3)
        ),
        status=bucket.status,
        detection_source=bucket.detection_source,
        camera=bucket.camera,
    )
    ctx = build_help_context(bucket, gateway=gw, event_root=tmp_path)
    assert ctx.kind == "focus_bracket"
    assert ctx.member_count == 3

    # The anchor stem follows the spec/57 §2.1 link-name rule.
    expected = "D03_G9_IMG_0001"
    assert ctx.name_prefix == expected
    # Every member's prefix is captured so the panel can offer per-frame
    # copy if needed.
    assert ctx.member_prefixes == (
        "D03_G9_IMG_0001",
        "D03_G9_IMG_0002",
        "D03_G9_IMG_0003",
    )

    panel = BracketHelpPanel(ctx, parent=None)
    try:
        # The exposed test API matches the spec/110 §5 acceptance:
        # the copy-name action returns a string starting with a real
        # bracket-member stem.
        assert panel.name_prefix() == expected
        assert any(panel.name_prefix().startswith(stem)
                   for stem in ctx.member_prefixes)

        # Focus panels do NOT expose the Merge-in-Mira action.
        assert panel.merge_button is None

        # Triggering the Copy action puts the prefix on the clipboard.
        panel.copy_prefix_button.click()
        clip = QGuiApplication.clipboard()
        assert clip is not None
        assert clip.text() == expected
    finally:
        panel.deleteLater()


# --------------------------------------------------------------------- #
# Exposure panel — Merge-in-Mira action is exposed and fires the callback
# --------------------------------------------------------------------- #


def test_exposure_panel_exposes_merge_in_mira_action(qapp, tmp_path):
    items = [_StubItem(f"item-{i}", ordinal=i + 1) for i in range(3)]
    gw = _StubGateway(items, event_root=tmp_path)
    bucket = CullBucket(
        bucket_key="b-exp", kind="exposure_bracket", title="t",
        items=tuple(
            CullItem(
                item_id=items[i].id,
                path=Path(items[i].origin_relpath),
                kind="photo",
                capture_time_corrected=items[i].capture_time_corrected,
            )
            for i in range(3)
        ),
        status=SimpleNamespace(color=None),
        detection_source="auto", camera="G9",
    )
    ctx = build_help_context(bucket, gateway=gw, event_root=tmp_path)
    assert ctx.kind == "exposure_bracket"

    fired: list = []

    def _on_merge() -> None:
        fired.append(ctx.bracket_key)

    panel = BracketHelpPanel(ctx, on_merge=_on_merge, parent=None)
    try:
        # spec/110 §3 — the exposure panel exposes the action.
        btn = panel.merge_button
        assert btn is not None
        assert btn.isEnabled() is True
        # Same link stem rule still holds — the exposure panel renders
        # the contract for users who prefer their own HDR tool.
        assert panel.name_prefix() == "D03_G9_IMG_0001"

        btn.click()
        assert fired == ["b-exp"]
    finally:
        panel.deleteLater()


def test_exposure_panel_merge_disabled_without_callback(qapp, tmp_path):
    """spec/110 §6 fallback — when the host can't dispatch the merge
    (no callback wired), the button stays visible-but-disabled with a
    "lands in Edit" tooltip so the user knows the action exists but
    has to be taken from Edit instead."""
    items = [_StubItem(f"item-{i}", ordinal=i + 1) for i in range(2)]
    gw = _StubGateway(items, event_root=tmp_path)
    bucket = CullBucket(
        bucket_key="b-exp", kind="exposure_bracket", title="t",
        items=tuple(
            CullItem(
                item_id=items[i].id,
                path=Path(items[i].origin_relpath),
                kind="photo",
                capture_time_corrected=items[i].capture_time_corrected,
            )
            for i in range(2)
        ),
        status=SimpleNamespace(color=None),
        detection_source="auto", camera="G9",
    )
    ctx = build_help_context(bucket, gateway=gw, event_root=tmp_path)
    panel = BracketHelpPanel(ctx, on_merge=None, parent=None)
    try:
        btn = panel.merge_button
        assert btn is not None
        assert btn.isEnabled() is False
        tip = btn.toolTip().lower()
        assert "edit" in tip
    finally:
        panel.deleteLater()


# --------------------------------------------------------------------- #
# End-to-end — the Picker's _open_bracket_help wires the signal
# --------------------------------------------------------------------- #


def test_picker_emits_inapp_merge_requested(page, tmp_path, monkeypatch):
    """When the panel's Merge button is clicked from the live Picker,
    the page must emit ``inapp_merge_requested(bracket_key)`` — that's
    the seam main_window routes to the spec/109 batch entry point."""
    items = [_StubItem(f"item-{i}", ordinal=i + 1) for i in range(3)]
    gw = _StubGateway(items, event_root=tmp_path)

    bucket = CullBucket(
        bucket_key="b-exp-live", kind="exposure_bracket", title="t",
        items=tuple(
            CullItem(
                item_id=items[i].id,
                path=Path(items[i].origin_relpath),
                kind="photo",
                capture_time_corrected=items[i].capture_time_corrected,
            )
            for i in range(3)
        ),
        status=SimpleNamespace(color=None),
        detection_source="auto", camera="G9",
    )
    page._bucket = bucket
    page._eg = gw

    captured: list = []
    page.inapp_merge_requested.connect(captured.append)

    # Stub out exec() so the dialog doesn't block the test thread.
    panels: list = []

    def _fake_exec(self):
        panels.append(self)
        # Click the Merge button BEFORE returning — simulates the user
        # action while the dialog is modal.
        self.merge_button.click()
        return 0

    monkeypatch.setattr(BracketHelpPanel, "exec", _fake_exec)

    page._open_bracket_help()
    assert panels, "panel was not constructed"
    assert captured == ["b-exp-live"]
