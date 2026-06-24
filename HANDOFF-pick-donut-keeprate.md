# Handoff — Phases "Pick" donut → picked / captured

Branch: **main**. Read CLAUDE.md. Tiny, well-scoped metric change.

## Change

In the Phases 2×2, the **Pick** donut currently plots **decided /
captured** (reviewed = picked+skipped+compare ÷ captured). Nelson wants
it to plot **picked / captured** (keepers ÷ all captured — a keep rate).
This also makes the donut agree with the Days-List "Picked" bar, which
already plots picked / captured.

Decision (Nelson 2026-06-22): denominator is **captured** (not
picked+skipped). So an event with undecided shots shows the keep rate
over everything captured, climbing to its true value as decisions land.

## Edits — `mira/ui/pages/phases_page.py`

1. In `set_event`, the Pick snapshot (~line 616):
   ```python
   snapshots.append(self._ratio_snapshot(
       "pick", "Pick", decided_total, captured_total, p,   # <- change
   ))
   ```
   → use `picked_total` as the numerator:
   ```python
   snapshots.append(self._ratio_snapshot(
       "pick", "Pick", picked_total, captured_total, p,
   ))
   ```
   `picked_total = eg.phase_picked_count("pick")` is already computed.
   `decided_total` becomes unused — remove its assignment
   (`decided_total = eg.phase_decided_count("pick")`). Leave the gateway
   method `phase_decided_count` in place (other callers may use it).

2. Tooltip dict (~line 92-95): change the `"pick"` entry from
   `"Share of captures reviewed (picked or skipped)."` to
   `"Share of captures kept (picked)."`.

3. `_format_delta` (~line 354-363): the "to go" line no longer fits a
   keep rate (remaining = captured − picked = the *not-kept* set, not a
   completion gap). Either:
   - **(recommended)** suppress the delta for Pick: return `""` for
     `snapshot.key == "pick"` (like Collect), OR
   - reword to non-completion phrasing (e.g. drop "to review"/"All
     reviewed"). Don't leave the misleading "N to review".

   Leave Edit/Export deltas unchanged.

## Spec

Update **spec/66**'s Pick-metric line: the Phases Pick donut is **picked
÷ captured (keep rate)**, not decided ÷ captured. (Review-completeness,
decided ÷ captured, still exists in the gateway as
`phase_decided_count`; it's just no longer what the donut shows.) Note
the alignment with the Days-List Picked bar.

## Tests

- `_ratio_snapshot` for Pick uses `picked_total / captured_total`
  (numerator = picked keepers): assert center pct + sub = picked/captured.
- The Pick tooltip text reads the new wording.
- `_format_delta` for a Pick snapshot returns the new behavior (empty, or
  the reworded string — match whichever you implement).

Run the phases-page suite, then full `verify.bat`.

## Commit + push (on main)

```
change: Phases Pick donut now plots picked / captured (keep rate)

Was decided/captured (review completeness). Nelson wants the keep rate —
keepers over all captured — which also matches the Days-List Picked bar.
Tooltip + delta wording updated; spec/66 Pick-metric note revised.
phase_decided_count stays in the gateway for other callers.
```

Then `git push` on `main`.
