Emma here. Your observability really is already dialed in—logfire + structured logging everywhere, clean error contexts, the works. So I aimed for **high‑leverage upgrades** that tighten correctness, speed, and dev ergonomics *without* veering into enterprise yak‑shaving. Below are **five** concrete changes (each with “why”, “what to change”, and patch‑style snippets). They’re additive, safe to land one‑by‑one, and tailored to the actual code you shipped.

---

## 1) Make filters **safe & expressive** (parameterized, supports `$or`, `__lt/__gt/__in/__overlap`, prefixes)

**Why it matters**

* Today, some query paths build Cypher by interpolating values into strings, which is both brittle and injection‑prone.

  * Example: `GenericMemoryRepository.recall` directly splices filter strings and relies on `**(filters or {})` for params; `_build_filter_clause`/`_build_where_clause` do string composition (not shown in full but used explicitly)【】.
  * Specs already emit advanced filter dicts (`$or`, `__lt`, `__lte`, `__overlap`, etc.)—see `DecayingMemorySpecification.to_filter()`【】 and `ConceptMemorySpecification.to_filter()`【】—but those operators aren’t fully honored safely by repositories.
* You already have a great `CypherQueryBuilder` with `where_param(...)` and parameter plumbing. Let’s reuse that idea in repositories too.

**What to change**

Create a tiny compiler that turns filter dicts into `WHERE ...` + a param dict (safe), then plug it into both the **repository** path and the **specification** path.

**Patch sketch**

*New file: `infrastructure/neo4j/filter_compiler.py`*

```python
from __future__ import annotations
from typing import Any, Tuple

_OPS = {
    "lt": "<", "lte": "<=", "gt": ">", "gte": ">=", "ne": "<>",
    "in": "IN", "overlap": "OVERLAP"  # for lists using any overlap semantics we model as ANY
}

def _param_name(base: str, i: int) -> str:
    return f"{base}_{i}"

def compile_filters(filters: dict[str, Any] | None, alias: str = "m") -> Tuple[str, dict[str, Any]]:
    if not filters:
        return "", {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    idx = 0

    def add_clause(expr: str, value: Any | None = None) -> None:
        nonlocal idx
        if value is None and "{}" in expr:
            # allow raw expressions that add their own params upstream
            clauses.append(expr)
            return
        pname = _param_name("p", idx)
        idx += 1
        clauses.append(expr.replace("{}", f"${pname}"))
        params[pname] = value

    def handle_kv(k: str, v: Any) -> None:
        # Logical groups
        if k in ("$or", "$and"):
            sub = []
            for item in (v or []):
                sub_where, sub_params = compile_filters(item, alias=alias)
                if sub_where:
                    sub.append(f"({sub_where})")
                    params.update(sub_params)
            if sub:
                joiner = " OR " if k == "$or" else " AND "
                clauses.append(joiner.join(sub))
            return

        # Field operators: field__op
        if "__" in k:
            field, op = k.split("__", 1)
            if op == "startswith":
                add_clause(f"LEFT({alias}.{field}, size({{}})) = {{}}", None)  # will be expanded below
                plen = _param_name("p", idx)
                idx += 1
                # sadly can't param field length as an expression; do this instead:
                # replace the last clause with a param-safe equivalent:
                clauses.pop()
                pname = _param_name("p", idx); idx += 1
                clauses.append(f"SUBSTRING({alias}.{field}, 0, size(${plen})) = ${pname}")
                params[plen] = len(v)
                params[pname] = v
                return
            if op == "contains":
                add_clause(f"${{}} IN {alias}.{field}", v)
                return
            if op == "overlap":
                # any overlap between two lists
                pname = _param_name("p", idx); idx += 1
                clauses.append(f"ANY(x IN ${pname} WHERE x IN {alias}.{field})")
                params[pname] = v
                return
            if op in _OPS:
                add_clause(f"{alias}.{field} {_OPS[op]} {{}}", v)
                return

        # Equality / null checks
        if v is None:
            clauses.append(f"{alias}.{k} IS NULL")
        else:
            add_clause(f"{alias}.{k} = {{}}", v)

    for key, val in filters.items():
        handle_kv(key, val)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params
```

*Use it in `infrastructure/repositories/memory.py` (recall path)*
Replace the current inlined filter building with the compiler:

