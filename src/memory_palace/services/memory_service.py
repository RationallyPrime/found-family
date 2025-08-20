"""Refactored Memory Service with discriminated unions and advanced features.

This module implements MP-002, MP-003, and MP-008 by providing:
- Integration with discriminated union models
- Generic repository usage
- Specification-based filtering
- Automatic relationship detection and topic classification
- Multi-stage recall with ontology boost
"""

from __future__ import annotations

# Standard logging replaced with Logfire logging
from typing import TYPE_CHECKING, LiteralString, cast
from uuid import UUID, uuid4

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import error_context, with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.domain.models.base import MemoryType
from memory_palace.domain.models.memories import (
    ClaudeUtterance,
    FriendUtterance,
    Memory,
    MemoryRelationship,  # Used for return types only - not stored as nodes
)
from memory_palace.infrastructure.neo4j.queries import (
    MemoryQueries,
)
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.infrastructure.repositories.memory import (
    GenericMemoryRepository,
    MemoryRepository,
)
from memory_palace.services.clustering import DBSCANClusteringService

if TYPE_CHECKING:
    from neo4j import AsyncSession

    from memory_palace.services import EmbeddingService

logger = get_logger(__name__)


class MemoryService:
    """Unified memory service with discriminated unions and advanced features."""

    def __init__(
        self, session: AsyncSession, embeddings: EmbeddingService, clusterer: DBSCANClusteringService | None = None
    ):
        self.session = session
        self.embeddings = embeddings
        # Accept clustering service as dependency, don't create a new one
        self.clusterer = clusterer

        # Create typed repositories
        self.friend_repo = GenericMemoryRepository[FriendUtterance](session)
        self.claude_repo = GenericMemoryRepository[ClaudeUtterance](session)
        # Use the specialized MemoryRepository for the discriminated union
        self.memory_repo = MemoryRepository(session)
        # No relationship_repo needed - relationships are edges, not nodes

    async def run_query(self, query: str, **params):
        """Helper method to run queries with proper type casting.

        This wraps session.run() to handle the LiteralString requirement
        in a trusted context where we control the query construction.
        """
        # Cast to LiteralString for Neo4j driver (trusted internal context)
        trusted_query = cast(LiteralString, query)
        return await self.session.run(trusted_query, **params)

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember_message(
        self,
        content: str,
        role: str,
        conversation_id: UUID | None = None,
        salience: float | None = None,
        ontology_path: list[str] | None = None,
        metadata: dict | None = None,
        auto_classify: bool = True,
    ):
        """Store a single memory message.

        Args:
            content: The message content
            role: Either 'user' or 'assistant'
            conversation_id: Optional conversation UUID
            salience: Importance rating (0.0-1.0)
            ontology_path: Hierarchical categorization
            metadata: Additional metadata
            auto_classify: Whether to auto-assign topic clusters

        Returns:
            The stored memory object
        """
        logger.info(f"Storing {role} message for conversation {conversation_id}")

        # Generate embedding
        embeddings = await self.embeddings.embed_batch([content])
        embedding = embeddings[0]

        # Use provided salience or default
        from memory_palace.core.constants import SALIENCE_DEFAULT

        memory_salience = salience if salience is not None else SALIENCE_DEFAULT

        # Auto-classify into topics if clusterer is available
        topic_id = None
        if self.clusterer and auto_classify:
            topic_ids = await self.clusterer.predict([embedding])
            topic_id = topic_ids[0] if topic_ids and topic_ids[0] != -1 else None

        # Create appropriate memory type based on role
        if role == "user":
            memory = FriendUtterance(
                id=uuid4(),
                content=content,
                embedding=embedding,
                conversation_id=conversation_id,
                topic_id=topic_id,
                salience=memory_salience,
            )
        else:  # assistant
            memory = ClaudeUtterance(
                id=uuid4(),
                content=content,
                embedding=embedding,
                conversation_id=conversation_id,
                topic_id=topic_id,
                salience=memory_salience,
            )

        # Store in appropriate repository based on type
        if role == "user":
            await self.friend_repo.remember(memory)
        else:
            await self.claude_repo.remember(memory)

        logger.info(f"Stored {role} memory {memory.id} with topic {topic_id}")
        return memory

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def create_relationship(
        self,
        source_id: UUID,
        target_id: UUID,
        relationship_type: str,
        strength: float = 1.0,
    ):
        """Create a relationship between two memories.

        Args:
            source_id: Source memory UUID
            target_id: Target memory UUID
            relationship_type: Type of relationship (e.g., PRECEDES, FOLLOWS)
            strength: Relationship strength (0.0-1.0)
        """
        await self.memory_repo.connect(source_id, target_id, relationship_type, {"strength": strength})
        logger.info(f"Created {relationship_type} relationship from {source_id} to {target_id}")

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember_turn(
        self,
        user_content: str,
        assistant_content: str,
        conversation_id: UUID | None = None,
        detect_relationships: bool = True,
        auto_classify: bool = True,
        similarity_threshold: float = 0.85,
        salience: float | None = None,  # Explicit importance (0.0-1.0)
    ) -> tuple:
        """Store a conversation turn as two individual memories with a relationship.

        This method maintains backward compatibility but now stores memories individually.
        """
        logger.info(f"Storing conversation turn for conversation {conversation_id}")

        # Store user message
        user_memory = await self.remember_message(
            content=user_content,
            role="user",
            conversation_id=conversation_id,
            salience=salience,
            auto_classify=auto_classify,
        )

        # Store assistant message
        assistant_memory = await self.remember_message(
            content=assistant_content,
            role="assistant",
            conversation_id=conversation_id,
            salience=salience,
            auto_classify=auto_classify,
        )

        # Create PRECEDES relationship between messages
        await self.create_relationship(
            source_id=user_memory.id,
            target_id=assistant_memory.id,
            relationship_type="PRECEDES",
            strength=1.0,
        )

        # Detect additional relationships if enabled
        if detect_relationships:
            logger.debug("Detecting semantic relationships")
            user_relationships = await self._detect_and_create_relationships(user_memory, similarity_threshold) or []
            assistant_relationships = (
                await self._detect_and_create_relationships(assistant_memory, similarity_threshold) or []
            )

            # Update salience based on relationship count
            await self._update_salience_from_relationships(user_memory, len(user_relationships))
            await self._update_salience_from_relationships(assistant_memory, len(assistant_relationships))

        logger.info(f"Successfully stored turn: user={user_memory.id}, assistant={assistant_memory.id}")
        return (user_memory, assistant_memory)

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def _detect_and_create_relationships(
        self, memory: FriendUtterance | ClaudeUtterance, similarity_threshold: float | None = None
    ) -> list[MemoryRelationship]:
        """Find and create semantic relationships using the query builder and specifications."""
        from memory_palace.core.constants import SIMILARITY_THRESHOLD_HIGH

        if similarity_threshold is None:
            similarity_threshold = SIMILARITY_THRESHOLD_HIGH

        relationships = []

        # Use centralized query for relationship detection
        query, _ = MemoryQueries.detect_relationships()
        result = await self.run_query(
            query,
            embedding=memory.embedding,
            id=str(memory.id),
            threshold=similarity_threshold,
        )

        # Process similar memories
        async for record in result:
            other_data = dict(record["other"])
            similarity = record["similarity"]
            other_id = UUID(other_data["id"])

            # Infer relationship type based on content and similarity
            rel_type = self._infer_relationship_type(memory.content, other_data.get("content", ""), similarity)

            # Create relationship using repository
            await self.memory_repo.connect(
                memory.id, other_id, rel_type, {"strength": similarity, "auto_detected": True}
            )

            # Create relationship object for return value (but don't store as node)
            relationship = MemoryRelationship(
                source_id=memory.id,
                target_id=other_id,
                relationship_type=rel_type,
                strength=similarity,
                metadata={"detection_method": "vector_index"},
            )
            # Relationships are edges, not nodes - they were already created with connect()
            relationships.append(relationship)

            logger.debug(f"Created {rel_type} relationship: {memory.id} -> {other_id} (strength={similarity:.3f})")

        return relationships

    def _infer_relationship_type(self, content1: str, content2: str, similarity: float) -> str:
        """Infer relationship type based on content similarity and patterns."""
        # Simple heuristic-based relationship type inference
        # In a real implementation, this could use NLP models

        if similarity > 0.95:
            return "VERY_SIMILAR_TO"
        elif similarity > 0.90:
            return "SIMILAR_TO"
        elif "question" in content1.lower() and "answer" in content2.lower():
            return "ANSWERED_BY"
        elif "problem" in content1.lower() and "solution" in content2.lower():
            return "SOLVED_BY"
        else:
            return "RELATES_TO"

    async def _update_salience_from_relationships(
        self, memory: FriendUtterance | ClaudeUtterance, relationship_count: int
    ):
        """Update memory salience based on relationship count and strength."""
        # Boost salience based on how connected the memory is
        base_boost = 0.1 * relationship_count
        max_salience = min(1.0, memory.salience + base_boost)

        # Update using repository
        memory.salience = max_salience
        if isinstance(memory, FriendUtterance):
            await self.friend_repo.remember(memory)  # This will update existing
        else:
            await self.claude_repo.remember(memory)

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def recall_with_graph(
        self,
        query: str,
        k: int = 24,
        use_ontology_boost: bool = True,
        expand_depth: int = 2,
        boost_factor: float = 1.2,  # noqa: ARG002
    ) -> list[Memory]:
        """Multi-stage recall with ontology boost and graph expansion.

        Implements MP-009 four-stage recall process.
        """
        logger.info(f"Starting multi-stage recall for query: '{query[:50]}...'")

        # Stage 1: Generate query embedding and initial vector search
        query_embedding = await self.embeddings.embed_text(query)
        embedding = query_embedding

        # Get broad candidate set using similarity search
        candidates = await self.memory_repo.recall_any(
            similarity_search=(embedding, 0.5),  # Lower threshold for broad search
            limit=64,
        )

        logger.debug(f"Stage 1: Found {len(candidates)} initial candidates")

        # Stage 2: Ontology boost (predict topic and boost matching memories)
        if use_ontology_boost and self.clusterer and candidates:
            try:
                predicted_topic = await self.clusterer.predict([embedding])
                topic_id = predicted_topic[0] if predicted_topic[0] != -1 else None

                if topic_id is not None:
                    logger.debug(f"Stage 2: Predicted topic {topic_id}, applying ontology boost")

                    # Separate topical vs non-topical memories
                    topical = []
                    non_topical = []

                    for memory in candidates:
                        if hasattr(memory, "topic_id") and memory.topic_id == topic_id:
                            topical.append(memory)
                        else:
                            non_topical.append(memory)

                    # Reorder: topical memories first (with boost), then others
                    candidates = topical + non_topical
                    logger.debug(f"Stage 2: Boosted {len(topical)} memories from topic {topic_id}")

            except Exception as e:
                # Log ontology boost failure but continue - it's optional
                from memory_palace.core.errors import ProcessingError

                # Create error for logging but don't raise (it's optional)
                error = ProcessingError(
                    message="Ontology boost failed during recall",
                    details={
                        "source": "memory_service",
                        "operation": "recall_with_graph",
                        "stage": "ontology_boost",
                        "error_message": str(e),
                    },
                )
                logger.warning("Stage 2 ontology boost failed", extra={"error": str(error), "details": error.details})

        # Stage 3: Graph expansion (traverse relationships from top candidates)
        top_candidates = candidates[:10]  # Expand from best candidates only
        expanded_memories = await self._expand_via_relationships(top_candidates, expand_depth)

        # Add expanded memories to candidate set
        candidate_ids = {m.id for m in candidates}
        for expanded in expanded_memories:
            if expanded.id not in candidate_ids:
                candidates.append(expanded)
                candidate_ids.add(expanded.id)

        logger.debug(f"Stage 3: Added {len(expanded_memories)} memories via graph expansion")

        # Stage 4: Re-rank combined set (for now, just use original similarity order)
        # In a more sophisticated implementation, this would use cross-encoders
        # or other re-ranking models

        final_results = candidates[:k]
        logger.info(f"Multi-stage recall complete: returning {len(final_results)} memories")

        return final_results

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def _expand_via_relationships(self, seed_memories: list[Memory], depth: int = 2) -> list[Memory]:
        """
        Recursively expand a set of seed memories by finding similar memories using vector similarity.

        For each memory in the current set, retrieves a set of similar memories (based on embedding similarity)
        and adds those not already visited to the expansion set. This process is repeated up to the specified
        depth, avoiding revisiting memories by tracking their IDs.

        Args:
            seed_memories (list[Memory]): The initial set of memories to expand from.
            depth (int): The maximum recursion depth for expansion.

        Returns:
            list[Memory]: The expanded set of related memories found via vector similarity.
        """
        if not seed_memories or depth <= 0:
            return []

        expanded: list[Memory] = []
        visited_ids = {m.id for m in seed_memories}

        for seed in seed_memories:
            if not getattr(seed, "embedding", None):
                continue
            similar = await self.memory_repo.recall_any(similarity_search=(seed.embedding, 0.7), limit=5)
            for memory in similar:
                if memory.id not in visited_ids and memory.id != seed.id:
                    expanded.append(memory)
                    visited_ids.add(memory.id)

        if depth > 1:
            recursive_expanded = await self._expand_via_relationships(expanded, depth - 1)
            for memory in recursive_expanded:
                if memory.id not in visited_ids:
                    expanded.append(memory)
                    visited_ids.add(memory.id)

        return expanded

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=True)
    async def search_memories(
        self,
        query: str | None = None,
        memory_types: list[MemoryType] | None = None,
        conversation_id: UUID | None = None,
        topic_id: int | None = None,
        min_salience: float | None = None,
        similarity_threshold: float = 0.7,
        limit: int = 50,
    ) -> list[Memory]:
        """Search memories using specifications and filters.

        Implements MP-002 integration with specification support.
        """
        filters = {}

        # Build filter dictionary
        if conversation_id:
            filters["conversation_id"] = str(conversation_id)
        if topic_id is not None:
            filters["topic_id"] = topic_id
        if min_salience is not None:
            filters["salience__gte"] = min_salience

        # If we have a query, use similarity search
        similarity_search = None
        if query:
            logger.info(f"Performing similarity search with threshold {similarity_threshold}")
            query_embedding = await self.embeddings.embed_text(query)
            similarity_search = (query_embedding, similarity_threshold)

        # Use repository for type-safe querying
        logger.debug(
            f"Calling recall_any with filters={filters}, similarity_search={'Yes' if similarity_search else 'No'}, limit={limit}"
        )
        results = await self.memory_repo.recall_any(filters=filters, similarity_search=similarity_search, limit=limit)
        logger.debug(f"recall_any returned {len(results)} results")

        # Filter by memory types if specified
        if memory_types:
            type_values = {mt.value for mt in memory_types}
            results = [r for r in results if r.memory_type.value in type_values]

        logger.info(f"Search returned {len(results)} memories after filtering")
        return results

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=True)
    async def recall_with_specifications(
        self,
        specifications: list,
        query: str | None = None,
        similarity_threshold: float = 0.7,
        limit: int = 50,
    ) -> list[Memory]:
        """Recall memories using powerful specification-based filtering.

        This method allows composing specifications for complex queries:
        - Combine multiple criteria with AND/OR logic
        - Filter by salience, topics, conversations, recency, emotions, etc.
        - Optionally add semantic similarity search

        Args:
            specifications: List of specification objects to apply
            query: Optional text query for similarity search
            similarity_threshold: Minimum similarity score (if query provided)
            limit: Maximum number of results

        Returns:
            List of memories matching the specifications

        Example:
            >>> # Find recent, salient memories from a specific conversation
            >>> specs = [
            ...     RecentMemorySpecification(days=7),
            ...     SalientMemorySpecification(min_salience=0.7),
            ...     ConversationMemorySpecification(conversation_id),
            ... ]
            >>> memories = await service.recall_with_specifications(specs)
        """
        # Build Cypher query from specifications
        filter_conditions: list[str] = []
        for spec in specifications:
            if hasattr(spec, "to_cypher"):
                clause = spec.to_cypher(alias="node")
                filter_conditions.append(clause)

        filter_clause = " AND ".join(filter_conditions)
        if filter_clause:
            filter_clause = "AND " + filter_clause

        if query:
            query_embedding = await self.embeddings.embed_text(query)
            cypher_query = f"""
            CALL db.index.vector.queryNodes('memory_embeddings', $limit, $embedding)
            YIELD node, score
            WHERE score > $threshold {filter_clause}
            RETURN node as m, score as similarity
            ORDER BY similarity DESC
            LIMIT $limit
            """
            result = await self.run_query(
                cypher_query,
                embedding=query_embedding,
                threshold=similarity_threshold,
                limit=limit,
            )
        else:
            builder = CypherQueryBuilder()
            builder.match(lambda p: p.node("Memory", "m"))
            for spec in specifications:
                if hasattr(spec, "to_cypher"):
                    builder.where(spec.to_cypher())
            builder.return_clause("m")
            builder.order_by("m.timestamp DESC")
            builder.limit(limit)
            query_str, params = builder.build()
            result = await self.run_query(query_str, **params)

        memories = []
        async for record in result:
            memory_data = dict(record["m"])
            # Use Pydantic's validation - let ValidationError bubble up
            # as it indicates a data integrity issue that should be addressed

            memory = Memory.model_validate(memory_data)
            memories.append(memory)

        logger.info(f"Specification-based recall returned {len(memories)} memories")
        return memories

    async def get_conversation_history(self, conversation_id: UUID, limit: int = 100) -> list[Memory]:
        """Get complete conversation history in chronological order."""
        return await self.memory_repo.recall_any(filters={"conversation_id": str(conversation_id)}, limit=limit)

    @error_context(error_level=ErrorLevel.INFO)
    async def get_memory_relationships(self, memory_id: UUID) -> list[dict]:
        """Get all relationships for a specific memory.

        Returns relationship edges from the graph, not nodes.
        """
        # Use centralized query from MemoryQueries
        query, _ = MemoryQueries.get_relationship_edges()

        result = await self.run_query(query, memory_id=str(memory_id))
        relationships = []
        async for record in result:
            relationships.append(
                {
                    "relationship_type": record["relationship_type"],
                    "strength": record["strength"],
                    "auto_detected": record["auto_detected"],
                    "other_id": record["other_id"],
                    "direction": record["direction"],
                }
            )

        return relationships

    async def get_topic_memories(self, topic_id: int, limit: int = 50) -> list[Memory]:
        """Get all memories belonging to a specific topic cluster."""
        return await self.memory_repo.recall_any(filters={"topic_id": topic_id}, limit=limit)
