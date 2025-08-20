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
    metadata: dict | None = None

    # Incremental ontology support
    ontology_path: list[str] | None = None

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

    query: str
    k: int = 10
    threshold: float = 0.7

    # Enhanced search filters
    min_salience: float | None = None  # Only return memories above this importance (0.0-1.0)
    topic_ids: list[int] | None = None
    ontology_path: list[str] | None = None


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
        salience=request.salience,  # Pass through the importance rating
        ontology_path=request.ontology_path,
        metadata=request.metadata,
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
            ontology_path=mem_request.ontology_path,
            metadata=mem_request.metadata,
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
    """Search and recall relevant memories."""
    logger.info("Searching memories", extra={"query": request.query, "k": request.k, "threshold": request.threshold})

    messages = await memory_service.search_memories(
        query=request.query,
        limit=request.k,  # Map k to limit
        similarity_threshold=request.threshold,  # Pass threshold to service
        min_salience=request.min_salience,
        topic_id=request.topic_ids[0] if request.topic_ids else None,
    )

    # Convert to dict for response
    message_dicts = []
    for msg in messages:
        msg_dict = {
            "id": str(msg.id),
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
            "memory_type": msg.memory_type.value,
        }
        # Add role based on memory type with personalized names
        if msg.memory_type.value == "friend_utterance":
            msg_dict["role"] = settings.friend_name
        elif msg.memory_type.value == "claude_utterance":
            msg_dict["role"] = settings.claude_name
        elif msg.memory_type.value == "user_utterance":  # Legacy support
            msg_dict["role"] = settings.friend_name
        elif msg.memory_type.value == "assistant_utterance":  # Legacy support
            msg_dict["role"] = settings.claude_name
        else:
            msg_dict["role"] = msg.memory_type.value

        message_dicts.append(msg_dict)

    logger.info("Search completed", extra={"result_count": len(messages)})

    return SearchResponse(
        messages=message_dicts,
        count=len(messages),
    )
