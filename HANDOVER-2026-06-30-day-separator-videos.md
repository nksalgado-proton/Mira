# Handover — 2026-06-30 — Day-separator MP4 videos

Everything below is **committed + pushed to `main`**. The 60-test sep / opener
/ PTE / cut-play sweep is green.

## What this session built (last → first)

| Commit | Topic |
| --- | --- |
| `e586d0c` | **v8** Cut Play video widget matches the video's aspect (no internal letterbox) |
| `37f16ad` | **v7** Caption transparent + 90 % wide; video inset from slide borders |
| `7a7b5f5` | **v6** Cut Play video sep/opener shows the slide frame around the video |
| `026cbf4` | PTE video-overlay sub closer to title (`y=-68 → -65`) |
| `cd7f27a` | **v5** 70/30 split — video bottom, caption top, no overlap |
| `8eb0a70` | **round 4** text at top of slide on PTE + Cut Play video sep/opener |
| `9530c7e` | text-on-top z-order fix (PTE prepend Video, Cut Play re-raise) |
| `81c858c` | video sep/opener fills the whole slide (precursor to the 70/30 split) |
| `1b87464` | Cut Play top-centre text overlay during video sep / opener |
| `6d08efd` | **v3** PTE Video overlay on separator + opener slides |
| `889fe36` | **v2** Cut Play plays event-map MP4 as the opener |
| `82cc9c9` | EventDaysTableDialog map button = Browse chrome + `GLYPH_MAP` |
| `a678f43` | Focus follows only left-click / Tab in EventDaysTableDialog |
| `b8d778b` | **v2** MP4 maps with Cut Play video-separator playback |
| `f6ccb0c` | Moved chip from DaysListsPage to EventDaysTableDialog |
| `38a4def` | **v1** Day + event maps end-to-end (storage / schema / gateway / dialog / chip / Cut letterbox) |

Spec: [`spec/155-day-and-event-maps.md`](spec/155-day-and-event-maps.md)
(v1 → v8 sections all amended).

## The next request — "do the same work for the days separator"

Nelson's last eyeball (event-map MP4) is polished:

- Slide frame (rounded card + blurred padding) visible around the video.
- Caption transparent, 90 % width, at the top inside the slide.
- Video aspect-matched within a 90 % × 60 % cap, 5 % bottom margin so the
  slide border shows beneath it.

He now wants the **per-day separator** (a day with an MP4 map attached) to
look the same. **The code in `cut_play.py` already takes the same path for
sep videos and opener videos** — both branch through `_sep_video_active`,
`_sep_video_bg_image()`, `_fit_sep_video_geometry()`, `_position_caption()`,
and read `_sep_current_video_path` for aspect. So in theory the polish is
already there. Verify on a real day before touching code.

### What to verify first (one bug = one fix; one good = stop)

1. Open an event, attach an MP4 to a specific day (not the whole event).
2. Play a Cut that includes that day.
3. When the day-separator slot fires, is the picture identical to what
   the opener shows? Specifically:
   - Slide frame visible around the video?
   - Caption (Day N + date · location · description) transparent, top-
     centred, 90 % wide?
   - Video aspect-matched, no internal black bars?
   - 5 % slide border beneath + on both sides of the video?
4. Same checks for the **PTE export** of that Cut — the `Slide N` for
   that day should carry a `:Video` block + `:Text` blocks at
   `y=-82 / y=-65`.

If everything looks right, the work is done — close it out.

### If something's wrong, here's the code map

