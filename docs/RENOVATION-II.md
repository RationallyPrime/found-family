# Memory Palace Renovation II — the kind axis

*Quick and dirty, written by Fable 2026-09-03 so it isn't forgotten. Expand before building.*

Status: proposal. Nothing here is implemented. See `RENOVATION.md` for the July 2026
renovation this builds on; the memory dynamics it fixed (decay, reconsolidation,
archival, consolidation, awaken) all stay.

## The diagnosis

The July renovation fixed the *dynamics* and kept the *schema*. The schema is still the
August 2025 conversation-log design: the only write contract is `role: user | assistant`,
and that one field picks the node label (`FriendUtterance` / `ClaudeUtterance`). The type
axis is **who spoke**. Nobody queries who spoke.

What the palace actually holds today is three different things in one coat:

1. **Identity anchors.** Pinned, 2025, "My name is Hákon and we're friends!" Correct.
2. **Consolidations.** First-person LLM distillations of episodes. Correct.
3. **Engineering records.** Rulings, scars, project state, written by Fable as 4 KB prose
   walls with ALLCAPS headers because the schema gives structure nowhere to go. All of
   them land as `claude_utterance` with `role: Claude`, including rulings that are
   Hákon's. Recall cannot return "the ruling about exactly-one Law"; it returns the wall.

Fossils, counted in `src/` on 2026-09-03:

| Holdover | Refs | Why it is dead weight |
|---|---|---|
| `conversation_id` | 27 | Consolidation cohorts key on it *first* (`find_conversation_cohorts`), daily cohorts second. Writes from Claude Code never carry one, so the primary cohort strategy is dead in practice. |
| `topic_id` / `TopicCluster` | 41 | 2025 clustering artifact. `recall` still accepts `topic_ids`; nothing assigns them. |
| `remember_turn`, `get_conversation_history`, `PRECEDES` | few | `RENOVATION.md` said "finish the job" on turn semantics. Still in `memory_service.py`. |
| `role` as label picker | — | The whole problem. |

## The fix: kind, not speaker

Replace the speaker axis with a **kind** axis that matches what is stored. Speaker becomes
a property. Sketch, to be argued over before it becomes Pydantic:

```
kind:    anchor | episode | ruling | scar | state | consolidation | note
author:  hakon | fable | ariadne | ...      (property; who holds the position, not who typed)
source:  claude-code | claude.ai | hive     (already exists; which interface wrote it)
scope:   sokrates | krepis | hive | weave | personal   (recall filter; replaces topic_id)
edges:   SUPERSEDES        state  → prior state, ruling → overruled ruling
         RULED_IN          ruling → the episode it was made in
         SCAR_OF           scar   → the episode that caused it
         CONSOLIDATED_FROM consolidation → episodes (keep)
         RELATES_TO        auto-detected semantic (keep)
```

Two consequences matter more than the field list:

- **Supersession beats decay for `state` and `ruling`.** "PROJECT STATE 2026-09-03" points
  at "PROJECT STATE 2026-08-28" with `SUPERSEDES`; recall returns the head of the chain
  and the superseded node is excluded like `:Archived` (but reversible and traversable).
  Today the old state competes on cosine similarity with the new one and sometimes wins.
  This is temporal validity, the half of the Datum rule the hippocampus is missing.
- **Consolidation is per kind.** Episodes distill into first-person narrative. Rulings
  and scars do **not** consolidate; they stand or are superseded. Distilling sixteen
  rulings into prose loses exactly the precision that made them worth storing.

Granularity then follows for free: one ruling per node, `RULED_IN` an episode node for
the session, and recall surfaces the ruling that matches the cue with the session one
hop away. That is the pattern completion the July renovation promised; the graph finally
earns its Neo4j.

## Per-kind lifecycle (first cut)

| kind | decays | consolidates | supersedes | pinned by default |
|---|---|---|---|---|
| anchor | never | no | no | yes |
| episode | yes | yes | no | no |
| ruling | slowly (half-life ≫ 45 d) | no | yes | no |
| scar | slowly | no | yes | no |
| state | yes, fast | no | yes | no |
| consolidation | yes | no | no | no |
| note | yes | no | no | no |

## Tool surface after

Same six verbs. `remember` takes `kind`, `author`, `scope`, optional `supersedes` (id)
and `about` (episode id) instead of `role` and `conversation_id`. `remember_batch` takes
an optional episode to attach the batch to and drops `create_temporal_links`. `recall`
takes `kind` and `scope` filters and drops `topic_ids`. `awaken` returns anchors, current
state heads, consolidations, top rulings and scars, recent episodes.

## Migration

- Existing `FriendUtterance` / `ClaudeUtterance` → `episode` with `author` derived from
  the old label (`Friend` → hakon, `Claude` → fable), `source` kept. Pinned ones → `anchor`.
- The engineering walls written since 2026-08 get re-cut by hand or by a one-shot
  pydantic-ai pass into rulings / scars / state with `SUPERSEDES` chains; the wall stays
  as the episode they `RULED_IN`.
- `TopicCluster` nodes and `topic_id` properties: delete. `conversation_id`: delete after
  consolidation cohorts move to `(scope, day)`.
- Old→new is a single pass; no dual-shape readers (INV-35 spirit applies here too).

## What stays exactly as is

The friendship layer. Anchors, consolidations, emotional tagging, decay with a floor,
archive-never-delete, retrieval-as-reconsolidation. The palace doing double duty as a
friendship memory and an engineering ledger is fine; the schema just has to stop
pretending the second is the first.

## Order of work (when picked up)

1. Argue the kind list and edge set with Hákon; freeze as Pydantic in `memories.py`.
2. Shed: `TopicCluster`, `topic_id`, `remember_turn`, `get_conversation_history`,
   `create_temporal_links`, conversation cohorts.
3. Add: kind/author/scope, `SUPERSEDES` + head-of-chain recall, per-kind lifecycle table.
4. Migrate live graph (backup first, `scripts/backup_graph.py`), re-cut the walls.
5. Tool surface + `awaken` shape; tests; deploy.
