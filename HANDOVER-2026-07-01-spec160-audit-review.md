# Handover — 2026-07-01 — spec/160 audit review + open decisions

Session continuation doc. Nelson ran out of context mid-conversation
after receiving the plain-language translation of the spec/160
migration audit. Fresh agent picks up here.

## Where we are

**Design + audit landed. Awaiting Nelson sign-off + 2 real design
decisions before implementation of spec/160 can begin.**

- spec/159 §4–§6+ shipped on `DCDetailPage` this week (commits
  `484ea1a`, `c6ba3b0`, `e398729`). 83 tests green.
- [spec/160](spec/160-media-pool-format-cut.md) captured the target
  vocabulary — Media Pool + Format + Cut. Retires "Collection" + "Recipe"
  as UI nouns.
- [spec/161](spec/161-vocabulary-migration-audit.md) captured the audit
  workplan.
- [`docs/spec-160-audit.md`](docs/spec-160-audit.md) is the closed audit
  (1026 lines, produced by a fresh agent).
- Latest commit: `83c603a` (agent prompt for the audit itself).
- Working tree: clean.

## What Nelson has already agreed

All from the 2026-06-30 afternoon design session (see
[HANDOVER-2026-06-30-spec159-filters-and-preferred.md](HANDOVER-2026-06-30-spec159-filters-and-preferred.md)):

- **Three nouns**: Media Pool · Format · Cut. Recipe as a UI noun
  retires. Cross-event "Collection" retires (both scopes call the
  frozen output a Cut).
- **Rules narrowing-only** (no pick/skip verdicts).
- **Same dialog widget, both scopes.** Event face deliberately
  narrower (§1.1 principle + §6.2 visibility matrix).
- **Word for reusable presentation preset = Format** (not Recipe,
  not Preset, not Template).

## What Nelson still needs to decide

**2 real design calls (need his input):**

1. **Standalone Format editor?** Today Formats will only be editable
   from inside a Cut dialog. Options:
   - (a) No standalone editor. Editing happens only in a Cut. Simplest.
   - (b) Small dedicated editor for saved Formats.
   - (c) Library row is read-only; open it inside a Cut dialog to edit.

2. **"Start all picked / skipped" toggle placement — Cut or Format?**
   Every Cut needs it. Every Format could carry a default. His own gut
   in spec/160 §10: probably Format.

**4 quick confirmations (audit recommends; Nelson says yes/no):**

3. Keep `recipe` table, narrow its `flavour` enum to `'format'`
   (vs renaming). Auditor recommends: keep. Cheap + reversible.
4. Two developer-comfort schema renames:
   - `cut.source_dc_kind` / `.source_dc_id` / `.source_dc_tag` →
     `source_pool_*`
   - `Recipe` dataclass → `Format`
   Both optional polish. Cost: one small migration + one class-rename
   sweep. Nelson decides at sign-off.
5. Tone-Recipes (spec/54 `recipe_json` edit-tone system) stay OUT of
   the retirement. Already agreed in principle; needs one carve-out
   sentence written into spec/160 §7.
6. Trivial cleanups (retire dead `cut_template` code path; add an
   `event_collection` docstring). No decision needed; implementation
   catches them.

**3 spec/160 amendments to land before phase 1:**

1. Add tone-Recipe carve-out (item 5 above).
2. Acknowledge two composers exist today (`NewRecipeDialog`
   two-flavour + standalone `NewCrossEventDcDialog`). Consolidation
   is a real design task, not a trivial reshape.
3. Flag phase 4 (pin-mode collapse) as highest-risk in §9.3.

## The plain-language audit summary I gave Nelson

Nelson asked "the audit closed and it is so technical I cannot even
understand the implications and risks." The translation:

**Scale:**
- ~114 user-facing string sites.
- ~40 gateway API renames (all shipped with old-name aliases; no hard
  breaks until phase 5).
- 13 UI surfaces to reshape.
- 1 substantive schema decision (item 3 above).
- 1 test file (`test_collection_vocabulary.py`) is the guard that
  flips when the sweep is done.

**Risk map:**