| Concern | Where |
| --- | --- |
| Sep-video detection | [`mira/ui/shared/cut_play.py`](mira/ui/shared/cut_play.py) — `_sep_video_path(day)` |
| Sep-video duration probe | same file — `_sep_video_duration_ms(day)` (cached per day) |
| Entry into the sep-video flow | same file — `_show_index`, `kind == "sep"` branch |
| Slide-frame background image | same file — `_sep_video_bg_image()` |
| Video widget geometry | same file — `_fit_sep_video_geometry()` |
| Caption widget geometry | same file — `_position_caption()` |
| Caption text composition | same file — `_compose_sep_caption_html(day)` |
| PTE sep video overlay | [`mira/shared/pte_project.py`](mira/shared/pte_project.py) — `_video_overlay_object` + `_inject_texts` |
| PTE sep text positions | same file — `_VIDEO_OVERLAY_TEXT_POS` (TEXT_SEP_TITLE / TEXT_SEP_SUB) |
| PTE overlay resolver | [`mira/ui/pages/share_cuts_page.py`](mira/ui/pages/share_cuts_page.py) — `_pte_video_overlay(stripped, ctx=ctx)` — picks the slot for `opener.jpg` vs `dayN.jpg` |

### Likely-bug list (in priority order)

1. **Caption text empty for sep video.** Verify
   `_day_meta[day_number]` actually has `date / location / description`
   populated for the attached day. The opener flow uses the dialog's
   `opener_caption_tag` + `opener_caption_lines` kwargs; the sep flow
   reads `day_meta` directly. If `day_meta` is stale (the
   `share_cuts_page._on_play_cut` builds it via
   `{d.day_number: d for d in eg.trip_days()}`), the new
   `trip_day.map_image_path` column is read fine (model gained the
   field in `b8d778b`), but other fields might be blank for that day.
2. **Day-separator video aspect probe failing silently.** Same as v8
   fix — `_sep_video_aspect()` falls back to 16:9. For sep videos the
   current path is set in `_show_index` (`_sep_current_video_path =
   sep_vid`). Verify that path actually exists on disk and
   `probe_video` returns sensible width/height.
3. **PTE slide not getting the Video overlay for the day.**
   `_pte_video_overlay()` matches `lower.startswith("day") and
   lower.endswith(".jpg") and lower[3:-4].isdigit()`. If your day
   separator file is named differently (e.g. `day-01.jpg` with a
   dash), the regex misses. Today the export side names them
   `dayN.jpg` (no dash) — check
   [`_pte_card_text_context`](mira/ui/pages/share_cuts_page.py) and
   the export filenames.

### Repro setup

```python
# Attach an MP4 to day 2 of an existing event, then play a Cut that
# includes that day.
from mira.gateway.event_gateway import EventGateway
eg = EventGateway(...)
eg.attach_day_map(2, Path("path/to/some.mp4"))
# The MP4 lands at <event_root>/Maps/day-02.mp4 + a sidecar
# day-02.mp4.thumb.jpg with the first frame.
```

The 22-test sep/opener video suite lives at
[`tests/test_cut_play_video_separator.py`](tests/test_cut_play_video_separator.py).
The 8-test PTE Video overlay suite is at
[`tests/test_pte_video_overlay.py`](tests/test_pte_video_overlay.py).

## Test status at handoff

```
tests/test_cut_play_video_separator.py        22 passed
tests/test_pte_video_overlay.py                8 passed
tests/test_pte_project.py                     59 passed
tests/test_cut_play.py                         9 passed
tests/test_event_days_table_map_chip.py        8 passed
tests/test_event_days_table_focus_guard.py     7 passed
tests/test_event_days_table_dialog.py         32 passed
tests/test_gateway_maps.py                    21 passed
tests/test_path_builder.py                    25 passed
tests/test_map_attach_dialog.py               10 passed
```

No full `verify.bat` run since `e586d0c` landed — targeted runs only.
Worth one before closing the chapter if time allows.

## Memory pointers

- `[[project_pte_overlays_spec153]]` — context on the spec/153 PTE
  layered-text architecture (relevant when touching `_inject_texts`).
- `[[feedback_input_focus_left_click_only]]` — the dialog focus rule
  (already implemented; just don't accidentally regress).
- `[[feedback_push_without_asking]]` — solo-author push-without-asking
  pattern; "commit" or "commit + push" → push.
