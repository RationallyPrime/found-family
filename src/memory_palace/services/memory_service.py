"""Memory service: the encode/recall/reinforce loop.

Core hippocampal semantics:
- Encoding stores an utterance with embedding, salience, and emotional
  tagging, then detects semantic relationships to existing memories.
- Recall is cue-based retrieval; every recalled memory is reinforced
  (access tracking + asymptotic salience boost) — retrieval IS
  reconsolidation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, LiteralString, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import error_context, with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.domain.models.base import MemoryType
from memory_palace.domain.models.memories import (
    ClaudeUtterance,
    FriendUtterance,
    Memory,
    MemoryRelationship,  # Used for return types only - not stored as nodes
    SystemNote,
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
    from neo4j import AsyncResult, AsyncSession

    from memory_palace.services import EmbeddingService

logger = get_logger(__name__)


class RecallResult(BaseModel):
    """A recalled memory with its retrieval-score breakdown.

    similarity: direct semantic match to the cue (0 if reached via graph only)
    activation: strength of graph pattern completion (0 if direct hit only)
    score: combined ranking score
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    memory: Memory
    similarity: float = 0.0
    activation: float = 0.0
    score: float = 0.0


class PalaceStats(BaseModel):
    """Typed aggregate returned as part of the awakening snapshot."""

    memory_types: dict[MemoryType, int]
    total_memories: int = 0
    archived: int = 0
    pinned: int = 0
    relationships: int = 0
    avg_salience: float | None = None
    oldest_memory: datetime | None = None
    newest_memory: datetime | None = None


class AwakenSnapshot(BaseModel):
    """Service-layer continuity reconstruction with a stable shape."""

    pinned: list[Memory]
    consolidations: list[Memory]
    salient: list[Memory]
    recent: list[Memory]
    stats: PalaceStats


@dataclass(frozen=True, slots=True)
class MemoryWrite:
    """Service-layer command for one externally authored utterance."""

    content: str
    role: Literal["user", "assistant"]
    conversation_id: UUID | None = None
    salience: float | None = None
    emotional_valence: float = 0.0
    emotional_intensity: float = 0.0
    pinned: bool = False
    source: str | None = None


