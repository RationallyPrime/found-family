"""Refactored Memory Service with discriminated unions and advanced features.

This module implements MP-002, MP-003, and MP-008 by providing:
- Integration with discriminated union models
- Generic repository usage
- Specification-based filtering  
- Automatic relationship detection and topic classification
- Multi-stage recall with ontology boost
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from memory_palace.core.base import ErrorLevel
from memory_palace.core.decorators import error_context, with_error_handling
from memory_palace.domain.models.base import MemoryType
from memory_palace.domain.models.memories import (
    AssistantUtterance,
    Memory,
    MemoryRelationship,
    Turn,
    UserUtterance,
)
from memory_palace.infrastructure.neo4j.query_builder import CypherQueryBuilder
from memory_palace.infrastructure.repositories.memory import (
    GenericMemoryRepository,
    MemoryRepository,
)

if TYPE_CHECKING:
    from neo4j import AsyncSession

    from memory_palace.services import ClusteringService, EmbeddingService

logger = logging.getLogger(__name__)


class MemoryService:
    """Unified memory service with discriminated unions and advanced features."""
    
    def __init__(
        self,
        session: AsyncSession,
        embeddings: EmbeddingService,
        clusterer: ClusteringService | None = None,
    ):
        self.session = session
        self.embeddings = embeddings
        self.clusterer = clusterer
        
        # Create typed repositories
        self.user_repo = GenericMemoryRepository[UserUtterance](session)
        self.assistant_repo = GenericMemoryRepository[AssistantUtterance](session)
        # Use the specialized MemoryRepository for the discriminated union
        self.memory_repo = MemoryRepository(session)
        self.relationship_repo = GenericMemoryRepository[MemoryRelationship](session)
    
    @with_error_handling(error_level=ErrorLevel.ERROR, reraise=True)
    async def remember_turn(
        self,
        user_content: str,
        assistant_content: str,
        conversation_id: UUID | None = None,
        detect_relationships: bool = True,
        auto_classify: bool = True,
        similarity_threshold: float = 0.85,
    ) -> Turn:
        """Store a complete conversation turn with relationship detection and classification.
        
        Implements MP-003 requirements for enhanced memory storage.
        """
        try:
            logger.info(f"Storing conversation turn for conversation {conversation_id}")
            
            # Generate embeddings for both messages
            embeddings = await self.embeddings.embed_batch(
                [user_content, assistant_content]
            )
            
            # Create memory objects with discriminated union types
            user_memory = UserUtterance(
                id=uuid4(),
                content=user_content,
                embedding=embeddings[0],
                conversation_id=conversation_id,
                salience=0.5  # Base salience
            )
            
            assistant_memory = AssistantUtterance(
                id=uuid4(),
                content=assistant_content,
                embedding=embeddings[1],
                conversation_id=conversation_id,
                salience=0.5
            )
            
            # Auto-classify into topics if enabled and clusterer available
            if auto_classify and self.clusterer:
                logger.debug("Performing topic classification")
                topic_ids = await self.clusterer.predict([embeddings[0], embeddings[1]])
                user_memory.topic_id = topic_ids[0] if topic_ids[0] != -1 else None
                assistant_memory.topic_id = topic_ids[1] if topic_ids[1] != -1 else None
                logger.debug(f"Assigned topics: user={user_memory.topic_id}, assistant={assistant_memory.topic_id}")
            
            # Store memories using typed repositories
            await self.user_repo.remember(user_memory)
            await self.assistant_repo.remember(assistant_memory)
            
            # Create FOLLOWED_BY relationship between user and assistant
            await self.user_repo.connect(
                user_memory.id,
                assistant_memory.id,
                "FOLLOWED_BY",
                {"strength": 1.0, "sequence": "conversation_turn"}
            )
            
            # Detect relationships with existing memories if enabled
            if detect_relationships:
                logger.debug("Detecting semantic relationships")
                user_relationships = await self._detect_and_create_relationships(
                    user_memory, similarity_threshold
                )
                assistant_relationships = await self._detect_and_create_relationships(
                    assistant_memory, similarity_threshold
                )
                
                # Update salience based on relationship count and strength
                await self._update_salience_from_relationships(
                    user_memory, len(user_relationships)
                )
                await self._update_salience_from_relationships(
                    assistant_memory, len(assistant_relationships)
                )
            
            logger.info(f"Successfully stored turn: user={user_memory.id}, assistant={assistant_memory.id}")
            return (user_memory, assistant_memory)
            
        except Exception as e:
            logger.error(f"Failed to store conversation turn: {e}", exc_info=True)
            raise
    
    async def _detect_and_create_relationships(
        self,
        memory: UserUtterance | AssistantUtterance,
        similarity_threshold: float = 0.85
    ) -> list[MemoryRelationship]:
        """Find and create semantic relationships using the query builder and specifications."""
        relationships = []
        
        try:
            # Use query builder to find similar memories
            builder = CypherQueryBuilder()
            query = (
                builder
                .match(lambda p: p.node("Memory", "other"))
                .where(f"other.id <> '{memory.id}'")
                .where("other.embedding IS NOT NULL")
                .with_clause(
                    "other",
                    "gds.similarity.cosine(other.embedding, $embedding) AS similarity"
                )
                .where(f"similarity > {similarity_threshold}")
                .return_clause("other", "similarity")
                .order_by("similarity DESC")
                .limit(5)
            )
            
            # Execute with embedding parameter
            result = await self.session.run(*query.build(), embedding=memory.embedding)
            
            # Process similar memories
            async for record in result:
                other_data = dict(record["other"])
                similarity = record["similarity"]
                other_id = UUID(other_data["id"])
                
                # Infer relationship type based on content and similarity
                rel_type = self._infer_relationship_type(
                    memory.content,
                    other_data.get("content", ""),
                    similarity
                )
                
                # Create relationship using repository
                await self.memory_repo.connect(
                    memory.id,
                    other_id,
                    rel_type,
                    {"strength": similarity, "auto_detected": True}
                )
                
                # Store relationship metadata as a node (for advanced querying)
                relationship = MemoryRelationship(
                    source_id=memory.id,
                    target_id=other_id,
                    relationship_type=rel_type,
                    strength=similarity,
                    metadata={"detection_method": "cosine_similarity"}
                )
                
                await self.relationship_repo.remember(relationship)
                relationships.append(relationship)
                
                logger.debug(f"Created {rel_type} relationship: {memory.id} -> {other_id} (strength={similarity:.3f})")
            
            return relationships
            
        except Exception as e:
            logger.error(f"Failed to detect relationships for {memory.id}: {e}")
            return relationships
    
    def _infer_relationship_type(
        self, 
        content1: str, 
        content2: str, 
        similarity: float
    ) -> str:
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
        self,
        memory: UserUtterance | AssistantUtterance,
        relationship_count: int
    ):
        """Update memory salience based on relationship count and strength."""
        # Boost salience based on how connected the memory is
        base_boost = 0.1 * relationship_count
        max_salience = min(1.0, memory.salience + base_boost)
        
        # Update using repository
        memory.salience = max_salience
        if isinstance(memory, UserUtterance):
            await self.user_repo.remember(memory)  # This will update existing
        else:
            await self.assistant_repo.remember(memory)
    
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
        
        try:
            # Stage 1: Generate query embedding and initial vector search
            query_embedding = await self.embeddings.embed_text(query)
            embedding = query_embedding
            
            # Get broad candidate set using similarity search
            candidates = await self.memory_repo.recall_any(
                similarity_search=(embedding, 0.5),  # Lower threshold for broad search
                limit=64
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
                            if hasattr(memory, 'topic_id') and memory.topic_id == topic_id:
                                topical.append(memory)
                            else:
                                non_topical.append(memory)
                        
                        # Reorder: topical memories first (with boost), then others
                        candidates = topical + non_topical
                        logger.debug(f"Stage 2: Boosted {len(topical)} memories from topic {topic_id}")
                    
                except Exception as e:
                    logger.warning(f"Stage 2 ontology boost failed: {e}")
            
            # Stage 3: Graph expansion (traverse relationships from top candidates)
            top_candidates = candidates[:10]  # Expand from best candidates only
            expanded_memories = await self._expand_via_relationships(
                top_candidates, expand_depth
            )
            
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
            
        except Exception as e:
            logger.error(f"Multi-stage recall failed: {e}", exc_info=True)
            # Fallback to simple similarity search
            return await self.memory_repo.recall_any(
                similarity_search=(embedding, 0.7),
                limit=k
            )
    
    async def _expand_via_relationships(
        self,
        seed_memories: list[Memory],
        depth: int = 2
    ) -> list[Memory]:
        """Expand memory set by following relationship edges."""
        if not seed_memories or depth <= 0:
            return []
        
        expanded = []
        visited_ids = {m.id for m in seed_memories}
        
        try:
            for seed in seed_memories:
                # Find memories connected to this seed
                builder = CypherQueryBuilder()
                seed_id_str = str(seed.id)
                query = (
                    builder
                    .match(lambda p, sid=seed_id_str: p
                          .node("Memory", "seed", id=sid)
                          .rel("RELATES_TO|SIMILAR_TO|FOLLOWED_BY", "r")
                          .node("Memory", "connected")
                          )
                    .where("connected.id <> seed.id")
                    .return_clause("connected", "r.strength as strength")
                    .order_by("r.strength DESC")
                    .limit(5)  # Limit expansion per seed
                )
                
                result = await self.session.run(*query.build())
                
                async for record in result:
                    connected_data = dict(record["connected"])
                    connected_id = UUID(connected_data["id"])
                    
                    if connected_id not in visited_ids:
                        # Convert to appropriate memory type based on memory_type
                        memory_type_str = connected_data.get("memory_type")
                        if memory_type_str:
                            try:
                                # Use discriminated union to automatically route to correct type
                                connected_memory = Memory.model_validate(connected_data)
                                expanded.append(connected_memory)
                                visited_ids.add(connected_id)
                            except Exception as e:
                                logger.warning(f"Failed to deserialize connected memory {connected_id}: {e}")
            
            # Recursively expand if depth > 1
            if depth > 1:
                recursive_expanded = await self._expand_via_relationships(expanded, depth - 1)
                for memory in recursive_expanded:
                    if memory.id not in visited_ids:
                        expanded.append(memory)
                        visited_ids.add(memory.id)
            
            return expanded
            
        except Exception as e:
            logger.error(f"Graph expansion failed: {e}")
            return []
    
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
        logger.debug(f"Calling recall_any with filters={filters}, similarity_search={'Yes' if similarity_search else 'No'}, limit={limit}")
        results = await self.memory_repo.recall_any(
            filters=filters,
            similarity_search=similarity_search,
            limit=limit
        )
        logger.debug(f"recall_any returned {len(results)} results")
        
        # Filter by memory types if specified
        if memory_types:
            type_values = {mt.value for mt in memory_types}
            results = [r for r in results if r.memory_type.value in type_values]
        
        logger.info(f"Search returned {len(results)} memories after filtering")
        return results
    
    async def get_conversation_history(
        self,
        conversation_id: UUID,
        limit: int = 100
    ) -> list[Memory]:
        """Get complete conversation history in chronological order."""
        return await self.memory_repo.recall_any(
            filters={"conversation_id": str(conversation_id)},
            limit=limit
        )
    
    @error_context(error_level=ErrorLevel.INFO)
    async def get_memory_relationships(
        self,
        memory_id: UUID
    ) -> list[MemoryRelationship]:
        """Get all relationships for a specific memory."""
        # Get both outgoing and incoming relationships
        outgoing = await self.relationship_repo.recall(
            MemoryRelationship,
            filters={"source_id": str(memory_id)}
        )
        
        incoming = await self.relationship_repo.recall(
            MemoryRelationship,
            filters={"target_id": str(memory_id)}
        )
        
        return outgoing + incoming
    
    async def get_topic_memories(
        self,
        topic_id: int,
        limit: int = 50
    ) -> list[Memory]:
        """Get all memories belonging to a specific topic cluster."""
        return await self.memory_repo.recall_any(
            filters={"topic_id": topic_id},
            limit=limit
        )