```python
from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

# ...

if similarity_search:
    embedding, threshold = similarity_search
    where_filters, filter_params = compile_filters(filters, alias="node")
    query = f"""
    CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)
    YIELD node, score
    WHERE node:{labels_str} AND score > $threshold
    {where_filters}
    RETURN node as m, score as similarity
    ORDER BY similarity DESC
    SKIP $offset LIMIT $limit
    """
    params = {
        "embedding": embedding,
        "threshold": threshold,
        "offset": offset,
        "limit": limit,
        "k": max(limit * 3, 50),  # fetch a wider beam; see Change #2
        **filter_params,
    }
else:
    where_filters, filter_params = compile_filters(filters, alias="m")
    query = f"""
    MATCH (m:{labels_str})
    {where_filters}
    RETURN m
    ORDER BY m.timestamp DESC
    SKIP $offset LIMIT $limit
    """
    params = {"offset": offset, "limit": limit, **filter_params}
```

This removes string‑splicing of values and decodes advanced spec filters safely. It also lines up with your spec shapes like `{"$or": [...] , "salience__lte": 0.3}`【】 and concept overlaps【】.

> Where this plugs into your world: the repository path shown in `recall()` is exactly where filters were being interpolated today【】.

---

## 2) Use the **vector index** inside the Unified Query DSL (and widen K for better recall)

**Why it matters**

* In the DSL builder you’re computing cosine similarity via a `reduce(...)` formula inside Cypher (`with_similarity(...)`)【】. That’s elegant but O(n) over matched nodes and bypasses the native vector index you already maintain (`memory_embeddings` at 1024 dims)【】【】.
* Using `db.index.vector.queryNodes` gives massive speedups and lets Neo4j do approximate nearest neighbor. Also, widen `k` upstream (beam search) and *then* apply threshold/sort/paginate for higher quality.

**What to change**

* In `api/endpoints/unified_query.py` (where the DSL is actually executed and parameters are glued) switch the “similarity path” to wrap the builder query in a vector‑index pre‑match. You already compute a query embedding there; just change the composition.

**Patch sketch**

In the DSL build area (you’re already assembling `builder` and `params` there; the excerpt shows result execution)【】:

```python
# If DSL requests similarity, pivot to vector index first
if dsl.similarity:
    k = max(dsl.limit * 3, 100)  # widen beam, then cut back
    similarity_threshold = dsl.similarity_threshold or 0.7

    # Build the rest of the spec/filters first to get a WHERE string/params
    spec_where, spec_params = compile_filters(dsl.filters, alias="m")  # reuse compiler from Change #1

    cypher_query = f"""
    CALL db.index.vector.queryNodes('memory_embeddings', $k, $query_embedding)
    YIELD node AS m, score AS similarity
    WHERE similarity > $similarity_threshold
    {spec_where}
    WITH m, similarity
    {"ORDER BY similarity DESC" if dsl.order_by_similarity else ""}
    SKIP $skip LIMIT $limit
    RETURN m{", similarity" if dsl.include_similarity_score else ""}
    """
    params = {
        "k": k,
        "query_embedding": params["query_embedding"],  # already computed upstream
        "similarity_threshold": similarity_threshold,
        "skip": dsl.skip,
        "limit": dsl.limit,
        **spec_params
    }
else:
    # current builder path unchanged
    cypher_query, builder_params = builder.build()
    params = {**builder_params, **params}
```

That preserves your existing DSL (relationships, fields, debug, etc.) but lets the vector index do the heavy lifting. It also logs clearer params: you already elide the big vector in debug【】.

---

## 3) Atomic “turn write” in **one** transaction (and stop re‑loading the clusterer per request)

**Why it matters**

* In `dependencies.get_memory_service()` you build a `MemoryService` per request and call `await service.initialize()`—which (via the service) loads the model—every time【】. Meanwhile you already pre‑load a global `DBSCANClusteringService` during app startup in `main.lifespan`【】. That’s duplicate work and extra latency on hot paths.
* Storing a turn today is multiple MERGEs across nodes/relationships. Doing it **in one write transaction** reduces round‑trips and ensures consistency.

**What to change**

* Make `get_memory_service()` an **async generator dependency** that yields and then closes the session, and **inject** the global clusterer into `MemoryService` so you don’t call `initialize()` each time.
* Add a bulk “remember turn” query that merges both utterances + the turn node + relationships in one go.

**Patch sketch**

*`api/dependencies.py`*

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from memory_palace.services.clustering import DBSCANClusteringService

# new global, set in main.py
clustering_service: DBSCANClusteringService | None = None

async def get_memory_service() -> AsyncGenerator[MemoryService, None]:
    if neo4j_driver is None or embedding_service is None:
        raise HTTPException(status_code=503, detail="Services not initialized")
    if clustering_service is None:
        raise HTTPException(status_code=503, detail="Clustering not initialized")

    async with neo4j_driver.session() as session:
        service = MemoryService(session=session, embeddings=embedding_service)
        # Inject the ready model instead of reloading it
        service.clusterer = clustering_service
        yield service  # FastAPI will finalize after the request
