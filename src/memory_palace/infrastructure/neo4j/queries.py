"""Centralized query definitions using QueryBuilder.

This is the SINGLE SOURCE OF TRUTH for all Cypher queries in the application.
All queries should be built here using the QueryBuilder for type safety and consistency.
"""

from typing import Any, LiteralString, cast

from memory_palace.core.logging import get_logger
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder

logger = get_logger(__name__)


class MemoryQueries:
    """All memory-related queries in one place."""

    @staticmethod
    def similarity_search(
        limit: int = 10,  # noqa: ARG004
        labels: str | None = None,
        additional_filters: str | None = None,
    ) -> tuple[LiteralString, dict[str, Any]]:
        """Standard similarity search query using vector index.

        Args:
            limit: Maximum number of results
            labels: Optional node labels to filter by (e.g., "FriendUtterance")
            additional_filters: Optional additional WHERE conditions

        Returns:
            Tuple of (query, params)
        """
        # Use CALL for vector index - we'll need to handle this specially
        # since our builder doesn't have a native CALL method yet
        query_parts = []

        # Build the CALL clause
        query_parts.append("CALL db.index.vector.queryNodes('memory_embeddings', $k, $embedding)")
        query_parts.append("YIELD node, score")

        # Build WHERE conditions
        where_conditions = ["score > $threshold"]
        if labels:
            where_conditions.append(f"node:{labels}")
        if additional_filters:
            where_conditions.append(additional_filters)

        query_parts.append(f"WHERE {' AND '.join(where_conditions)}")
        query_parts.append("RETURN node as m, score as similarity")
        query_parts.append("ORDER BY similarity DESC")
        query_parts.append("SKIP $offset LIMIT $limit")

        query = " ".join(query_parts)

        # Return query with parameter placeholders  
        # Query is already LiteralString, no cast needed
        return query, {}

    @staticmethod
    def store_memory_merge(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """MERGE query for storing/updating a memory.

        Args:
            labels: List of labels for the memory node

        Returns:
            Tuple of (query, params)
        """
        labels_str = ":".join(labels)

        # Since we need MERGE which isn't in the builder yet, we'll use raw query
        query = f"""
        MERGE (m:{labels_str} {{id: $id}})
        SET m += $properties
        RETURN m
        """

        return cast(LiteralString, query), {}

    @staticmethod
    def atomic_turn_storage() -> tuple[LiteralString, dict[str, Any]]:
        """Atomic query for storing a complete conversation turn.

        Returns:
            Tuple of (query, params)
        """
        query = """
            // Create friend utterance
            CREATE (f:Memory:FriendUtterance {
                id: $friend_id,
                content: $user_content,
                embedding: $user_embedding,
                salience: $salience,
                topic_id: $topic_user,
                conversation_id: $conversation_id,
                timestamp: datetime(),
                memory_type: 'friend_utterance'
            })

            // Create claude utterance
            CREATE (c:Memory:ClaudeUtterance {
                id: $claude_id,
                content: $assistant_content,
                embedding: $assistant_embedding,
                salience: $salience,
                topic_id: $topic_assistant,
                conversation_id: $conversation_id,
                timestamp: datetime(),
                memory_type: 'claude_utterance'
            })

            // Create turn node
            CREATE (t:ConversationTurn {
                id: $turn_id,
                friend_utterance_id: $friend_id,
                claude_utterance_id: $claude_id,
                conversation_id: $conversation_id,
                timestamp: datetime()
            })

            // Create relationships
            CREATE (f)-[:FOLLOWED_BY {strength: 1.0, sequence: 'conversation_turn'}]->(c)
            CREATE (t)-[:HAS_FRIEND]->(f)
            CREATE (t)-[:HAS_CLAUDE]->(c)

            RETURN f, c, t
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def create_relationship(relationship_type: str = "RELATES_TO") -> tuple[LiteralString, dict[str, Any]]:
        """Create a relationship between two memories.

        Args:
            relationship_type: Type of relationship to create (default: RELATES_TO)

        Returns:
            Tuple of (query, params)
        """
        # Build query with the specific relationship type
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
        """Delete relationship(s) between two memories.

        Args:
            relationship_type: Optional specific relationship type to delete

        Returns:
            Tuple of (query, params)
        """
        if relationship_type:
            query = f"""
                MATCH (source:Memory {{id: $source_id}})-[r:`{relationship_type}`]->(target:Memory {{id: $target_id}})
                DELETE r
                RETURN count(r) as deleted
                """
        else:
            query = """
                MATCH (source:Memory {id: $source_id})-[r]->(target:Memory {id: $target_id})
                DELETE r
                RETURN count(r) as deleted
                """

        return cast(LiteralString, query), {}

    @staticmethod
    def get_memory_by_id(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """Get a specific memory by ID.

        Args:
            labels: List of labels for the memory node

        Returns:
            Tuple of (query, params)
        """
        labels_str = ":".join(labels)

        builder = CypherQueryBuilder()
        builder.match(lambda p: p.node(labels_str, "m", id="$id"))
        builder.return_clause("m")

        return builder.build()

    @staticmethod
    def recall_memories_with_filter(labels: list[str]) -> tuple[LiteralString, dict[str, Any]]:
        """Recall memories with filters.

        Args:
            labels: List of labels for the memory nodes

        Returns:
            Tuple of (query, params)
        """
        labels_str = ":".join(labels)

        # This will be built dynamically with filters
        # For now, return a template
        query = f"""
            MATCH (m:{labels_str})
            {{where_clause}}
            RETURN m
            ORDER BY m.timestamp DESC
            SKIP $offset LIMIT $limit
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def detect_relationships() -> tuple[LiteralString, dict[str, Any]]:
        """Find similar memories for relationship detection.

        Returns:
            Tuple of (query, params)
        """
        query = """
            CALL db.index.vector.queryNodes('memory_embeddings', 5, $embedding)
            YIELD node, score
            WHERE node.id <> $id AND score > $threshold
            RETURN node as other, score as similarity
            ORDER BY similarity DESC
            """

        return cast(LiteralString, query), {}


class DreamJobQueries:
    """All dream job/maintenance queries in one place."""

    @staticmethod
    def refresh_salience() -> tuple[LiteralString, dict[str, Any]]:
        """Apply exponential decay to memory salience.

        Returns:
            Tuple of (query, params)
        """
        builder = CypherQueryBuilder()
        builder.match(lambda p: p.node("Memory", "m"))
        builder.where("m.salience > 0.05")
        builder.set_property("m", {"salience": "m.salience * $decay_factor"})
        builder.return_clause("count(m) as updated")

        # For now, use raw query since SET needs expression support
        query = """
            MATCH (m:Memory)
            WHERE m.salience > 0.05
            SET m.salience = m.salience * $decay_factor
            RETURN count(m) as updated
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def evict_low_salience() -> tuple[LiteralString, dict[str, Any]]:
        """Remove memories with very low salience.

        Returns:
            Tuple of (query, params)
        """
        builder = CypherQueryBuilder()
        builder.match(lambda p: p.node("Memory", "m"))
        builder.where("m.salience < 0.05")
        builder.detach_delete("m")
        builder.return_clause("count(m) as evicted")

        # Build returns validation error, use raw for now
        query = """
            MATCH (m:Memory)
            WHERE m.salience < 0.05
            WITH m, m.id as id
            DETACH DELETE m
            RETURN count(id) as evicted
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def find_unassigned_memories() -> tuple[LiteralString, dict[str, Any]]:
        """Find recent memories without topic assignments.

        Returns:
            Tuple of (query, params)
        """
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
        """Assign topic ID to a memory.

        Returns:
            Tuple of (query, params)
        """
        builder = CypherQueryBuilder()
        builder.match(lambda p: p.node("Memory", "m", id="$id"))
        builder.set_property("m", {"topic_id": "$topic_id"})

        # Use raw since we need parameter in SET
        query = """MATCH (m:Memory {id: $id}) SET m.topic_id = $topic_id"""

        return cast(LiteralString, query), {}

    @staticmethod
    def get_all_memories_for_clustering() -> tuple[LiteralString, dict[str, Any]]:
        """Get all memories with embeddings for clustering.

        Returns:
            Tuple of (query, params)
        """
        builder = CypherQueryBuilder()
        builder.match(lambda p: p.node("Memory", "m"))
        builder.where("m.embedding IS NOT NULL")
        builder.return_clause("m.id AS id", "m.embedding AS embedding", "m.topic_id AS current_topic")
        builder.order_by("m.timestamp DESC")

        return builder.build()


class VectorIndexQueries:
    """Queries for managing Neo4j vector indexes."""

    @staticmethod
    def check_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Check if vector index exists and get its configuration.

        Returns:
            Tuple of (query, params)
        """
        query = """
            SHOW INDEXES 
            YIELD name, type, options 
            WHERE name = 'memory_embeddings' AND type = 'VECTOR'
            RETURN options
            """

        return cast(LiteralString, query), {}

    @staticmethod
    def drop_vector_index() -> tuple[LiteralString, dict[str, Any]]:
        """Drop the existing vector index.

        Returns:
            Tuple of (query, params)
        """
        query = "DROP INDEX memory_embeddings IF EXISTS"

        return cast(LiteralString, query), {}

    @staticmethod
    def create_vector_index(dimensions: int) -> tuple[LiteralString, dict[str, Any]]:
        """Create vector index with specified dimensions.

        Args:
            dimensions: Number of dimensions for the embeddings

        Returns:
            Tuple of (query, params)
        """
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
    """Factory for building dynamic queries with runtime parameters."""

    @staticmethod
    def build_similarity_search(
        embedding: list[float],
        threshold: float,
        limit: int,
        offset: int = 0,
        labels: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a complete similarity search query with parameters.

        Args:
            embedding: Query embedding vector
            threshold: Minimum similarity threshold
            limit: Maximum results
            offset: Number of results to skip
            labels: Optional node labels
            filters: Optional additional filters

        Returns:
            Tuple of (query, params)
        """
        # Get base query
        query, _ = MemoryQueries.similarity_search(limit, labels)

        # Build parameters
        params = {
            "embedding": embedding,
            "threshold": threshold,
            "limit": limit,
            "offset": offset,
            "k": max(limit * 3, 50),  # Widen k for better recall
        }

        # Add filter parameters if provided
        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            filter_clause, filter_params = compile_filters(filters, alias="node")
            # Update query with filter clause
            if filter_clause:
                query = query.replace(
                    "WHERE score > $threshold",
                    f"WHERE score > $threshold AND {filter_clause[6:]}",  # Remove "WHERE "
                )
                params.update(filter_params)

        return query, params

    @staticmethod
    def build_filtered_recall(
        labels: list[str], filters: dict[str, Any] | None, limit: int, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """Build a filtered recall query.

        Args:
            labels: Node labels to match
            filters: Optional filters
            limit: Maximum results
            offset: Number of results to skip

        Returns:
            Tuple of (query, params)
        """
        labels_str = ":".join(labels)

        if filters:
            from memory_palace.infrastructure.neo4j.filter_compiler import compile_filters

            where_clause, where_params = compile_filters(filters, alias="m")
        else:
            where_clause = ""
            where_params = {}

        query = f"""
            MATCH (m:{labels_str})
            {where_clause}
            RETURN m
            ORDER BY m.timestamp DESC
            SKIP $offset LIMIT $limit
            """

        params = {"offset": offset, "limit": limit, **where_params}

        return query, params
