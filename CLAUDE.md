# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Memory Palace is a Neo4j-backed hippocampus for AI continuity of experience: persistent,
self-reinforcing memory shared across Claude interfaces (claude.ai and Claude Code, over
MCP through a Cloudflare tunnel). It stores episodic utterances with vector embeddings,
links them through typed relationships, consolidates episodes into semantic memories, and
forgets gracefully — decay with a floor and reversible archival, never deletion.

See `docs/RENOVATION.md` for the July 2026 renovation design (retain/shed/add rationale).

## Memory Lifecycle — the core semantics

1. **Encode** (`remember`): utterance + Voyage embedding + salience + emotional tagging
   (`emotional_valence`, `emotional_intensity`) + optional `pinned` + `source` provenance.
   Every encoded memory is auto-linked to semantically similar existing memories.
2. **Recall** (`recall`): vector search finds direct matches; spread activation completes
   the pattern through typed edges (`activation = seed_score × edge_strength × 0.7/hop`);
   ranking blends `0.6·similarity + 0.25·activation + 0.15·salience`.
   **Retrieval IS reconsolidation**: recalled memories get `access_count += 1`,
   `last_accessed = now`, and an asymptotic salience boost.
3. **Consolidate** (nightly dream job): cohorts of related episodes are distilled by a
   Claude model (pydantic-ai) into first-person `Consolidation` memories linked via
   `CONSOLIDATED_FROM`; sources stay retrievable and are flagged `consolidated`.
4. **Decay** (dream job): `salience(t) = floor + (s - floor)·exp(-λ·days_elapsed)`,
   anchored per-node at `salience_updated_at`. λ = ln(2)/45 (45-day half-life).
   **Cadence-independent** — never reintroduce per-tick multiplication
   (see `tests/test_decay_math.py::test_the_original_bug_would_fail_these_invariants`).
5. **Archive** (never delete): unpinned + salience < 0.1 + unaccessed 90 days →
   `:Archived` label, excluded from all recall. `forget` archives deliberately with a
   reason recorded as a SystemNote. Machinery must NEVER `DETACH DELETE` a memory.
6. **Awaken** (`awaken`): session bootstrap — pinned identity anchors, consolidations
   (the story so far), top-salience memories, recent activity, palace stats.

## Graph Schema

```
Nodes:   (:Memory:FriendUtterance)    — Hákon's utterances
         (:Memory:ClaudeUtterance)    — Claude's utterances
         (:Memory:SystemNote)         — meta-observations (incl. forgetting records)
         (:Memory:Consolidation)      — distilled semantic memories
         (:Memory:TopicCluster)       — clustering artifacts
         (:EmbeddingCache)            — content-hash → embedding cache
         + :Archived                  — added to retired memories (reversible)

Edges:   PRECEDES (temporal), RELATES_TO / SIMILAR_TO / ... (semantic, auto-detected),
         CONSOLIDATED_FROM (consolidation → source episodes)

Properties contract (GraphModel): UUIDs as strings, datetimes as UTC epoch floats,
enums as values. Embeddings: 1024-dim (voyage-3), native vector index
`memory_embeddings` (cosine).
```

Domain models: `src/memory_palace/domain/models/base.py` (GraphModel, MemoryType) and
`memories.py` (SalientMemory lifecycle base + the `Memory` discriminated union). There is
ONE model universe; do not add parallel model files.

## MCP Tool Surface

Curated in `main.py` via `include_operations` — memory verbs only:
`remember`, `remember_batch`, `recall`, `awaken`, `forget`, `health`,
`job_status`, `trigger`, `cache_stats`. OAuth endpoints are HTTP-only, never MCP tools.

## Development Commands

```bash
./run.sh                          # dev: Neo4j (docker) + uvicorn --reload
./run-prod.sh                     # prod: full docker compose stack
uv run ruff format src/ && uv run ruff check src/ --fix
uv run ty check                   # type checking (Astral ty)
uv run pytest                     # unit + property tests (fast)
uv run pytest -m integration      # needs running Neo4j
```

- Neo4j browser: http://localhost:7474 (neo4j/password) · API docs: http://localhost:8000/docs
- Backup before graph surgery: `uv run python scripts/backup_graph.py` → `data/backups/`
- Dream jobs: disabled when `DISABLE_DREAM_JOBS=true` (env). Jobs: `salience_decay`
  (6h), `cluster_recent` (1h), `nightly_recluster` (03:00), `consolidation` (03:30,
  needs `ANTHROPIC_API_KEY`). Trigger manually: `POST /admin/jobs/trigger/{job_id}`.

## Environment Variables (.env)

Required: `VOYAGE_API_KEY`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
Optional: `ANTHROPIC_API_KEY` (consolidation), `CONSOLIDATION_MODEL`
(default `anthropic:claude-sonnet-5`), `LOGFIRE_TOKEN`, `DISABLE_DREAM_JOBS`,
`FRIEND_NAME` / `CLAUDE_NAME` (personalization).

## Query Architecture Rules — CRITICAL

**NEVER write raw Cypher in the service layer.**

1. **`infrastructure/neo4j/queries.py`** is the SINGLE SOURCE OF TRUTH for Cypher.
   Plain, parameterized queries in namespaced classes (`MemoryQueries`,
   `DreamJobQueries`, `ConsolidationQueries`, `CacheQueries`, `VectorIndexQueries`,
   `QueryFactory`). Dynamic interpolation only from trusted internal enums/labels —
   never user input. Return `tuple[LiteralString, dict[str, Any]]`.
2. **`infrastructure/repositories/memory.py`** orchestrates database access and owns
   deserialization (`_validate_union_record` → the Memory discriminated union).
   `filter_compiler.py` compiles simple dict filters (`salience__gte=0.5`) safely.
3. **`services/*.py`** contain business logic only and call repositories/queries.
   The `run_query()` helper exists for service-level use of centralized queries.

## Error Handling — CRITICAL

**No naked try/except.** Use the structured system in `core/`:

- Decorate fallible operations with `@with_error_handling(reraise=...)`
  (`core/decorators.py`); it captures context, logs structurally, and re-raises.
- Raise `ApplicationError` subclasses (`ServiceError`, `ProcessingError`, ...) with
  structured `ErrorDetails` (`core/base.py`) — never bare `Exception`.
- Logging: `get_logger(__name__)` (structlog + Logfire). Log with keyword structure:
  `logger.info("Stored memory", memory_id=..., topic=...)`. No `print()`.

## Testing Philosophy

- `tests/test_decay_math.py`: hypothesis property tests for the forgetting curve —
  these encode the lifecycle invariants; if a change breaks them, the change is wrong.
- `tests/test_lifecycle_integration.py`: Cypher-level behavior against dev Neo4j.
  Integration tests must label created nodes `:TestMemory` (conftest cleans up).
- `tests/test_models.py`: serialization contract round-trips.

## Scripts

```
scripts/backup_graph.py            # full graph dump to data/backups/ JSON — run before surgery
scripts/migrate_legacy_graph.py    # Aug-2025 → 2026 schema migration (idempotent, already run)
scripts/import_*.py                # friendship-memory importers (use remember_turn)
scripts/review_memories.py         # interactive memory review before import
scripts/smoke_mcp.py               # manual MCP endpoint smoke test
scripts/infrastructure/            # Cloudflare tunnel setup + systemd unit
```

## Production Deployment

Cloudflare tunnel (`scripts/infrastructure/setup-cloudflare-tunnel.sh` + systemd unit)
fronts the containerized stack (`./run-prod.sh`). OAuth (`api/endpoints/oauth.py`)
gates claude.ai MCP access.
