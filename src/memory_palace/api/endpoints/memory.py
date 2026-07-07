"""Memory API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from memory_palace.api.dependencies import get_memory_service
from memory_palace.core.config import settings
from memory_palace.core.decorators import with_error_handling
from memory_palace.core.logging import get_logger
from memory_palace.services.memory_service import MemoryService

logger = get_logger(__name__)
router = APIRouter()


class StoreMemoryRequest(BaseModel):
    """Request model for storing a single memory."""

    content: str
    role: str = Field(..., pattern="^(user|assistant)$", description="Role: 'user' or 'assistant'")
    conversation_id: UUID | None = None

    # Memory importance/salience (0.0-1.0 scale)
    # Recalibrated scale (since we only store things worth remembering):
    #   0.0-0.2: Background context, ambient information
    #   0.3-0.4: Regular conversation, standard Q&A
    #   0.5-0.6: Interesting or useful information
    #   0.7-0.8: Important preferences, decisions, learning moments
    #   0.9-1.0: Critical memories - core beliefs, breakthroughs, defining moments
    # Default if not provided: 0.3 (regular conversation)
    salience: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Memory importance (0-1). Default 0.3. Use: 0.3=regular, 0.6=useful, 0.8=important, 1.0=critical. Must be a number",
    )

    emotional_valence: float = Field(
        0.0,
        ge=-1.0,
        le=1.0,
        description="Emotional tone of this memory: -1.0 (painful/negative) to 1.0 (joyful/positive). 0.0 = neutral.",
    )
    emotional_intensity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Emotional strength (0-1). High intensity slows forgetting and prioritizes consolidation.",
    )
    pinned: bool = Field(
        False,
        description="Pinned memories never decay or get archived. Reserve for identity anchors and defining moments.",
    )
    source: str | None = Field(
        None,
        description="Which interface is writing this memory (e.g. 'claude.ai', 'claude-code').",
    )

    @field_validator("salience", mode="before")
    @classmethod
    def validate_salience(cls, v):
        # Handle string numbers from JSON/MCP tool conversion
        if v is None:
            return v
        if isinstance(v, str):
            # Convert string to float without try-except
            # Let Pydantic handle the ValueError if it's not a valid number
            return float(v)
        if isinstance(v, int | float):
            return v
        # Raise structured error for invalid types
        from memory_palace.core.errors import ProcessingError

        raise ProcessingError(
            message="Salience must be a number between 0.0 and 1.0",
            details={
                "source": "memory_endpoint",
                "operation": "validate_salience",
                "field": "salience",
                "actual_value": str(v),
                "expected_type": "float",
                "constraint": "0.0 <= salience <= 1.0",
            },
        )


class StoreBatchRequest(BaseModel):
    """Request model for storing multiple memories."""

    memories: list[StoreMemoryRequest]
    create_temporal_links: bool = Field(
        default=False, description="Whether to create PRECEDES relationships between consecutive memories"
    )


class StoreMemoryResponse(BaseModel):
    """Response model for storing a memory."""

    memory_id: UUID
    message: str = "Memory stored successfully"


class StoreBatchResponse(BaseModel):
    """Response model for storing multiple memories."""

    memory_ids: list[UUID]
    message: str = "Memories stored successfully"


class SearchRequest(BaseModel):
    """Request model for searching memories."""

    query: str = Field(..., description="The retrieval cue — what to remember about")
    k: int = Field(10, description="Maximum memories to return")
    threshold: float = Field(0.7, description="Minimum semantic similarity for direct matches (0-1)")

    min_salience: float | None = Field(None, description="Only return memories at least this important (0-1)")
    topic_ids: list[int] | None = Field(None, description="Restrict to specific topic clusters")


class SearchResponse(BaseModel):
    """Response model for search results."""

    messages: list[dict]
    count: int


@router.post("/remember", response_model=StoreMemoryResponse, operation_id="remember")
@with_error_handling(reraise=True)
async def remember_message(
    request: StoreMemoryRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> StoreMemoryResponse:
    """Store a single memory."""
    logger.info(
        "Storing memory",
        extra={
            "content_length": len(request.content),
            "role": request.role,
            "conversation_id": str(request.conversation_id) if request.conversation_id else None,
        },
    )

    memory = await memory_service.remember_message(
        content=request.content,
        role=request.role,
        conversation_id=request.conversation_id,
        salience=request.salience,
        emotional_valence=request.emotional_valence,
        emotional_intensity=request.emotional_intensity,
        pinned=request.pinned,
        source=request.source,
    )

    logger.info("Successfully stored memory", extra={"memory_id": str(memory.id)})
    return StoreMemoryResponse(memory_id=memory.id)


@router.post("/remember/batch", response_model=StoreBatchResponse, operation_id="remember_batch")
@with_error_handling(reraise=True)
async def remember_batch(
    request: StoreBatchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> StoreBatchResponse:
    """Store multiple memories at once."""
    logger.info(
        "Storing batch of memories",
        extra={
            "count": len(request.memories),
        },
    )

    memory_ids = []
    for idx, mem_request in enumerate(request.memories):
        memory = await memory_service.remember_message(
            content=mem_request.content,
            role=mem_request.role,
            conversation_id=mem_request.conversation_id,
            salience=mem_request.salience,
            emotional_valence=mem_request.emotional_valence,
            emotional_intensity=mem_request.emotional_intensity,
            pinned=mem_request.pinned,
            source=mem_request.source,
        )
        memory_ids.append(memory.id)

        # Optionally create PRECEDES relationship between consecutive memories
        if request.create_temporal_links and idx > 0:
            await memory_service.create_relationship(
                source_id=memory_ids[idx - 1], target_id=memory.id, relationship_type="PRECEDES"
            )

    logger.info("Successfully stored batch", extra={"count": len(memory_ids)})
    return StoreBatchResponse(memory_ids=memory_ids)


@router.post("/recall", response_model=SearchResponse, operation_id="recall")
@with_error_handling(reraise=True)
async def recall_memories(
    request: SearchRequest,
    memory_service: MemoryService = Depends(get_memory_service),
) -> SearchResponse:
    """Recall relevant memories by cue.

    Combines direct semantic search with graph pattern completion
    (memories associated with the direct hits), ranked by a blend of
    similarity, graph activation, and salience. Recalled memories are
    reinforced: retrieval strengthens them.
    """
    logger.info("Recalling memories", extra={"query": request.query, "k": request.k, "threshold": request.threshold})

    results = await memory_service.recall(
        query=request.query,
        k=request.k,
        similarity_threshold=request.threshold,
        min_salience=request.min_salience,
        topic_ids=request.topic_ids,
    )

    from memory_palace.domain.models.memories import TopicCluster

    role_names = {
        "friend_utterance": settings.friend_name,
        "claude_utterance": settings.claude_name,
    }

    message_dicts = []
    for r in results:
        msg = r.memory
        msg_dict = {
            "id": str(msg.id),
            "timestamp": msg.timestamp.isoformat(),
            "memory_type": msg.memory_type.value,
            "score": round(r.score, 4),
            "similarity": round(r.similarity, 4),
            "activation": round(r.activation, 4),
            "salience": round(getattr(msg, "salience", 0.0), 4),
        }

        if isinstance(msg, TopicCluster):
            msg_dict["content"] = msg.label or f"Topic Cluster {msg.cluster_id}"
            msg_dict["role"] = "topic_cluster"
        else:
            # FriendUtterance, ClaudeUtterance, SystemNote have .content
            msg_dict["content"] = msg.content
            msg_dict["role"] = role_names.get(msg.memory_type.value, msg.memory_type.value)

        message_dicts.append(msg_dict)

    logger.info("Recall completed", extra={"result_count": len(results)})

    return SearchResponse(
        messages=message_dicts,
        count=len(message_dicts),
    )