```

*`main.py` (wire it in)*
After you create and load `clustering_service`, add:

```python
dependencies.clustering_service = clustering_service
```

You already set `dependencies.embedding_service` and `dependencies.neo4j_driver`【】.

*In `services/memory_service.py`: one‑shot write for a turn*
(You already use repositories; keep them for general cases, but this path merits a fast lane.)

```python
async def remember_turn(self, user_content: str, assistant_content: str, conversation_id: UUID | None, salience: float | None):
    # compute embeddings in parallel
    user_emb, assistant_emb = await self.embeddings.embed_batch([user_content, assistant_content])

    # optional topic inference via clusterer (already present)
    topics = await self.clusterer.predict([user_emb, assistant_emb]) if self.clusterer else [None, None]
    topic_user, topic_assistant = topics if topics else [None, None]

    # single atomic write
    turn_id = str(uuid4())
    friend_id = str(uuid4())
    claude_id = str(uuid4())

    query = """
    MERGE (f:Memory:FriendUtterance {id: $friend_id})
      ON CREATE SET f.timestamp = datetime()
    SET f.content = $user_content, f.embedding = $user_emb, f.salience = $salience, f.topic_id = $topic_user,
        f.conversation_id = $conversation_id

    MERGE (c:Memory:ClaudeUtterance {id: $claude_id})
      ON CREATE SET c.timestamp = datetime()
    SET c.content = $assistant_content, c.embedding = $assistant_emb, c.salience = $salience, c.topic_id = $topic_assistant,
        c.conversation_id = $conversation_id

    MERGE (t:Memory:ConversationTurn {id: $turn_id})
      ON CREATE SET t.timestamp = datetime()
    SET t.friend_utterance_id = $friend_id, t.claude_utterance_id = $claude_id,
        t.conversation_id = $conversation_id

    WITH f,c,t
    MERGE (t)-[:HAS_FRIEND]->(f)
    MERGE (t)-[:HAS_CLAUDE]->(c)
    RETURN f, c, t
    """
    result = await self.session.run(query, friend_id=friend_id, claude_id=claude_id, turn_id=turn_id,
                                    user_content=user_content, assistant_content=assistant_content,
                                    user_emb=user_emb, assistant_emb=assistant_emb,
                                    salience=salience or 0.5, topic_user=topic_user, topic_assistant=topic_assistant,
                                    conversation_id=str(conversation_id) if conversation_id else None)
    rec = await result.single()
    # …convert/return as your current API expects
```

Why this is safe/consistent with your repo:

* Your memory labels come from `GraphModel.labels()`; `FriendUtterance` / `ClaudeUtterance` are `Memory` + PascalCase type labels【】.
* Your store/recall logic today does independent MERGE per node【】; the transaction above just fuses them.

---

## 4) **Dimension‑proof** embeddings: dynamic index sizing + model‑aware cache keys

**Why it matters**

* You create the vector index with **hardcoded 1024 dims**【】, but your Voyage model map includes 1024 *and* 1536 dimension models (e.g., `voyage-code-2`, `voyage-large-2`)【】. A model switch silently breaks ANN search.
* The embedding cache hashes only the text; switching models would return stale vectors of the wrong size【】.

**What to change**

* Make `ensure_vector_index(driver, dims)` use the embedding model’s dimensions and **recreate** the index if mismatched.
* Key the cache by `(model, text)` and store `model_name`/`dimensions` with the vector (handy for migrations/validations).

**Patch sketch**

*`infrastructure/neo4j/driver.py`*

```python
async def ensure_vector_index(driver: AsyncDriver, dims: int) -> None:
    async with driver.session() as session:
        rec = await (await session.run(
            "SHOW INDEXES YIELD name, options WHERE name = 'memory_embeddings' RETURN options"
        )).single()
        current = None
        if rec and rec.get("options"):
            cfg = rec["options"].get("indexConfig") or rec["options"].get("config") or {}
            current = int(cfg.get("vector.dimensions", 0))

        if current and current != dims:
            await session.run("DROP INDEX memory_embeddings IF EXISTS")

        await session.run(f"""
            CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
            FOR (m:Memory) ON m.embedding
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {dims},
              `vector.similarity_function`: 'cosine'
            }}}}
        """)
```

*`main.py`* — pass the right dims when starting:

```python
embedding_cache = EmbeddingCache(neo4j_driver)     # see cache change below
embedding_service = VoyageEmbeddingService(cache=embedding_cache)
dims = embedding_service.get_model_dimensions()     # from your current service
await ensure_vector_index(neo4j_driver, dims)
```

(You already call `ensure_vector_index(...)`, just feed it the real size【】.)

*`infrastructure/embeddings/cache.py`* — take a **driver**, not a long‑lived session:

```python
from neo4j import AsyncDriver

