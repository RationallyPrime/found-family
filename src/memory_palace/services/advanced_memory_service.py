"""Advanced memory service with ontology support and graph traversal.

This service provides rich memory operations that leverage the full power
of the graph database and semantic embeddings for AI continuity.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from structlog import get_logger

from memory_palace.domain.models.ontology import (
    ConversationContext,
    EnhancedMemoryChunk,
    MemoryRelationship,
    RelationType,
    TopicCluster,
)
from memory_palace.domain.specifications.memory import (
    ConceptMemorySpecification,
    EmotionalMemorySpecification,
    OntologyPathSpecification,
    RecentMemorySpecification,
    RelatedMemorySpecification,
    SalientMemorySpecification,
    TopicMemorySpecification,
)
from memory_palace.infrastructure.embeddings.voyage import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.driver import Neo4jDriver
from memory_palace.infrastructure.neo4j.query_builder.builder import CypherQueryBuilder

logger = get_logger(__name__)


class AdvancedMemoryService:
    """Service for advanced memory operations with ontology support."""

    def __init__(
        self,
        neo4j_driver: Neo4jDriver,
        embedding_service: VoyageEmbeddingService,
    ):
        self.neo4j = neo4j_driver
        self.embeddings = embedding_service

    async def remember_with_ontology(
        self,
        user_content: str,
        assistant_content: str,
        conversation_id: UUID | None = None,
        ontology_path: list[str] | None = None,
        concepts: list[str] | None = None,
        emotional_valence: float = 0.0,
        emotional_intensity: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[EnhancedMemoryChunk, EnhancedMemoryChunk]:
        """Store a conversation turn with rich ontology information."""

        # Generate embeddings
        user_embedding = await self.embeddings.create_embedding(user_content, input_type="document")
        assistant_embedding = await self.embeddings.create_embedding(assistant_content, input_type="document")

        # Create memory chunks
        turn_id = uuid4()
        conversation_id = conversation_id or uuid4()

        user_memory = EnhancedMemoryChunk(
            id=uuid4(),
            role="user",
            content=user_content,
            embedding=user_embedding,
            conversation_id=conversation_id,
            turn_id=turn_id,
            ontology_path=ontology_path or [],
            concepts=concepts or [],
            emotional_valence=emotional_valence,
            emotional_intensity=emotional_intensity,
            metadata=metadata or {},
        )

        assistant_memory = EnhancedMemoryChunk(
            id=uuid4(),
            role="assistant",
            content=assistant_content,
            embedding=assistant_embedding,
            conversation_id=conversation_id,
            turn_id=turn_id,
            ontology_path=ontology_path or [],
            concepts=concepts or [],
            emotional_valence=emotional_valence,
            emotional_intensity=emotional_intensity,
            metadata=metadata or {},
        )

        # Detect relationships with existing memories
        await self._detect_and_create_relationships(user_memory)
        await self._detect_and_create_relationships(assistant_memory)

        # Store in Neo4j with query builder
        await self._store_memories_with_relationships(user_memory, assistant_memory, turn_id)

        # Update salience based on relationships
        await self._update_salience_scores([user_memory.id, assistant_memory.id])

        return user_memory, assistant_memory

    async def recall_with_graph(
        self,
        query: str,
        k: int = 10,
        include_context: bool = True,
        max_hops: int = 2,
        filters: dict[str, Any] | None = None,
    ) -> list[EnhancedMemoryChunk]:
        """Recall memories with graph traversal and context."""

        # Generate query embedding
        query_embedding = await self.embeddings.create_embedding(query, input_type="query")

        # Build composite specification from filters
        spec = self._build_specification_from_filters(filters or {})

        # Use query builder for complex recall
        builder = CypherQueryBuilder[EnhancedMemoryChunk]()

        # Start with vector similarity search
        builder.match(
            lambda p: p.node("Message", "m")
        ).with_clause(
            "m",
            f"gds.similarity.cosine(m.embedding, {query_embedding}) AS similarity"
        )

        # Apply specification filters if any
        if spec:
            cypher_filter = spec.to_cypher() if hasattr(spec, 'to_cypher') else None
            if cypher_filter:
                builder.where(cypher_filter)

        # Include context if requested
        if include_context:
            builder.optional_match(
                lambda p: p.node("Message", "m")
                .rel_to("*1.." + str(max_hops))
                .node("Message", "related")
            )
            builder.with_clause(
                "m", "similarity", "COLLECT(DISTINCT related) AS context"
            )

        # Order and limit
        builder.order_by("similarity DESC")
        builder.limit(k)

        # Add return clause
        if include_context:
            builder.return_clause("m", "similarity", "context")
        else:
            builder.return_clause("m", "similarity")

        # Execute query
        query_str, params = builder.build()
        results = await self.neo4j.run_query(query_str, params)

        # Parse results into memory chunks
        memories = []
        for record in results:
            memory_data = dict(record["m"])
            memory = EnhancedMemoryChunk(**memory_data)

            # Update access count and last accessed
            memory.access_count += 1
            memory.last_accessed = datetime.utcnow()
            await self._update_memory_access(memory.id)

            memories.append(memory)

        return memories

    async def find_related_memories(
        self,
        source_id: UUID,
        relationship_types: list[RelationType] | None = None,
        depth: int = 2,
        limit: int = 20,
    ) -> list[tuple[EnhancedMemoryChunk, RelationType, float]]:
        """Find memories related to a source memory through the graph."""

        spec = RelatedMemorySpecification(
            source_id=source_id,
            relationship_types=relationship_types,
            min_strength=0.0
        )

        # Build traversal query
        builder = CypherQueryBuilder[Any]()

        rel_pattern = ""
        if relationship_types:
            rel_types = "|".join(r.value for r in relationship_types)
            rel_pattern = f":{rel_types}"

        builder.match(
            lambda p: p.node("Message", "source", id=str(source_id))
        ).match(
            lambda p: p.custom(f"(source)-[r{rel_pattern}*1..{depth}]->(target:Message)")
        ).return_clause(
            "target", "type(r) AS rel_type", "r.strength AS strength"
        ).order_by(
            "strength DESC"
        ).limit(limit)

        query_str, params = builder.build()
        results = await self.neo4j.run_query(query_str, params)

        related = []
        for record in results:
            memory = EnhancedMemoryChunk(**dict(record["target"]))
            rel_type = RelationType(record["rel_type"])
            strength = record["strength"]
            related.append((memory, rel_type, strength))

        return related

    async def get_conversation_context(
        self,
        conversation_id: UUID,
        include_summary: bool = True,
    ) -> ConversationContext:
        """Get full context for a conversation."""

        # Query all memories in conversation
        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "m", conversation_id=str(conversation_id))
        ).return_clause("m").order_by("m.timestamp")

        query_str, params = builder.build()
        results = await self.neo4j.run_query(query_str, params)

        # Extract topics and emotional arc
        topic_ids = set()
        emotional_arc = []
        salient_memories = []

        for record in results:
            memory_data = dict(record["m"])

            if memory_data.get("topic_id"):
                topic_ids.add(memory_data["topic_id"])

            emotional_arc.append(memory_data.get("emotional_valence", 0.0))

            if memory_data.get("salience", 0) > 0.7:
                salient_memories.append(UUID(memory_data["id"]))

        context = ConversationContext(
            id=conversation_id,
            turn_count=len(results) // 2,  # Assuming paired turns
            primary_topics=list(topic_ids),
            emotional_arc=emotional_arc,
            salient_memories=salient_memories,
        )

        # Generate summary if requested
        if include_summary and salient_memories:
            context.title = await self._generate_conversation_summary(salient_memories[:5])

        return context

    async def evolve_ontology(
        self,
        min_cluster_size: int = 15,
        merge_threshold: float = 0.8,
    ) -> list[TopicCluster]:
        """Evolve the ontology by clustering and merging topics."""

        # This would integrate with HDBSCAN and c-TF-IDF
        # For now, returning a placeholder
        logger.info("Evolving ontology", min_cluster_size=min_cluster_size)

        # Query all embeddings
        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "m")
        ).where(
            "m.embedding IS NOT NULL"
        ).return_clause(
            "m.id AS id", "m.embedding AS embedding", "m.topic_id AS topic_id"
        )

        query_str, params = builder.build()
        results = await self.neo4j.run_query(query_str, params)

        # Here you would:
        # 1. Run HDBSCAN clustering
        # 2. Compute c-TF-IDF for labels
        # 3. Detect and merge similar clusters
        # 4. Update memories with new topic assignments

        return []

    async def _detect_and_create_relationships(
        self,
        memory: EnhancedMemoryChunk,
        similarity_threshold: float = 0.85,
    ) -> None:
        """Detect and create relationships with existing memories."""

        # Find similar memories
        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "other")
        ).where(
            f"other.id <> '{memory.id}'"
        ).with_clause(
            "other",
            f"gds.similarity.cosine(other.embedding, {memory.embedding}) AS similarity"
        ).where(
            f"similarity > {similarity_threshold}"
        ).return_clause(
            "other", "similarity"
        ).order_by(
            "similarity DESC"
        ).limit(5)

        query_str, params = builder.build()
        results = await self.neo4j.run_query(query_str, params)

        # Create appropriate relationships
        for record in results:
            other_data = dict(record["other"])
            similarity = record["similarity"]

            # Determine relationship type based on content analysis
            rel_type = self._determine_relationship_type(memory, other_data, similarity)

            if rel_type:
                relationship = MemoryRelationship(
                    source_id=memory.id,
                    target_id=UUID(other_data["id"]),
                    relationship_type=rel_type,
                    strength=similarity,
                    confidence=similarity,
                )

                await self._create_relationship(relationship)

    async def _store_memories_with_relationships(
        self,
        user_memory: EnhancedMemoryChunk,
        assistant_memory: EnhancedMemoryChunk,
        turn_id: UUID,
    ) -> None:
        """Store memories and their relationships in Neo4j."""

        # Create nodes
        for memory in [user_memory, assistant_memory]:
            builder = CypherQueryBuilder[Any]()
            builder.create(
                lambda p: p.node(
                    "Message",
                    "m",
                    **memory.model_dump(exclude={"embedding"})
                )
            ).set_property(
                "m",
                {"embedding": memory.embedding}
            ).return_clause("m")

            query_str, params = builder.build()
            await self.neo4j.run_query(query_str, params)

        # Create FOLLOWS relationship
        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "u", id=str(user_memory.id))
        ).match(
            lambda p: p.node("Message", "a", id=str(assistant_memory.id))
        ).create(
            lambda p: p.custom(f"(u)-[:FOLLOWS {{turn_id: '{turn_id}'}}]->(a)")
        ).return_clause("u", "a")

        query_str, params = builder.build()
        await self.neo4j.run_query(query_str, params)

    async def _update_salience_scores(self, memory_ids: list[UUID]) -> None:
        """Update salience scores based on relationships and access patterns."""

        for memory_id in memory_ids:
            # Count relationships
            builder = CypherQueryBuilder[Any]()
            builder.match(
                lambda p: p.node("Message", "m", id=str(memory_id))
            ).optional_match(
                lambda p: p.custom("(m)-[r]-()")
            ).return_clause(
                "m", "COUNT(r) AS rel_count"
            )

            query_str, params = builder.build()
            results = await self.neo4j.run_query(query_str, params)

            if results:
                record = results[0]
                rel_count = record["rel_count"]

                # Update salience based on relationship count
                # More relationships = higher salience
                new_salience = min(1.0, 0.5 + (rel_count * 0.1))

                update_builder = CypherQueryBuilder[Any]()
                update_builder.match(
                    lambda p: p.node("Message", "m", id=str(memory_id))
                ).set_property(
                    "m",
                    {"salience": new_salience}
                ).return_clause("m")

                query_str, params = update_builder.build()
                await self.neo4j.run_query(query_str, params)

    async def _update_memory_access(self, memory_id: UUID) -> None:
        """Update access count and timestamp for a memory."""

        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "m", id=str(memory_id))
        ).set_property(
            "m",
            {
                "access_count": "m.access_count + 1",
                "last_accessed": datetime.utcnow().isoformat(),
            }
        ).return_clause("m")

        query_str, params = builder.build()
        await self.neo4j.run_query(query_str, params)

    def _build_specification_from_filters(
        self,
        filters: dict[str, Any],
    ) -> Any:
        """Build a composite specification from filter dictionary."""

        specs = []

        if "min_salience" in filters:
            specs.append(SalientMemorySpecification(filters["min_salience"]))

        if "topic_ids" in filters:
            specs.append(TopicMemorySpecification(filters["topic_ids"]))

        if "time_range" in filters:
            time_range = filters["time_range"]
            if "after" in time_range:
                days_ago = (datetime.timezone.utc() - datetime.fromisoformat(time_range["after"])).days
                specs.append(RecentMemorySpecification(days=days_ago))

        if "emotional" in filters:
            emotional = filters["emotional"]
            specs.append(EmotionalMemorySpecification(
                min_intensity=emotional.get("min_intensity", 0.5),
                valence_range=emotional.get("valence_range", (-1.0, 1.0))
            ))

        if "ontology_path" in filters:
            specs.append(OntologyPathSpecification(filters["ontology_path"]))

        if "concepts" in filters:
            specs.append(ConceptMemorySpecification(filters["concepts"]))

        # Combine specifications with AND
        if not specs:
            return None
        elif len(specs) == 1:
            return specs[0]
        else:
            result = specs[0]
            for spec in specs[1:]:
                result = result.and_(spec)
            return result

    def _determine_relationship_type(
        self,
        memory: EnhancedMemoryChunk,
        other: dict[str, Any],
        similarity: float,
    ) -> RelationType | None:
        """Determine the type of relationship between two memories."""

        # Simple heuristics for now
        if similarity > 0.95:
            return RelationType.ELABORATES
        elif similarity > 0.9:
            return RelationType.REFERENCES
        elif memory.emotional_valence * other.get("emotional_valence", 0) < -0.5:
            return RelationType.CONTRASTS_WITH
        elif abs(memory.emotional_valence - other.get("emotional_valence", 0)) < 0.2:
            return RelationType.RESONATES_WITH

        return None

    async def _create_relationship(self, relationship: MemoryRelationship) -> None:
        """Create a relationship in Neo4j."""

        builder = CypherQueryBuilder[Any]()
        builder.match(
            lambda p: p.node("Message", "source", id=str(relationship.source_id))
        ).match(
            lambda p: p.node("Message", "target", id=str(relationship.target_id))
        ).create(
            lambda p: p.custom(
                f"(source)-[:{relationship.relationship_type.value} "
                f"{{strength: {relationship.strength}, "
                f"confidence: {relationship.confidence}, "
                f"discovered_at: '{relationship.discovered_at.isoformat()}'}}]->(target)"
            )
        ).return_clause("source", "target")

        query_str, params = builder.build()
        await self.neo4j.run_query(query_str, params)

    async def _generate_conversation_summary(
        self,
        memory_ids: list[UUID],
    ) -> str:
        """Generate a summary of key memories."""

        # This would use an LLM to summarize
        # For now, return a placeholder
        return f"Conversation with {len(memory_ids)} key memories"
