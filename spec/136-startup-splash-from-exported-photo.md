# 136 — Startup splash from a random exported photo (also masks the boot flicker)

**Status: PROPOSED (Nelson 2026-06-23). On launch, small transient windows
flash before the main window appears (spec/—, the boot-flicker the user
reported). Rather than only chase each stray top-level, show a proper
**splash screen** during startup — and make it a **random exported photo
from a random closed event** when any are available, so the user is greeted
by their own finished work. A `QSplashScreen` is a frameless always-on-top
window, so it covers the `MainWindow` construction window and **hides the
flicker** as a bonus. Fast (loads the cached proxy, not the full JPEG),
time-boxed, and falls back to the bundled mark when there are no closed
events. Touches `mira/ui/app.py` (show/finish the splash around
`MainWindow()` / `window.show()`), a small splash-source helper, and reuses
`list_events` / `exported_files` / `resolve_proxy`. Optional Settings toggle.
No data-model change.**

## 1. Pick the splash image

- Enumerate **closed** events (`Gateway.list_events` with the closed status /
  `is_closed`). Pick a **random** closed event that has exported photos;
  from its `exported_files()` pick a **random** frame. (Random each launch —
  a rotating greeting from past trips.)
- **Load fast:** resolve the frame's **cached proxy** (`resolve_proxy`,
  ≤2560 px) and scale to the splash size; never full-res decode on the boot
  path. If no proxy exists yet, scale the export JPEG with a draft/reduced
  decode (spec/135), still time-boxed.
- **Fallback** (no closed events, no exports, or any failure / timeout):
  the bundled `assets/icons/mira.png` (or `mira-mark.svg` rendered) — a clean
  branded splash. Never block startup waiting for an image.

## 2. Show / finish the splash

- After the `QApplication` exists and the library root is resolved (so the
  index is readable), build the pixmap (§1) and
  `splash = QSplashScreen(pixmap)`, `splash.show()`, `app.processEvents()` —
  **before** `MainWindow()` so it covers construction + the startup
  reconcile.
- After `window.show()`, `splash.finish(window)` so it hands off cleanly to
  the main window.
- **Display duration = the build time, not a timer.** The main window takes
  ~2 s to construct (+ the startup reconcile); the splash is visible for
  exactly that window and is dismissed the moment the window is ready. There
  is **no artificial minimum delay** — we are *filling* existing dead time
  (today's flicker gap), not adding any. The only thing that must be fast is
  the image *sourcing* (§3); the *display* simply lasts as long as the build
  already does.
- Keep it lightweight: a frameless centred image; no buttons. Honor the
  active theme's background behind a non-cover-fit image.

### 2a. Decorate the splash (text over the photo)

Composite the splash pixmap with `QPainter` (draw onto a copy of the loaded
image), with a **legibility scrim** so text reads over any photo — a subtle
dark→transparent gradient under the **top-left** and along the **bottom**
(or a soft text shadow). Two text blocks:

- **Top-left — large title:** **"Mira by NKS starting…"** — a prominent,
  brand-weight headline (the largest text on the splash). Acts as the wordmark
  + the loading cue.
- **Bottom — small caption:** the splash photo's **when** and **where**,
  smaller and lighter, e.g. *"October 2025 · Kathmandu, Nepal."* Source from
  the frame's `FrameProvenance` (reuse the spec/134 item→provenance resolver):
  **`when` = `capture_time_corrected`** (the TZ/clock-corrected time, never
  raw — same rule as cuts/spec/134), **`where`** = the day's city / country
  (`cut_overlay._where_text`). Omit a part gracefully when its data is
  missing (e.g. where-less photo → date only).

For the **bundled-mark fallback** (no photo / no provenance): draw only the
top-left title; no when/where caption.

## 3. Performance + safety

- **Time-box** the whole splash-source step (e.g. ≤150–250 ms): if picking /
  loading overruns, fall back to the bundled image and proceed — the splash
  must never *add* perceptible startup latency.
- The closed-event query is a cheap index read; cap it (sample a few closed
  events rather than scanning all) and guard every step
  (`try/except → fallback`).
- Read-only: the splash path opens nothing for write (no lock interaction).

## 4. Optional Settings toggle

- A Settings option **"Show a recent photo on startup"** (default **on**).
  Off → the bundled mark splash (or no splash). Lets a user who screen-shares
  or prefers neutral boots opt out. Small `Settings` bool
  (`startup_photo_splash: bool = True`).

## 5. Acceptance

- With ≥1 closed event that has exported photos, launch shows a random
  exported frame as the splash — with **"Mira by NKS starting…"** large in
  the top-left and the photo's **when · where** small at the bottom (when =
  corrected time), text legible over the photo via the scrim — then the main
  window; a different frame on subsequent launches.
- No closed events / no exports → the bundled mark splash; launch is
  otherwise identical.
- The splash covers the construction window — the previously-visible
  transient popups are no longer seen flashing.
- Startup is not perceptibly slower (cached-proxy load, time-boxed,
  fallback).
- Toggle off → no photo splash.

## 6. Tests

- `tests/test_splash_source.py` — picks a random exported frame from a random
  closed event when available; returns the bundled fallback when there are no
  closed events / no exports / a load error; prefers the cached proxy path;
  respects the time-box (a slow load → fallback).
- `tests/test_splash_decoration.py` — the composited pixmap carries the
  top-left title and the bottom when·where caption; `when` derives from
  `capture_time_corrected` (not raw); a missing where → date-only caption; the
  bundled-mark fallback draws the title only (no caption). (Assert via the
  caption-string builder + that a scrim/draw pass ran; pixel-exactness not
  required.)
- `tests/test_splash_lifecycle.py` — the splash is shown before `MainWindow`
  is constructed and `finish(window)` is called after `window.show()`
  (assert ordering via a harness; offscreen-safe).
- `tests/test_startup_splash_setting.py` — the toggle gates photo vs bundled
  splash; default on.

## 7. Note on the flicker root cause

The splash **masks** the boot flicker; it is not strictly a root-cause fix
for the stray top-level windows. If those should also be eliminated outright
(e.g. for a fully clean boot even without a splash), that remains a separate
follow-up — parent every startup-constructed widget to the window / defer any
`.show()` until after `window.show()`. The splash is the high-value,
low-risk win; the deeper cleanup is optional.