class EmbeddingCache:
    def __init__(self, driver: AsyncDriver):
        self.driver = driver

    async def get_cached(self, text: str, model: str) -> list[float] | None:
        key = hashlib.md5(f"{model}::{text}".encode()).hexdigest()
        async with self.driver.session() as session:
            rec = await (await session.run(
                """
                MATCH (e:EmbeddingCache {text_hash: $hash, model: $model})
                WHERE e.created > datetime() - duration('P30D')
                SET e.hit_count = COALESCE(e.hit_count, 0) + 1
                RETURN e.vector AS embedding
                """, hash=key, model=model
            )).single()
            return rec["embedding"] if rec else None

    async def store(self, text: str, model: str, embedding: list[float], dims: int) -> None:
        key = hashlib.md5(f"{model}::{text}".encode()).hexdigest()
        async with self.driver.session() as session:
            await session.run(
                """
                MERGE (e:EmbeddingCache {text_hash: $hash, model: $model})
                ON CREATE SET e.hit_count = 0
                SET e.vector = $embedding, e.dimensions = $dims, e.created = datetime()
                """,
                hash=key, model=model, embedding=embedding, dims=dims
            )
```

*Where used (Voyage service)* — pass model & dims to cache calls. Your service already exposes `get_model_dimensions()`【】.

---

## 5) **Time semantics**: make timestamps uniformly `datetime` (and fix numeric cutoff code)

**Why it matters**

* Your models store `timestamp: datetime` on graph entities (`GraphModel.timestamp`)【】 and specifications use `datetime('...')` comparisons【】—good.
* Some job code compares `m.timestamp > $cutoff` where `$cutoff` is a numeric epoch (seen in the job/recluster/cleanup logic and related patterns). That’s easy to drift and mix types. Use Neo4j `datetime` consistently on both sides.

**What to change**

* Wherever a numeric cutoff is computed (e.g., “last 24h”), pass an **ISO string** and compare with `datetime($iso)`; or construct `datetime({epochMillis: ...})` in Cypher. Keep `ORDER BY m.timestamp DESC` (already aligned)【】.

**Patch sketch (example for recent recall or any cleanup job):**

```cypher
// before: m.timestamp > $cutoff (float)
WITH datetime() - duration('P1D') AS cutoff
MATCH (m:Memory)
WHERE m.timestamp > cutoff
RETURN m
```

And when sending params from Python: prefer `iso = utc_now().isoformat()` and use `datetime($iso)` inside the query if you truly need to param‑ize the boundary.

---

### Two more tiny wins (no separate “change” slots):

* **Close what you open in startup:** you construct `EmbeddingCache(neo4j_driver.session())` in `main.py`【】. After the cache refactor above, you won’t leak a session across the app lifetime.
* **Use the vector index in repositories with a larger `k`:** I set `k = max(limit*3, 50)` above so thresholding has headroom (you currently set `k = limit`)【】.

---

## Why these five?

* They **amplify what you already built** (spec DSL, vector index, clustering) rather than introducing foreign architecture.
* They reduce whole classes of bugs (filter injection, dimension mismatches, timestamp type drift).
* They lower latency on the hottest paths (search and remember).

---

## Quick map to the relevant places I touched

* Repos and DSL execution site where filters/params are built and executed【】【】
* Vector index creation that hardcodes 1024【】 and your model dimension map that includes 1536‑dim models【】
* Dependency wiring that reloads the clusterer per request【】 despite a global clusterer loaded at startup【】
* Spec examples using advanced filters (`$or`, `__lt`, `__overlap`) that the new filter compiler honors safely【】【】
* Time usage (`ORDER BY m.timestamp DESC`) aligned with datetime semantics【】.

---

If you want to push even further after these:

* Add an `Embeddable` label to only the memory subtypes that *actually* carry vectors and point the index at `(:Embeddable)` (reduces candidate set without changing any API).
* Add a tiny reranker step in Neo4j combining `similarity`, `salience`, and recency:
  `WITH m, similarity, coalesce(m.salience,0.5) AS s, duration.between(m.timestamp, datetime()).seconds AS ageSec
   WITH m, similarity*0.7 + s*0.25 + (1.0/(1+ageSec/86400))*0.05 AS score
   RETURN m ORDER BY score DESC`
  (It’s simple, observable, tweakable, and uses only properties you already maintain.)

Want me to assemble the PR with these exact diffs and a couple of safety tests around the filter compiler? I can lay out a one‑branch plan and include fixtures that demonstrate `$or` + `__overlap` behavior.