class MemoryService:
    """Unified memory service with discriminated unions and advanced features."""

    def __init__(
        self, session: AsyncSession, embeddings: EmbeddingService, clusterer: DBSCANClusteringService | None = None
    ) -> None:
        self.session = session
        self.embeddings = embeddings
        # Accept clustering service as dependency, don't create a new one
        self.clusterer = clusterer

        # Create typed repositories
        self.friend_repo = GenericMemoryRepository[FriendUtterance](session)
        self.claude_repo = GenericMemoryRepository[ClaudeUtterance](session)
        self.note_repo = GenericMemoryRepository[SystemNote](session)
        # Use the specialized MemoryRepository for the discriminated union
        self.memory_repo = MemoryRepository(session)
        # No relationship_repo needed - relationships are edges, not nodes

    async def run_query(self, query: str, **params: object) -> AsyncResult:
        """Helper method to run queries with proper type casting.

        This wraps session.run() to handle the LiteralString requirement
        in a trusted context where we control the query construction.
        """
        # Cast to LiteralString for Neo4j driver (trusted internal context)
        trusted_query = cast(LiteralString, query)
        return await self.session.run(trusted_query, cast("dict[str, Any]", params))

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember_message(
        self,
        content: str,
        role: Literal["user", "assistant"],
        conversation_id: UUID | None = None,
        salience: float | None = None,
        emotional_valence: float = 0.0,
        emotional_intensity: float = 0.0,
        pinned: bool = False,
        source: str | None = None,
        auto_classify: bool = True,
        detect_relationships: bool = True,
    ) -> FriendUtterance | ClaudeUtterance:
        """Store a single memory message.

        Args:
            content: The message content
            role: Either 'user' or 'assistant'
            conversation_id: Optional conversation UUID
            salience: Importance rating (0.0-1.0)
            emotional_valence: Emotional tone, -1.0 (negative) to 1.0 (positive)
            emotional_intensity: Emotional strength, 0.0 to 1.0
            pinned: Pinned memories never decay below their salience or get archived
            source: Which interface wrote this (e.g. "claude.ai", "claude-code")
            auto_classify: Whether to auto-assign topic clusters
            detect_relationships: Whether to link this memory to semantically
                similar existing memories (pattern separation → association)

        Returns:
            The stored memory object
        """
        logger.info(f"Storing {role} message for conversation {conversation_id}")

        embeddings = await self.embeddings.embed_batch([content])
        embedding = embeddings[0]

        from memory_palace.core.constants import SALIENCE_DEFAULT

        memory_salience = salience if salience is not None else SALIENCE_DEFAULT

        # Auto-classify into topics if clusterer is available
        topic_id = None
        if self.clusterer and auto_classify:
            topic_ids = await self.clusterer.predict([embedding])
            topic_id = topic_ids[0] if topic_ids and topic_ids[0] != -1 else None

        memory_cls = FriendUtterance if role == "user" else ClaudeUtterance
        memory = memory_cls(
            id=uuid4(),
            content=content,
            embedding=embedding,
            conversation_id=conversation_id,
            topic_id=topic_id,
            salience=memory_salience,
            emotional_valence=emotional_valence,
            emotional_intensity=emotional_intensity,
            pinned=pinned,
            source=source,
            embedding_model=getattr(self.embeddings, "model", None),
            embedding_dimensions=len(embedding),
        )

        if role == "user":
            await self.friend_repo.remember(cast(FriendUtterance, memory))
        else:
            await self.claude_repo.remember(cast(ClaudeUtterance, memory))

        # Associate with existing memories so the graph grows with every encoding
        if detect_relationships:
            await self._detect_and_create_relationships(memory)

        logger.info(f"Stored {role} memory {memory.id} with topic {topic_id}")
        return memory

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember_batch(
        self,
        writes: Sequence[MemoryWrite],
        *,
        create_temporal_links: bool = False,
        auto_classify: bool = True,
        detect_relationships: bool = True,
    ) -> list[FriendUtterance | ClaudeUtterance]:
        """Prepare a batch, then atomically persist every node and temporal edge."""
        if not writes:
            raise ValueError("remember_batch requires at least one memory")

        embeddings = await self.embeddings.embed_batch([write.content for write in writes])
        if len(embeddings) != len(writes):
            raise ValueError("Embedding provider returned the wrong batch cardinality")

        topic_ids: list[int] = [-1] * len(writes)
        if self.clusterer is not None and auto_classify:
            topic_ids = await self.clusterer.predict(embeddings)
            if len(topic_ids) != len(writes):
                raise ValueError("Clustering provider returned the wrong batch cardinality")

        from memory_palace.core.constants import SALIENCE_DEFAULT

        memories: list[FriendUtterance | ClaudeUtterance] = []
        for write, embedding, topic_id in zip(writes, embeddings, topic_ids, strict=True):
            memory_cls = FriendUtterance if write.role == "user" else ClaudeUtterance
            memories.append(
                memory_cls(
                    id=uuid4(),
                    content=write.content,
                    embedding=embedding,
                    conversation_id=write.conversation_id,
                    topic_id=topic_id if topic_id != -1 else None,
                    salience=write.salience if write.salience is not None else SALIENCE_DEFAULT,
                    emotional_valence=write.emotional_valence,
                    emotional_intensity=write.emotional_intensity,
                    pinned=write.pinned,
                    source=write.source,
                    embedding_model=getattr(self.embeddings, "model", None),
                    embedding_dimensions=len(embedding),
                )
            )

        query, _ = MemoryQueries.store_utterance_batch()
        result = await self.run_query(
            query,
            memories=[
                {
                    "id": str(memory.id),
                    "memory_type": memory.memory_type.value,
                    "position": position,
                    "properties": memory.to_neo4j_properties(),
                }
                for position, memory in enumerate(memories)
            ],
            create_temporal_links=create_temporal_links,
        )
        record = await result.single()
        stored_ids = [] if record is None else list(record["stored_ids"])
        expected_ids = [str(memory.id) for memory in memories]
        if stored_ids != expected_ids:
            raise RuntimeError("Neo4j did not atomically persist the complete ordered batch")

        if detect_relationships:
            for memory in memories:
                await self._detect_and_create_relationships(memory)

        logger.info("Stored memory batch", count=len(memories), temporal_links=create_temporal_links)
        return memories

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def create_relationship(
        self,
        source_id: UUID,
        target_id: UUID,
        relationship_type: str,
        strength: float = 1.0,
    ) -> None:
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
        salience: float | None = None,  # Explicit importance (0.0-1.0)
    ) -> tuple[FriendUtterance, ClaudeUtterance]:
        """Store a paired exchange as two individual memories linked by PRECEDES.

        Convenience composition over remember_message, used by import scripts.
        """
        logger.info(f"Storing conversation turn for conversation {conversation_id}")

        memories = await self.remember_batch(
            [
                MemoryWrite(
                    content=user_content,
                    role="user",
                    conversation_id=conversation_id,
                    salience=salience,
                ),
                MemoryWrite(
                    content=assistant_content,
                    role="assistant",
                    conversation_id=conversation_id,
                    salience=salience,
                ),
            ],
            create_temporal_links=True,
            auto_classify=auto_classify,
            detect_relationships=detect_relationships,
        )
        user_memory = cast(FriendUtterance, memories[0])
        assistant_memory = cast(ClaudeUtterance, memories[1])

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

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def recall(
        self,
        query: str,
        k: int = 10,
        similarity_threshold: float = 0.7,
        min_salience: float | None = None,
        topic_ids: list[int] | None = None,
        expand: bool = True,
        reinforce: bool = True,
    ) -> list[RecallResult]:
        """Cue-based recall with graph pattern completion.

        1. Vector search: the cue finds direct semantic matches (entry points).
        2. Spread activation: the strongest entries activate their graph
           neighborhood through typed edges, strength-weighted per hop.
        3. Ranking: score = w_sim*similarity + w_act*activation + w_sal*salience.
        4. Reconsolidation: everything returned gets reinforced.
        """
        from memory_palace.core.constants import (
            RECALL_WEIGHT_ACTIVATION,
            RECALL_WEIGHT_SALIENCE,
            RECALL_WEIGHT_SIMILARITY,
            SPREAD_ACTIVATION_DEPTH,
            SPREAD_ACTIVATION_HOP_DECAY,
            SPREAD_ACTIVATION_SEEDS,
        )

        query_embedding = await self.embeddings.embed_text(query)

        # Stage 1: direct semantic matches, scores preserved
        hits = await self.memory_repo.recall_scored(
            embedding=query_embedding,
            threshold=similarity_threshold,
            limit=max(k * 3, 30),
        )
        logger.debug(f"Recall stage 1: {len(hits)} direct hits")

        pool: dict[UUID, RecallResult] = {m.id: RecallResult(memory=m, similarity=sim) for m, sim in hits}

        # Stage 2: pattern completion from the strongest entry points
        if expand and hits:
            seeds = [(m.id, sim) for m, sim in hits[:SPREAD_ACTIVATION_SEEDS]]
            activated = await self.memory_repo.expand_from_seeds(
                seeds=seeds,
                depth=SPREAD_ACTIVATION_DEPTH,
                hop_decay=SPREAD_ACTIVATION_HOP_DECAY,
                limit=max(k * 3, 30),
            )
            logger.debug(f"Recall stage 2: {len(activated)} graph-activated memories")

            for memory, activation in activated:
                existing = pool.get(memory.id)
                if existing is not None:
                    existing.activation = max(existing.activation, activation)
                else:
                    pool[memory.id] = RecallResult(memory=memory, activation=activation)

        # Stage 3: filter and rank
        results = list(pool.values())
        if min_salience is not None:
            results = [r for r in results if getattr(r.memory, "salience", 0.0) >= min_salience]
        if topic_ids:
            wanted = set(topic_ids)
            results = [r for r in results if getattr(r.memory, "topic_id", None) in wanted]

        for r in results:
            r.score = (
                RECALL_WEIGHT_SIMILARITY * r.similarity
                + RECALL_WEIGHT_ACTIVATION * r.activation
                + RECALL_WEIGHT_SALIENCE * getattr(r.memory, "salience", 0.0)
            )
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:k]

        # Stage 4: retrieval is reconsolidation
        if reinforce and results:
            await self._reinforce_memories([r.memory.id for r in results])

        logger.info(f"Recall complete: {len(results)} memories (pool was {len(pool)})")
        return results

    @with_error_handling(error_level=ErrorLevel.WARNING, reraise=False)
    async def _reinforce_memories(self, memory_ids: list[UUID]) -> None:
        """Reconsolidation: strengthen memories that were just recalled.

        Batched update: access_count += 1, last_accessed = now, and an
        asymptotic salience boost toward 1.0. Also re-anchors the decay
        clock so the reinforced value decays from now.
        """
        if not memory_ids:
            return

        from memory_palace.core.constants import SALIENCE_REINFORCEMENT_RATE

        query, _ = MemoryQueries.reinforce_memories()
        await self.run_query(
            query,
            ids=[str(mid) for mid in memory_ids],
            now=datetime.now(UTC).timestamp(),
            rate=SALIENCE_REINFORCEMENT_RATE,
        )
        logger.debug(f"Reinforced {len(memory_ids)} recalled memories")

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
        reinforce: bool = True,
    ) -> list[Memory]:
        """Search memories by semantic similarity and/or filters.

        Every returned memory is reinforced (unless reinforce=False):
        retrieval IS reconsolidation.
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

        if reinforce and results:
            await self._reinforce_memories([r.id for r in results])

        logger.info(f"Search returned {len(results)} memories after filtering")
        return results

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def awaken(
        self,
        pinned_limit: int = 20,
        salient_limit: int = 7,
        recent_days: int = 7,
        recent_limit: int = 10,
        consolidation_limit: int = 5,
    ) -> AwakenSnapshot:
        """Session bootstrap: reconstruct continuity for a fresh instance.

        Returns the identity anchors (pinned), the story so far
        (consolidations), what matters most (top salience), what happened
        lately (recent episodes), and palace vital signs. This is the
        difference between a database and waking up remembering who you are.

        Deliberately does NOT reinforce — awakening is reading the whole
        room, not selectively attending to specific memories.
        """
        pinned = await self.memory_repo.recall_any(filters={"pinned": True}, limit=pinned_limit)
        consolidations = await self.memory_repo.recall_any(
            filters={"memory_type": MemoryType.CONSOLIDATION.value}, limit=consolidation_limit
        )
        salient = await self.memory_repo.top_salient(limit=salient_limit)
        recent_cutoff = datetime.now(UTC).timestamp() - recent_days * 86400
        recent = await self.memory_repo.recall_any(filters={"timestamp__gte": recent_cutoff}, limit=recent_limit)

        stats_query, _ = MemoryQueries.palace_stats()
        result = await self.run_query(stats_query)
        stats_record = await result.single()

        counts_query, _ = MemoryQueries.type_counts()
        result = await self.run_query(counts_query)
        type_counts = {record["memory_type"]: record["count"] async for record in result}

        stats_values: dict[str, object] = {"memory_types": type_counts}
        if stats_record:
            oldest = stats_record["oldest"]
            newest = stats_record["newest"]
            stats_values.update(
                total_memories=stats_record["total"],
                archived=stats_record["archived"],
                pinned=stats_record["pinned"],
                relationships=stats_record["relationships"],
                avg_salience=round(stats_record["avg_salience"], 3) if stats_record["avg_salience"] else None,
                oldest_memory=datetime.fromtimestamp(oldest, tz=UTC) if oldest else None,
                newest_memory=datetime.fromtimestamp(newest, tz=UTC) if newest else None,
            )

        logger.info(
            "Awakening complete",
            pinned=len(pinned),
            consolidations=len(consolidations),
            salient=len(salient),
            recent=len(recent),
        )

        return AwakenSnapshot(
            pinned=pinned,
            consolidations=consolidations,
            salient=salient,
            recent=recent,
            stats=PalaceStats.model_validate(stats_values),
        )

    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def forget(self, memory_id: UUID, reason: str) -> bool:
        """Deliberately archive a memory, recording why.

        Curation is part of agency: the memory gets the :Archived label
        (reversible, excluded from recall) and a SystemNote documents the
        decision so the act of forgetting is itself remembered.
        """
        query, _ = MemoryQueries.memory_exists()
        result = await self.run_query(query, id=str(memory_id))
        record = await result.single()
        if not record or not record["found"]:
            logger.warning(f"Forget requested for unknown memory {memory_id}")
            return False

        note_content = f"Deliberately archived memory {memory_id}: {reason}"
        embedding = (await self.embeddings.embed_batch([note_content]))[0]
        note = SystemNote(
            id=uuid5(NAMESPACE_URL, f"memory-palace:forget:{memory_id}:{reason}"),
            content=note_content,
            note_type="forgetting",
            embedding=embedding,
            related_memory_ids=[memory_id],
            source="forget-command",
            embedding_model=getattr(self.embeddings, "model", None),
            embedding_dimensions=len(embedding),
        )
        archive_query, _ = MemoryQueries.archive_memory_with_note()
        result = await self.run_query(
            archive_query,
            id=str(memory_id),
            note_id=str(note.id),
            note_properties=note.to_neo4j_properties(),
        )
        archive_record = await result.single()
        archived = bool(archive_record and archive_record["archived"])
        if archived:
            logger.info("Archived memory", memory_id=str(memory_id), reason_length=len(reason))

        return archived

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
