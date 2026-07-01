# Agent prompt — spec/161 vocabulary migration audit

Paste the block below to a fresh agent. It's self-contained.

---

## Context

The Mira codebase is preparing for a vocabulary migration captured in
two paired specs on 2026-06-30:

- [`spec/160`](spec/160-media-pool-format-cut.md) — the target vocabulary:
  **Media Pool · Format · Cut**. Retires "Collection" and "Recipe" as
  user-facing nouns. Includes a load-bearing "event-scope
  simplification" principle (§1.1) + a field-by-field visibility matrix
  (§6.2) for the unified New Cut dialog.
- [`spec/161`](spec/161-vocabulary-migration-audit.md) — the audit
  workplan. Your task lives here.

**Implementation of spec/160 is gated on the audit landing + Nelson
sign-off.** No rename PRs, no schema changes, no compatibility shims
begin until the audit doc is complete and Nelson has read it. That's
the whole point of your task: build the checklist that implementation
walks through.

## Read this first

Do NOT start scanning code before you've read:

1. `CLAUDE.md` — project invariants (English-not-firm rule especially:
   the vocabulary sweep must respect `tr()` boundaries).
2. `spec/160-media-pool-format-cut.md` — end to end. Especially:
   - §1.1 (why the event-scope face is deliberately narrower)
   - §6.2 (the field-by-field visibility matrix — this is your
     categorisation reference for every dialog widget)
   - §7 (the vocabulary retirement table — this is your
     categorisation reference for every string)
3. `spec/161-vocabulary-migration-audit.md` — end to end. §1 is your
   scope; §2 is your deliverable format; §3 is your categorisation
   rubric; §5 is your acceptance criteria.
4. `spec/93-recipe-collection-storage-and-placement.md` — the
   "schema-internal names stay" rule. Confirms most schema tables
   don't rename.
5. `HANDOVER-2026-06-30-spec159-filters-and-preferred.md` — the
   afternoon session that led into spec/160. Gives you context for
   why the FilterBar widget exists + why it's expected to reuse into
   Cut-compose surfaces.

## The task

Perform the audit per spec/161. Produce exactly one document —
`docs/spec-160-audit.md` — with six sections mirroring spec/161 §1.1
through §1.6, plus a summary block at the top and cross-references as
described in spec/161 §2.

Every hit in the current tree gets one row. Every row has a target.
No row is empty.

## Discipline

- **100 % read-only.** No rename PRs, no `tr()` string swaps, no
  gateway API rewrites, no schema changes. If you're editing a `.py`
  file, you've done it wrong.
- **Grep is your friend.** Use `git grep -in` (case-insensitive) for
  every retiring term (case-insensitive because "Collection" /
  "collection" both count). Reconcile hit-count against your audit
  doc — a `git grep` count that doesn't match your audit table is a
  gap.
- **Exhaustive, not aesthetic.** Better to list a marginal hit and
  flag it "keep — internal note" than to omit it. The auditor's cost
  of a listed-but-kept row is one line; the cost of a missed rename
  is a broken PR two months from now.

## Ambiguity handling

You WILL encounter cases spec/160 didn't cleanly resolve. Examples:

- A gateway method named `list_dcs()` — clearly maps to Media Pools,
  but is the method itself user-facing (via a shell command, a
  developer console, a `--help` output)? Rename or keep?
- A dialog section that mixes vocabulary — "Which items? Filters" —
  where "Filters" alone maps clean but "Which items?" reads
  awkwardly under the Media Pool label.
- An error message that reads *"This recipe references a deleted
  collection."* — every noun in it is retiring simultaneously; the
  sentence needs a rewrite, not a rename.

**Do NOT decide these unilaterally.** Flag every ambiguous case
`unresolved — Nelson decides` in the target column. Come back to
Nelson with the list at the end and get resolutions. Amend spec/160
with the resolutions BEFORE marking the audit complete.

## Deliverable format

Follow spec/161 §2 exactly:

- **Summary block at the top** — total hits per subsection, breaking-
  change count (§1.2 direct-rename tags), doc-banner list (§1.6),
  surprises that need spec/160 amendment.
- **Six tables**, one per subsection, sorted file → line. Column
  shape per the "What to record" bullets in spec/161 §1.
- **Cross-references** — gateway APIs list their callers; dialogs list
  their gateway APIs; tests list their protected category.

Use plain Markdown tables. No fancy layout, no visualisation. This
doc will be read + edited by many people (including next-session
agents), so it needs to render on every Markdown renderer.

## Non-goals

Straight from spec/161 §4:

- **Not the surface plan.** You're documenting what exists, not
  designing the target UI. The surface plan is a separate deliverable.
- **Not implementation.** No renames, no shims, no schema changes.
- **Not a spec/159 §8 fix.** The cross-event projection extension is
  deferred; it's not in scope for this audit.
- **Not legacy data migration.** List what you find; migration
  design belongs to the implementation phase.

## Verification / done criteria

Straight from spec/161 §5. The audit is complete when:

1. Fresh `git grep -in` for each retiring noun turns up nothing that
   isn't in your doc. Reconcile 1:1.
2. Every row has a target column populated. Ambiguous ones carry the
   `unresolved — Nelson decides` flag.
3. Cross-refs resolve: every gateway API's caller list is complete;
   every dialog's gateway dependencies are named; every test's
   category is tagged.
4. Surprises (spec/160 assumptions your audit disproved) are listed
   in the summary and staged for spec/160 amendment.
5. **Nelson signs off.** Come back to Nelson with the doc + the
   ambiguity list. Do NOT self-declare the audit complete.

## Style

- Terse. Every audit doc row is a data point, not a paragraph.
- Consistent. Use spec/160 §7's replacement names verbatim — never
  paraphrase "Media Pool" as "media pool" or "the Pool" in a
  target column.
- Traceable. Every hit carries file path + line number. When Nelson
  reads a target and asks "why?", the answer is one click away.

## Before you code (there is no code)

Before you start scanning, do TWO things and come back to Nelson:

1. **Read spec/160 § 6.2 (visibility matrix) carefully** and confirm
   you can distinguish, for a given filter widget in a current
   dialog, whether it belongs to the event face, the library face,
   or both. If you can't tell, ask.
2. **Estimate.** spec/161 §7 sizes the audit at ~16–22 h in one
   focused block. If your first hour's scan implies dramatically
   more or less, tell Nelson before you keep going.

## When you're done

Commit + push the audit doc. Handover message to Nelson with:

- Total hit counts per subsection (matches the summary block).
- The ambiguity list, formatted as "here are N questions for you."
- Any spec/160 assumption your audit disproved (proposed amendments).

Do NOT begin implementation. Nelson will read + sign off + then
decide whether to task the next phase (spec/160 §9.2 surface plan or
§9.3 phase 1 vocabulary sweep) as a fresh agent session.
