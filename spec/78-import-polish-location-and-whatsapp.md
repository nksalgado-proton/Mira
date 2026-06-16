# spec/78 — Import polish: ask-once location + WhatsApp filename recovery

**Status:** written 2026-06-16 from a design session with Nelson, for the
production-import run (decades of photos, event by event). Two small,
independent import-quality fixes bundled in one doc. **Self-contained** — it
does not modify any other spec or the events-screen work in flight.

Read first: `spec/57` (folders + round trip / event creation) and `spec/64`
(the existing per-location-group prompt). Constraints as ever: pure-logic in
`core/` stays Qt-free; user-facing strings via `tr()`.

---

## §A. Ask once for location, fill all no-GPS days

**Problem (observed).** When creating an event from existing media, the day
plan prompts for Country / TZ on **every** day that has no phone GPS. For a
grab-bag import whose days are scattered (e.g. photos across many years), every
day is its own "location group", so the user is asked over and over. The
current behaviour is the per-location-group-of-*consecutive*-days prompt
(spec/64 §4.4) — grouping doesn't help when days aren't consecutive.

**Decision (Nelson).** Prompt **once**: ask Country / TZ a single time for the
first day lacking GPS, then **apply that same Country / TZ to every no-GPS day**.
The user corrects any wrong ones afterwards in the **Event Days Table** (which
already has per-day Country / TZ / Location columns).

**Where.** The collect/day-plan prompt paths in `mira/ui/shell/main_window.py`
(the "{n} day(s) without GPS … manual country" flow and the per-location-group
prompt around the existing-media collect path). Replace the per-group loop with
a single ask-once-then-fill step:

- If **all** days have GPS → no prompt (unchanged).
- If **any** day lacks GPS → one dialog: "N day(s) have no location data. Set a
  default Country / time zone for them — you can fix individual days in the days
  table." Apply the chosen Country / TZ to every no-GPS day.
- Phone-GPS days keep their auto-filled values; only the no-GPS days are filled.

**Tradeoff (accepted).** A genuine multi-country trip with no phone GPS gets one
country applied to all its undated days — fine, because the days table is right
there to correct them, and it beats prompting per day.

**Test.** Given a scan with several non-consecutive no-GPS days, the flow asks
once and writes the same Country / TZ to all of them; GPS days are untouched.

---

## §B. WhatsApp filename timestamp recovery

**Problem.** `core/filename_timestamp.py` (the filename → capture-time recovery
that now runs in `scan_source.build_scan_result`, per the 2026-06-16 import fix)
recovers dates from `IMG_20180224_204237.jpg`, `2018-02-24 20.42.37.jpg`, etc.
But WhatsApp's own naming — `WhatsApp Image 2018-02-24 at 20.42.37.jpeg` — has
the word **`at`** between the date and time, so the existing separated/compact
patterns don't match, and these land undated.

**Fix.** Add a WhatsApp-aware pattern to `core/filename_timestamp.py` (Qt-free,
pure regex), tried before the date-only fallbacks:

- Full: `WhatsApp Image YYYY-MM-DD at HH.MM.SS` (and the `(N)` dedupe suffix,
  any image extension) → full timestamp.
- Date-only: `WhatsApp Image YYYY-MM-DD` with no time → date with the
  module's existing noon default (`time_is_default=True`).
- Reuse the existing `_build_datetime` range validation and the
  `ParsedTimestamp` return shape; keep the "last match wins" rule intact.

Generalise lightly: accept ` at ` as a date↔time separator so
`… 2018-02-24 at 20.42.37 …` works regardless of the `WhatsApp Image` prefix
(WhatsApp Video, screenshots, etc. follow the same convention).

**Test (`tests/test_filename_timestamp.py`).**
`WhatsApp Image 2018-02-24 at 20.42.37.jpeg` → `2018-02-24T20:42:37`;
`WhatsApp Image 2018-02-24 at 20.42.37 (1).jpeg` → same;
`WhatsApp Image 2018-02-24.jpg` → `2018-02-24T12:00:00` (`time_is_default`);
a non-WhatsApp name with no date still returns `None`.

---

## §C. Definition of done
- `verify.bat` green incl. the two new tests.
- Creating an event from a multi-day, no-GPS source asks for location **once**
  and fills all such days; corrections still possible in the days table.
- WhatsApp-named photos recover their capture date and group onto the right day
  instead of landing undated.

Both are isolated, low-risk, and directly improve the decades-of-photos import.
