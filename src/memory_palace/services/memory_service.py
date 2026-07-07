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
        auto_classify: bool = True,
    ):
        """Store a single memory message.

        Args:
            content: The message content
            role: Either 'user' or 'assistant'
            conversation_id: Optional conversation UUID
            salience: Importance rating (0.0-1.0)
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
