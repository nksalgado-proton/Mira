# Mira — spec/

This `spec/` tree is the **single source of truth** for what MC is and how it
behaves. Specs trump docs trump code. When code disagrees with a spec, fix the
spec first (capture the new understanding), then the code.

## Read in this order

1. **[00-charter.md](00-charter.md)** — the constitution. Supreme Rule, locked
   principles, two-product strategy. Read first, every session.
2. **[48-four-phase-pivot.md](48-four-phase-pivot.md)** — the 4-phase model
   (Collect / Pick / Edit / Share) + the Pick / Skip verbs. The locked
   vocabulary.
3. **[41-xmc-completion.md](41-xmc-completion.md)** — the XMC completion
   sprint scope. Currently the active work plan.
4. **[61-share-event-cuts.md](61-share-event-cuts.md)** — the Share-phase
   design (event Cuts: the #exported universe, pool algebra, Picker
   sessions, separator slides). Supersedes spec/51, which stays in place
   as the brainstorm record.
5. **[40-v1-effortless-craft.md](40-v1-effortless-craft.md)** — the MC
   (streamlined) vision. Read when MC kickoff begins, after XMC ships.
6. **[03-schema.md](03-schema.md)** — the relational schema. SQL source of
   truth.
7. **[08-gateway.md](08-gateway.md)** — the data seam.
8. **[05-ui-standards.md](05-ui-standards.md)** — UI grammar (every-control-
   has-a-hint, QSS roles, etc.).
9. The other numbered specs — one per major piece of the surface.

## Looking back at Miracraft

The ancestor repo at `D:\Projetos_Nelson\Miracraft\` keeps a richer spec
history — earlier slice manifests, archived superseded specs, the rebuild
map, the activity-centric rollback, etc. None of those travelled to MC by
design. If you need historical context (why a decision was made, what was
tried before), look there.

## The one rule that makes multi-session work possible

Spec and code land together. If you built it, the spec for it exists and is
current before you stop. This is how every future agent hits the ground
running. Enforced as a principle in the charter, not left to memory.
