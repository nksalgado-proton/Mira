"""spec/99 §A + §B — Quick Sweep cold-path decode profile.

The pre-ingest Quick Sweep can't reach the post-ingest thumb/proxy
caches, so its viewport opts into a lower windowed-browse ceiling and
a deeper forward-biased prefetch plan. Every other surface keeps
today's behaviour exactly (regression guard).

Tests cover:

* ``sweep_ceiling`` clamps the long edge of ``_target_size`` /
  ``_nav_target_size`` to the sweep ceiling — never above it.
* ``set_ceiling_suspended(True)`` lifts the clamp back to the
  display-quality ceiling so F11 fullscreen shows a sharp frame.
* A viewport with no profile set clamps to the display ceiling exactly
  as before (the default-off invariant).
* The Quick Sweep prefetch plan is the deeper forward set; a default
  viewport keeps the Picker's proven ``(1, 2, -1)``.
"""
from __future__ import annotations

import pytest

from core import machine_settings
from mira.ui.media.photo_cache import PhotoCache
from mira.ui.media.photo_viewport import (
    _PREFETCH_OFFSETS,
    PhotoViewport,
)


@pytest.fixture
def cache(qapp):
    c = PhotoCache()
    yield c
    c.shutdown()


@pytest.fixture
def viewport(qapp, cache):
    vp = PhotoViewport(cache=cache)
    vp.resize(1920, 1080)
    yield vp
    vp.deleteLater()


@pytest.fixture(autouse=True)
def _isolate_machine_settings(tmp_path, monkeypatch):
    """Pin display_quality to ``balanced`` in a tempdir so the ceiling
    is deterministic across tests / hosts."""
    machine_file = tmp_path / "machine.json"
    monkeypatch.setattr(
        machine_settings, "machine_settings_path",
        lambda: machine_file)
    machine_settings.write_display_quality("balanced")
    return machine_file


def _patch_dpr(vp, dpr):
    vp.devicePixelRatioF = lambda: float(dpr)        # type: ignore[method-assign]


# ── §A — sweep ceiling clamps the windowed-browse target ─────────


def test_sweep_ceiling_clamps_target_long_edge(viewport):
    """A viewport with ``sweep_ceiling=2048`` must clamp the long edge
    of BOTH ``_target_size`` and ``_nav_target_size`` to ≤ 2048 (within
    the 512-step quantisation band), even on an oversized native /
    HiDPI configuration that would otherwise want a 4K-class target."""
    _patch_dpr(viewport, 2.0)
    # 1920×1080 × DPR 2.0 → 3840×2160 — well above the sweep cap.
    viewport.set_sweep_ceiling(2048)
    settle = viewport._target_size()
    nav = viewport._nav_target_size()
    # Long edge of both must stay within the ceiling + one 512 step
    # (the post-clamp quantisation bumps to the next step).
    for size in (settle, nav):
        long_edge = max(size.width(), size.height())
        assert long_edge <= 2048 + 512, (
            f"sweep_ceiling=2048 must clamp the long edge — "
            f"got {long_edge} on {size}")


def test_sweep_ceiling_lifted_in_fullscreen_suspend(viewport):
    """``set_ceiling_suspended(True)`` must lift the cap so the browse
    tier decodes back at the display ceiling (3840 for balanced).
    Pairs with ``refresh_current()`` in the host — here we just check
    the math."""
    _patch_dpr(viewport, 2.0)
    viewport.set_sweep_ceiling(2048)
    capped_size = viewport._target_size()
    capped = max(capped_size.width(), capped_size.height())
    viewport.set_ceiling_suspended(True)
    lifted_size = viewport._target_size()
    lifted = max(lifted_size.width(), lifted_size.height())
    assert lifted > capped, (
        "suspend must lift the sweep ceiling — "
        f"capped={capped}, lifted={lifted}")
    # And lifted must hit the balanced ceiling band (3840 → 4096 quantised).
    assert lifted >= 3840


def test_default_viewport_matches_display_ceiling_exactly(viewport):
    """The regression guard: a viewport with NO sweep profile set must
    target exactly today's display-quality ceiling band — Picker,
    Editor, Days Grid, and Cut surfaces are untouched by spec/99."""
    assert viewport._sweep_ceiling is None
    _patch_dpr(viewport, 2.0)
    target = viewport._target_size()
    long_edge = max(target.width(), target.height())
    # Balanced ceiling = 3840 → quantised to 4096 (next 512 step).
    assert long_edge == 4096, (
        "default viewport must clamp at the display ceiling (3840 / "
        f"4096 quantised) — got {long_edge}")


def test_active_ceiling_picks_lower_of_sweep_and_display(viewport):
    """When BOTH the sweep and display caps apply, ``_active_ceiling``
    returns the lower one — a tiny display can't blow past its own
    quality cap via the sweep tier."""
    machine_settings.write_display_quality("balanced")     # 3840
    viewport.set_sweep_ceiling(2048)
    assert viewport._active_ceiling() == 2048             # sweep wins
    viewport.set_sweep_ceiling(9999)
    assert viewport._active_ceiling() == 3840             # display wins
    viewport.set_sweep_ceiling(None)
    assert viewport._active_ceiling() == 3840             # default behaviour
    # Suspending always lifts to the display ceiling, even with a
    # sweep cap set.
    viewport.set_sweep_ceiling(2048)
    viewport.set_ceiling_suspended(True)
    assert viewport._active_ceiling() == 3840


# ── §B — per-instance prefetch plan ─────────────────────────────


def test_default_prefetch_plan_matches_picker(viewport):
    """A fresh viewport carries the Picker's proven ``(1, 2, -1)`` so
    every non-Quick-Sweep surface inherits today's prefetch cadence."""
    assert viewport._prefetch_plan == _PREFETCH_OFFSETS
    assert viewport._prefetch_plan == (1, 2, -1)


def test_quick_sweep_prefetch_plan_is_deeper_forward_set(qapp):
    """The Quick Sweep page installs ``(1, 2, 3, 4, -1)`` on its
    viewport (spec/99 §B). The cold sweep is strictly forward, so a
    deeper forward window keeps the worker ahead of the user."""
    from mira.ui.pages.quick_sweep_page import QuickSweepPage
    page = QuickSweepPage()
    try:
        assert page._viewport._prefetch_plan == (1, 2, 3, 4, -1)
        # And the sweep ceiling defaults to 2048 (the §A start value).
        assert page._viewport._sweep_ceiling == 2048
    finally:
        page.deleteLater()


def test_set_prefetch_plan_overrides_module_default(viewport):
    """``set_prefetch_plan`` is the public hook — any sequence of ints
    is accepted and replaces the per-instance plan; the module-level
    constant stays untouched so other viewports inherit it."""
    viewport.set_prefetch_plan([1, 2, 3, 4, -1])
    assert viewport._prefetch_plan == (1, 2, 3, 4, -1)
    assert _PREFETCH_OFFSETS == (1, 2, -1)             # module unchanged
