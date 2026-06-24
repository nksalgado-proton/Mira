# 110 — Bracket Help button: contextual guidance when a bracket cluster is open in Pick

**Status: SHIPPED (Nelson 2026-06-22) in one commit
([7234e3a](https://github.com/nksalgado-proton/Mira/commit/7234e3a)).
What landed: `core/bracket_help.py` (Qt-free `BracketHelpContext` +
`build_help_context`, reusing `core.picked_media.link_name` so the panel's
rendered name prefix stays in lock-step with the spec/57 §3.2 return-scanner
matcher — the lock-step concern, handled); `mira/ui/picked/bracket_help_panel.py`
(compact kind-aware dialog — focus shows the drop-folder convention +
Copy/Open/Full-guide; exposure adds spec/109's "Merge in Mira" as a
`#Primary` button, disabled-with-"lands in Edit" when no callback per §6);
`picker_page.py` `_BRACKET_KINDS` + `_bracket_help_btn` beside
`_film_btn`/`_combined_btn`, gated in `_refresh_cluster_buttons`, label
flipping focus/exposure; a new `#HelpInvite` QSS role in `redesign.qss`
(soft accent-subtle fill → full accent on hover; distinct from `#Primary`
so it never competes); `main_window` wires `inapp_merge_requested(str)` to
the existing spec/109 `_start_in_app_exposure_merge` entry point. 10 tests
in `tests/test_bracket_help_button.py` green; no `_film_btn`/`_combined_btn`
regression. Original proposal follows.**

**Status: PROPOSED (Nelson 2026-06-22). When the user opens a **bracket**
cluster in the Picker (focus or exposure), Mira shows a prominent,
*inviting* **Help** button in the cluster controls. Pressing it opens a
bracket-type-specific panel that tells the user exactly what to do with
*this* bracket — for focus brackets, the external-stacking workflow + the
precise drop-folder and naming convention (the one thing users get wrong);
for exposure brackets, the in-app Merge option plus the external path. It
is the in-context surfacing of spec/108's round-trip contract, anchored
beside the existing Play Stack control. Touches `mira/ui/pages/picker_page.py`
(+ a small help-panel widget). Relates to spec/108 (the contract/doc it
surfaces), spec/109 (the exposure "Merge in Mira" action it offers), and
spec/57/72 (the round-trip mechanics). No data-model change.**

## 1. Why

Focus stacking is the one workflow Mira can't do natively — the user
*must* use an external tool and return the result by the round-trip
naming convention (spec/108). That convention is invisible and easy to get
wrong. The fix is to put the guidance **exactly where the user meets the
bracket**: the Picker, while inspecting/playing the stack. Mira already
detects the cluster kind and offers Play (and, for exposure, Combined)
there — the Help button rides alongside.

## 2. The button

- **Where:** the cluster controls row in `picker_page.py`, next to the
  Play (`_film_btn`) / Combined (`_combined_btn`) buttons.
- **When:** visible iff the open cluster's `kind` is a bracket. Add
  `_BRACKET_KINDS = frozenset(("focus_bracket", "exposure_bracket"))` and
  gate it the same way the existing buttons are gated (the
  `kind in _PLAY_KINDS` / `_COMBINED_KINDS` block, picker_page ~753):
  `self._bracket_help_btn.setVisible(kind in _BRACKET_KINDS)`.
- **Inviting, not a passive `?`:** an accented, labelled button — e.g.
  **"ⓘ How to handle this {focus|exposure} bracket"** — styled to draw the
  eye (a dedicated QSS role, e.g. `#HelpInvite`, distinct from the global
  title-bar Help/F1). The label itself invites the press and names the
  bracket type. (Optional: a one-time subtle highlight the first few times
  a bracket is opened, then settle to steady-state — a setting-remembered
  "seen" flag; nice-to-have, not required.)
- It is **separate** from the surface's global Help (F1) — this one is
  bracket-specific and content changes with the kind.

## 3. The panel (kind-aware content)

On press, open a compact help panel/dialog. Header: **"{Focus|Exposure}
bracket — {N} frames."** Then:

**Focus bracket** (external-only):

- Plain explanation: Mira doesn't merge focus stacks; here's how to get one
  sharp result.
- The **exact contract for THIS bracket**, filled in from the bracket's
  members (not generic): "Save your stacked result into **`Picked Media/`**
  (the root), with a filename that **starts with** `‹member link stem›`
  (e.g. `D{day}_{camera}_{originalname}`)." Pull the stem from the bracket
  members via the same helpers the return scanner uses
  (`external_returns._link_stem` / `_all_item_stems`).
- Actions: **[Copy name prefix]** (the stem), **[Open `Picked Media/`]**
  (reveal in Explorer), **[Full round-trip guide]** (the spec/108 doc),
  and a reminder: "then run **Scan for returns** in Edit — Mira adopts it
  as the bracket's master and badges it `ext`."

**Exposure bracket** (two paths):

- **[Merge in Mira]** — runs the in-app Mertens consolidation (spec/109);
  Mira fuses the bracket into one balanced master. (Disabled with a "lands
  in Edit" note if invoked before that flow is reachable.)
- **Or process externally** — the same drop-folder + naming guidance as
  focus (one stacker, one fused frame), for users who prefer their own HDR
  tool.

Content is sourced from the spec/108 contract but **rendered inline with
this bracket's concrete stem + paths**, so the user never has to translate
a generic doc to their file.

## 4. Acceptance

- Opening a focus or exposure bracket cluster in the Picker shows the
  inviting Help button; opening a non-bracket cluster (burst/repeat) or a
  single photo does not.
- Focus panel shows the exact `Picked Media/` drop path and the precise
  name prefix for that bracket; **[Copy name prefix]** copies it;
  **[Open folder]** reveals `Picked Media/`.
- Exposure panel offers **Merge in Mira** (spec/109) and the external
  path.
- The button is visually distinct from, and additive to, the global
  Help/F1; the Play/Combined controls are unchanged.

## 5. Tests

- `tests/test_bracket_help_button.py` — visibility gated on
  `kind in {"focus_bracket","exposure_bracket"}` (hidden for burst /
  single); the focus panel's copy-name action yields a string starting
  with a real bracket-member stem; the exposure panel exposes the
  Merge-in-Mira action.
- No regression to the Play (`_film_btn`) / Combined (`_combined_btn`)
  visibility logic.

## 6. Dependencies

- The exposure **Merge in Mira** action depends on spec/109 (in progress);
  until it lands, that button can be present-but-disabled with a tooltip,
  or hidden behind the same flag. The focus path depends only on the
  round-trip contract (spec/57/72) + the spec/108 doc link.
