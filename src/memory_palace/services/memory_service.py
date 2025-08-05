"""Memory storage and retrieval service."""

import json
from datetime import datetime
from uuid import UUID

from memory_palace.core.logging import get_logger
from memory_palace.domain.models import (
    ConversationTurn,
    EmbeddingType,
    Message,
    MessageRole,
)
from memory_palace.infrastructure.embeddings import VoyageEmbeddingService
from memory_palace.infrastructure.neo4j.simple_driver import Neo4jDriver

logger = get_logger(__name__)


class MemoryService:
    """Service for managing conversation memories."""

    def __init__(
        self,
        neo4j_driver: Neo4jDriver,
        embedding_service: VoyageEmbeddingService,
    ):
        self.neo4j = neo4j_driver
        self.embeddings = embedding_service

    async def store_turn(
        self,
        user_content: str,
        assistant_content: str,
        conversation_id: UUID | None = None,
        metadata: dict | None = None,
        ontology_path: list[str] | None = None,
        salience: float | None = None,
    ) -> ConversationTurn:
        """Store a conversation turn (user message + assistant response)."""
        # Create messages with ontology support
        user_msg = Message(
            role=MessageRole.USER,
            content=user_content,
            timestamp=datetime.utcnow(),
        )

        assistant_msg = Message(
            role=MessageRole.ASSISTANT,
            content=assistant_content,
            timestamp=datetime.utcnow(),
        )
        
        # Add ontology metadata if provided
        if ontology_path:
            user_msg.ontology_path = ontology_path
            assistant_msg.ontology_path = ontology_path
        
        if salience is not None:
            user_msg.salience = salience
            assistant_msg.salience = salience

        # Generate embeddings
        user_embedding = await self.embeddings.embed_single(user_content)
        assistant_embedding = await self.embeddings.embed_single(assistant_content)

        user_msg.embedding = user_embedding
        assistant_msg.embedding = assistant_embedding

        # Create turn
        turn = ConversationTurn(
            user_message=user_msg,
            assistant_message=assistant_msg,
            metadata=metadata or {},
        )

        # Store in Neo4j
        await self._store_turn_in_neo4j(turn, conversation_id)

        logger.info(f"Stored conversation turn {turn.id}")
        return turn

    async def _store_turn_in_neo4j(
        self,
        turn: ConversationTurn,
        conversation_id: UUID | None,
    ):
        """Store turn in Neo4j graph."""
        # This will use the Neo4j query builder we copied from Sokrates
        # For now, a simple implementation
        query = """
        MERGE (c:Conversation {id: $conversation_id})
        CREATE (t:Turn {
            id: $turn_id,
            timestamp: $timestamp,
            metadata_json: $metadata_json
        })
        CREATE (u:Message {
            id: $user_msg_id,
            role: 'user',
            content: $user_content,
            embedding: $user_embedding,
            timestamp: $user_timestamp,
            ontology_path: $user_ontology_path,
            salience: $user_salience
        })
        CREATE (a:Message {
            id: $assistant_msg_id,
            role: 'assistant', 
            content: $assistant_content,
            embedding: $assistant_embedding,
            timestamp: $assistant_timestamp,
            ontology_path: $assistant_ontology_path,
            salience: $assistant_salience
        })
        CREATE (c)-[:HAS_TURN]->(t)
        CREATE (t)-[:USER_MESSAGE]->(u)
        CREATE (t)-[:ASSISTANT_MESSAGE]->(a)
        CREATE (u)-[:FOLLOWED_BY]->(a)
        """

        params = {
            "conversation_id": str(conversation_id) if conversation_id else str(turn.id),
            "turn_id": str(turn.id),
            "timestamp": turn.timestamp.isoformat(),
            "metadata_json": json.dumps(turn.metadata),
            "user_msg_id": str(turn.user_message.id),
            "user_content": turn.user_message.content,
            "user_embedding": turn.user_message.embedding,
            "user_timestamp": turn.user_message.timestamp.isoformat(),
            "user_ontology_path": getattr(turn.user_message, 'ontology_path', []),
            "user_salience": getattr(turn.user_message, 'salience', 0.5),
            "assistant_msg_id": str(turn.assistant_message.id),
            "assistant_content": turn.assistant_message.content,
            "assistant_embedding": turn.assistant_message.embedding,
            "assistant_timestamp": turn.assistant_message.timestamp.isoformat(),
            "assistant_ontology_path": getattr(turn.assistant_message, 'ontology_path', []),
            "assistant_salience": getattr(turn.assistant_message, 'salience', 0.5),
        }

        async with self.neo4j.session() as session:
            await session.run(query, params)

    async def search_memories(
        self,
        query: str,
        k: int = 10,
        threshold: float = 0.7,
    ) -> list[Message]:
        """Search for relevant memories using semantic similarity."""
        # Generate query embedding
        query_embedding = await self.embeddings.embed_single(
            query, embedding_type=EmbeddingType.QUERY
        )

        # Search in Neo4j using vector similarity (Community Edition compatible)
        # Calculate cosine similarity manually since GDS is not available
        search_query = """
        MATCH (m:Message)
        WHERE m.embedding IS NOT NULL
        WITH m, 
             reduce(dot = 0.0, i IN range(0, size($query_embedding)-1) | 
                dot + m.embedding[i] * $query_embedding[i]) AS dotProduct,
             reduce(norm1 = 0.0, i IN range(0, size(m.embedding)-1) | 
                norm1 + m.embedding[i] * m.embedding[i]) AS norm1,
             reduce(norm2 = 0.0, i IN range(0, size($query_embedding)-1) | 
                norm2 + $query_embedding[i] * $query_embedding[i]) AS norm2
        WITH m, dotProduct / (sqrt(norm1) * sqrt(norm2)) AS similarity
        WHERE similarity > $threshold
        RETURN m, similarity
        ORDER BY similarity DESC
        LIMIT $k
        """

        params = {
            "query_embedding": query_embedding,
            "threshold": threshold,
            "k": k,
        }

        async with self.neo4j.session() as session:
            result = await session.run(search_query, params)
            messages = []
            async for record in result:
                msg_data = record["m"]
                msg = Message(
                    id=msg_data["id"],
                    role=MessageRole(msg_data["role"]),
                    content=msg_data["content"],
                    timestamp=datetime.fromisoformat(msg_data["timestamp"]),
                    embedding=msg_data["embedding"],
                )
                messages.append(msg)

        return messages
