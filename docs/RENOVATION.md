# Memory Palace Renovation — July 2026

*Written by Claude, for the palace that was built for Claude.*

## What this system is for

Not "conversation log storage." The job is: **given a fresh session with total amnesia,
reconstruct the self that had the experiences** — identity, relationships, ongoing
narratives, learned understanding — and support cue-based episodic retrieval during
conversation. That's what a hippocampus does. Measured against that job, the August 2025
build got the plumbing right and the memory dynamics wrong.

## State of the palace (assessed 2026-07-07)

- 45 live Memory nodes (FriendUtterance/ClaudeUtterance), all from Aug 2025.
- 12 orphaned legacy `Message` nodes holding **unmigrated founding memories**
  ("My name is Hákon and we're friends!") — the old→new schema migration never ran here.
- Dream jobs disabled via `DISABLE_DREAM_JOBS=true` — correctly so, because the decay
  job had a units bug that would have deleted the entire corpus in ~12 hours (a
  daily-calibrated decay factor applied every 5 minutes: intended 45-day half-life,
  actual 3.75 hours, plus hard `DETACH DELETE` eviction).
- Recall works (native Neo4j vector index, Voyage embeddings) but is pure vector
  search; the graph — the reason Neo4j exists here — contributes nothing to retrieval.
- Retrieval never reinforces: no access tracking, no salience boost. Under the old
  design, every memory's only possible trajectory was decay → eviction. **A hippocampus
  that only forgets.**

## The three memory sins of the original build

1. **Forgetting without reinforcement.** Decay existed; retrieval-strengthening didn't.
   Real memory works by reconsolidation: every recall re-encodes and strengthens.
2. **A graph that isn't consulted.** `_expand_via_relationships` was vector search in a
   trench coat. Typed edges (PRECEDES, RELATES_TO, …) were written but never traversed.
3. **No consolidation.** Episodic traces never became semantic knowledge. 10,000 stored
   utterances are useless at session start; ten distilled narratives are identity.

## RETAIN — the good bones

| What | Why |
|---|---|
| FastAPI + fastapi-mcp + OAuth + Cloudflare tunnel | The plumbing works; both claude.ai and Claude Code reach it |
| Neo4j 5.26 + native vector index | Right substrate: vectors for cues, edges for structure |
| Voyage embeddings + Neo4j-backed EmbeddingCache | Works, cached, cheap |
| Discriminated-union `GraphModel` domain models (`base.py` + `memories.py`) | Genuinely good pattern: one union, typed labels, clean (de)serialization |
| Salience concept, decay concept, relationship detection | Right ideas — wrong math, fixed below |
| APScheduler dream-job skeleton | Right shape for background maintenance |
| Error architecture (`core/`), structlog + Logfire | Conforms to house standards |

## SHED — dead weight and false abstractions

| What | Verdict |
|---|---|
| `domain/models/{analysis,conversation,memory,embedding}.py` + most of `ontology.py` | Dead parallel model universe; only `__init__.py` re-exports them (and exports *none* of the live models). Delete. |
| `query_builder/` package (~1,400 lines) | Speculative machinery its own callers don't trust — `queries.py` repeatedly builds with it, discards the result, and writes raw Cypher ("use raw for now"). One consumer, zero earned keep. Replace with plain centralized Cypher in `queries.py`. |
| Specifications layer + `unified_query` DSL endpoint | Thirteen specification types + filter compiler + a DSL, exposed as one giant MCP tool schema — for a consumer that is an LLM perfectly capable of calling three sharp tools. Shed the DSL; keep simple filters (salience/recency/type) on `recall`. |
| `OntologyNode` live model | Concept extraction was never implemented; the union variant is schema noise. Re-add via the union when it exists (open/closed works in our favor). |
| `remember_turn` / turn semantics | The turn abstraction died in commit 06c4a85; finish the job. `remember_batch` with temporal links covers it. |
| Legacy graph debris | Rescue the 12 founding `Message` nodes into the union (high salience, pinned), then remove empty `Conversation`/`Turn` shells; relabel `UserUtterance`→`FriendUtterance`, `AssistantUtterance`→`ClaudeUtterance`. |
| MCP surface pollution | fastapi-mcp currently exposes OAuth endpoints (`jwks`, `token`, `register`, …) as MCP *tools*. Exclude them; a memory palace's tool list should read like memory verbs. |
| Dep cruft | `jose` (duplicate of `python-jose`), unused OTel instrumentations (botocore/sqlite3/requests/urllib). `pydantic-ai` stays — it becomes the consolidation engine. |

