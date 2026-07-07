"""Centralized Cypher query definitions.

This is the SINGLE SOURCE OF TRUTH for all Cypher queries in the application.
Queries are plain, parameterized Cypher: what runs against the database is
exactly what you read here. Dynamic parts (labels, relationship types) are
interpolated from trusted internal enums/models only — never from user input.
"""

from typing import Any, LiteralString, cast

from memory_palace.core.logging import get_logger

logger = get_logger(__name__)


class MemoryQueries:
    """All memory-related queries in one place."""

    @staticmethod
    def similarity_search(
        labels: str | None = None,
        additional_filters: str | None = None,
    ) -> tuple[LiteralString, dict[str, Any]]:
        """Vector-index similarity search.

        Args:
            labels: Optional node label filter (e.g., "FriendUtterance")
            additional_filters: Optional pre-compiled WHERE conditions
                (from filter_compiler, parameterized — never raw user input)

        Returns:
            Tuple of (query, params)
        """
        where_conditions = ["score > $threshold", "NOT node:Archived"]
        if labels:
            where_conditions.append(f"node:{labels}")
        if additional_filters:
            where_conditions.append(additional_filters)

        query = f"""
            CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)
            YIELD node, score
            WHERE {" AND ".join(where_conditions)}
            RETURN node AS m, score AS similarity
            ORDER BY similarity DESC
            SKIP $offset LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def store_memory_merge(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """MERGE query for storing/updating a memory.

        Args:
            labels: Node labels (from GraphModel.labels(), trusted)
        """
        labels_str = ":".join(labels)

        query = f"""
            MERGE (m:{labels_str} {{id: $id}})
            SET m += $properties
            RETURN m
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_memory_by_id(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """Get a specific memory by ID."""
        labels_str = ":".join(labels)

        query = f"""
            MATCH (m:{labels_str} {{id: $id}})
            RETURN m
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def create_relationship(relationship_type: str = "RELATES_TO") -> tuple[LiteralString, dict[str, Any]]:
        """Create (or update) a relationship between two memories."""
        query = f"""
            MATCH (source:Memory {{id: $source_id}})
            MATCH (target:Memory {{id: $target_id}})
            MERGE (source)-[r:`{relationship_type}`]->(target)
            SET r += $properties
            RETURN r
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def delete_relationship(relationship_type: str | None = None) -> tuple[LiteralString, dict[str, Any]]:
        """Delete relationship(s) between two memories."""
        if relationship_type:
            query = f"""
                MATCH (source:Memory {{id: $source_id}})-[r:`{relationship_type}`]->(target:Memory {{id: $target_id}})
                DELETE r
                RETURN count(r) AS deleted
                """
        else:
            query = """
                MATCH (source:Memory {id: $source_id})-[r]->(target:Memory {id: $target_id})
                DELETE r
                RETURN count(r) AS deleted
                """

        return cast(LiteralString, query), {}

    @staticmethod
    def delete_memory(labels: list[str] | None = None) -> tuple[LiteralString, dict[str, Any]]:
        """Hard-delete a memory and its relationships by id.

        For explicit administrative deletion only — lifecycle machinery
        archives, it never deletes.
        """
        labels_str = ":".join(labels) if labels else "Memory"

        query = f"""
            MATCH (m:{labels_str} {{id: $id}})
            DETACH DELETE m
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def detect_relationships() -> tuple[LiteralString, dict[str, Any]]:
        """Find similar memories for relationship detection."""
        query = """
            CALL db.index.vector.queryNodes('memory_embeddings', 5, $embedding)
            YIELD node, score
            WHERE node.id <> $id AND score > $threshold
            RETURN node AS other, score AS similarity
            ORDER BY similarity DESC
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def spread_activation(depth: int) -> tuple[LiteralString, dict[str, Any]]:
        """Pattern completion: spread activation from seed memories over typed edges.

        For each seed (a vector-search hit carrying its similarity score),
        traverses up to `depth` hops over any relationship. Activation along
        a path = seed_score * PROD(edge strength * hop_decay). Each reached
        memory keeps its strongest activation across all paths/seeds.

        Args:
            depth: Max hops (interpolated — Cypher cannot parameterize
                variable-length bounds; callers pass a trusted constant)

        Params: $seeds (list of {id, score}), $hop_decay, $limit
        """
        query = f"""
            UNWIND $seeds AS seed
            MATCH (s:Memory {{id: seed.id}})
            MATCH path = (s)-[*1..{depth}]-(m:Memory)
            WHERE NOT m:Archived AND m.id <> seed.id
            WITH m,
                 max(reduce(a = seed.score,
                            r IN relationships(path) |
                            a * coalesce(r.strength, 0.5) * $hop_decay)) AS activation
            RETURN m, activation
            ORDER BY activation DESC
            LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def reinforce_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Reconsolidation: strengthen memories that were just recalled.

        Retrieval IS reinforcement — updates access tracking and boosts
        salience asymptotically toward 1.0. Also re-anchors the decay clock.

        Params: $ids (list of memory id strings), $now (epoch float), $rate
        """
        query = """
            UNWIND $ids AS mid
            MATCH (m:Memory {id: mid})
            SET m.access_count = coalesce(m.access_count, 0) + 1,
                m.last_accessed = $now,
                m.salience = coalesce(m.salience, 0.3) + (1.0 - coalesce(m.salience, 0.3)) * $rate,
                m.salience_updated_at = $now
            RETURN count(m) AS reinforced
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_relationship_edges() -> tuple[LiteralString, dict[str, Any]]:
        """Get all relationship edges for a specific memory."""
        query = """
            MATCH (m:Memory {id: $memory_id})-[r]-(other:Memory)
            RETURN type(r) AS relationship_type,
                   r.strength AS strength,
                   r.auto_detected AS auto_detected,
                   other.id AS other_id,
                   CASE WHEN startNode(r) = m THEN 'outgoing' ELSE 'incoming' END AS direction
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def top_salient() -> tuple[LiteralString, dict[str, Any]]:
        """Most important unarchived memories, by salience then recency.

        Params: $limit
        """
        query = """
            MATCH (m:Memory)
            WHERE NOT m:Archived AND m.salience IS NOT NULL
            RETURN m
            ORDER BY m.salience DESC, m.timestamp DESC
            LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def archive_memory() -> tuple[LiteralString, dict[str, Any]]:
        """Archive a single memory by id (reversible :Archived label).

        Params: $id
        """
        query = """
            MATCH (m:Memory {id: $id})
            SET m:Archived
            RETURN count(m) AS archived
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def palace_stats() -> tuple[LiteralString, dict[str, Any]]:
        """Global statistics for the palace: counts by type, time span, health."""
        query = """
            MATCH (m:Memory)
            WITH count(m) AS total,
                 sum(CASE WHEN m:Archived THEN 1 ELSE 0 END) AS archived,
                 min(m.timestamp) AS oldest,
                 max(m.timestamp) AS newest,
                 avg(m.salience) AS avg_salience,
                 sum(CASE WHEN coalesce(m.pinned, false) THEN 1 ELSE 0 END) AS pinned
            OPTIONAL MATCH (:Memory)-[r]-(:Memory)
            RETURN total, archived, oldest, newest, avg_salience, pinned,
                   count(DISTINCT r) AS relationships
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def type_counts() -> tuple[LiteralString, dict[str, Any]]:
        """Unarchived memory counts grouped by memory_type."""
        query = """
            MATCH (m:Memory)
            WHERE NOT m:Archived
            RETURN m.memory_type AS memory_type, count(*) AS count
            ORDER BY count DESC
            """

        return cast(LiteralString, query), {}


class DreamJobQueries:
    """All dream job/maintenance queries in one place."""

    @staticmethod
    def decay_salience() -> tuple[LiteralString, dict[str, Any]]:
        """Apply exponential decay to memory salience based on ELAPSED TIME.

        salience(t) = floor + (salience - floor) * exp(-lambda * days_elapsed)

        Anchored at `salience_updated_at` (epoch float), so the job is
        idempotent with respect to wall-clock time: running it every minute
        or once a week produces the same trajectory. Pinned memories and
        already-archived memories are untouched.

        Params: $now (epoch float), $decay_lambda (per-day), $floor
        """
        query = """
            MATCH (m:Memory)
            WHERE m.salience IS NOT NULL
              AND coalesce(m.pinned, false) = false
              AND NOT m:Archived
              AND m.salience > $floor
            WITH m, ($now - coalesce(m.salience_updated_at, m.timestamp, $now)) / 86400.0 AS days
            WHERE days > 0
            SET m.salience = $floor + (m.salience - $floor) * exp(-$decay_lambda * days),
                m.salience_updated_at = $now
            RETURN count(m) AS updated
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def archive_stale_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Archive (never delete) low-salience, long-unaccessed memories.

        Adds the :Archived label, which excludes the memory from all recall
        paths. Fully reversible; nothing is destroyed.

        Params: $threshold (salience), $cutoff (epoch float, last-access horizon)
        """
        query = """
            MATCH (m:Memory)
            WHERE coalesce(m.pinned, false) = false
              AND NOT m:Archived
              AND m.salience IS NOT NULL AND m.salience < $threshold
              AND coalesce(m.last_accessed, m.timestamp) < $cutoff
            SET m:Archived
            RETURN count(m) AS archived
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def find_unassigned_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Find recent memories without topic assignments."""
        query = """
            MATCH (m:Memory)
            WHERE m.topic_id IS NULL AND m.timestamp > $cutoff
            RETURN m.id AS id, m.embedding AS embedding
            ORDER BY m.timestamp DESC
            LIMIT 500
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def assign_topic() -> tuple[LiteralString, dict[str, Any]]:
        """Assign topic ID to a memory."""
        query = """MATCH (m:Memory {id: $id}) SET m.topic_id = $topic_id"""

        return cast(LiteralString, query), {}

    @staticmethod
    def get_all_memories_for_clustering() -> tuple[LiteralString, dict[str, Any]]:
        """Get all memories with embeddings for clustering."""
        query = """
            MATCH (m:Memory)
            WHERE m.embedding IS NOT NULL
            RETURN m.id AS id, m.embedding AS embedding, m.topic_id AS current_topic
            ORDER BY m.timestamp DESC
            """

        return cast(LiteralString, query), {}


class ConsolidationQueries:
    """Queries for the episodic → semantic consolidation dream job."""

    @staticmethod
    def find_conversation_cohorts() -> tuple[LiteralString, dict[str, Any]]:
        """Un-consolidated episodic memories grouped by conversation.

        Params: $min_cohort, $max_cohorts, $max_cohort_size
        """
        query = """
            MATCH (m:Memory)
            WHERE (m:FriendUtterance OR m:ClaudeUtterance)
              AND NOT m:Archived
              AND coalesce(m.consolidated, false) = false
              AND m.conversation_id IS NOT NULL
            WITH m ORDER BY m.timestamp
            WITH m.conversation_id AS cohort_key, collect({
                id: m.id, content: m.content, memory_type: m.memory_type,
                timestamp: m.timestamp, salience: m.salience
            }) AS episodes
            WHERE size(episodes) >= $min_cohort
            RETURN cohort_key, episodes[0..$max_cohort_size] AS episodes
            ORDER BY size(episodes) DESC
            LIMIT $max_cohorts
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def find_daily_cohorts() -> tuple[LiteralString, dict[str, Any]]:
        """Un-consolidated conversation-less memories grouped by UTC day.

        Params: $min_cohort, $max_cohorts, $max_cohort_size
        """
        query = """
            MATCH (m:Memory)
            WHERE (m:FriendUtterance OR m:ClaudeUtterance)
              AND NOT m:Archived
              AND coalesce(m.consolidated, false) = false
              AND m.conversation_id IS NULL
            WITH m ORDER BY m.timestamp
            WITH toString(toInteger(m.timestamp / 86400.0)) AS cohort_key, collect({
                id: m.id, content: m.content, memory_type: m.memory_type,
                timestamp: m.timestamp, salience: m.salience
            }) AS episodes
            WHERE size(episodes) >= $min_cohort
            RETURN cohort_key, episodes[0..$max_cohort_size] AS episodes
            ORDER BY size(episodes) DESC
            LIMIT $max_cohorts
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def mark_consolidated() -> tuple[LiteralString, dict[str, Any]]:
        """Flag source episodes as consolidated (still retrievable).

        Params: $ids
        """
        query = """
            UNWIND $ids AS mid
            MATCH (m:Memory {id: mid})
            SET m.consolidated = true
            RETURN count(m) AS marked
            """

        return cast(LiteralString, query), {}


class OAuthQueries:
    """Queries for OAuth state persistence (DCR clients + auth codes).

    Client registrations must survive restarts (claude.ai stores its
    client_id); auth codes are 10-minute single-use ephemera.
    """

    @staticmethod
    def get_client() -> tuple[LiteralString, dict[str, Any]]:
        """Params: $client_id"""
        query = """
            MATCH (c:OAuthClient {client_id: $client_id})
            RETURN c.data_json AS data_json
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def save_client() -> tuple[LiteralString, dict[str, Any]]:
        """Params: $client_id, $data_json, $now"""
        query = """
            MERGE (c:OAuthClient {client_id: $client_id})
            ON CREATE SET c.created_at = $now
            SET c.data_json = $data_json, c.updated_at = $now
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def save_auth_code() -> tuple[LiteralString, dict[str, Any]]:
        """Store a code and opportunistically purge expired ones.

        Params: $code, $data_json, $expires_at, $now
        """
        query = """
            OPTIONAL MATCH (stale:OAuthCode)
            WHERE stale.expires_at < $now
            DETACH DELETE stale
            WITH count(*) AS _
            CREATE (c:OAuthCode {code: $code, data_json: $data_json, expires_at: $expires_at})
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def consume_auth_code() -> tuple[LiteralString, dict[str, Any]]:
        """Atomically fetch-and-delete a code (single use).

        Returns data_json and whether it was still valid at $now.

        Params: $code, $now
        """
        query = """
            MATCH (c:OAuthCode {code: $code})
            WITH c, c.data_json AS data_json, c.expires_at >= $now AS valid
            DETACH DELETE c
            RETURN data_json, valid
            """

        return cast(LiteralString, query), {}


class CacheQueries:
    """Queries for the Neo4j-backed embedding cache."""

    @staticmethod
    def get_cached_embedding() -> tuple[LiteralString, dict[str, Any]]:
        """Fetch a cached embedding (model-scoped, 30-day TTL), counting the hit.

        Params: $key, $model
        """
        query = """
            MATCH (e:EmbeddingCache {cache_key: $key, model: $model})
            WHERE e.created > datetime() - duration('P30D')
            SET e.hit_count = coalesce(e.hit_count, 0) + 1
            RETURN e.vector AS embedding
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def store_embedding() -> tuple[LiteralString, dict[str, Any]]:
        """Store an embedding in the cache with model metadata.

        Params: $key, $model, $embedding, $dimensions, $text
        """
        query = """
            MERGE (e:EmbeddingCache {cache_key: $key, model: $model})
            ON CREATE SET e.hit_count = 0
            SET e.vector = $embedding,
                e.dimensions = $dimensions,
                e.created = datetime(),
                e.text_preview = left($text, 100)
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_cache_stats() -> tuple[LiteralString, dict[str, Any]]:
        """Get statistics about the embedding cache."""
        query = """
            MATCH (e:EmbeddingCache)
            RETURN count(e) AS size,
                   sum(coalesce(e.hit_count, 0)) AS total_hits
            """

        return cast(LiteralString, query), {}


class VectorIndexQueries:
    """Queries for managing Neo4j vector indexes."""

    @staticmethod
    def check_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Check if vector index exists and get its configuration."""
        query = """
            SHOW INDEXES
            YIELD name, type, options
            WHERE name = 'memory_embeddings' AND type = 'VECTOR'
            RETURN options
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def drop_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Drop the existing vector index."""
        query = "DROP INDEX memory_embeddings IF EXISTS"

        return cast(LiteralString, query), {}

    @staticmethod
    def create_vector_index(dimensions: int) -> tuple[LiteralString, dict[str, Any]]:
        """Create vector index with specified dimensions."""
        query = f"""
            CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
            FOR (m:Memory) ON m.embedding
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {dimensions},
              `vector.similarity_function`: 'cosine'
            }}}}
            """

        return cast(LiteralString, query), {}


class QueryFactory:
    """Assembles complete queries with runtime parameters."""

    @staticmethod
    def build_similarity_search(
        embedding: list[float],
        threshold: float,
        limit: int,
        offset: int = 0,
        labels: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a complete similarity search query with parameters."""
        from memory_palace.core.constants import VECTOR_SEARCH_K_MULTIPLIER

        filter_clause = None
        params: dict[str, Any] = {
            "embedding": embedding,
            "threshold": threshold,
            "limit": limit,
            "offset": offset,
            "k": max(limit * VECTOR_SEARCH_K_MULTIPLIER, 50),  # Widen k for better recall
        }

        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            where_clause, filter_params = compile_filters(filters, alias="node")
            if where_clause.startswith("WHERE "):
                filter_clause = where_clause[6:]
                params.update(filter_params)

        query, _ = MemoryQueries.similarity_search(labels=labels, additional_filters=filter_clause)

        return query, params

    @staticmethod
    def build_filtered_recall(
        labels: list[str], filters: dict[str, Any] | None, limit: int, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """Build a filtered recall query."""
        labels_str = ":".join(labels)

        conditions = ["NOT m:Archived"]
        where_params: dict[str, Any] = {}
        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            where_clause, where_params = compile_filters(filters, alias="m")
            if where_clause.startswith("WHERE "):
                conditions.append(where_clause[6:])

        query = f"""
            MATCH (m:{labels_str})
            WHERE {" AND ".join(conditions)}
            RETURN m
            ORDER BY m.timestamp DESC
            SKIP $offset LIMIT $limit
            """

        params = {"offset": offset, "limit": limit, **where_params}

        return query, params
