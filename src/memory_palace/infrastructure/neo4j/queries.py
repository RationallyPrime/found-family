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


class CacheQueries:
    """Queries for cache management."""

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