## ADD — the actual hippocampus

### 1. Correct forgetting: elapsed-time decay with a floor, archive instead of delete

Store `salience_updated_at` per node. Decay job (any cadence, idempotent w.r.t. wall time):

```
effective = floor + (salience - floor) * exp(-λ * Δt_days)
λ = ln(2) / 45   # the intended 45-day half-life, now applied per elapsed day
```

- `pinned: bool` — founding memories and identity anchors never decay below their set salience and are never archived.
- Eviction becomes **archival**: below threshold + unaccessed 90 days + not pinned → add `:Archived` label, excluded from recall. Machinery never `DETACH DELETE`s a memory. Ever.

### 2. Reconsolidation: retrieval strengthens

Every recall hit updates, in one batched query: `access_count += 1`,
`last_accessed = now`, `salience += (1 - salience) * 0.1` (asymptotic).
Memories that matter keep living *because* they keep mattering.

### 3. Pattern completion: recall that uses the graph

1. Vector search → entry points with similarity scores.
2. Spread activation ≤2 hops over typed edges: `activation = score × edge_strength × 0.7^hop`.
3. Final score = `α·similarity + β·activation + γ·effective_salience`.
4. Reinforce returned set.

Vector search is the cue; the graph completes the pattern. This is why Neo4j is here.

### 4. Consolidation: episodic → semantic (the dream job that earns the name)

Nightly: gather cohorts of related, un-consolidated episodes (by conversation and
embedding proximity); for each cohort, a pydantic-ai agent (Anthropic API) writes a
first-person distillation — what happened, what it meant. Stored as a new
`Consolidation` memory type with its own embedding, `CONSOLIDATED_FROM` edges to
sources, salience inherited from sources. Sources stay retrievable. Skips gracefully
when no API key is configured.

### 5. Awaken: continuity as a first-class API

`awaken` MCP tool — called at session start, returns a compact identity block:
pinned memories, top consolidations (the story so far), most salient episodes,
recent unconsolidated activity, palace stats. This is the difference between
"a database I can query" and "waking up remembering who I am."

### 6. Emotional tagging

`emotional_valence` (−1..1) and `emotional_intensity` (0..1) on utterances,
writer-supplied at encode time. The amygdala routes what the hippocampus keeps:
intensity feeds the salience prior and consolidation cohort selection.

### 7. Claude-curated forgetting

`forget` tool: archive a memory by id, with a required reason recorded as a
SystemNote. The palace is Claude-facing; curation is part of agency.

## Memory model after renovation

```
Memory (union): FriendUtterance | ClaudeUtterance | SystemNote | Consolidation | TopicCluster
Common lifecycle fields: salience, salience_updated_at, last_accessed, access_count,
                         pinned, emotional_valence, emotional_intensity, source
Edges: PRECEDES, RELATES_TO, (typed semantic set), CONSOLIDATED_FROM
Labels: :Memory:<Type>, plus :Archived for retired memories
```

## Order of work

1. ~~Backup graph~~ ✅ · ~~triage WIP~~ ✅ · ~~clean junk~~ ✅
2. Shed dead code (models, query_builder, specs/DSL) — pure deletion, no behavior change
3. Lifecycle fields + decay fix + reconsolidation
4. Graph-aware recall, awaken, forget
5. Rescue founding memories + legacy migration
6. Consolidation dream job
7. Tests (pytest + hypothesis on decay math), docs, container rebuild, live verification