| Risk | Likelihood | Mitigation |
|---|---|---|
| Stale "Collection"/"Recipe" string ships in some corner | Medium | 114-site checklist + end-of-phase-1 `git grep` reconcile |
| Downstream caller breaks on rename | Very low | Every rename ships with old-name alias for one release |
| `recipe` schema decision wrong | Low | Auditor's recommendation is the reversible one |
| **Pin-mode collapse breaks legacy Cuts** | **Medium — this is the real risk** | Highest-risk phase; can be piloted on copy of `event.db` before landing |
| Users confused during transition | Low | Phase 1 (string sweep) has zero behavioural change; revertable |

**Phase 4 pin-mode collapse is the one place the migration is
genuinely more work than spec/160 read.** It's not one place — it's a
database column + a rule-verdict retirement + a UI collapse. Three
moving parts, all at once.

## The last message before context ran out

I offered to pull the 2 real decisions (items 1 + 2 above) into an
`AskUserQuestion` so Nelson can lock them and move on. Nelson asked me
to compact context instead of continuing — so we're pausing there.

## What the fresh agent should do

**Do NOT begin implementation.** Nelson still has 2 decisions + 4
confirmations pending, and 3 spec/160 amendments need to land before
any phase-1 code work begins.

Suggested opening move when Nelson resumes:

1. Confirm you've read this handover + the audit summary in
   `docs/spec-160-audit.md` §0 (the summary block).
2. Restate the 2 real design decisions to Nelson via
   `AskUserQuestion` — item 1 (standalone Format editor?) + item 2
   (start-toggle placement?).
3. Once Nelson answers, batch the 4 quick confirmations in a second
   `AskUserQuestion`.
4. Draft the 3 spec/160 amendments (they're small — probably one
   commit).
5. Ask Nelson if he wants to sign off + kick off the surface plan
   work (spec/160 §9.2) or the phase-1 vocabulary sweep first.

## Files that matter

| File | Role |
|---|---|
| `spec/160-media-pool-format-cut.md` | Target vocabulary. Read §1.1 (event-scope principle) + §6.2 (visibility matrix) + §7 (retirement table) + §9 (implementation phases). |
| `spec/161-vocabulary-migration-audit.md` | Audit workplan. Read §5 (acceptance criteria) so you know what "audit closed" means. |
| `docs/spec-160-audit.md` | The closed audit itself. §0 is the summary block. §7 is the "Nelson decides" ambiguity list. 1026 lines — don't re-read cover-to-cover; §0 + §7 gets you 90% of the way. |
| `HANDOVER-2026-06-30-spec159-filters-and-preferred.md` | Yesterday's session summary. Context for how spec/160 emerged from spec/159 work. |
| `AGENT-PROMPT-vocab-audit.md` | The prompt that produced the audit. Useful only if you need to re-audit; probably not. |
| `AGENT-PROMPT-B-cut-filters.md` | A DIFFERENT deferred task (Cut-surface filter extension). NOT what you're working on. Ignore unless Nelson pivots. |

## Session context tone

Nelson's engaged, sharp, moves fast, prefers direct answers. Doesn't
want to review 1026 lines of tables — wants plain-language impact
+ crisp decisions. Uses `AskUserQuestion` when the choice is discrete;
free-text when nuanced. Pushes back on jargon. Rewards short, dense
responses.

The design conversation this week landed in a good place — he's
proud of the three-noun model + the event-scope simplification
principle. Don't relitigate; just help him ship the decisions.

## Commit history for this thread

```
83c603a docs: agent prompt for spec/161 vocabulary migration audit
d857017 spec/161: vocabulary migration audit — the pre-implementation gate
e398729 spec/160: promote event-scope simplification to first-class principle
64c0132 spec/160: Media Pool · Format · Cut vocabulary simplification (design agreed)
b23fac6 docs: handover for spec/159 §4–§6+ + Plan B agent prompt
c6ba3b0 spec/159 §4.5: Filters as a group-box bar (reusable)
484ea1a spec/159 §6+: preferred-version surface + cluster + Compare reuse
```

Plus one uncommitted at handover time: this doc. Fresh agent may not
see this in git yet if Nelson didn't commit before switching sessions —
if not, the raw file is on disk at repo root.
